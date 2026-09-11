"""Production Chiller/serial behavior with synthetic memory endpoints only."""

import errno
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import perf_counter

import pytest
from fakes import FakeCodec, FakeSerial, fake_settings, make_serial

from tark_chiller.controller import Chiller, ProtocolError, Status
from tark_chiller.serial import RS485Mode, SerialDevice, SerialSettings


def test_absent_protocol_prevents_endpoint_creation():
    endpoint = FakeSerial()
    chiller = Chiller(SerialDevice(fake_settings(), serial_factory=endpoint.factory))
    with pytest.raises(ProtocolError, match="manual"):
        chiller.connect()
    assert endpoint.factory_arguments == []
    assert endpoint.open_count == endpoint.write_count == 0
    assert not chiller.is_connected
    chiller.disconnect()


@pytest.mark.parametrize("rs485", [False, True])
def test_common_api_uses_explicit_settings_and_no_startup_writes(rs485):
    device, endpoint = make_serial(rs485=rs485)
    chiller = Chiller(device)
    try:
        chiller.connect()
        chiller.connect()
        assert endpoint.open_count == 1 and endpoint.write_count == 0
        assert endpoint.port == "FAKE-ONLY"
        assert endpoint.rts is False and endpoint.dtr is False
        assert endpoint.factory_arguments == [
            dict(
                port=None,
                baudrate=19200,
                bytesize=7,
                parity="E",
                stopbits=2,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                timeout=1.0,
                write_timeout=1.0,
            )
        ]
        if rs485:
            assert endpoint.rs485_mode.rts_level_for_tx is True
            assert endpoint.rs485_mode.rts_level_for_rx is False
        else:
            assert endpoint.rs485_mode is None
        assert chiller.read_temperature() == chiller.read_setpoint() == 20
        chiller.set_setpoint(18)
        assert chiller.read_setpoint() == 18
        assert chiller.read_status().backend == "hardware"
        assert endpoint.applied_writes == 1
    finally:
        chiller.disconnect()
    chiller.disconnect()
    assert endpoint.close_count == 1


@pytest.mark.parametrize("value", [True, "20", {}, [], math.nan, math.inf, -1, 40.1, 10**400])
def test_public_guard_rejects_before_codec_or_endpoint_access(value):
    device, endpoint = make_serial()
    with pytest.raises(ValueError):
        Chiller(device).set_setpoint(value)
    assert device._codec.request_id == 0
    assert endpoint.factory_arguments == []
    assert endpoint.applied_writes == endpoint.write_count == 0


@pytest.mark.parametrize(
    "changes",
    [
        dict(port=""),
        dict(baudrate=True),
        dict(baudrate=0),
        dict(bytesize=9),
        dict(parity="?"),
        dict(stopbits=True),
        dict(stopbits=3),
        dict(rts=1),
        dict(xonxoff=None),
    ],
)
def test_invalid_settings_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(fake_settings(), **changes)


def test_settings_and_rs485_have_no_implicit_defaults():
    with pytest.raises(TypeError):
        SerialSettings()
    with pytest.raises(TypeError):
        RS485Mode()
    with pytest.raises(ValueError):
        RS485Mode(True, False, False, -1, None)
    with pytest.raises(ValueError):
        RS485Mode(1, False, False, None, None)


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf, True, "1", 10**400])
def test_invalid_timeout_never_creates_endpoint(timeout):
    endpoint = FakeSerial()
    with pytest.raises(ValueError):
        make_serial(endpoint=endpoint, timeout_s=timeout)
    assert endpoint.factory_arguments == []


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_invalid_response_budget(budget):
    with pytest.raises(ValueError):
        make_serial(max_response_bytes=budget)


@pytest.mark.parametrize(
    "name,value",
    [
        ("settings", replace(fake_settings(), port="OTHER-FAKE-ONLY")),
        ("rs485", RS485Mode(True, False, False, None, None)),
        ("timeout_s", math.nan),
        ("max_response_bytes", math.inf),
    ],
)
def test_serial_configuration_is_read_only_before_and_after_connect(name, value):
    device, _ = make_serial()
    original = getattr(device, name)
    with pytest.raises(AttributeError):
        setattr(device, name, value)
    with Chiller(device):
        with pytest.raises(AttributeError):
            setattr(device, name, value)
        assert getattr(device, name) == original


@pytest.mark.parametrize("rs485", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(errno.ENOENT, "fake port missing"),
        PermissionError(errno.EACCES, "fake permission denied"),
        OSError(errno.EBUSY, "fake port busy"),
    ],
)
def test_port_failures_keep_reason_and_exhaust_finite_recovery(rs485, error):
    device, endpoint = make_serial(rs485=rs485)
    endpoint.open_error = error
    chiller = Chiller(device, reconnect_attempts=2, reconnect_delay_s=0.001)
    with pytest.raises(OSError, match=str(error.args[-1])):
        chiller.connect()
    assert endpoint.open_count == 3
    assert endpoint.write_count == 0 and not endpoint.is_open
    for _ in range(3):
        with pytest.raises(ConnectionError):
            chiller.read_temperature()
    assert endpoint.open_count == 3
    chiller.disconnect()


