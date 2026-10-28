"""Already-satisfied mode requests, including the retained notebook 05 trace."""

import pytest

from tark_chiller.gui import create_app
from tark_chiller.protocols import ProtocolError, _t255p_frame
from tark_chiller.simulator import T255PSimulator


@pytest.fixture
def device():
    endpoint = T255PSimulator()
    requests = []
    write = endpoint.write

    def capture(request):
        requests.append(request)
        return write(request)

    endpoint.write = capture
    with endpoint.chiller(reconnect_attempts=2) as c:
        requests.clear()
        yield c, endpoint, requests


@pytest.mark.parametrize(
    "requested,data,reported",
    [
        ("RUN", b"2010", "RUN"),
        ("RUN", b"0010", "AUTO START"),
        ("STANDBY", b"1000", "STANDBY"),
    ],
)
def test_satisfied_workflow_sends_only_u_and_preserves_reported_mode(
    device, requested, data, reported
):
    c, endpoint, requests = device
    respond = endpoint._respond

    def reject_redundant_g(request):
        if request[1:2] == b"U":
            # AUTO START/on uses the exact retained device reply #U0001069<CR>.
            return _t255p_frame(b"#U0" + data)
        if request[1:2] == b"G":
            return b"#G31CE\r"  # Exact device rejection from the notebook.
        return respond(request)

    endpoint._respond = reject_redundant_g
    status = c.set_mode(requested)
    assert requests == [b".U83\r"]
    assert status["mode"] == reported
    assert status["chiller_on"] is (requested == "RUN")
    assert status["mode_command_sent"] is False
    assert c.is_connected and not c._write_uncertain


@pytest.mark.parametrize(
    "requested,data",
    [
        ("RUN", b"2000"),  # RUN with on=False is not a satisfied request.
        ("RUN", b"0000"),  # AUTO START without on=True is not running.
        ("RUN", b"3010"),  # SAFETY DEFAULT must not be relabeled as RUN.
        ("STANDBY", b"1010"),  # STANDBY with on=True needs an actual request.
    ],
)
def test_inconsistent_or_other_states_do_not_skip_the_requested_transition(device, requested, data):
    c, endpoint, requests = device
    respond = endpoint._respond

    def initial_status(request):
        if request[1:2] == b"U" and not any(r[1:2] == b"G" for r in requests):
            return _t255p_frame(b"#U0" + data)
        return respond(request)

    endpoint._respond = initial_status
    status = c.set_mode(requested)
    qualifier = b".G1A6\r" if requested == "RUN" else b".G0A5\r"
    assert requests == [b".U83\r", qualifier, b".U83\r"]
    assert status["mode"] == requested and status["mode_command_sent"] is True


def test_required_transition_propagates_the_recorded_g_error_without_replay(device):
    c, endpoint, requests = device
    endpoint.mode = "1"
    respond = endpoint._respond
    endpoint._respond = lambda request: b"#G31CE\r" if request[1:2] == b"G" else respond(request)
    with pytest.raises(ProtocolError, match="qualifier out of bounds"):
        c.set_mode("RUN")
    assert requests == [b".U83\r", b".G1A6\r"]
    assert c._write_uncertain and not endpoint.is_open


def test_gui_mode_request_skips_redundant_run_without_a_success_description(device):
    c, endpoint, requests = device
    respond = endpoint._respond
    endpoint._respond = lambda request: b"#U0001069\r" if request[1:2] == b"U" else respond(request)
    app = create_app(c)
    response = app.server.test_client().post(
        "/_dash-update-component",
        json={
            "output": "mode-result.children",
            "outputs": {"id": "mode-result", "property": "children"},
            "inputs": [{"id": "toggle-mode", "property": "n_clicks", "value": 1}],
            "state": [{"id": "toggle-mode", "property": "children", "value": "RUN"}],
            "changedPropIds": ["toggle-mode.n_clicks"],
        },
    )
    assert response.status_code == 200
    assert response.json["response"]["mode-result"]["children"] == ""
    assert requests == [b".U83\r"]
