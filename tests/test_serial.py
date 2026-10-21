"""Serial ownership, deadlines and recovery using actual CAL frames in memory."""

import builtins
import errno
import math
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

import pytest
from fakes import FakeSerial, fake_settings, make_serial

from tark_chiller.controller import Cal33xx, ProtocolError, TarkChiller, _rtu


@pytest.mark.parametrize("protocol", [None, object()])
def test_absent_or_unsupported_protocol_prevents_endpoint_creation(protocol):
    endpoint = FakeSerial()
    chiller = TarkChiller(fake_settings(), protocol=protocol, serial_factory=endpoint.factory)
    with pytest.raises(ProtocolError, match="protocol=Cal33xx.*protocol=T255P"):
        chiller.connect()
    assert not endpoint.factory_arguments and not endpoint.open_count
    chiller.disconnect()


def test_missing_pyserial_explains_required_dependency_without_creating_port(monkeypatch):
    original_import = builtins.__import__

    def without_pyserial(name, *args, **kwargs):
        if name == "serial":
            raise ModuleNotFoundError("No module named 'serial'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pyserial)
    chiller = TarkChiller(fake_settings(), protocol=Cal33xx(1))
    with pytest.raises(OSError, match="python -m pip install pyserial"):
        chiller.connect()
    assert not chiller.is_connected
    chiller.disconnect()


@pytest.mark.parametrize("rs485", [False, True])
def test_common_api_uses_explicit_settings_and_no_startup_actuation(rs485):
    chiller, endpoint = make_serial(rs485=rs485)
    with chiller as chiller:
        chiller.connect()
        assert endpoint.open_count == 1 and not endpoint.applied_writes
        assert all(request[1] == 3 for request in endpoint.writes)
        assert endpoint.port == "FAKE-ONLY" and not endpoint.rts and not endpoint.dtr
        assert endpoint.factory_arguments == [
            dict(
                port=None,
                baudrate=19200,
                bytesize=8,
                parity="E",
                stopbits=1,
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
        assert chiller.read_status()["backend"] == "simulator"
        assert endpoint.applied_writes == 1
    chiller.disconnect()
    assert not endpoint.is_open and endpoint.close_count == 1


@pytest.mark.parametrize("value", [True, "20", {}, [], math.nan, math.inf, -1, 40.1, 10**400])
def test_public_guard_rejects_before_endpoint_access(value):
    chiller, endpoint = make_serial()
    with pytest.raises(ValueError):
        chiller.set_setpoint(value)
    assert not endpoint.factory_arguments and not endpoint.write_count


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
        TarkChiller({**fake_settings(), **changes})


def test_settings_and_rs485_have_no_implicit_defaults():
    with pytest.raises(ValueError):
        TarkChiller({})
    with pytest.raises(ValueError):
        TarkChiller(fake_settings(), rs485={})
    for changes in ({"delay_before_tx": -1}, {"rts_level_for_tx": 1}):
        mode = {
            "rts_level_for_tx": True,
            "rts_level_for_rx": False,
            "loopback": False,
            "delay_before_tx": None,
            "delay_before_rx": None,
        }
        with pytest.raises(ValueError):
            TarkChiller(fake_settings(), rs485={**mode, **changes})


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf, True, "1", 10**400])
def test_invalid_timeout_never_creates_endpoint(timeout):
    endpoint = FakeSerial()
    with pytest.raises(ValueError):
        make_serial(endpoint=endpoint, timeout_s=timeout)
    assert not endpoint.factory_arguments


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_invalid_response_budget(budget):
    with pytest.raises(ValueError):
        make_serial(max_response_bytes=budget)


@pytest.mark.parametrize(
    "name,value",
    [
        ("settings", {**fake_settings(), "port": "OTHER-FAKE-ONLY"}),
        (
            "rs485",
            {
                "rts_level_for_tx": True,
                "rts_level_for_rx": False,
                "loopback": False,
                "delay_before_tx": None,
                "delay_before_rx": None,
            },
        ),
        ("timeout_s", math.nan),
        ("max_response_bytes", math.inf),
    ],
)
def test_serial_configuration_is_read_only_before_and_after_connect(name, value):
    chiller, _ = make_serial()
    original = getattr(chiller, name)
    with pytest.raises(AttributeError):
        setattr(chiller, name, value)
    with chiller:
        with pytest.raises(AttributeError):
            setattr(chiller, name, value)
        assert getattr(chiller, name) == original


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
    chiller, endpoint = make_serial(rs485=rs485, reconnect_attempts=2, reconnect_delay_s=0.001)
    endpoint.open_error = error
    with pytest.raises(OSError, match=str(error.args[-1])):
        chiller.connect()
    assert endpoint.open_count == 3 and not endpoint.is_open
    for _ in range(3):
        with pytest.raises(ConnectionError):
            chiller.read_temperature()
    assert endpoint.open_count == 3 and not endpoint.write_count
    chiller.disconnect()


@pytest.mark.parametrize(
    "reply",
    [
        b"garbage",
        _rtu(b"\x02\x03\x02\x00\xc8"),
        b"\x01\x03\x02\x00\xc8\x00\x00",
    ],
)
def test_malformed_or_unexpected_reply_suspends_recovery(reply):
    chiller, endpoint = make_serial(reconnect_attempts=2, reconnect_delay_s=0.001)
    chiller.connect()
    baseline = endpoint.write_count
    endpoint.response = reply
    with pytest.raises(ProtocolError):
        chiller.read_temperature()
    assert not endpoint.is_open and endpoint.write_count == baseline + 1
    with pytest.raises(ConnectionError):
        chiller.read_temperature()
    assert endpoint.open_count == 1
    chiller.disconnect()


@pytest.mark.parametrize("reply", [b"", b"\x01\x03\x02\x00"])
def test_absent_or_truncated_reply_times_out(reply):
    chiller, endpoint = make_serial(timeout_s=0.02)
    with chiller as chiller:
        baseline = endpoint.write_count
        endpoint.response = reply
        with pytest.raises(TimeoutError):
            chiller.read_temperature()
        assert not endpoint.is_open and endpoint.write_count == baseline + 1


@pytest.mark.parametrize("budget,succeeds", [(7, True), (6, False)])
def test_exact_response_byte_budget_and_overflow(budget, succeeds):
    chiller, endpoint = make_serial(max_response_bytes=budget)
    if succeeds:
        with chiller:
            assert chiller.read_temperature() == 20
    else:
        with pytest.raises(ProtocolError, match="byte budget"):
            chiller.connect()
        assert not endpoint.is_open
        chiller.disconnect()


@pytest.mark.parametrize("kind", ["write", "partial", "read", "pyserial_timeout"])
def test_io_failure_closes_endpoint_and_never_retries_write(kind):
    chiller, endpoint = make_serial(reconnect_attempts=2, reconnect_delay_s=0.001)
    with chiller as chiller:
        baseline = endpoint.write_count
        if kind == "write":
            endpoint.write_error = OSError("fake write failure")
        elif kind == "partial":
            endpoint.write_result = 1
        elif kind == "read":
            endpoint.read_error = OSError("fake unplug during preflight")
        else:
            from serial import SerialTimeoutException

            endpoint.write_error = SerialTimeoutException("fake write timeout")
        with pytest.raises(TimeoutError if kind == "pyserial_timeout" else OSError):
            chiller.set_setpoint(18)
        assert endpoint.write_count == baseline + 1 and endpoint.open_count == 1
        assert not endpoint.is_open and not endpoint.applied_writes


def test_lost_write_acknowledgement_is_latched_and_never_replayed(monkeypatch):
    chiller, endpoint = make_serial(timeout_s=0.02, reconnect_attempts=2)
    original = endpoint._respond

    def lost_ack(request):
        response = original(request)
        return b"" if request[1:4] == b"\x06\x16\x00" else response

    monkeypatch.setattr(endpoint, "_respond", lost_ack)
    with chiller as chiller:
        with pytest.raises(ProtocolError, match="uncertain"):
            chiller.set_setpoint(18)
        before = endpoint.write_count
        assert endpoint.applied_writes == 1 and not endpoint.is_open
        with pytest.raises(ProtocolError, match="uncertain"):
            chiller.connect()
        assert endpoint.write_count == before and endpoint.applied_writes == 1


def test_mid_run_partial_reply_disconnect_recovers_only_reads():
    class UnpluggedEndpoint(FakeSerial):
        unplug_after = None

        def read(self, size=1):
            if self.unplug_after == self.read_count:
                self.unplug_after = None
                raise OSError("fake cable removed midway through response")
            return super().read(size)

    chiller, endpoint = make_serial(
        endpoint=UnpluggedEndpoint(), reconnect_attempts=1, reconnect_delay_s=0.001
    )
    with chiller as chiller:
        assert chiller.read_temperature() == 20
        endpoint.unplug_after = endpoint.read_count + 3
        assert chiller.read_temperature() == 20
        assert endpoint.open_count == 2 and not endpoint.applied_writes


def test_stale_buffered_reply_is_discarded_before_next_request():
    chiller, endpoint = make_serial()
    with chiller as chiller:
        endpoint._buffer = _rtu(b"\x01\x03\x02\xff\xff")
        assert chiller.read_temperature() == 20
        assert not endpoint.in_waiting


@pytest.mark.parametrize("delay,succeeds", [(0.005, True), (0.06, False)])
def test_delayed_complete_reply_respects_transaction_budget(delay, succeeds):
    chiller, endpoint = make_serial(timeout_s=0.04)
    with chiller as chiller:
        endpoint.delay_s = delay
        if succeeds:
            assert chiller.read_temperature() == 20
        else:
            with pytest.raises(TimeoutError):
                chiller.read_temperature()
            assert not endpoint.is_open


def test_pending_read_shutdown_cancels_recovery_without_reopening():
    chiller, endpoint = make_serial(timeout_s=0.08, reconnect_attempts=2, reconnect_delay_s=5)
    chiller.connect()
    endpoint.response = b""
    endpoint.read_pending.clear()
    with ThreadPoolExecutor(1) as workers:
        pending = workers.submit(chiller.read_temperature)
        assert endpoint.read_pending.wait(1)
        started = perf_counter()
        chiller.disconnect()
        assert perf_counter() - started < 1
        with pytest.raises(ConnectionError):
            pending.result(timeout=1)
    assert endpoint.open_count == 1 and not endpoint.is_open


def test_close_failure_disables_io_and_retains_handle_for_cleanup():
    chiller, endpoint = make_serial()
    chiller.connect()
    endpoint.close_error = OSError("fake close failure")
    with pytest.raises(OSError, match="close failure"):
        chiller.disconnect()
    assert not chiller.is_connected
    baseline = endpoint.write_count
    with pytest.raises(ConnectionError):
        chiller.read_temperature()
    assert endpoint.write_count == baseline
    endpoint.close_error = None
    chiller.disconnect()
    assert not endpoint.is_open and endpoint.close_count == 2


@pytest.mark.parametrize("exception_type", [ProtocolError, KeyboardInterrupt, SystemExit])
def test_context_preserves_primary_exception_when_close_fails(exception_type):
    chiller, endpoint = make_serial()
    primary = exception_type("Primary operation failed")
    try:
        with pytest.raises(exception_type) as raised:
            with chiller:
                endpoint.close_error = OSError("Endpoint close failed")
                raise primary
        assert raised.value is primary
        assert any("Disconnect cleanup failed" in note for note in primary.__notes__)
        assert chiller._serial is endpoint and (not chiller.is_connected)
    finally:
        endpoint.close_error = None
        chiller.disconnect()
    assert not endpoint.is_open


def test_context_propagates_close_error_without_a_primary_exception():
    chiller, endpoint = make_serial()
    try:
        with pytest.raises(OSError, match="Endpoint close failed"):
            with chiller:
                endpoint.close_error = OSError("Endpoint close failed")
        assert chiller._serial is endpoint and (not chiller.is_connected)
    finally:
        endpoint.close_error = None
        chiller.disconnect()


@pytest.mark.parametrize("stage", ["open", "read", "write", "decode"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interruption_closes_endpoint_and_preserves_exception(stage, interruption, monkeypatch):
    class InterruptedEndpoint(FakeSerial):
        def open(self):
            super().open()
            if stage == "open":
                raise interruption("fake interruption")

        def write(self, request):
            result = super().write(request)
            if stage == "write" and request[1:4] == b"\x06\x16\x00":
                raise interruption("fake interruption")
            return result

    def interrupted_decode(raw):
        raise interruption("fake interruption")

    endpoint = InterruptedEndpoint()
    if stage == "read":
        endpoint.read_error = interruption("fake interruption")
    if stage == "decode":
        monkeypatch.setattr(Cal33xx, "_temperature", staticmethod(interrupted_decode))
    chiller, _ = make_serial(endpoint=endpoint)
    try:
        with pytest.raises(interruption, match="fake interruption"):
            chiller.connect()
            chiller.set_setpoint(18) if stage == "write" else chiller.read_temperature()
        assert not endpoint.is_open and endpoint.open_count == 1
        assert endpoint.applied_writes == (1 if stage == "write" else 0)
    finally:
        chiller.disconnect()


@pytest.mark.parametrize("cleanup_error", [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("primary_error", [ProtocolError, KeyboardInterrupt, SystemExit])
def test_interruption_survives_failed_cleanup_and_handle_can_be_closed_later(
    primary_error, cleanup_error
):
    chiller, endpoint = make_serial()
    chiller.connect()
    endpoint.read_error = primary_error("fake interrupted read")
    endpoint.close_error = cleanup_error("fake close failure")
    with pytest.raises(primary_error, match="interrupted read") as interrupted:
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

    chiller, endpoint = make_serial(endpoint=UnsupportedMode(), rs485=True)
    with pytest.raises(OSError, match="unsupported native mode"):
        chiller.connect()
    assert not endpoint.open_count and not endpoint.write_count and endpoint.close_count == 1


def test_serial_dictionaries_are_copied_on_input_and_output():
    endpoint = FakeSerial()
    settings = fake_settings()
    mode = {
        "rts_level_for_tx": True,
        "rts_level_for_rx": False,
        "loopback": False,
        "delay_before_tx": None,
        "delay_before_rx": None,
    }
    chiller = TarkChiller(
        settings, rs485=mode, protocol=Cal33xx(1), serial_factory=endpoint.factory
    )
    settings["port"] = "CHANGED"
    mode["rts_level_for_tx"] = False
    chiller.settings["baudrate"] = 0
    chiller.rs485["delay_before_tx"] = -1
    with chiller:
        assert endpoint.port == "FAKE-ONLY"
        assert chiller.settings["baudrate"] == 19200
        assert endpoint.rs485_mode.rts_level_for_tx is True
        assert endpoint.rs485_mode.delay_before_tx is None
        chiller.settings.clear()
        chiller.rs485.clear()
        assert chiller.settings["port"] == "FAKE-ONLY"
        assert chiller.rs485["rts_level_for_tx"] is True


@pytest.mark.parametrize(
    "settings",
    [
        None,
        [],
        {},
        {key: value for key, value in fake_settings().items() if key != "rts"},
        {**fake_settings(), "unexpected": True},
    ],
)
def test_serial_dictionary_schema_rejects_before_factory(settings):
    endpoint = FakeSerial()
    with pytest.raises(ValueError):
        TarkChiller(settings, serial_factory=endpoint.factory)
    assert not endpoint.factory_arguments


@pytest.mark.parametrize("mode", [True, [], {}, {"rts_level_for_tx": True}])
def test_rs485_dictionary_schema_rejects_before_factory(mode):
    endpoint = FakeSerial()
    with pytest.raises(ValueError):
        TarkChiller(fake_settings(), rs485=mode, serial_factory=endpoint.factory)
    assert not endpoint.factory_arguments


@pytest.mark.parametrize("model", ["cal", "t255p"])
def test_controller_constructs_and_owns_pyserial_directly(model, monkeypatch):
    import serial

    from tark_chiller.controller import T255P
    from tark_chiller.simulator import CalSimulator, T255PSimulator

    endpoint = CalSimulator(clock=lambda: 0) if model == "cal" else T255PSimulator(clock=lambda: 0)
    settings = endpoint.chiller().settings
    calls = []

    def create_port(**config):
        calls.append(config)
        return endpoint.factory(**config)

    monkeypatch.setattr(serial, "Serial", create_port)
    protocol = Cal33xx(1) if model == "cal" else T255P()
    chiller = TarkChiller(settings, protocol=protocol)
    assert not calls and chiller._serial is None and not endpoint.is_open
    assert not hasattr(chiller, "_backend")
    with chiller:
        assert chiller._serial is endpoint
        assert chiller.read_temperature() == pytest.approx(20)
        assert len(calls) == 1 and calls[0]["port"] is None
        assert endpoint.port == "MEMORY-ONLY"
        assert not endpoint.applied_writes
    assert not endpoint.is_open and chiller._serial is None


def test_legacy_constructor_and_simulator_factories_are_removed():
    from tark_chiller import controller
    from tark_chiller.simulator import CalSimulator, T255PSimulator

    assert not hasattr(controller, "SerialDevice")
    for endpoint in (CalSimulator(), T255PSimulator()):
        assert not hasattr(endpoint, "device")
        chiller = endpoint.chiller()
        with pytest.raises(ValueError, match="Serial settings"):
            TarkChiller(chiller)
        with pytest.raises(TypeError, match="codec"):
            TarkChiller(chiller.settings, codec=Cal33xx(1))
        assert not endpoint.is_open and not endpoint.applied_writes


def test_context_entry_failure_retries_cleanup_without_masking_connection_error(monkeypatch):
    chiller, endpoint = make_serial()
    endpoint.read_error = ProtocolError("Identity read failed")
    close = endpoint.close

    def first_close_fails():
        if endpoint.close_count == 0:
            endpoint.close_count += 1
            raise OSError("First close failed")
        close()

    monkeypatch.setattr(endpoint, "close", first_close_fails)
    with pytest.raises(ProtocolError, match="Identity read failed") as raised:
        with chiller:
            pytest.fail("Failed connection entered context")
    assert "First close failed" in raised.value.__notes__[0]
    assert not endpoint.is_open and chiller._serial is None
