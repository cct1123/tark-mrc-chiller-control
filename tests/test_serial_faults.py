"""Adversarial production-stack tests; every serial factory is memory-only."""

import errno
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import perf_counter, sleep

import pytest

from tark_chiller import Chiller, RecoveryPolicy
from tark_chiller.errors import (
    ChillerConnectionError,
    ChillerTimeoutError,
    ProtocolError,
    TransportError,
)
from tark_chiller.gui import render_snapshot
from tark_chiller.hardware import SerialDevice
from tark_chiller.monitoring import LiveState, Monitor
from tark_chiller.testing import FakeProtocol, FakeSerialEndpoint, make_fake_device
from tark_chiller.transport import RS232Transport, RS485Mode, RS485Transport, SerialSettings


def device_for(endpoint, interface, timeout=0.1):
    # Arbitrary fixture settings; endpoint.serial_factory cannot open OS ports.
    settings = SerialSettings("MEMORY-ONLY", 9600, 8, "N", 1, False, False, False, False, False)
    options = dict(transaction_timeout_s=timeout, serial_factory=endpoint.serial_factory)
    if interface == "rs232":
        transport = RS232Transport(settings, **options)
    else:
        transport = RS485Transport(
            settings,
            RS485Mode(True, False, False, None, None),
            rs485_settings_factory=dict,
            **options,
        )
    return SerialDevice(transport, FakeProtocol())


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
@pytest.mark.parametrize("attempts", [0, 2])
@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(errno.ENOENT, "synthetic port missing"),
        PermissionError(errno.EACCES, "synthetic port permission denied"),
        OSError(errno.EBUSY, "synthetic port already in use"),
    ],
)
def test_port_open_failures_preserve_reason_and_never_transmit(interface, attempts, error):
    class UnavailableEndpoint(FakeSerialEndpoint):
        def open(self):
            self.open_count += 1
            raise error

    endpoint = UnavailableEndpoint()
    device = device_for(endpoint, interface)
    chiller = Chiller(device, recovery=RecoveryPolicy(attempts, 0.001))
    with pytest.raises(ChillerConnectionError if attempts else TransportError) as raised:
        chiller.connect()
    assert str(error) in str(raised.value)
    assert str(error) in chiller.get_status().detail
    assert endpoint.open_count == 1 + attempts
    assert endpoint.write_count == 0
    assert not endpoint.is_open
    for _ in range(3):
        with pytest.raises(ChillerConnectionError):
            chiller.get_temperature()
    state = LiveState()
    Monitor(chiller, state).poll_once()
    text, figure = render_snapshot(state.snapshot())
    assert str(error) in text
    assert "Unavailable" in text
    assert figure.data[0].y == (None,)
    assert endpoint.open_count == 1 + attempts
    assert endpoint.write_count == 0
    chiller.disconnect()


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
def test_mid_response_disconnect_is_typed_and_never_returns_partial_value(interface):
    class UnpluggedEndpoint(FakeSerialEndpoint):
        def read(self, size=1):
            if self.read_count == 10:
                self.is_open = False
                raise OSError("synthetic device unplugged during reply")
            return super().read(size)

    endpoint = UnpluggedEndpoint()
    device = device_for(endpoint, interface)
    chiller = Chiller(device)
    chiller.connect()
    with pytest.raises(TransportError, match="unplugged during reply"):
        chiller.get_temperature()
    assert not chiller.is_connected
    assert "unplugged during reply" in chiller.get_status().detail
    assert endpoint.write_count == 1
    assert endpoint.read_count == 10
    assert endpoint.applied_setpoints == 0
    chiller.disconnect()


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
def test_delayed_complete_reply_within_budget_is_accepted(interface):
    class DelayedEndpoint(FakeSerialEndpoint):
        def read(self, size=1):
            if self.read_count == 0:
                sleep(0.01)
            return super().read(size)

    endpoint = DelayedEndpoint()
    device = device_for(endpoint, interface, timeout=0.2)
    device.connect()
    try:
        assert device.get_temperature() == 20.0
        assert endpoint.write_count == 1
    finally:
        device.disconnect()


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
def test_reply_delayed_past_reconnect_is_rejected_by_identity(interface):
    class LateOldReplyEndpoint(FakeSerialEndpoint):
        old_reply = b""

        def write(self, request):
            result = super().write(request)
            if self.write_count == 1:
                # Model the controller/adapter still holding the first reply,
                # even after the host closes its local port following a timeout.
                self.old_reply = bytes(self._response)
                self._response.clear()
            else:
                self._response[:0] = self.old_reply
            return result

    endpoint = LateOldReplyEndpoint()
    device = device_for(endpoint, interface, timeout=0.05)
    chiller = Chiller(device, recovery=RecoveryPolicy(1, 0.001))
    chiller.connect()
    with pytest.raises(ProtocolError, match="outstanding request"):
        chiller.get_temperature()
    assert endpoint.open_count == 2
    assert endpoint.write_count == 2
    assert endpoint.applied_setpoints == 0
    assert not chiller.is_connected
    assert "suspended" in chiller.get_status().detail
    with pytest.raises(ChillerConnectionError, match="suspended"):
        chiller.get_temperature()
    assert endpoint.write_count == 2
    chiller.disconnect()


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
def test_disconnect_during_pending_read_waits_for_budget_without_reconnecting(interface):
    pending = Event()

    class PendingEndpoint(FakeSerialEndpoint):
        def read(self, size=1):
            pending.set()
            sleep(self.timeout)
            return b""

    endpoint = PendingEndpoint()
    device = device_for(endpoint, interface, timeout=0.08)
    chiller = Chiller(device, recovery=RecoveryPolicy(3, 5))
    chiller.connect()
    with ThreadPoolExecutor(max_workers=1) as pool:
        read = pool.submit(chiller.get_temperature)
        assert pending.wait(1)
        started = perf_counter()
        chiller.disconnect()
        assert perf_counter() - started < 1
        with pytest.raises(ChillerConnectionError, match="cancelled"):
            read.result(timeout=1)
    assert not endpoint.is_open
    assert endpoint.open_count == 1
    assert endpoint.write_count == 1
    assert chiller.get_status().detail == "intentionally disconnected"


def test_short_real_clock_deadline_never_expires_before_its_budget():
    # Windows Python 3.12's coarse monotonic clock previously expired a 10 ms
    # deadline after only tens of microseconds when GetTickCount64 advanced.
    device, endpoint = make_fake_device(transaction_timeout_s=0.01)
    device.connect()
    try:
        for _ in range(5000):
            started = perf_counter()
            try:
                assert device.get_temperature() == 20.0
            except ChillerTimeoutError:
                # Scheduler delays may legitimately exhaust a short budget.
                assert perf_counter() - started >= 0.01
                device.connect()
        assert endpoint.applied_setpoints == 0
    finally:
        device.disconnect()
