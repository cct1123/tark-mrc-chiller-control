"""TEST-004/005: entirely synthetic byte fixtures, NEVER MRC wire commands.

No test constructs pySerial or discovers/opens an OS serial port. Numeric serial
settings are arbitrary fixture inputs and establish no physical compatibility.
"""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import sleep

import pytest

from tark_chiller import Chiller, CoolantProfile, DeviceStatus
from tark_chiller.errors import (
    ChillerConnectionError,
    ChillerTimeoutError,
    ProtocolError,
    ProtocolUnavailableError,
    SetpointValidationError,
    TransportError,
)
from tark_chiller.hardware import SerialDevice
from tark_chiller.protocol import MissingProtocol
from tark_chiller.transport import RS232Transport, RS485Mode, RS485Transport, SerialSettings


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSerial:
    def __init__(self, response=b"test-response!", clock=None):
        self.is_open = False
        self.opens = 0
        self.closes = 0
        self.writes = []
        self.response = response
        self.buffer = b""
        self.clock = clock
        self.write_result = None
        self.write_error = None
        self.read_error = None
        self.open_error = None
        self.close_error = None
        self.factory_arguments = []

    def factory(self, **kwargs):
        self.factory_arguments.append(kwargs)
        assert kwargs["port"] is None
        return self

    def open(self):
        self.opens += 1
        if self.open_error:
            raise self.open_error
        self.is_open = True

    def close(self):
        self.closes += 1
        if self.close_error:
            raise self.close_error
        self.is_open = False

    def write(self, request):
        self.writes.append(request)
        if self.write_error:
            raise self.write_error
        self.buffer = self.response
        return len(request) if self.write_result is None else self.write_result

    def read(self, size):
        if self.read_error:
            raise self.read_error
        if self.clock is not None:
            self.clock.now += 0.1
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result


def settings(**changes):
    # Test data, not a proposed MRC configuration. Never used with a real factory.
    value = SerialSettings("FAKE-ONLY", 19200, 7, "E", 2, False, False, False, False, False)
    return replace(value, **changes)


def transport(fake=None, **kwargs):
    fake = fake or FakeSerial()
    return RS232Transport(settings(), serial_factory=fake.factory, **kwargs), fake


class SyntheticCodec:
    """A minimal fixture demonstrating routing, with no simulated Tark protocol."""

    def __init__(self):
        self.operations = []
        self.setpoint = 20.0
        self.invalid_result = False
        self.decode_error = None

    def ensure_available(self):
        pass

    def encode(self, operation, value=None):
        self.operations.append((operation, value))
        if operation == "set_setpoint":
            self.setpoint = value
        return b"test-request!"

    def is_complete(self, response):
        return response.endswith(b"!")

    def decode(self, operation, response):
        if self.decode_error:
            raise self.decode_error
        if self.invalid_result:
            return math.nan
        assert response == b"test-response!"
        if operation == "get_temperature":
            return 21.5
        if operation == "get_setpoint":
            return self.setpoint
        if operation == "get_status":
            return DeviceStatus(True, "hardware", "synthetic test only")
        return None


def test_missing_protocol_blocks_before_factory_open_or_write():
    link, fake = transport()
    device = SerialDevice(link)
    assert not device.get_status().connected
    with pytest.raises(ProtocolUnavailableError, match="manual"):
        device.connect()
    assert fake.factory_arguments == []
    assert fake.opens == 0
    assert fake.writes == []
    assert not device.is_connected
    assert "manual" in device.get_status().detail
    for operation in (device.get_temperature, device.get_setpoint, lambda: device.set_setpoint(20)):
        with pytest.raises(ChillerConnectionError):
            operation()
    missing = MissingProtocol()
    for operation in (
        lambda: missing.encode("get_temperature"),
        lambda: missing.decode("get_temperature", b""),
        lambda: missing.is_complete(b""),
    ):
        with pytest.raises(ProtocolUnavailableError):
            operation()


def test_same_public_api_routes_synthetic_codec_without_startup_write():
    link, fake = transport()
    codec = SyntheticCodec()
    device = SerialDevice(link, codec)
    chiller = Chiller(device)
    chiller.connect()
    chiller.connect()
    assert fake.opens == 1
    assert fake.writes == []
    assert chiller.is_connected
    assert chiller.get_temperature() == 21.5
    assert chiller.get_setpoint() == 20
    assert chiller.get_status().backend == "hardware"
    chiller.set_setpoint(25)
    assert chiller.get_setpoint() == 25
    assert ("set_setpoint", 25.0) in codec.operations
    chiller.disconnect()
    chiller.disconnect()
    assert fake.closes == 1
    assert not chiller.is_connected


@pytest.mark.parametrize("invalid", [-1, 1.99, 40.01, math.nan, math.inf, True, "20", 10**400])
def test_hardware_guard_precedes_encoding_or_write(invalid):
    link, fake = transport()
    codec = SyntheticCodec()
    device = SerialDevice(link, codec)
    device.connect()
    with pytest.raises(SetpointValidationError):
        device.set_setpoint(invalid)
    assert codec.operations == []
    assert fake.writes == []
    assert device.is_connected


