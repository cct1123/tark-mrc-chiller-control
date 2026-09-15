"""Dash HTTP and presentation tests using the real simulator, driver and monitor."""

import csv
import re
from dataclasses import replace
from datetime import timedelta
from json import dumps
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from plotly.utils import PlotlyJSONEncoder

from tark_chiller import Chiller, Simulator
from tark_chiller.gui import create_app, render_snapshot


def text(value):
    return dumps(value, cls=PlotlyJSONEncoder, ensure_ascii=False)


def wait_for(predicate):
    deadline = monotonic() + 2
    while not predicate():
        assert monotonic() < deadline, "Monitor did not publish the expected state"
        sleep(0.005)


@pytest.fixture
def dashboard(tmp_path):
    with Chiller(Simulator(time_constant_s=0.1)) as chiller:
        path = tmp_path / "session.csv"
        monitor = chiller.start_monitoring(interval_s=0.02, csv_path=path)
        wait_for(lambda: monitor.snapshot().sample_count > 0)
        yield chiller, monitor, create_app(chiller, monitor), path


def refresh(app):
    return app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": next(key for key in app.callback_map if "live-status" in key),
            "outputs": [
                {"id": "live-status", "property": "children"},
                {"id": "temperature-history", "property": "figure"},
            ],
            "inputs": [{"id": "refresh", "property": "n_intervals", "value": 1}],
            "state": [],
            "changedPropIds": ["refresh.n_intervals"],
        },
    )


def submit(app, value):
    return app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": "setpoint-result.children",
            "outputs": {"id": "setpoint-result", "property": "children"},
            "inputs": [{"id": "apply-setpoint", "property": "n_clicks", "value": 1}],
            "state": [{"id": "setpoint-input", "property": "value", "value": value}],
            "changedPropIds": ["apply-setpoint.n_clicks"],
        },
    )


def test_simulator_monitor_csv_and_dash_share_real_readings(dashboard):
    chiller, monitor, app, path = dashboard
    response = submit(app, 18)
    assert response.status_code == 200
    assert "completed" in text(response.json)
    wait_for(lambda: monitor.snapshot().latest.temperature_c < 19.5)
    response = refresh(app)
    assert response.status_code == 200
    content = response.json["response"]
    assert "18.00" in text(content["live-status"])
    assert "CSV Enabled" in text(content["live-status"])
    figure = content["temperature-history"]["figure"]
    assert figure["layout"]["xaxis"]["title"]["text"] == "Time (UTC)"
    assert figure["layout"]["yaxis"]["title"]["text"] == "Temperature (°C)"
    assert figure["data"][1]["y"][-1] == 18
    chiller.stop_monitoring()
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == monitor.snapshot().logged_samples
    assert float(rows[-1]["temperature_c"]) < 19.5
    assert float(rows[-1]["setpoint_c"]) == 18


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "20",
        "20 °C",
        "68 °F",
        [],
        {},
        float("nan"),
        float("inf"),
        -float("inf"),
        -1,
        40.01,
        68,
        293.15,
    ],
)
def test_invalid_http_setpoint_cannot_change_target(dashboard, value):
    chiller, _monitor, app, _path = dashboard
    response = submit(app, value)
    assert response.status_code == 200
    assert "Rejected" in text(response.json)
    assert chiller.read_setpoint() == 20


def test_disconnected_write_returns_understandable_error(dashboard):
    chiller, _monitor, app, _path = dashboard
    chiller.disconnect()
    response = submit(app, 18)
    assert response.status_code == 200
    assert "disconnected" in text(response.json).lower()
    assert not chiller.is_connected


def test_read_only_dashboard_refuses_even_direct_callback_submission(dashboard):
    chiller, monitor, _, _ = dashboard
    app = create_app(chiller, monitor, allow_setpoints=False)
    assert "Read-only session" in app.server.test_client().get("/_dash-layout").get_data(
        as_text=True
    )
    response = submit(app, 18)
    assert "read-only" in text(response.json)
    assert chiller.read_setpoint() == 20 and chiller.is_connected


def test_refresh_and_reload_do_not_start_or_read_a_device(dashboard, monkeypatch):
    chiller, monitor, app, _path = dashboard
    chiller.stop_monitoring()
    count = monitor.snapshot().sample_count

    def forbidden(*_args, **_kwargs):
        pytest.fail("Dashboard attempted to own device or monitor lifecycle")

    for method in (
        "connect",
        "read_temperature",
        "read_setpoint",
        "read_status",
        "start_monitoring",
    ):
        monkeypatch.setattr(chiller, method, forbidden)
    client = app.server.test_client()
    for _ in range(2):
        assert client.get("/").status_code == 200
        layout = client.get("/_dash-layout")
        assert layout.status_code == 200
        assert "telemetry unavailable" in text(layout.json)
        assert "connect-device" not in text(layout.json)
        assert refresh(app).status_code == 200
    assert monitor.snapshot().sample_count == count
    assert not monitor.running


