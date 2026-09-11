"""TEST-008/009: actual Dash callback transport, without a browser or device."""

from datetime import timedelta
from json import dumps

import pytest
from plotly.utils import PlotlyJSONEncoder

from development.testing import make_fake_device
from tark_chiller import Chiller, SimulatedDevice
from tark_chiller.gui import create_app, render_snapshot, submit_setpoint
from tark_chiller.monitoring import LiveState, Monitor


def status_text(component):
    return dumps(component, cls=PlotlyJSONEncoder, ensure_ascii=False)


@pytest.fixture
def dashboard():
    chiller = Chiller(SimulatedDevice())
    chiller.connect()
    state = LiveState()
    monitor = Monitor(chiller, state)
    monitor.poll_once()
    state.set_running(True)
    app = create_app(chiller, state)
    yield chiller, state, monitor, app
    chiller.disconnect()


def test_render_live_stale_and_error_states(dashboard):
    chiller, state, monitor, _app = dashboard
    snap = state.snapshot()
    cards, figure = render_snapshot(snap, now=snap.latest.timestamp_utc)
    text = status_text(cards)
    assert "simulator" in text and "Connected" in text
    assert "20.00" in text and "°C" in text
    assert figure.layout.xaxis.title.text == "Time (UTC)"
    assert figure.layout.yaxis.title.text == "Temperature (°C)"
    assert len(figure.data) == 2
    cards, _ = render_snapshot(snap, now=snap.latest.timestamp_utc + timedelta(seconds=10))
    assert "Stale" in status_text(cards)
    chiller.disconnect()
    monitor.poll_once()
    state.set_error("CSV test failure", logging=True)
    cards, figure = render_snapshot(state.snapshot())
    text = status_text(cards)
    assert "Unavailable" in text and "ChillerConnectionError" in text
    assert "CSV test failure" in text
    assert figure.data[0].y[-1] is None
    assert figure.data[0].connectgaps is False


def test_http_layout_and_refresh_do_not_acquire(dashboard):
    _chiller, state, _monitor, app = dashboard
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    assert "telemetry unavailable" in layout.get_data(as_text=True)
    count = len(state.snapshot().history)
    key = next(k for k in app.callback_map if "live-status" in k)
    for tick in range(2):
        response = client.post(
            "/_dash-update-component",
            json={
                "output": key,
                "outputs": [
                    {"id": "live-status", "property": "children"},
                    {"id": "temperature-history", "property": "figure"},
                ],
                "inputs": [{"id": "refresh", "property": "n_intervals", "value": tick}],
                "state": [],
                "changedPropIds": ["refresh.n_intervals"],
            },
        )
        assert response.status_code == 200
        assert "simulator" in status_text(response.json["response"]["live-status"]["children"])
    assert len(state.snapshot().history) == count


@pytest.mark.parametrize("value", [None, True, "12", -1, 41, float("nan"), float("inf")])
def test_invalid_input_rejected_at_server_boundary(dashboard, value):
    chiller, _state, _monitor, _app = dashboard
    assert "Rejected" in submit_setpoint(chiller, value)
    assert chiller.get_setpoint() == 20


def test_actual_setpoint_callback_uses_api_and_readback(dashboard):
    chiller, state, monitor, app = dashboard
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json={
            "output": "setpoint-result.children",
            "outputs": {"id": "setpoint-result", "property": "children"},
            "inputs": [{"id": "apply-setpoint", "property": "n_clicks", "value": 1}],
            "state": [{"id": "setpoint-input", "property": "value", "value": 18}],
            "changedPropIds": ["apply-setpoint.n_clicks"],
        },
    )
    assert response.status_code == 200
    assert "completed" in response.json["response"]["setpoint-result"]["children"]
    assert chiller.get_setpoint() == 18
    monitor.poll_once()
    _text, figure = render_snapshot(state.snapshot())
    assert figure.data[1].y[-1] == 18


def test_freshness_uses_monotonic_clock_despite_wall_clock_change(dashboard, monkeypatch):
    from dataclasses import replace

    from tark_chiller import gui

    _chiller, state, _monitor, _app = dashboard
    snapshot = state.snapshot()
    latest = replace(
        snapshot.latest, timestamp_utc=snapshot.latest.timestamp_utc + timedelta(days=1)
    )
    snapshot = replace(snapshot, history=(latest,))
    monkeypatch.setattr(gui, "monotonic", lambda: latest.sampled_monotonic + 10)
    cards, _figure = render_snapshot(snapshot)
    text = status_text(cards)
    assert "Stale" in text and "Sample age 10.0 s" in text


def test_gui_connection_callbacks_preserve_monitor_ownership(dashboard):
    chiller, state, monitor, app = dashboard
    client = app.server.test_client()
    for button, connected in (("disconnect-device", False), ("connect-device", True)):
        response = client.post(
            "/_dash-update-component",
            json={
                "output": "connection-result.children",
                "outputs": {"id": "connection-result", "property": "children"},
                "inputs": [
                    {"id": "connect-device", "property": "n_clicks", "value": 1},
                    {"id": "disconnect-device", "property": "n_clicks", "value": 1},
                ],
                "state": [],
                "changedPropIds": [button + ".n_clicks"],
            },
        )
        assert response.status_code == 200
        assert "failed" not in response.json["response"]["connection-result"]["children"]
        assert chiller.is_connected is connected
        assert state.snapshot().running
        assert monitor.poll_once().status.connected is connected


