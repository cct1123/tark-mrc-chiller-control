"""CAL guide byte vectors and production driver ↔ memory controller acceptance.

Sources/pages and the deliberately restricted candidate are in docs/protocol.md.
No OS serial endpoint is used. Simulator agreement alone is not protocol proof:
the published PV vector and literal register/sequence assertions are independent.
"""

import math
from dataclasses import replace
from time import sleep

import pytest

from tark_chiller import Chiller, ProtocolError
from tark_chiller.serial import Cal33xx, CalSimulator, SerialDevice, _rtu


def setup(*, writable=True, timeout=0.03, endpoint=None):
    endpoint = endpoint or CalSimulator(clock=lambda: 0)
    requests = []
    original = endpoint.write

    def capture(request):
        requests.append(request)
        return original(request)

    endpoint.write = capture
    device = endpoint.device()
    device = SerialDevice(
        device.settings,
        codec=Cal33xx(1, writable, 1, 2),
        serial_factory=endpoint.factory,
        timeout_s=timeout,
    )
    return Chiller(device), endpoint, requests


def words(requests, function):
    return [
        (int.from_bytes(r[2:4], "big"), int.from_bytes(r[4:6], "big"))
        for r in requests
        if r[1] == function
    ]


def test_published_temperature_request_reply_and_crc():
    # CAL guide printed p16: 19.6 C. These CRC bytes are published, not computed fixtures.
    assert _rtu(bytes.fromhex("01 03 00 1C 00 01")) == bytes.fromhex("01 03 00 1C 00 01 45 CC")
    assert _rtu(bytes.fromhex("01 03 02 00 C4")) == bytes.fromhex("01 03 02 00 C4 B9 D7")
    c, endpoint, requests = setup()
    with c:
        original = endpoint._respond
        endpoint._respond = lambda request: (
            bytes.fromhex("01 03 02 00 C4 B9 D7")
            if request[2:4] == b"\x00\x1c"
            else original(request)
        )
        assert c.read_temperature() == 19.6
    assert bytes.fromhex("01 03 00 1C 00 01 45 CC") in requests


def test_identification_reads_only_and_five_write_sequence():
    c, endpoint, requests = setup()
    with c:
        assert words(requests, 3) == [(0x04FC, 1), (0x04FD, 1), (0x0198, 1), (0x0199, 1)]
        assert not words(requests, 6)
        assert c.read_setpoint() == 10
        assert c.read_status().backend == "simulator"
        requests.clear()
        c.set_setpoint(18.5)
        assert c.read_setpoint() == 18.5
        assert words(requests, 6) == [
            (0x0300, 5),
            (0x1500, 0),
            (0x007F, 185),
            (0x0300, 6),
            (0x1600, 0),
        ]
        assert words(requests, 1) == [(0x0028, 1), (0x002A, 1)] * 2
        assert endpoint.applied_writes == 1 and not endpoint.remote_program_mode
    assert not endpoint.is_open


@pytest.mark.parametrize(
    "config",
    [
        dict(address=0),
        dict(address=248),
        dict(address=True),
        dict(address=1, allow_writes=True),
        dict(address=1, expected_model=7),
        dict(address=1, expected_firmware=3),
        dict(address=1, allow_writes=1),
    ],
)
def test_invalid_protocol_configuration(config):
    with pytest.raises(ValueError):
        Cal33xx(**config)


@pytest.mark.parametrize(
    "register,value",
    [(0x04FC, 7), (0x04FC, 2), (0x04FD, 3), (0x04FD, 1), (0x0198, 11), (0x0199, 2), (0x0199, 257)],
)
def test_wrong_identity_units_or_input_refused_without_writes(register, value):
    c, endpoint, requests = setup()
    endpoint.registers[register] = value
    with pytest.raises(ProtocolError):
        c.connect()
    assert not endpoint.is_open and not words(requests, 6)