def test_pending_device_read_does_not_block_gui_refresh(dashboard, monkeypatch):
    chiller, monitor, app, _path = dashboard
    entered, release, responded = Event(), Event(), Event()
    original = chiller.read_temperature

    def pending_read():
        entered.set()
        assert release.wait(2)
        return original()

    monkeypatch.setattr(chiller, "read_temperature", pending_read)
    assert entered.wait(1)
    response = []

    def request():
        response.append(refresh(app))
        responded.set()

    thread = Thread(target=request)
    thread.start()
    try:
        assert responded.wait(1), "GUI waited for the device instead of reading a snapshot"
        assert response[0].status_code == 200
        assert monitor.running
    finally:
        release.set()
        thread.join(2)


def test_reload_uses_latest_readback_and_current_monotonic_age(dashboard, monkeypatch):
    from tark_chiller import gui

    chiller, monitor, app, _path = dashboard
    client = app.server.test_client()
    assert "20.00" in text(client.get("/_dash-layout").json)
    chiller.set_setpoint(18)
    wait_for(lambda: monitor.snapshot().latest.setpoint_c == 18)
    assert "18.00" in text(client.get("/_dash-layout").json)
    monkeypatch.setattr(gui, "monotonic", lambda: monotonic() + 10)
    result = text(client.get("/_dash-layout").json)
    assert "Stale" in result and "No fresh reading" in result
    assert "18.00" not in result


@pytest.mark.parametrize("running, stale", [(False, False), (True, True)])
def test_old_readings_remain_in_history_but_are_not_live(dashboard, monkeypatch, running, stale):
    from tark_chiller import gui

    _chiller, monitor, _app, _path = dashboard
    snapshot = monitor.snapshot()
    latest = replace(
        snapshot.latest, timestamp_utc=snapshot.latest.timestamp_utc + timedelta(days=1)
    )
    snapshot = replace(snapshot, running=running, history=(latest,))
    monkeypatch.setattr(gui, "monotonic", lambda: latest.sampled_monotonic + (10 if stale else 0))
    cards, figure = render_snapshot(snapshot)
    assert "No fresh reading" in text(cards)
    assert "20.00" not in text(cards)
    assert figure.data[0].y[-1] == 20
    assert "Stale" in text(cards) if stale else "Monitoring stopped" in text(cards)


def test_fault_and_csv_failure_are_explicit(dashboard):
    _chiller, monitor, _app, _path = dashboard
    snapshot = monitor.snapshot()
    failed = replace(
        snapshot.latest,
        temperature_c=None,
        setpoint_c=None,
        status=replace(snapshot.latest.status, connected=False),
        error="Read timed out",
    )
    cards, figure = render_snapshot(
        replace(
            snapshot,
            history=snapshot.history + (failed,),
            logging_error="CSV disk full",
            failed_samples=1,
        )
    )
    assert all(
        word in text(cards)
        for word in ("Unavailable", "Read timed out", "CSV Failed", "CSV disk full")
    )
    assert "20.00" not in text(cards)
    assert figure.data[0].y[-1] is None and figure.data[0].connectgaps is False


def test_csv_failure_preserves_temperature_and_empty_history_is_honest(dashboard):
    _chiller, monitor, _app, _path = dashboard
    snapshot = monitor.snapshot()
    cards, _figure = render_snapshot(replace(snapshot, logging_error="CSV disk full"))
    assert "CSV Failed" in text(cards) and "20.00" in text(cards)
    cards, figure = render_snapshot(replace(snapshot, history=(), sample_count=0))
    assert "Waiting for the first sample" in text(cards)
    assert "No sample" in text(cards) and "simulator" not in text(cards)
    assert "20.00" not in text(cards) and not figure.data[0].y


def test_dashboard_serves_css_and_license_locally(dashboard):
    _chiller, _monitor, app, _path = dashboard
    client = app.server.test_client()
    styles = re.findall(
        r'<link[^>]+href="([^"]+\.css[^\"]*)"', client.get("/").get_data(as_text=True)
    )
    assert len(styles) == 2 and all(url.startswith("/assets/") for url in styles)
    for url in styles:
        response = client.get(url)
        assert response.status_code == 200 and "text/css" in response.content_type
    assert "Bootstrap  v5.3.8" in client.get(styles[0]).get_data(as_text=True)
    assert "The MIT License" in client.get("/assets/bootstrap-LICENSE.txt").get_data(as_text=True)
