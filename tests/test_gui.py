"""Dash HTTP and presentation tests using the real simulator, driver and chiller."""

import csv
import re
from datetime import timedelta
from json import dumps
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from plotly.utils import PlotlyJSONEncoder

from tark_chiller.controller import ProtocolError
from tark_chiller.gui import create_app, render_snapshot
from tark_chiller.simulator import CalSimulator, T255PSimulator


def text(value):
    return dumps(value, cls=PlotlyJSONEncoder, ensure_ascii=False)


def wait_for(predicate):
    deadline = monotonic() + 2
    while not predicate():
        assert monotonic() < deadline, "Monitor did not publish the expected state"
        sleep(0.005)


def test_latched_protocol_fault_stops_polling_and_is_visible_in_dashboard():
    endpoint = T255PSimulator()
    with endpoint.chiller() as chiller:
        endpoint.alarms = "000100"  # Sensor alarm: the production protocol rejects PV.
        chiller.start_monitoring(interval_s=0.01)
        wait_for(lambda: not chiller.running)
        snapshot = chiller.snapshot()
        assert snapshot["sample_count"] == snapshot["failed_samples"] == 1
        status, _ = render_snapshot(snapshot)
        content = text(status)
        assert "Monitoring stopped" in content
        assert "T255P sensor alarm" in content
        assert "No fresh reading" in content


def test_freshness_uses_poll_completion_instead_of_start(dashboard, monkeypatch):
    from tark_chiller import gui

    chiller, _, _ = dashboard
    snapshot = chiller.snapshot()
    latest = {**snapshot["history"][-1], "sampled_monotonic": 100.0,
              "completed_monotonic": 110.0}
    monkeypatch.setattr(gui, "monotonic", lambda: 110.5)
    cards, _ = render_snapshot({**snapshot, "history": [latest]})
    content = text(cards)
    assert "20.00" in content and "Monitoring" in content
    assert "0.5 s ago" in content and "Stale" not in content


def test_recovery_and_service_fault_have_distinct_consistent_states(dashboard):
    chiller, _, _ = dashboard
    snapshot = chiller.snapshot()
    cards, _ = render_snapshot({**snapshot, "recovery_error": "Wrong command"})
    content = text(cards)
    assert "Recovering" in content and "Wrong command" in content
    assert "20.00" not in content and "Monitoring stopped" not in content
    cards, _ = render_snapshot({**snapshot, "service_error": "Device rejected command",
                               "recovery_error": "Wrong command"})
    content = text(cards)
    assert "Monitoring fault" in content and "20.00" not in content
    assert "Monitoring stopped" not in content  # The worker has not finished yet.
    assert "Recovering" not in content


def test_failed_poll_between_retries_still_shows_recovering(dashboard):
    chiller, _, _ = dashboard
    snapshot = chiller.snapshot()
    latest = {**snapshot["history"][-1], "error": "T255P bad command",
              "status": {"connected": False, "backend": "hardware", "detail": "poll failed"}}
    cards, _ = render_snapshot({**snapshot, "history": [latest], "recovery_error": ""})
    content = text(cards)
    assert "Recovering" in content and "bad command" in content
    assert "Monitoring stopped" not in content


@pytest.fixture
def dashboard(tmp_path):
    # Accelerate modeled thermal time for the cooling assertion. Serial timeout
    # waits remain real; the ordinary monitor still sends actual protocol frames.
    endpoint = T255PSimulator(clock=lambda: monotonic() * 10)
    # Keep the host ceiling at 40 C so invalid-target tests reject before I/O.
    with endpoint.chiller(setpoint_range=(5.0, 40.0)) as chiller:
        path = tmp_path / "session.csv"
        chiller.start_monitoring(interval_s=0.02, csv_path=path)
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)
        yield chiller, create_app(chiller), path


