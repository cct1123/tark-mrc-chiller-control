"""Rev C vectors, production protocol and memory-only T255P acceptance.

No serial factory here can create an OS endpoint. The published U/G exchanges
and independent ASCII data fixtures complement driver/emulator agreement.
"""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest

from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import T255P, ProtocolError, _t255p_frame
from tark_chiller.simulator import T255PSimulator


def setup(*, writable=True, timeout=0.02, clock=None, reconnect_attempts=0):
    endpoint = T255PSimulator(**({"clock": clock} if clock else {}))
    requests = []
    write = endpoint.write

    def capture(request):
        requests.append(request)
        return write(request)

    endpoint.write = capture
    chiller = endpoint.chiller(
        allow_writes=writable,
        timeout_s=timeout,
        reconnect_attempts=reconnect_attempts,
        reconnect_delay_s=0.001,
    )
    return chiller, endpoint, requests


def response(command, payload, error=b"0"):
    # Test-owned ASCII builder, separate from production framing/parsing.
    body = b"#" + command + error + payload
    return body + bytes(f"{sum(body) % 256:02X}", "ascii") + b"\r"


def wait_for(predicate):
    deadline = monotonic() + 2
    while not predicate():
        assert monotonic() < deadline, "T255P monitor did not publish the expected state"
        sleep(0.005)


def test_published_watchdog_and_standby_vectors():
    assert _t255p_frame(b".U") == b".U83\r"
    assert T255P.reply(b"U", b"#U020116C\r") == b"2011"
    assert _t255p_frame(b".G0") == b".G0A5\r"
    assert T255P.reply(b"G", b"#G00CA\r") == b"0"
    assert _t255p_frame(b".I") == b".I77\r"
    assert _t255p_frame(b".J") == b".J78\r"
    assert _t255p_frame(b".H0") == b".H0A6\r"
    # Temperature bytes +295 are published on p29; full checksums are calculated fixtures.
    assert _t255p_frame(b".M+295") == b".M+29546\r"
    assert T255P.reply(b"M", b"#M0+2956B\r") == b"+295"


def test_connect_read_and_write_command_order_and_no_mode_command():
    c, endpoint, requests = setup()
    with c:
        assert requests == [b".U83\r"]
        assert endpoint.mode == "2" and endpoint.remote_active
        assert c.read_temperature() == 20
        assert c.read_setpoint() == 20
        assert "human-selected; identity not queried" in c.collect_status()["detail"]
        requests.clear()
        c.apply_setpoint(29.5)
        assert requests == [b".U83\r", b".M+29546\r", b".H0A6\r"]
        assert c.read_setpoint() == 29.5
    assert not endpoint.is_open and endpoint.applied_writes == 1
    assert not any(r[1:2] in (b"G", b"O") for r in requests)


@pytest.mark.parametrize("target", [5, 5.1, 40, 45])
def test_water_boundaries(target):
    c, endpoint, _ = setup()
    with c:
        c.apply_setpoint(target)
        assert c.read_setpoint() == target
    assert endpoint.applied_writes == 1


@pytest.mark.parametrize("value", [True, "20", math.nan, math.inf, -5, 2, 4.9, 45.1])
def test_public_water_policy_rejects_without_io(value):
    c, endpoint, requests = setup()
    with c:
        before = len(requests)
        with pytest.raises(ValueError):
            c.apply_setpoint(value)
        assert len(requests) == before
        assert endpoint.applied_writes == 0


@pytest.mark.parametrize("target", [2, 4.9, 45.1, 20.01])
def test_backend_guard_precedes_all_io_even_with_broader_host_policy(target):
    _, endpoint, requests = setup()
    device = TarkChiller(
        endpoint.chiller().settings,
        protocol=T255P(True),
        serial_factory=endpoint.factory,
        setpoint_range=(-5.0, 50.0),
        coolant_source="Adversarial test",
    )
    c = device
    with c:
        before = len(requests)
        with pytest.raises(ProtocolError):
            c.apply_setpoint(target)
        assert len(requests) == before and not endpoint.applied_writes


def test_write_opt_in_guard_precedes_all_io():
    c, endpoint, requests = setup(writable=False)
    with c:
        before = len(requests)
        with pytest.raises(ProtocolError, match="writes disabled"):
            c.apply_setpoint(20)
        assert len(requests) == before and not endpoint.applied_writes
    with pytest.raises(ValueError):
        T255P(allow_writes=1)


