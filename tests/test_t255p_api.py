"""Explicit command API acceptance through the memory-only T255P endpoint."""

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

import pytest

from tark_chiller.protocols import T255P, ProtocolError, _t255p_frame
from tark_chiller.simulator import CalSimulator, T255PSimulator


@pytest.fixture
def device():
    endpoint = T255PSimulator()
    requests = []
    write = endpoint.write

    def capture(request):
        requests.append(request)
        return write(request)

    endpoint.write = capture
    chiller = endpoint.chiller(timeout_s=0.02, reconnect_attempts=1)
    with chiller:
        requests.clear()
        yield chiller, endpoint, requests


@pytest.mark.parametrize(
    "method,expected,commands",
    [
        ("get_temperature", 20.0, [b".I77\r"]),
        ("get_memory", {"setpoint_c": 20.0, "max_power": 100}, [b".H0A6\r"]),
    ],
)
def test_explicit_queries_and_exact_wire_sequences(device, method, expected, commands):
    c, _, requests = device
    assert getattr(c, method)() == expected
    assert requests == commands


def test_structured_watchdog_and_all_alarm_flags(device):
    c, endpoint, requests = device
    endpoint.dryer_on = True
    endpoint.alarms = "101011"
    status = c.get_status()
    state = {
        key: status[key] for key in ("mode", "mode_code", "summary_alarm", "chiller_on", "dryer_on")
    }
    assert state == {
        "mode": "RUN",
        "mode_code": 2,
        "summary_alarm": True,
        "chiller_on": True,
        "dryer_on": True,
    }
    assert requests == [b".U83\r"]
    requests.clear()
    alarms = c.get_alarms()
    assert alarms == {
        "float_switch": True,
        "high_temp": False,
        "low_temp": True,
        "sensor": False,
        "eeprom": True,
        "watchdog": True,
    }
    assert all(type(value) is bool for value in alarms.values())
    assert requests == [b".J78\r"]
    requests.clear()
    status = c.get_status()
    assert {key: status[key] for key in state} == state
    assert "alarms" not in status
    assert requests == [b".U83\r"]
    requests.clear()
    status = c.collect_status()
    assert status == {
        **state,
        "alarms": alarms,
        "connected": True,
        "backend": "simulator",
        "detail": status["detail"],
    }
    assert requests == [b".U83\r", b".J78\r"]
    status["alarms"]["float_switch"] = False
    assert c.collect_status()["alarms"]["float_switch"] is True


@pytest.mark.parametrize(
    "code,name", list(enumerate(["AUTO START", "STANDBY", "RUN", "SAFETY DEFAULT"]))
)
def test_all_documented_mode_codes(device, code, name):
    c, endpoint, _ = device
    respond = endpoint._respond

    def reply(request):
        if request[1:2] == b"U":
            return _t255p_frame(b"#U0" + str(code).encode() + b"000")
        return respond(request)

    endpoint._respond = reply
    assert c.get_status()["mode"] == name
    assert c.get_status()["mode_code"] == code


@pytest.mark.parametrize("mode,frame", [("RUN", b".G1A6\r"), ("STANDBY", b".G0A5\r")])
def test_named_mode_control(device, mode, frame):
    c, endpoint, requests = device
    endpoint.mode = "1" if mode == "RUN" else "2"
    status = c.set_mode(mode)
    assert requests == [b".U83\r", frame, b".U83\r"]
    assert status["mode_command_sent"] is True
    assert c.get_status()["mode"] == mode


@pytest.mark.parametrize("mode,frame", [("INTERNAL", b".O0AD\r"), ("EXTERNAL", b".O1AE\r")])
def test_sensor_selection_checks_echo_without_invented_readback(device, mode, frame):
    c, endpoint, requests = device
    c.set_sensor_mode(mode)
    assert requests == [frame]
    assert endpoint.sensor_mode == mode
    assert c.is_connected and not c._write_uncertain
    assert not hasattr(c, "get_sensor_mode")