def refresh(app):
    return app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": next(key for key in app.callback_map if "live-status" in key),
            "outputs": [
                {"id": "live-status", "property": "children"},
                {"id": "temperature-history", "property": "figure"},
                {"id": "toggle-monitoring", "property": "children"},
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


def submit_mode(app, action):
    return app.server.test_client().post("/_dash-update-component", json={
        "output": "mode-result.children",
        "outputs": {"id": "mode-result", "property": "children"},
        "inputs": [{"id": "run-device", "property": "n_clicks", "value": 1},
                   {"id": "standby-device", "property": "n_clicks", "value": 1}],
        "state": [], "changedPropIds": [f"{action}.n_clicks"],
    })


def test_mode_buttons_use_production_api_and_leave_monitoring_active():
    endpoint = T255PSimulator()
    with endpoint.chiller() as chiller:
        chiller.start_monitoring(interval_s=0.01)
        app = create_app(chiller)
        layout = app.server.test_client().get("/_dash-layout").get_data(as_text=True)
        assert "run-device" in layout and "standby-device" in layout
        for action, mode in [("standby-device", "1"), ("run-device", "2")]:
            result = submit_mode(app, action)
            assert result.status_code == 200 and "verified" in text(result.json)
            assert endpoint.mode == mode and chiller.running
        assert not endpoint.applied_writes


@pytest.mark.parametrize("action", ["run-device", "standby-device"])
def test_read_only_gui_rejects_direct_mode_callbacks(action):
    endpoint = T255PSimulator()
    with endpoint.chiller() as chiller:
        app = create_app(chiller, allow_setpoints=False)
        result = submit_mode(app, action)
        assert "Rejected" in text(result.json) and endpoint.mode == "2"
        app = create_app(chiller, allow_mode_changes=False)
        assert "Rejected" in text(submit_mode(app, action).json)


def test_mode_http_errors_are_visible_without_connecting_or_retrying():
    endpoint = T255PSimulator()
    chiller = endpoint.chiller()
    app = create_app(chiller)
    assert "disconnected" in text(submit_mode(app, "run-device").json).lower()
    assert not endpoint.is_open
    with chiller:
        endpoint.alarms = "010000"
        assert "RUN refused" in text(submit_mode(app, "run-device").json)
        assert not endpoint.is_open


def test_cal_api_and_dashboard_refuse_mode_control_without_io(monkeypatch):
    endpoint = CalSimulator()
    with endpoint.chiller() as chiller:
        monkeypatch.setattr(endpoint, "write", lambda *_args: pytest.fail("CAL mode request sent I/O"))
        assert not chiller.supports_mode_control
        app = create_app(chiller)
        for method, action in [(chiller.run, "run-device"), (chiller.standby, "standby-device")]:
            with pytest.raises(ProtocolError, match="only for T255P"):
                method()
            assert "unsupported" in text(submit_mode(app, action).json)
        assert chiller.is_connected


def test_mode_callbacks_share_action_lock_and_refuse_requests_after_shutdown(monkeypatch):
    endpoint = T255PSimulator()
    with endpoint.chiller() as chiller:
        app = create_app(chiller, on_shutdown=lambda: None)
        entered, release = Event(), Event()
        original = chiller.run

        def slow_run():
            entered.set()
            assert release.wait(2)
            original()

        monkeypatch.setattr(chiller, "run", slow_run)
        worker = Thread(target=lambda: submit_mode(app, "run-device"))
        worker.start()
        try:
            assert entered.wait(1)
            assert "in progress" in text(submit_mode(app, "standby-device").json)
            assert "in progress" in text(submit(app, 18).json)
            assert "in progress" in text(session_action(app, "shutdown-gui").json)
            assert refresh(app).status_code == 200
        finally:
            release.set()
            worker.join(2)
        assert not worker.is_alive()
        assert "shut down" in text(session_action(app, "shutdown-gui").json)
        assert "shut down" in text(submit_mode(app, "standby-device").json)
        assert endpoint.mode == "2" and not endpoint.is_open


def session_action(app, action, *, interval=0.01, path=""):
    return app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": next(key for key in app.callback_map if "session-result" in key),
            "outputs": [{"id": "session-result", "property": "children"},
                        {"id": "refresh", "property": "disabled"},
                        {"id": "live-status", "property": "children"},
                        {"id": "toggle-monitoring", "property": "children"}],
            "inputs": [{"id": name, "property": "n_clicks", "value": int(name == action)}
                       for name in ("connect-device", "toggle-monitoring", "shutdown-gui")],
            "state": [{"id": "monitor-interval", "property": "value", "value": interval},
                      {"id": "monitor-csv", "property": "value", "value": path}],
            "changedPropIds": [f"{action}.n_clicks"],
        },
    )