@pytest.mark.parametrize(
    "operation, mode, qualifier",
    [
        ("run", "2", b".G1A6\r"),
        ("standby", "1", b".G0A5\r"),
    ],
)
def test_mode_api_checks_echo_and_state_without_changing_target(operation, mode, qualifier):
    c, endpoint, requests = setup()
    with c:
        endpoint.mode = "1" if operation == "run" else "2"
        requests.clear()
        c.set_mode(operation.upper())
        assert requests == [b".U83\r", qualifier, b".U83\r"]
        assert endpoint.mode == mode and c.supports_mode_control
        assert c.read_setpoint() == 20
    assert endpoint.mode == mode and not endpoint.applied_writes


@pytest.mark.parametrize("operation", ["run", "standby"])
def test_mode_api_requires_connection_and_write_opt_in(operation):
    c, endpoint, requests = setup(writable=False)
    with pytest.raises(ConnectionError):
        c.set_mode(operation.upper())
    assert not requests
    with c:
        requests.clear()
        with pytest.raises(ProtocolError, match="writes disabled"):
            c.set_mode(operation.upper())
        assert not requests
    assert not endpoint.is_open


@pytest.mark.parametrize("mode,alarms", [("1", "010000"), ("3", "000000"), ("1", "000001")])
def test_mode_requests_are_not_vetoed_by_diagnostic_state(mode, alarms):
    c, endpoint, requests = setup()
    with c:
        endpoint.mode, endpoint.alarms = mode, alarms
        requests.clear()
        c.set_mode("RUN")
        assert endpoint.mode == "2" and c.is_connected
        assert "summary_alarm=" in c.collect_status()["detail"]
        c.set_mode("STANDBY")
        assert endpoint.mode == "1"
        assert sum(r[1:2] == b"G" for r in requests) == 2


@pytest.mark.parametrize(
    "fault", ["lost_ack", "partial", "wrong_echo", "wrong_mode", "wrong_on", "interrupt"]
)
def test_mode_failure_latches_across_reconnect_and_never_replays(fault):
    c, endpoint, requests = setup(reconnect_attempts=2)
    with c:
        endpoint.mode = "1"  # Exercise a real transition, not an already-running no-op.
        original = endpoint._respond
        write = endpoint.write

        def respond(request):
            result = original(request)
            if request[1:2] == b"G":
                if fault == "lost_ack":
                    raise TimeoutError("Lost G acknowledgement")
                if fault == "interrupt":
                    raise KeyboardInterrupt
                if fault == "wrong_echo":
                    return response(b"G", b"0")
                if fault == "wrong_mode":
                    endpoint.mode = "1"
            if (
                request[1:2] == b"U"
                and fault == "wrong_on"
                and any(r[1:2] == b"G" for r in requests)
            ):
                return response(b"U", b"2000")
            return result

        def partial_write(request):
            result = write(request)
            return result - 1 if request[1:2] == b"G" else result

        endpoint._respond = respond
        if fault == "partial":
            endpoint.write = partial_write
        requests.clear()
        with pytest.raises(KeyboardInterrupt if fault == "interrupt" else ProtocolError):
            c.set_mode("RUN")
        assert sum(r[1:2] == b"G" for r in requests) == 1
        assert not endpoint.is_open
        with pytest.raises(ProtocolError, match="uncertain"):
            c.connect()
        assert sum(r[1:2] == b"G" for r in requests) == 1