@pytest.mark.parametrize("method", ["set_mode", "set_sensor_mode"])
@pytest.mark.parametrize("value", [True, 0, None, "invalid", "run", "AUTO START"])
def test_invalid_control_values_do_not_send_traffic(device, method, value):
    c, _, requests = device
    with pytest.raises(ValueError):
        getattr(c, method)(value)
    assert not requests


def test_sensor_selection_requires_connection_and_write_opt_in():
    endpoint = T255PSimulator()
    c = endpoint.chiller(allow_writes=False)
    with pytest.raises(ConnectionError):
        c.set_sensor_mode("EXTERNAL")
    with c:
        with pytest.raises(ProtocolError, match="writes disabled"):
            c.set_sensor_mode("EXTERNAL")
    assert endpoint.sensor_mode == "INTERNAL"


@pytest.mark.parametrize(
    "fault", ["lost_ack", "bad_echo", "bad_checksum", "short_write", "interrupt"]
)
def test_sensor_write_failure_latches_and_never_replays(device, fault):
    c, endpoint, requests = device
    respond, write = endpoint._respond, endpoint.write

    def reply(request):
        result = respond(request)
        if request[1:2] == b"O":
            if fault == "lost_ack":
                raise TimeoutError("lost O acknowledgement")
            if fault == "interrupt":
                raise KeyboardInterrupt
            if fault == "bad_echo":
                return b"#O00D2\r"
            if fault == "bad_checksum":
                return b"#O0100\r"
        return result

    def short_write(request):
        count = write(request)
        return count - 1 if request[1:2] == b"O" else count

    endpoint._respond = reply
    if fault == "short_write":
        endpoint.write = short_write
    with pytest.raises(KeyboardInterrupt if fault == "interrupt" else ProtocolError):
        c.set_sensor_mode("EXTERNAL")
    assert endpoint.sensor_mode == "EXTERNAL"  # Applied despite the failed acknowledgement.
    assert not endpoint.is_open and c._write_uncertain
    with pytest.raises(ProtocolError, match="uncertain"):
        c.connect()
    assert sum(request[1:2] == b"O" for request in requests) == 1


@pytest.mark.parametrize(
    "method,payload,command",
    [
        ("get_memory", b"0+200;100", b"H"),
        ("get_alarms", b"000002", b"J"),
        ("get_status", b"4010", b"U"),
    ],
)
def test_explicit_queries_reject_malformed_data(device, method, payload, command):
    c, endpoint, _ = device
    respond = endpoint._respond
    endpoint._respond = lambda request: (
        _t255p_frame(b"#" + command + b"0" + payload)
        if request[1:2] == command
        else respond(request)
    )
    with pytest.raises(ProtocolError):
        getattr(c, method)()
    assert not endpoint.is_open


def test_command_query_never_retries_or_handshakes_even_with_recovery_enabled(device):
    c, endpoint, requests = device
    respond = endpoint._respond

    def fail_once(request):
        if request[1:2] == b"H":
            endpoint._respond = respond
            # This is retryable only in a named read workflow, never a get_ command.
            return _t255p_frame(b"#H2")
        return respond(request)

    endpoint._respond = fail_once
    with pytest.raises(ProtocolError, match="bad command"):
        c.get_memory()
    assert requests == [b".H0A6\r"]
    assert not c.is_connected


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_temperature", ()),
        ("get_status", ()),
        ("get_memory", ()),
        ("get_alarms", ()),
        ("set_sensor_mode", ("EXTERNAL",)),
        ("set_mode", ("RUN",)),
        ("set_setpoint", (20,)),
    ],
)
def test_t255p_only_methods_refuse_cal_without_traffic(method, args):
    endpoint = CalSimulator()
    with endpoint.chiller() as c:
        endpoint.write = lambda request: pytest.fail("T255P operation sent CAL traffic")
        with pytest.raises(ProtocolError, match="only for T255P"):
            getattr(c, method)(*args)