def test_explicit_session_controls_connect_monitor_stop_restart_and_shutdown(tmp_path):
    chiller = T255PSimulator().chiller()
    closed = Event()
    app = create_app(chiller, on_shutdown=closed.set)
    client = app.server.test_client()
    assert client.get("/_dash-layout").status_code == 200
    assert not chiller.is_connected and not chiller.running
    assert "failed" in text(session_action(app, "toggle-monitoring").json)
    assert not chiller.running
    try:
        assert "Connected" in text(session_action(app, "connect-device").json)
        assert chiller.is_connected and not chiller.running
        assert "Already connected" in text(session_action(app, "connect-device").json)
        path = tmp_path / "buttons.csv"
        started = session_action(app, "toggle-monitoring", path=str(path)).json
        assert "started" in text(started)
        assert started["response"]["toggle-monitoring"]["children"] == "Stop monitoring"
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)
        stopped = session_action(app, "toggle-monitoring").json
        assert "stopped" in text(stopped)
        assert stopped["response"]["toggle-monitoring"]["children"] == "Start monitoring"
        assert not chiller.running and chiller.is_connected
        path.unlink()  # Writer has been closed.
        assert "started" in text(session_action(app, "toggle-monitoring").json)
        response = session_action(app, "shutdown-gui")
        assert response.status_code == 200 and "shut down" in text(response.json)
        assert response.json["response"]["refresh"]["disabled"] is True
        assert "Monitoring stopped" in text(response.json["response"]["live-status"])
        assert "Device readback" not in text(response.json["response"]["live-status"])
        assert closed.is_set() and not chiller.running and not chiller.is_connected
        assert "shut down" in text(session_action(app, "connect-device").json)
        assert "Rejected" in text(submit(app, 18).json)
        assert not chiller.is_connected
    finally:
        chiller.disconnect()


def test_session_failures_are_visible_and_do_not_shut_down(dashboard, monkeypatch, tmp_path):
    chiller, _, _ = dashboard
    chiller.stop_monitoring()
    closed = Event()
    app = create_app(chiller, on_shutdown=closed.set)
    existing = tmp_path / "existing.csv"
    existing.write_text("preserve me", encoding="utf-8")
    assert "failed" in text(session_action(app, "toggle-monitoring", path=str(existing)).json)
    assert existing.read_text(encoding="utf-8") == "preserve me"
    assert "failed" in text(session_action(app, "toggle-monitoring", interval=0).json)
    assert not chiller.running

    def fail_disconnect():
        raise TimeoutError("Device read still pending")

    with monkeypatch.context() as patch:
        patch.setattr(chiller, "disconnect", fail_disconnect)
        response = session_action(app, "shutdown-gui")
        assert "Device read still pending" in text(response.json)
        assert response.json["response"]["refresh"]["disabled"] is False
        assert not closed.is_set()
    assert "shut down" in text(session_action(app, "shutdown-gui").json)
    assert closed.is_set()


def test_embedded_host_rejects_unavailable_shutdown(dashboard):
    chiller, app, _ = dashboard
    response = session_action(app, "shutdown-gui")
    assert "Shutdown unavailable" in text(response.json)
    assert chiller.is_connected and chiller.running


def test_concurrent_action_does_not_reopen_after_shutdown(monkeypatch):
    chiller = T255PSimulator().chiller()
    app = create_app(chiller, on_shutdown=lambda: None)
    entered, release = Event(), Event()
    original = chiller.connect

    def slow_connect():
        entered.set()
        assert release.wait(2)
        original()

    monkeypatch.setattr(chiller, "connect", slow_connect)
    worker = Thread(target=lambda: session_action(app, "connect-device"))
    worker.start()
    try:
        assert entered.wait(1)
        assert "in progress" in text(session_action(app, "shutdown-gui").json)
        assert refresh(app).status_code == 200
    finally:
        release.set()
        worker.join(2)
        chiller.disconnect()