@pytest.mark.parametrize(
    "changes",
    [
        dict(bytesize=7),
        dict(parity="S"),
        dict(stopbits=2),
        dict(baudrate=115200),
        dict(xonxoff=True),
        dict(rtscts=True),
        dict(dsrdtr=True),
    ],
)
def test_invalid_cal_settings_stop_before_port_creation(changes):
    s = CalSimulator()
    d = s.device()

    def forbidden(**kwargs):
        pytest.fail("Port creation attempted")

    c = Chiller(
        SerialDevice(replace(d.settings, **changes), codec=Cal33xx(1), serial_factory=forbidden)
    )
    with pytest.raises(ProtocolError, match="CAL needs"):
        c.connect()


def test_read_only_opt_in_blocks_every_write():
    c, endpoint, requests = setup(writable=False)
    with c:
        with pytest.raises(ProtocolError, match="writes disabled"):
            c.set_setpoint(18)
    assert not words(requests, 6)


@pytest.mark.parametrize("value", [1.9, 40.1, math.nan, True, "18"])
def test_public_guard_precedes_any_cal_io(value):
    c, _, requests = setup()
    with c:
        requests.clear()
        with pytest.raises(ValueError):
            c.set_setpoint(value)
        assert requests == []


@pytest.mark.parametrize(
    "register,value",
    [
        (0x0094, 170),
        (0x0096, 190),
        (0x0125, 0),
        (0x0306, 1),
        (0x0306, 2),
        (0x0306, 0x26),
        (0x0305, 2),
        (0x0199, 2),
        (0x04FC, 2),
        (0x0096, 0xFFFF),
    ],
)
def test_preflight_refuses_unsafe_or_unsupported_state(register, value):
    c, endpoint, requests = setup()
    with c:
        endpoint.registers[register] = value
        with pytest.raises(ProtocolError):
            c.set_setpoint(18)
    assert not words(requests, 6)


@pytest.mark.parametrize(
    "locked,high_resolution,target,succeeds",
    [
        (1, 1, 18, False),
        (0, 0, 18.5, False),
        (0, 0, 18, True),
        (0, 1, 18.55, False),
        (0, 1, 18.5, True),
    ],
)
def test_panel_lock_and_resolution(locked, high_resolution, target, succeeds):
    c, endpoint, requests = setup()
    endpoint.coils.update({0x28: locked, 0x2A: high_resolution})
    with c:
        if succeeds:
            c.set_setpoint(target)
        else:
            with pytest.raises(ProtocolError):
                c.set_setpoint(target)
            assert not words(requests, 6)


@pytest.mark.parametrize("stage", range(1, 6))
@pytest.mark.parametrize("fault", ["lost_ack", "bad_echo", "interrupt"])
def test_each_uncertain_write_stage_stops_without_cleanup_commit_or_retry(stage, fault):
    c, endpoint, requests = setup()
    c.connect()
    original = endpoint._respond
    count = 0

    def respond(request):
        nonlocal count
        response = original(request)
        if request[1] == 6:
            count += 1
            if count == stage:
                if fault == "interrupt":
                    raise KeyboardInterrupt
                return b"" if fault == "lost_ack" else _rtu(request[:5] + bytes((request[5] ^ 1,)))
        return response

    endpoint._respond = respond
    with pytest.raises(KeyboardInterrupt if fault == "interrupt" else ProtocolError):
        c.set_setpoint(18)
    assert len(words(requests, 6)) == stage
    assert endpoint.applied_writes == (stage == 5)
    assert not endpoint.is_open
    with pytest.raises(ProtocolError, match="uncertain"):
        c.connect()
    assert len(words(requests, 6)) == stage
    c.disconnect()


@pytest.mark.parametrize(
    "fault", ["crc", "address", "function", "length", "trailing", "exception", "truncated"]
)
def test_corrupt_read_replies_close_connection(fault):
    c, endpoint, _ = setup()
    c.connect()
    reply = bytearray.fromhex("01 03 02 00 C4 B9 D7")
    if fault == "crc":
        reply[-1] ^= 1
    elif fault == "address":
        reply = _rtu(bytes.fromhex("02 03 02 00 C4"))
    elif fault == "function":
        reply = _rtu(bytes.fromhex("01 01 01 00"))
    elif fault == "length":
        reply = _rtu(bytes.fromhex("01 03 01 00 C4"))
    elif fault == "trailing":
        reply += b"x"
    elif fault == "exception":
        reply = _rtu(bytes.fromhex("01 83 02"))
    else:
        reply = reply[:-1]
    endpoint._respond = lambda request: bytes(reply)
    with pytest.raises((ProtocolError, TimeoutError)):
        c.read_temperature()
    assert not endpoint.is_open