def test_explicit_custom_policy_and_default_backend_guard():
    profile = CoolantProfile("fixture", -3, 35, "test policy only, no physical suitability claim")
    link, fake = transport()
    device = SerialDevice(link, SyntheticCodec(), coolant=profile)
    chiller = Chiller(device, coolant=profile)
    chiller.connect()
    chiller.set_setpoint(-2)
    assert len(fake.writes) == 1
    link2, fake2 = transport()
    default = Chiller(SerialDevice(link2, SyntheticCodec()), coolant=profile)
    default.connect()
    with pytest.raises(SetpointValidationError):
        default.set_setpoint(-2)
    assert fake2.writes == []
    with pytest.raises(AttributeError):
        device.coolant = profile
    with pytest.raises(SetpointValidationError):
        SerialDevice(link, coolant=None)


@pytest.mark.parametrize(
    "operation", ["get_temperature", "get_setpoint", "get_status", "set_setpoint"]
)
def test_bad_decoded_response_closes_device(operation):
    link, fake = transport()
    codec = SyntheticCodec()
    codec.invalid_result = True
    device = SerialDevice(link, codec)
    device.connect()
    with pytest.raises(ProtocolError):
        getattr(device, operation)(20) if operation == "set_setpoint" else getattr(
            device, operation
        )()
    assert not device.is_connected
    assert fake.closes == 1
    assert device.get_status().detail


def test_codec_error_is_typed_and_cleanup_occurs():
    link, fake = transport()
    codec = SyntheticCodec()
    codec.decode_error = ValueError("synthetic malformed response")
    device = SerialDevice(link, codec)
    device.connect()
    with pytest.raises(ProtocolError, match="synthetic malformed"):
        device.get_temperature()
    assert not device.is_connected
    assert fake.closes == 1


@pytest.mark.parametrize("invalid", [0, -1, math.nan, math.inf, True, "1", 10**400, 1e100])
def test_timeout_configuration_rejected_before_factory(invalid):
    fake = FakeSerial()
    with pytest.raises(ValueError):
        transport(fake, transaction_timeout_s=invalid)
    assert fake.factory_arguments == []


@pytest.mark.parametrize("invalid", [0, -1, 1.5, True, None])
def test_response_budget_configuration_rejected(invalid):
    with pytest.raises(ValueError):
        transport(max_response_bytes=invalid)


@pytest.mark.parametrize(
    "changes",
    [
        {"port": ""},
        {"baudrate": 0},
        {"baudrate": True},
        {"bytesize": 9},
        {"parity": "?"},
        {"stopbits": True},
        {"stopbits": 3},
        {"rtscts": None},
        {"dtr": 0},
    ],
)
def test_explicit_serial_settings_are_validated(changes):
    with pytest.raises(ValueError):
        settings(**changes)