def test_connect_does_not_claim_stopped_monitor_will_resume(dashboard):
    _chiller, state, _monitor, app = dashboard
    state.set_running(False)
    with app.server.test_client() as client:
        response = client.post(
            "/_dash-update-component",
            json={
                "output": "connection-result.children",
                "outputs": {"id": "connection-result", "property": "children"},
                "inputs": [
                    {"id": "connect-device", "property": "n_clicks", "value": 1},
                    {"id": "disconnect-device", "property": "n_clicks", "value": 0},
                ],
                "state": [],
                "changedPropIds": ["connect-device.n_clicks"],
            },
        )
    assert "monitoring is stopped" in response.json["response"]["connection-result"]["children"]
    assert not state.snapshot().running


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
        68,  # A Fahrenheit-looking input is never converted implicitly.
        293.15,  # Neither is a Kelvin-looking input.
    ],
)
def test_adversarial_http_setpoint_cannot_reach_serial_write(value, monkeypatch):
    def no_os_serial(**settings):
        pytest.fail("GUI test attempted a physical serial factory")

    monkeypatch.setattr("tark_chiller.transport._serial_factory", no_os_serial)
    device, endpoint = make_fake_device()
    chiller = Chiller(device)
    chiller.connect()
    try:
        app = create_app(chiller, LiveState())
        wire_counts = endpoint.operation_counts
        with app.server.test_client() as client:
            response = client.post(
                "/_dash-update-component",
                json={
                    "output": "setpoint-result.children",
                    "outputs": {"id": "setpoint-result", "property": "children"},
                    "inputs": [{"id": "apply-setpoint", "property": "n_clicks", "value": 1}],
                    "state": [{"id": "setpoint-input", "property": "value", "value": value}],
                    "changedPropIds": ["apply-setpoint.n_clicks"],
                },
            )
        assert response.status_code == 200
        assert "Rejected" in response.json["response"]["setpoint-result"]["children"]
        assert endpoint.operation_counts == wire_counts
        assert endpoint.applied_setpoints == 0
        assert chiller.get_setpoint() == 20
    finally:
        chiller.disconnect()


def test_connection_wording_identifies_last_poll_without_refresh_io(dashboard):
    chiller, state, _monitor, _app = dashboard
    before_disconnect = state.snapshot()
    chiller.disconnect()
    cards, _figure = render_snapshot(state.snapshot())
    text = status_text(cards)
    assert "Last poll: Connected" in text
    assert "Sample age" in text
    assert state.snapshot().sample_count == before_disconnect.sample_count


@pytest.mark.parametrize("running, age_s", [(False, 0), (True, 10)])
def test_stopped_or_stale_readings_are_not_presented_as_current(dashboard, running, age_s):
    _chiller, state, _monitor, _app = dashboard
    state.set_running(running)
    snapshot = state.snapshot()
    cards, figure = render_snapshot(
        snapshot, now=snapshot.latest.timestamp_utc + timedelta(seconds=age_s)
    )
    text = status_text(cards)
    assert "No fresh reading" in text
    assert "20.00" not in text
    assert figure.data[0].y[-1] == 20
    assert figure.data[1].y[-1] == 20


def test_dashboard_serves_its_styles_without_a_cdn(dashboard):
    import re

    _chiller, _state, _monitor, app = dashboard
    client = app.server.test_client()
    index = client.get("/").get_data(as_text=True)
    styles = re.findall(r'<link[^>]+href="([^"]+\.css[^\"]*)"', index)
    assert len(styles) == 2
    assert all(url.startswith("/assets/") for url in styles)
    assert "00-bootstrap.min.css" in styles[0]
    assert "instrument.css" in styles[1]
    for url in styles:
        response = client.get(url)
        assert response.status_code == 200
        assert "text/css" in response.content_type
    assert "Bootstrap  v5.3.8" in client.get(styles[0]).get_data(as_text=True)
    assert "The MIT License" in client.get("/assets/bootstrap-LICENSE.txt").get_data(as_text=True)


def test_logging_failure_is_visible_without_hiding_fresh_temperature(dashboard):
    _chiller, state, _monitor, _app = dashboard
    state.set_error("CSV disk full", logging=True)
    snapshot = state.snapshot()
    cards, _figure = render_snapshot(snapshot, now=snapshot.latest.timestamp_utc)
    text = status_text(cards)
    assert "CSV Failed" in text and "CSV disk full" in text
    assert "Software fault" in text
    assert "20.00" in text


def test_unsampled_dashboard_has_no_invented_reading_or_backend():
    cards, figure = render_snapshot(LiveState().snapshot())
    text = status_text(cards)
    assert "Waiting for the first sample" in text
    assert "No sample" in text
    assert "20.00" not in text and "simulator" not in text
    assert not figure.data[0].y


def test_reload_reads_current_snapshot_and_recomputes_age(dashboard, monkeypatch):
    from tark_chiller import gui

    chiller, state, monitor, app = dashboard
    client = app.server.test_client()
    assert "20.00" in status_text(client.get("/_dash-layout").json)

    chiller.set_setpoint(18)
    monitor.poll_once()
    snapshot = state.snapshot()
    reloaded = client.get("/_dash-layout")
    assert reloaded.status_code == 200
    assert "18.00" in status_text(reloaded.json)

    monkeypatch.setattr(gui, "monotonic", lambda: snapshot.latest.sampled_monotonic + 10)
    stale_reload = status_text(client.get("/_dash-layout").json)
    assert "Stale" in stale_reload and "Sample age 10.0 s" in stale_reload
    assert "18.00" not in stale_reload
    assert state.snapshot().sample_count == snapshot.sample_count
