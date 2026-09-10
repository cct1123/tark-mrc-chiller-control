"""TEST-008/009: actual Dash callback transport, without a browser or device."""

from datetime import timedelta

import pytest

from tark_chiller import Chiller, SimulatedDevice
from tark_chiller.gui import create_app, render_snapshot, submit_setpoint
from tark_chiller.monitoring import LiveState, Monitor
from tark_chiller.testing import make_fake_device


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
    text, figure = render_snapshot(snap, now=snap.latest.timestamp_utc)
    assert "simulator" in text and "Connected" in text
    assert "20.00 °C" in text
    assert figure.layout.xaxis.title.text == "Time (UTC)"
    assert figure.layout.yaxis.title.text == "Temperature (°C)"
    assert len(figure.data) == 2
    text, _ = render_snapshot(snap, now=snap.latest.timestamp_utc + timedelta(seconds=10))
    assert "Stale" in text
    chiller.disconnect()
    monitor.poll_once()
    state.set_error("CSV test failure", logging=True)
    text, figure = render_snapshot(state.snapshot())
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
        assert "simulator" in response.json["response"]["live-status"]["children"]
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
    text, _figure = render_snapshot(snapshot)
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
    text, _figure = render_snapshot(state.snapshot())
    assert "Last poll: Connected" in text
    assert "Sample age" in text
    assert state.snapshot().sample_count == before_disconnect.sample_count