def test_required_settings_have_no_defaults_and_are_applied_before_open():
    with pytest.raises(TypeError):
        SerialSettings()
    link, fake = transport()
    link.open()
    assert fake.factory_arguments == [
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
    assert fake.port == "FAKE-ONLY"
    assert fake.rts is False and fake.dtr is False
    assert link.exchange(b"test-request!", lambda data: data.endswith(b"!")) == b"test-response!"


def test_native_rs485_mode_is_explicit_and_injected():
    fake = FakeSerial()
    mode = RS485Mode(True, False, False, None, 0.0)
    link = RS485Transport(
        settings(), mode, serial_factory=fake.factory, rs485_settings_factory=dict
    )
    link.open()
    assert fake.rs485_mode == dict(
        rts_level_for_tx=True,
        rts_level_for_rx=False,
        loopback=False,
        delay_before_tx=None,
        delay_before_rx=0.0,
    )
    with pytest.raises(TypeError):
        RS485Mode()
    with pytest.raises(ValueError):
        replace(mode, delay_before_tx=-1)
    with pytest.raises(ValueError):
        replace(mode, rts_level_for_tx=1)


def test_open_failure_cleans_up_without_write():
    link, fake = transport()
    fake.open_error = OSError("synthetic inaccessible port")
    with pytest.raises(TransportError, match="inaccessible"):
        link.open()
    assert fake.closes == 1
    assert fake.writes == []
    assert not link.is_open


def test_unsupported_rs485_mode_fails_and_closes_without_open_or_write():
    fake = FakeSerial()

    def unsupported(**kwargs):
        raise ValueError("synthetic unsupported native mode")

    link = RS485Transport(
        settings(),
        RS485Mode(True, False, False, None, None),
        serial_factory=fake.factory,
        rs485_settings_factory=unsupported,
    )
    with pytest.raises(TransportError, match="unsupported"):
        link.open()
    assert fake.opens == 0
    assert fake.writes == []
    assert fake.closes == 1


def test_framing_error_closes_transport():
    link, fake = transport()
    link.open()

    def malformed(data):
        raise ProtocolError("synthetic framing error")

    with pytest.raises(ProtocolError, match="framing"):
        link.exchange(b"test-request!", malformed)
    assert not link.is_open
    assert fake.closes == 1


@pytest.mark.parametrize("kind", ["exception", "nonboolean"])
def test_bad_framing_callback_is_a_protocol_error_not_a_recoverable_transport_error(kind):
    link, fake = transport()
    link.open()

    def malformed(data):
        if kind == "exception":
            raise ValueError("synthetic malformed frame")
        return "not a boolean"

    with pytest.raises(ProtocolError, match="framing check"):
        link.exchange(b"test-request!", malformed)
    assert not link.is_open
    assert fake.closes == 1


@pytest.mark.parametrize("operation", ["open", "write", "read", "close"])
def test_os_timeout_is_a_typed_chiller_timeout(operation):
    link, fake = transport()
    setattr(fake, f"{operation}_error", TimeoutError("synthetic OS timeout"))
    if operation != "open":
        link.open()
    with pytest.raises(ChillerTimeoutError, match="synthetic OS timeout"):
        if operation == "open":
            link.open()
        elif operation == "close":
            link.close()
        else:
            link.exchange(b"test-request!", lambda data: True)
    assert not link.is_open


def test_pyserial_write_timeout_is_a_typed_chiller_timeout():
    serial = pytest.importorskip("serial")
    link, fake = transport()
    fake.write_error = serial.SerialTimeoutException("synthetic pySerial timeout")
    link.open()
    with pytest.raises(ChillerTimeoutError, match="synthetic pySerial timeout"):
        link.exchange(b"test-request!", lambda data: True)
    assert not link.is_open
    assert len(fake.writes) == 1


def test_framing_callback_time_counts_toward_transaction_budget():
    clock = Clock()
    link, fake = transport(clock=clock)
    link.open()

    def late_completion(data):
        clock.now = 1.1
        return True

    with pytest.raises(ChillerTimeoutError, match="timed out"):
        link.exchange(b"test-request!", late_completion)
    assert fake.closes == 1


def test_completed_frame_at_byte_budget_is_accepted():
    fake = FakeSerial(response=b"!!")
    link, _ = transport(fake, max_response_bytes=2)
    link.open()
    assert link.exchange(b"test-request!", lambda data: len(data) == 2) == b"!!"


def test_write_duration_counts_toward_whole_transaction_deadline():
    clock = Clock()

    class LateWriter(FakeSerial):
        def write(self, request):
            result = super().write(request)
            clock.now = 1.1
            return result

    fake = LateWriter()
    link, _ = transport(fake, clock=clock)
    link.open()
    with pytest.raises(TransportError, match="timed out"):
        link.exchange(b"test-request!", lambda data: True)
    assert len(fake.writes) == 1
    assert fake.closes == 1


@pytest.mark.parametrize("kind", ["partial", "write", "read", "timeout", "budget"])
def test_exchange_failure_closes_and_never_retries(kind):
    clock = Clock()
    fake = FakeSerial(response=b"unfinished", clock=clock)
    link, _ = transport(fake, clock=clock, transaction_timeout_s=0.5, max_response_bytes=2)
    if kind == "partial":
        fake.write_result = 2
    elif kind == "write":
        fake.write_error = OSError("synthetic write timeout")
    elif kind == "read":
        fake.read_error = OSError("synthetic disconnected adapter")
    elif kind == "timeout":
        fake.response = b""
    link.open()
    with pytest.raises(ProtocolError if kind == "budget" else TransportError):
        link.exchange(b"test-request!", lambda data: False)
    assert len(fake.writes) == 1
    assert fake.closes == 1
    assert not link.is_open
    if kind == "timeout":
        assert clock.now == pytest.approx(0.5)
    link.open()
    assert len(fake.writes) == 1  # Reconnection does not replay a failed request.


def test_failed_close_preserves_handle_for_explicit_cleanup_but_disables_io():
    link, fake = transport()
    link.open()
    fake.close_error = OSError("synthetic close error")
    with pytest.raises(TransportError, match="close error"):
        link.close()
    assert not link.is_open
    with pytest.raises(TransportError, match="closed"):
        link.exchange(b"test-request!", lambda data: True)
    assert fake.writes == []
    fake.close_error = None
    link.close()
    assert fake.closes == 2


def test_parallel_transactions_do_not_interleave():
    entered = Event()
    release = Event()

    class PausedSerial(FakeSerial):
        def read(self, size):
            entered.set()
            assert release.wait(1.0)
            return super().read(size)

    fake = PausedSerial(response=b"!")
    link, _ = transport(fake, transaction_timeout_s=2.0)
    link.open()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(link.exchange, b"first-test!", lambda data: data == b"!")
        assert entered.wait(1.0)
        second = pool.submit(link.exchange, b"second-test!", lambda data: data == b"!")
        sleep(0.02)
        assert fake.writes == [b"first-test!"]
        release.set()
        assert first.result(timeout=1) == b"!"
        assert second.result(timeout=1) == b"!"
    assert fake.writes == [b"first-test!", b"second-test!"]