def test_standby_keeps_monitoring_active_and_serializes_with_reads():
    c, endpoint, requests = setup()
    with c:
        c.start_monitoring(interval_s=0.01)
        wait_for(lambda: c.snapshot()["sample_count"] > 0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            changing = pool.submit(c.set_mode, "STANDBY")
            reading = pool.submit(c.read_temperature)
            changing.result(timeout=2)
            assert math.isfinite(reading.result(timeout=2))
        assert c.running and endpoint.mode == "1"
        wait_for(lambda: "mode=STANDBY" in c.snapshot()["history"][-1]["status"]["detail"])
        c.set_mode("RUN")
        assert c.running and endpoint.mode == "2"


@pytest.mark.parametrize(
    "changes",
    [
        dict(baudrate=19200),
        dict(bytesize=7),
        dict(parity="E"),
        dict(stopbits=2),
        dict(xonxoff=False),
        dict(rtscts=True),
        dict(dsrdtr=True),
    ],
)
def test_invalid_wire_settings_refuse_before_factory(changes):
    endpoint = T255PSimulator()

    def forbidden(**kwargs):
        pytest.fail("Invalid settings reached endpoint factory")

    d = TarkChiller(
        {**endpoint.chiller().settings, **changes}, protocol=T255P(), serial_factory=forbidden
    )
    with pytest.raises(ProtocolError, match="9600/8N1"):
        d.connect()
    assert not endpoint.is_open


def test_rs485_refused_and_response_budget_defaults_to_three_seconds():
    endpoint = T255PSimulator()
    assert endpoint.chiller().timeout_s == 3
    d = TarkChiller(
        endpoint.chiller().settings,
        protocol=T255P(),
        serial_factory=endpoint.factory,
        rs485={
            "rts_level_for_tx": True,
            "rts_level_for_rx": False,
            "loopback": False,
            "delay_before_tx": None,
            "delay_before_rx": None,
        },
    )
    with pytest.raises(ProtocolError, match="RS-232"):
        d.connect()
    assert not endpoint.is_open


@pytest.mark.parametrize("reading", [b"+1960", b"-0050", b"+0000", b"-0001"])
def test_signed_hundredths_and_negative_target_reads(reading):
    c, endpoint, _ = setup()
    with c:
        original = endpoint._respond

        def respond(request):
            if request[1:2] == b"I":
                return response(b"I", reading)
            if request[1:2] == b"H":
                return response(b"H", b"0-050,100")
            return original(request)

        endpoint._respond = respond
        assert c.read_temperature() == int(reading) / 100
        assert c.read_setpoint() == -5


@pytest.mark.parametrize("error", [b"1", b"2", b"3", b"9"])
def test_only_link_communication_errors_use_read_retry_budget(error):
    c, endpoint, requests = setup(reconnect_attempts=2)
    with c:
        endpoint._respond = lambda request: response(b"U", b"", error)
        before = len(requests)
        with pytest.raises(ProtocolError, match="communication error"):
            c.read_temperature()
        assert len(requests) == before + (3 if error in (b"1", b"2") else 1)
        assert not endpoint.is_open


@pytest.mark.parametrize(
    "command,payload",
    [
        (b"U", b"201"),
        (b"U", b"4010"),
        (b"U", b"2210"),
        (b"J", b"00000"),
        (b"J", b"000002"),
        (b"I", b"01960"),
        (b"I", b"+196"),
        (b"I", b"+1x60"),
        (b"I", b"+01960"),
        (b"H", b"1+200,100"),
        (b"H", b"0+200;100"),
        (b"H", b"0+200,1x0"),
        (b"H", b"0+500,100"),
    ],
)
def test_malformed_fields_close_session(command, payload):
    c, endpoint, _ = setup()
    with c:
        original = endpoint._respond
        endpoint._respond = lambda r: (
            response(command, payload) if r[1:2] == command else original(r)
        )
        call = c.read_setpoint if command == b"H" else c.read_temperature
        with pytest.raises(ProtocolError):
            call()
        assert not endpoint.is_open


@pytest.mark.parametrize("fault", ["checksum", "echo", "prefix", "nonascii", "trailing", "short"])
def test_framing_faults_are_not_accepted(fault):
    c, endpoint, _ = setup()
    packet = response(b"U", b"2010")
    if fault == "checksum":
        packet = packet[:-3] + b"00\r"
    elif fault == "echo":
        packet = response(b"I", b"2010")
    elif fault == "prefix":
        packet = b"!" + packet[1:]
    elif fault == "nonascii":
        packet = response(b"U", b"20\xff0")
    elif fault == "trailing":
        packet += b"x"
    else:
        packet = packet[:-1]
    with c:
        endpoint._respond = lambda r: packet
        with pytest.raises((ProtocolError, TimeoutError)):
            c.read_temperature()
        assert not endpoint.is_open


@pytest.mark.parametrize("index", range(6))
def test_alarm_status_is_diagnostic_and_does_not_inhibit_target_writes(index):
    c, endpoint, requests = setup()
    with c:
        endpoint.alarms = "0" * index + "1" + "0" * (5 - index)
        detail = c.collect_status()["detail"]
        name = ("float_switch", "high_temp", "low_temp", "sensor", "eeprom", "watchdog")[index]
        assert f"{name}=1" in detail and "flow/leak unknown" in detail
        mode = endpoint.mode
        requests.clear()
        c.apply_setpoint(18)
        assert c.is_connected and endpoint.mode == mode
        assert endpoint.alarms == "0" * index + "1" + "0" * (5 - index)
        assert endpoint.applied_writes == 1
        assert requests == [b".U83\r", b".M+1803F\r", b".H0A6\r"]
        assert c.read_setpoint() == 18


def test_sensor_alarm_invalidates_measurement():
    c, endpoint, _ = setup()
    with c:
        endpoint.alarms = "000100"
        with pytest.raises(ProtocolError, match="sensor alarm"):
            c.read_temperature()
    assert endpoint.mode == "1"


@pytest.mark.parametrize("mode", ["1", "3"])
def test_target_write_preserves_standby_or_safety_default(mode):
    c, endpoint, requests = setup()
    endpoint.mode = mode
    with c:
        c.apply_setpoint(18)
        assert c.read_setpoint() == 18
    assert endpoint.mode == mode and endpoint.applied_writes == 1
    assert all(r[1:2] not in (b"G", b"O") for r in requests)


def test_summary_alarm_alone_does_not_veto_target_or_run():
    c, endpoint, requests = setup()
    with c:
        original = endpoint._respond

        def respond(request):
            result = original(request)
            if request[1:2] == b"U":
                on = b"1" if endpoint.mode == "2" else b"0"
                return response(b"U", endpoint.mode.encode() + b"1" + on + b"0")
            return result

        endpoint._respond = respond
        c.set_mode("STANDBY")
        requests.clear()
        c.apply_setpoint(18)
        c.set_mode("RUN")
        assert c.is_connected and c.read_setpoint() == 18 and endpoint.mode == "2"
        assert not any(r[1:2] == b"J" for r in requests)
        assert "summary_alarm=1" in c.collect_status()["detail"]


@pytest.mark.parametrize(
    "fault",
    ["lost_ack", "bad_echo", "partial_write", "interrupt", "readback_mismatch", "readback_timeout"],
)
def test_uncertain_write_latches_without_replay_or_cleanup_commands(fault):
    c, endpoint, requests = setup(reconnect_attempts=2)
    c.connect()
    original_respond, original_write = endpoint._respond, endpoint.write

    def respond(request):
        result = original_respond(request)
        if request[1:2] == b"M":
            if fault == "lost_ack":
                return b""
            if fault == "bad_echo":
                return response(b"M", b"+190")
            if fault == "interrupt":
                raise KeyboardInterrupt
        if request[1:2] == b"H" and endpoint.applied_writes:
            if fault == "readback_mismatch":
                return response(b"H", b"0+190,100")
            if fault == "readback_timeout":
                return b""
        return result

    def write(request):
        count = original_write(request)
        return count - 1 if fault == "partial_write" and request[1:2] == b"M" else count

    endpoint._respond, endpoint.write = respond, write
    with pytest.raises(KeyboardInterrupt if fault == "interrupt" else ProtocolError):
        c.apply_setpoint(18)
    before = len(requests)
    assert endpoint.applied_writes == 1 and not endpoint.is_open
    with pytest.raises(ProtocolError, match="uncertain"):
        c.connect()
    assert len(requests) == before and sum(r[1:2] == b"M" for r in requests) == 1
    c.disconnect()


def test_short_timeout_quarantines_recovery_and_discards_old_reply(monkeypatch):
    import tark_chiller.controller as serial
    import tark_chiller.simulator as simulator

    now = [100.0]
    monkeypatch.setattr(serial, "perf_counter", lambda: now[0])

    def advance(seconds):
        now[0] += seconds

    monkeypatch.setattr(serial, "sleep", advance)
    monkeypatch.setattr(simulator, "sleep", advance)
    c, endpoint, _ = setup(clock=lambda: now[0], reconnect_attempts=1, timeout=0.1)
    times = []
    original = endpoint._respond
    fail = [True]

    def respond(request):
        times.append((request[1:2], now[0]))
        if request[1:2] == b"I" and fail[0]:
            fail[0] = False
            return b""
        return original(request)

    endpoint._respond = respond
    with c:
        monkeypatch.setattr(c._cancel, "wait", lambda seconds: now.__setitem__(0, now[0] + seconds))
        assert c.read_temperature() == 20
        first = next(i for i, (cmd, _) in enumerate(times) if cmd == b"I")
        assert times[first + 1][0] == b"U"
        assert times[first + 1][1] - times[first][1] >= 3
        # A complete stale packet queued before the next request is discarded.
        endpoint._buffer = response(b"I", b"+9999")
        assert c.read_temperature() == 20
    assert not endpoint.applied_writes


def test_disconnect_cancels_quarantine_before_recovery_handshake(monkeypatch):
    c, endpoint, requests = setup(reconnect_attempts=1)
    waiting = Event()
    original_wait = TarkChiller._reconnect_wait_s.fget

    def quarantine(device):
        delay = original_wait(device)
        if delay:
            waiting.set()
        return delay

    monkeypatch.setattr(TarkChiller, "_reconnect_wait_s", property(quarantine))
    c.connect()
    original = endpoint._respond
    endpoint._respond = lambda r: b"" if r[1:2] == b"I" else original(r)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(c.read_temperature)
        assert waiting.wait(2)
        before = len(requests)
        c.disconnect()
        with pytest.raises(ConnectionError, match="cancelled"):
            pending.result(timeout=2)
    assert len(requests) == before and requests[-1][1:2] == b"I"
    assert not endpoint.is_open


def test_simulator_watchdog_idle_reacquisition_retention_and_continued_control():
    now = [0.0]
    c, endpoint, requests = setup(clock=lambda: now[0])
    with c:
        c.apply_setpoint(18)
        now[0] = 10
        assert endpoint.remote_active  # Strictly greater than ten seconds.
        now[0] = 11
        assert not endpoint.remote_active and endpoint.mode == "2"
        assert c.read_temperature() == pytest.approx(18 + 2 * math.exp(-11 / 30), abs=0.01)
        assert endpoint.remote_active
    now[0] = 41
    assert not endpoint.remote_active
    with c:
        assert c.read_temperature() == pytest.approx(18 + 2 * math.exp(-41 / 30), abs=0.01)
        assert c.read_setpoint() == 18
    endpoint.power_cycle()
    assert endpoint.mode == "0"
    with c:
        assert c.read_setpoint() == 18 and endpoint.mode == "2"
    assert endpoint.applied_writes == 1
    assert all(r[1:2] not in (b"G", b"O") for r in requests)


def test_simulator_raw_xon_standby_flags_and_errors():
    now = [0.0]
    endpoint = T255PSimulator(clock=lambda: now[0])
    endpoint.xonxoff = False
    endpoint.open()
    assert endpoint.read(1) == b"\x11"
    now[0] = 0.5
    assert endpoint.read(1) == b"\x11"
    endpoint.write(b".U83\r")
    assert endpoint.read(100).startswith(b"#U02010")
    endpoint.write(b".G0A5\r")
    assert endpoint.read(100) == b"#G00CA\r" and endpoint.mode == "1"
    now[0] = 11
    assert not endpoint.remote_active and endpoint.mode == "1"
    endpoint.reset_input_buffer()
    endpoint.write(b".U83\r")
    assert endpoint.read(100).startswith(b"#U01000")
    for request, error in [
        (b".I00\r", b"1"),
        (_t255p_frame(b".Z"), b"2"),
        (_t255p_frame(b".H1"), b"3"),
    ]:
        endpoint.write(request)
        packet = endpoint.read(100)
        assert packet[2:3] == error
    endpoint.close()


def test_flow_control_characters_are_consumed_and_xoff_bounds_writes():
    c, endpoint, _ = setup()
    with c:
        original = endpoint._respond
        endpoint._respond = lambda request: b"\x13" + original(request) + b"\x11"
        assert c.read_temperature() == 20
        endpoint.set_flow_paused(True)
        with pytest.raises(TimeoutError, match="XOFF"):
            c.read_temperature()
        assert not endpoint.is_open and not endpoint.applied_writes


def test_standby_pauses_synthetic_thermal_response():
    now = [0.0]
    c, endpoint, _ = setup(clock=lambda: now[0])
    with c:
        c.apply_setpoint(18)
        endpoint.mode = "1"
        now[0] = 30
        assert c.read_temperature() == 20


def test_monitor_reports_sensor_failure_as_blank_rows(tmp_path):
    import csv

    c, endpoint, _ = setup()
    path = tmp_path / "t255p.csv"
    with c:
        c.start_monitoring(interval_s=0.01, csv_path=path)
        wait_for(lambda: c.snapshot()["sample_count"] > 0)
        endpoint.alarms = "000100"
        wait_for(lambda: c.snapshot()["failed_samples"] > 0)
        c.stop_monitoring()
        snapshot = c.snapshot()
        assert snapshot["failed_samples"] and (not snapshot["history"][-1]["status"]["connected"])
    rows = list(csv.DictReader(path.open(newline="")))
    assert any(r["error"] and r["temperature_c"] == r["setpoint_c"] == "" for r in rows)


def test_dashboard_identifies_t255p_and_does_not_claim_physical_safety():
    from tark_chiller.gui import create_app

    c, _, _ = setup()
    with c:
        c.start_monitoring(interval_s=0.01)
        wait_for(lambda: c.snapshot()["sample_count"] > 0)
        app = create_app(c, allow_setpoints=False)
        layout = json.dumps(app.server.test_client().get("/_dash-layout").get_json())
        assert "T255P" in layout and "LABORATORY CHILLER" in layout
        assert "flow/leak telemetry unavailable" in layout and "human-selected" in layout
        assert "Read-only session" in layout