def test_run_dashboard_shutdown_closes_real_http_server(monkeypatch):
    import json
    from urllib.request import Request, urlopen

    from tark_chiller import gui

    chiller = T255PSimulator().chiller()
    ready = Event()
    servers, errors = [], []
    make_server = gui.make_server

    def capture_server(*args, **kwargs):
        server = make_server(*args, **kwargs)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr(gui, "make_server", capture_server)

    def run():
        try:
            gui.run_dashboard(chiller, port=0)
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=run)
    worker.start()
    try:
        assert ready.wait(2)
        url = f"http://127.0.0.1:{servers[0].server_port}"
        with urlopen(url + "/_dash-layout", timeout=2) as response:
            assert "Shutdown GUI" in response.read().decode()
        assert not chiller.is_connected
        # Generate a real callback payload, then send it over HTTP to the live server.
        app = gui.create_app(chiller, on_shutdown=lambda: None)
        output = next(key for key in app.callback_map if "session-result" in key)
        inputs = [{"id": name, "property": "n_clicks", "value": 1}
                  for name in ("connect-device", "toggle-monitoring", "shutdown-gui")]
        for action in ("connect-device", "toggle-monitoring", "shutdown-gui"):
            payload = {"output": output,
                       "outputs": [{"id": "session-result", "property": "children"},
                                   {"id": "refresh", "property": "disabled"},
                                   {"id": "live-status", "property": "children"},
                                   {"id": "toggle-monitoring", "property": "children"}],
                       "inputs": inputs,
                       "state": [{"id": "monitor-interval", "property": "value", "value": 0.01},
                                 {"id": "monitor-csv", "property": "value", "value": ""}],
                       "changedPropIds": [f"{action}.n_clicks"]}
            request = Request(url + "/_dash-update-component", data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=2) as response:
                assert "failed" not in response.read().decode()
        worker.join(3)
        assert not worker.is_alive() and not errors
        assert not chiller.running and not chiller.is_connected
        assert servers[0].socket.fileno() == -1
    finally:
        if worker.is_alive() and servers:
            servers[0].shutdown()
        worker.join(3)
        chiller.disconnect()


def test_simulator_monitor_csv_and_dash_share_real_readings(dashboard):
    chiller, app, path = dashboard
    response = submit(app, 18)
    assert response.status_code == 200
    assert "completed" in text(response.json)
    wait_for(lambda: chiller.snapshot()["history"][-1]["temperature_c"] < 19.5)
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
    assert len(rows) == chiller.snapshot()["logged_samples"]
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
    chiller, app, _path = dashboard
    response = submit(app, value)
    assert response.status_code == 200
    assert "Rejected" in text(response.json)
    assert chiller.read_setpoint() == 20


def test_disconnected_write_returns_understandable_error(dashboard):
    chiller, app, _path = dashboard
    chiller.disconnect()
    response = submit(app, 18)
    assert response.status_code == 200
    assert "disconnected" in text(response.json).lower()
    assert not chiller.is_connected


def test_read_only_dashboard_refuses_even_direct_callback_submission(dashboard):
    chiller, _, _ = dashboard
    app = create_app(chiller, allow_setpoints=False)
    assert "Read-only session" in app.server.test_client().get("/_dash-layout").get_data(
        as_text=True
    )
    response = submit(app, 18)
    assert "read-only" in text(response.json)
    assert chiller.read_setpoint() == 20 and chiller.is_connected


def test_refresh_and_reload_do_not_start_or_read_a_device(dashboard, monkeypatch):
    chiller, app, _path = dashboard
    chiller.stop_monitoring()
    count = chiller.snapshot()["sample_count"]

    def forbidden(*_args, **_kwargs):
        pytest.fail("Dashboard attempted to own device or chiller lifecycle")

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
        assert "connect-device" in text(layout.json)
        assert refresh(app).status_code == 200
    assert chiller.snapshot()["sample_count"] == count
    assert not chiller.running


def test_pending_device_read_does_not_block_gui_refresh(dashboard, monkeypatch):
    chiller, app, _path = dashboard
    entered, release, responded = Event(), Event(), Event()
    original = chiller._read_sample

    def pending_read():
        entered.set()
        assert release.wait(2)
        return original()

    monkeypatch.setattr(chiller, "_read_sample", pending_read)
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
        assert chiller.running
    finally:
        release.set()
        thread.join(2)