@pytest.mark.parametrize(
    "reply",
    [
        b"garbage\n",
        b"TEST|999|get_temperature|20\n",
        b"TEST|1|get_setpoint|20\n",
        b"TEST|1|get_temperature|NaN\n",
    ],
)
def test_malformed_or_unexpected_reply_suspends_recovery(reply):
    device, endpoint = make_serial(endpoint=FakeSerial(reply))
    chiller = Chiller(device, reconnect_attempts=2, reconnect_delay_s=0.001)
    chiller.connect()
    with pytest.raises(ProtocolError):
        chiller.read_temperature()
    assert not endpoint.is_open
    with pytest.raises(ConnectionError):
        chiller.read_temperature()
    assert endpoint.open_count == endpoint.write_count == 1
    chiller.disconnect()


@pytest.mark.parametrize("decoded_status", [None, True, 20.0, Status(True, "simulator")])
def test_codec_cannot_supply_application_connection_state(decoded_status):
    class BadStatusCodec(FakeCodec):
        def decode(self, operation, response):
            return decoded_status

    device, endpoint = make_serial(codec=BadStatusCodec())
    with Chiller(device, reconnect_attempts=2) as chiller:
        with pytest.raises(ProtocolError, match="invalid hardware status"):
            chiller.read_status()
        assert not chiller.read_status().connected
        assert "suspended" in chiller.read_status().detail
        assert endpoint.write_count == endpoint.open_count == 1
        assert not endpoint.is_open


@pytest.mark.parametrize("reply", [b"", b"TEST|1|get_temperature|20"])
def test_absent_or_truncated_reply_times_out(reply):
    device, endpoint = make_serial(endpoint=FakeSerial(reply), timeout_s=0.02)
    chiller = Chiller(device)
    chiller.connect()
    with pytest.raises(TimeoutError):
        chiller.read_temperature()
    assert not endpoint.is_open and endpoint.write_count == 1
    chiller.disconnect()


def test_exact_response_byte_budget_and_overflow():
    reply = b"TEST|1|get_temperature|20\n"
    for budget, succeeds in ((len(reply), True), (len(reply) - 1, False)):
        device, endpoint = make_serial(endpoint=FakeSerial(reply), max_response_bytes=budget)
        chiller = Chiller(device)
        chiller.connect()
        with pytest.raises(AttributeError):
            device.max_response_bytes = math.inf
        try:
            if succeeds:
                assert chiller.read_temperature() == 20
            else:
                with pytest.raises(ProtocolError, match="byte budget"):
                    chiller.read_temperature()
                assert not endpoint.is_open
        finally:
            chiller.disconnect()


@pytest.mark.parametrize("kind", ["write", "partial", "read", "pyserial_timeout"])
def test_io_failure_closes_endpoint_and_never_retries_write(kind):
    device, endpoint = make_serial()
    chiller = Chiller(device, reconnect_attempts=2, reconnect_delay_s=0.001)
    chiller.connect()
    if kind == "write":
        endpoint.write_error = OSError("fake write failure")
    elif kind == "partial":
        endpoint.write_result = 1
    elif kind == "read":
        endpoint.read_error = OSError("fake unplug during acknowledgement")
    else:
        from serial import SerialTimeoutException

        endpoint.write_error = SerialTimeoutException("fake write timeout")
    with pytest.raises(TimeoutError if kind == "pyserial_timeout" else OSError):
        chiller.set_setpoint(18)
    assert endpoint.write_count == endpoint.open_count == 1
    assert not endpoint.is_open
    chiller.disconnect()


def test_lost_acknowledgement_is_not_replayed_on_read_recovery():
    device, endpoint = make_serial(timeout_s=0.02)
    chiller = Chiller(device, reconnect_attempts=2, reconnect_delay_s=0.001)
    chiller.connect()
    endpoint.response = b""
    with pytest.raises(TimeoutError):
        chiller.set_setpoint(18)
    assert endpoint.applied_writes == 1
    endpoint.response = None
    assert chiller.read_setpoint() == 18
    assert endpoint.open_count == 2 and endpoint.applied_writes == 1
    chiller.disconnect()


def test_mid_run_partial_reply_disconnect_recovers_only_reads():
    class UnpluggedEndpoint(FakeSerial):
        unplug_after = None

        def read(self, size=1):
            if self.unplug_after == self.read_count:
                self.unplug_after = None
                raise OSError("fake cable removed midway through response")
            return super().read(size)

    device, endpoint = make_serial(endpoint=UnpluggedEndpoint())
    chiller = Chiller(device, reconnect_attempts=1, reconnect_delay_s=0.001)
    chiller.connect()
    assert chiller.read_temperature() == 20
    endpoint.unplug_after = endpoint.read_count + 5
    assert chiller.read_temperature() == 20
    assert endpoint.open_count == 2 and endpoint.write_count == 3
    assert endpoint.applied_writes == 0
    chiller.disconnect()