def test_monitoring_structured_status_is_detached_and_controls_serialize(device):
    c, endpoint, _ = device
    c.start_monitoring(interval_s=0.01)
    deadline = monotonic() + 2
    while not c.snapshot()["sample_count"]:
        assert monotonic() < deadline
        sleep(0.005)
    with ThreadPoolExecutor(2) as pool:
        control = pool.submit(c.set_sensor_mode, "EXTERNAL")
        query = pool.submit(c.get_alarms)
        control.result(timeout=2)
        assert query.result(timeout=2)["sensor"] is False
    c.stop_monitoring()
    snapshot = c.snapshot()
    assert snapshot["history"][-1]["status"]["mode"] == "RUN"
    snapshot["history"][-1]["status"]["alarms"]["sensor"] = True
    assert c.snapshot()["history"][-1]["status"]["alarms"]["sensor"] is False
    assert endpoint.sensor_mode == "EXTERNAL"


def test_protocol_uses_same_named_mode_values():
    requests = []

    def io(command, data):
        requests.append((command, data))
        if command == b"U":
            return b"1000" if any(c == b"G" for c, _ in requests) else b"2010"
        return data

    T255P(True).set_mode("STANDBY", io)
    assert requests == [(b"U", b""), (b"G", b"0"), (b"U", b"")]


@pytest.mark.parametrize("target,frame", [(5, b".M+0503B\r"), (29.5, b".M+29546\r")])
def test_setpoint_command_only_checks_its_own_echo(device, target, frame):
    c, _, requests = device
    c.set_setpoint(target)
    assert requests == [frame]
    requests.clear()
    assert c.get_memory()["setpoint_c"] == target
    assert requests == [b".H0A6\r"]


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_temperature", ()),
        ("get_memory", ()),
        ("get_alarms", ()),
        ("get_status", ()),
        ("set_setpoint", (18,)),
        ("set_sensor_mode", ("EXTERNAL",)),
    ],
)
def test_no_command_method_enters_connection_or_recovery_workflows(
    device, monkeypatch, method, args
):
    c, _, requests = device

    def forbidden(*args, **kwargs):
        pytest.fail("Single-command API entered an orchestration workflow")

    monkeypatch.setattr(c, "_read_with_recovery", forbidden)
    monkeypatch.setattr(c, "_open_port", forbidden)
    getattr(c, method)(*args)
    assert len(requests) == 1


def test_sensor_alarm_validation_is_an_explicit_read_workflow(device):
    c, endpoint, requests = device
    endpoint.alarms = "000100"
    assert c.get_temperature() == 20
    assert requests == [b".I77\r"]
    requests.clear()
    with pytest.raises(ProtocolError, match="sensor alarm"):
        c.read_temperature()
    assert requests == [b".U83\r", b".J78\r"]


@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("apply_setpoint", (29.5,), [b".U83\r", b".M+29546\r", b".H0A6\r"]),
        ("set_mode", ("STANDBY",), [b".U83\r", b".G0A5\r", b".U83\r"]),
        ("collect_status", (), [b".U83\r", b".J78\r"]),
    ],
)
def test_named_workflows_explicitly_coordinate_commands(device, method, args, expected):
    c, _, requests = device
    getattr(c, method)(*args)
    assert requests == expected


def test_expired_session_command_does_not_silently_send_watchdog(device):
    c, endpoint, requests = device
    endpoint._last_valid -= 11
    with pytest.raises(TimeoutError):
        c.get_memory()
    assert requests == [b".H0A6\r"]


def test_command_surface_has_seven_methods_and_no_compatibility_entry_points(device):
    c, _, _ = device
    commands = {
        "get_temperature",
        "get_memory",
        "get_alarms",
        "get_status",
        "set_setpoint",
        "set_mode",
        "set_sensor_mode",
    }
    for cls in (type(c), T255P):
        assert {name for name in vars(cls) if name.startswith(("get_", "set_"))} == commands
        for name in (
            "get_watchdog",
            "get_mode",
            "get_setpoint",
            "read_status",
            "run",
            "standby",
            "change_mode",
        ):
            assert not hasattr(cls, name)
    assert not hasattr(T255P, "verify")