def test_reload_uses_latest_readback_and_current_monotonic_age(dashboard, monkeypatch):
    from tark_chiller import gui

    chiller, app, _path = dashboard
    client = app.server.test_client()
    assert "20.00" in text(client.get("/_dash-layout").json)
    chiller.set_setpoint(18)
    wait_for(lambda: chiller.snapshot()["history"][-1]["setpoint_c"] == 18)
    assert "18.00" in text(client.get("/_dash-layout").json)
    monkeypatch.setattr(gui, "monotonic", lambda: monotonic() + 10)
    result = text(client.get("/_dash-layout").json)
    assert "Stale" in result and "No fresh reading" in result
    assert "18.00" not in result


@pytest.mark.parametrize("running, stale", [(False, False), (True, True)])
def test_old_readings_remain_in_history_but_are_not_live(dashboard, monkeypatch, running, stale):
    from tark_chiller import gui

    chiller, _app, _path = dashboard
    snapshot = chiller.snapshot()
    latest = {
        **snapshot["history"][-1],
        "timestamp_utc": snapshot["history"][-1]["timestamp_utc"] + timedelta(days=1),
    }
    snapshot = {**snapshot, "running": running, "history": [latest]}
    monkeypatch.setattr(
        gui, "monotonic", lambda: latest["sampled_monotonic"] + (10 if stale else 0)
    )
    cards, figure = render_snapshot(snapshot)
    assert "No fresh reading" in text(cards)
    assert "20.00" not in text(cards)
    assert figure.data[0].y[-1] == 20
    assert "Stale" in text(cards) if stale else "Monitoring stopped" in text(cards)


def test_fault_and_csv_failure_are_explicit(dashboard):
    chiller, _app, _path = dashboard
    snapshot = chiller.snapshot()
    failed = {
        **snapshot["history"][-1],
        "temperature_c": None,
        "setpoint_c": None,
        "status": {**snapshot["history"][-1]["status"], "connected": False},
        "error": "Read timed out",
    }
    cards, figure = render_snapshot(
        {
            **snapshot,
            "history": snapshot["history"] + [failed],
            "logging_error": "CSV disk full",
            "failed_samples": 1,
        }
    )
    assert all(
        word in text(cards)
        for word in ("Unavailable", "Read timed out", "CSV Failed", "CSV disk full")
    )
    assert "20.00" not in text(cards)
    assert figure.data[0].y[-1] is None and figure.data[0].connectgaps is False


def test_partial_temperature_is_identified_as_historical_after_target_failure(dashboard):
    chiller, _, _ = dashboard
    snapshot = chiller.snapshot()
    partial = {**snapshot["history"][-1], "setpoint_c": None,
               "status": {"connected": False, "backend": "simulator", "detail": "poll failed"},
               "error": "bad command"}
    cards, figure = render_snapshot({**snapshot, "running": False, "history": [partial],
                                    "service_error": "Monitoring stopped: bad command"})
    content = text(cards)
    assert "Last validated temperature 20.00" in content
    assert "No fresh reading" in content and "Waiting for fresh readback" in content
    assert "Device readback" not in content
    assert figure.data[0].y[-1] == 20 and figure.data[1].y[-1] is None


def test_csv_failure_preserves_temperature_and_empty_history_is_honest(dashboard):
    chiller, _app, _path = dashboard
    snapshot = chiller.snapshot()
    cards, _figure = render_snapshot({**snapshot, "logging_error": "CSV disk full"})
    assert "CSV Failed" in text(cards) and "20.00" in text(cards)
    cards, figure = render_snapshot({**snapshot, "history": [], "sample_count": 0})
    assert "Waiting for the first sample" in text(cards)
    assert "No sample" in text(cards) and "simulator" not in text(cards)
    assert "20.00" not in text(cards) and not figure.data[0].y


def test_dashboard_serves_css_and_license_locally(dashboard):
    _chiller, app, _path = dashboard
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


def test_dashboard_follows_controller_monitoring_restart(dashboard):
    chiller, app, _path = dashboard
    chiller.stop_monitoring()
    previous = chiller.snapshot()
    chiller.set_setpoint(18)
    chiller.start_monitoring(interval_s=0.01)
    wait_for(lambda: bool(chiller.snapshot()["history"]))
    assert chiller.snapshot()["history"][-1]["setpoint_c"] == 18
    response = refresh(app)
    assert response.status_code == 200
    assert "18" in text(response.json)
    assert not previous["running"] and previous["history"][-1]["setpoint_c"] == 20