def test_old_reply_after_reconnect_is_not_accepted_as_new_read():
    class LateReply(FakeSerial):
        def write(self, request):
            result = super().write(request)
            if self.write_count == 1:
                self.response = self.buffer
                self.buffer = b""
            return result

    device, endpoint = make_serial(endpoint=LateReply(), timeout_s=0.02)
    chiller = Chiller(device, reconnect_attempts=1, reconnect_delay_s=0.001)
    chiller.connect()
    with pytest.raises(ProtocolError, match="identity"):
        chiller.read_temperature()
    assert endpoint.write_count == endpoint.open_count == 2
    assert not endpoint.is_open
    chiller.disconnect()


@pytest.mark.parametrize("delay,succeeds", [(0.005, True), (0.06, False)])
def test_delayed_complete_reply_respects_transaction_budget(delay, succeeds):
    device, endpoint = make_serial(timeout_s=0.04)
    endpoint.delay_s = delay
    chiller = Chiller(device)
    chiller.connect()
    with pytest.raises(AttributeError):
        device.timeout_s = math.nan
    try:
        if succeeds:
            assert chiller.read_temperature() == 20
        else:
            with pytest.raises(TimeoutError):
                chiller.read_temperature()
            assert not endpoint.is_open
    finally:
        chiller.disconnect()


def test_pending_read_shutdown_cancels_recovery_without_reopening():
    device, endpoint = make_serial(endpoint=FakeSerial(b""), timeout_s=0.08)
    chiller = Chiller(device, reconnect_attempts=2, reconnect_delay_s=5)
    chiller.connect()
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(chiller.read_temperature)
        assert endpoint.read_pending.wait(1)
        started = perf_counter()
        chiller.disconnect()
        assert perf_counter() - started < 1
        with pytest.raises(ConnectionError):
            pending.result(timeout=1)
    assert endpoint.open_count == endpoint.write_count == 1
    assert not endpoint.is_open


def test_close_failure_disables_io_and_retains_handle_for_cleanup():
    device, endpoint = make_serial()
    chiller = Chiller(device)
    chiller.connect()
    endpoint.close_error = OSError("fake close failure")
    with pytest.raises(OSError, match="close failure"):
        chiller.disconnect()
    assert not chiller.is_connected
    with pytest.raises(ConnectionError):
        chiller.read_temperature()
    assert endpoint.write_count == 0
    endpoint.close_error = None
    chiller.disconnect()
    assert not endpoint.is_open and endpoint.close_count == 2


@pytest.mark.parametrize("stage", ["open", "read", "write", "decode"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interruption_closes_endpoint_and_preserves_exception(stage, interruption):
    class InterruptedEndpoint(FakeSerial):
        def open(self):
            super().open()
            if stage == "open":
                raise interruption("fake interruption")

        def write(self, request):
            result = super().write(request)
            if stage == "write":
                raise interruption("fake interruption")
            return result

    class InterruptedCodec(FakeCodec):
        def decode(self, operation, response):
            raise interruption("fake interruption")

    endpoint = InterruptedEndpoint()
    if stage == "read":
        endpoint.read_error = interruption("fake interruption")
    device, _ = make_serial(
        endpoint=endpoint, codec=InterruptedCodec() if stage == "decode" else None
    )
    chiller = Chiller(device)
    try:
        with pytest.raises(interruption, match="fake interruption"):
            chiller.connect()
            chiller.set_setpoint(18) if stage == "write" else chiller.read_temperature()
        assert not endpoint.is_open
        assert endpoint.open_count == 1
        assert endpoint.applied_writes == (1 if stage == "write" else 0)
    finally:
        chiller.disconnect()


def test_interruption_survives_failed_cleanup_and_handle_can_be_closed_later():
    device, endpoint = make_serial()
    chiller = Chiller(device)
    chiller.connect()
    endpoint.read_error = KeyboardInterrupt("fake interrupted read")
    endpoint.close_error = OSError("fake close failure")
    with pytest.raises(KeyboardInterrupt, match="interrupted read") as interrupted:
        chiller.read_temperature()
    assert "close failure" in interrupted.value.__notes__[0]
    assert not chiller.is_connected and endpoint.is_open
    endpoint.close_error = None
    chiller.disconnect()
    assert not endpoint.is_open


def test_unsupported_native_rs485_stops_before_port_open():
    class UnsupportedMode(FakeSerial):
        def __setattr__(self, name, value):
            if name == "rs485_mode" and value is not None:
                raise ValueError("fake unsupported native mode")
            super().__setattr__(name, value)

    device, endpoint = make_serial(endpoint=UnsupportedMode(), rs485=True)
    with pytest.raises(OSError, match="unsupported native mode"):
        Chiller(device).connect()
    assert endpoint.open_count == endpoint.write_count == 0
    assert endpoint.close_count == 1
