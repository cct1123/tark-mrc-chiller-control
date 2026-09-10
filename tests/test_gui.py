"""TEST-008/009: actual Dash callback transport, without a browser or device."""

from datetime import timedelta

import pytest

from tark_chiller import Chiller, SimulatedDevice
from tark_chiller.gui import create_app, render_snapshot, submit_setpoint
from tark_chiller.monitoring import LiveState, Monitor


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