def test_front_panel_busy_exception_has_no_setpoint_or_exit():
    c, endpoint, requests = setup()
    with c:
        endpoint.front_panel_busy = True
        with pytest.raises(ProtocolError, match="exception 6"):
            c.set_setpoint(18)
    assert words(requests, 6) == [(0x0300, 5), (0x1500, 0)]


def test_post_lock_changes_are_rechecked_before_staging():
    c, endpoint, requests = setup()
    with c:
        original = endpoint._respond

        def respond(request):
            response = original(request)
            if request[2:4] == b"\x15\x00":
                endpoint.registers[0x0094] = 170
            return response

        endpoint._respond = respond
        with pytest.raises(ProtocolError, match="uncertain"):
            c.set_setpoint(18)
    assert words(requests, 6) == [(0x0300, 5), (0x1500, 0)]


def test_simulator_stages_until_commit_and_keeps_controlling_after_serial_close():
    now = [0.0]
    endpoint = CalSimulator(clock=lambda: now[0])
    endpoint.open()

    def command(address, value):
        request = _rtu(b"\x01\x06" + address.to_bytes(2, "big") + value.to_bytes(2, "big"))
        endpoint.write(request)
        return endpoint.read(8)

    assert command(0x1500, 0) == b""  # Missing immediately preceding security byte.
    command(0x0300, 5)
    command(0x1500, 0)
    command(0x007F, 180)
    assert endpoint._model._read_setpoint() == 10
    assert endpoint.remote_program_mode
    command(0x0300, 6)
    command(0x1600, 0)
    assert endpoint._model._read_setpoint() == 18
    endpoint.close()
    now[0] = 30
    with Chiller(endpoint.device()) as c:
        assert c.read_temperature() == pytest.approx(18 + 2 / math.e, abs=0.05)


def test_protocol_simulator_monitor_csv_and_gui(tmp_path):
    from tark_chiller.gui import create_app

    c, _, _ = setup()
    with c:
        c.set_setpoint(18)
        m = c.start_monitoring(interval_s=0.05, csv_path=tmp_path / "cal.csv")
        sleep(0.5)
        app = create_app(c, m)
        assert app.server.test_client().get("/").status_code == 200
        c.stop_monitoring()
        s = m.snapshot()
        assert s.sample_count >= 2 and s.failed_samples == 0 and not s.logging_error
        assert all(x.status.backend == "simulator" for x in s.history)


@pytest.mark.parametrize("failure", ["mismatch", "timeout"])
def test_post_commit_readback_failure_is_uncertain_without_replay(failure):
    c, endpoint, requests = setup()
    with c:
        original = endpoint._respond

        def respond(request):
            reply = original(request)
            if endpoint.applied_writes and request[2:4] == b"\x00\x7f":
                return b"" if failure == "timeout" else _rtu(b"\x01\x03\x02\x00\xc8")
            return reply

        endpoint._respond = respond
        with pytest.raises(ProtocolError, match="uncertain"):
            c.set_setpoint(18)
    assert endpoint.applied_writes == 1 and len(words(requests, 6)) == 5


def test_fail_display_invalidates_measurement_but_status_is_diagnostic():
    c, endpoint, _ = setup()
    with c:
        endpoint.registers[0x0306] = 0x26
        assert "INPT FAIL" in c.read_status().detail
        with pytest.raises(ProtocolError, match="reports FAIL"):
            c.read_temperature()


@pytest.mark.parametrize("firmware", [0xFFFF, 1, 2])
def test_documented_firmware_codes_are_accepted_read_only(firmware):
    s = CalSimulator()
    s.registers[0x04FD] = firmware
    with Chiller(
        SerialDevice(s.device().settings, codec=Cal33xx(1), serial_factory=s.factory)
    ) as c:
        assert f"firmware=0x{firmware:04x}" in c.read_status().detail
