"""TEST-014: bounded, cancellable recovery using software-only devices."""

import math
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Event, current_thread
from time import monotonic

import pytest

from development.testing import make_fake_device
from tark_chiller import Chiller, DeviceStatus
from tark_chiller.api import RecoveryPolicy
from tark_chiller.errors import (
    ChillerConnectionError,
    ChillerTimeoutError,
    ProtocolError,
    ProtocolUnavailableError,
    SetpointValidationError,
    TransportError,
)


class FaultDevice:
    """No ports, wire commands, physical behavior, or third-party dependencies."""

    def __init__(self):
        self.connected = False
        self.calls = defaultdict(int)
        self.faults = defaultdict(deque)
        self.target = 20.0
        self.temperature = 20.0
        self.closed = Event()
        self.actual_closes = 0

    def _step(self, operation):
        self.calls[operation] += 1
        if self.faults[operation]:
            error = self.faults[operation].popleft()
            if error is not None:
                if isinstance(error, (TransportError, ChillerConnectionError)):
                    self.connected = False
                raise error

    def connect(self):
        self._step("connect")
        self.connected = True

    def disconnect(self):
        self._step("disconnect")
        self.actual_closes += int(self.connected)
        self.connected = False
        self.closed.set()

    @property
    def is_connected(self):
        return self.connected

    def _require_connected(self):
        if not self.connected:
            raise ChillerConnectionError("synthetic device disconnected")

    def get_temperature(self):
        self._require_connected()
        self._step("get_temperature")
        return self.temperature

    def get_setpoint(self):
        self._require_connected()
        self._step("get_setpoint")
        return self.target

    def set_setpoint(self, value_c):
        self._require_connected()
        # Deliberately apply before an injected fault: unknown write outcome.
        self.target = value_c
        self._step("set_setpoint")

    def get_status(self):
        self._step("get_status")
        return DeviceStatus(self.connected, "synthetic", "software fixture")


def recovering(device=None, attempts=2, delay=0.001):
    device = device or FaultDevice()
    return Chiller(device, recovery=RecoveryPolicy(attempts, delay)), device


@pytest.mark.parametrize("attempts", [-1, 11, True, 1.5, None, "2", 10**400])
def test_attempt_budget_is_a_small_explicit_integer(attempts):
    with pytest.raises(ValueError, match="integer from 0 to 10"):
        RecoveryPolicy(attempts)


@pytest.mark.parametrize("delay", [0, -0.1, True, None, "1", math.nan, math.inf, 61, 10**400])
def test_recovery_delay_is_finite_positive_and_bounded(delay):
    with pytest.raises(ValueError):
        RecoveryPolicy(delay_s=delay)


def test_recovery_policy_is_immutable_and_explicit():
    policy = RecoveryPolicy()
    assert policy.max_reconnect_attempts == 0
    with pytest.raises(FrozenInstanceError):
        policy.max_reconnect_attempts = 10
    with pytest.raises(ValueError, match="RecoveryPolicy"):
        Chiller(FaultDevice(), recovery=None)


def test_no_automatic_initial_connection_or_default_retry():
    chiller, device = recovering()
    with pytest.raises(ChillerConnectionError):
        chiller.get_temperature()
    assert device.calls["connect"] == 0
    conservative = Chiller(device)
    conservative.connect()
    device.faults["get_temperature"].append(ChillerTimeoutError("one timeout"))
    with pytest.raises(ChillerTimeoutError, match="one timeout"):
        conservative.get_temperature()
    assert device.calls["connect"] == 1
    assert not conservative.get_status().connected


@pytest.mark.parametrize(
    "error_type", [TransportError, ChillerConnectionError, ChillerTimeoutError]
)
def test_transient_initial_connect_failure_recovers_without_write(error_type):
    chiller, device = recovering()
    device.faults["connect"].append(error_type("synthetic transient open"))
    chiller.connect()
    assert chiller.is_connected
    assert device.calls["connect"] == 2
    assert device.calls["set_setpoint"] == 0


@pytest.mark.parametrize("operation", ["get_temperature", "get_setpoint", "get_status"])
def test_transient_read_failure_reconnects_and_reissues_only_read(operation):
    chiller, device = recovering()
    chiller.connect()
    device.faults[operation].append(ChillerTimeoutError("synthetic timeout"))
    result = getattr(chiller, operation)()
    assert result == (
        DeviceStatus(True, "synthetic", "software fixture") if operation == "get_status" else 20.0
    )
    assert device.calls["connect"] == 2
    assert device.calls[operation] == 2
    assert device.calls["set_setpoint"] == 0


def test_successful_read_resets_budget_for_a_later_outage():
    chiller, device = recovering(attempts=1)
    chiller.connect()
    for _ in range(3):
        device.faults["get_temperature"].append(TransportError("isolated outage"))
        assert chiller.get_temperature() == 20.0
    assert device.calls["connect"] == 4


def test_repeated_open_success_does_not_reset_failed_read_budget():
    chiller, device = recovering(attempts=2)
    chiller.connect()
    device.faults["get_temperature"].extend(TransportError("offline") for _ in range(20))
    with pytest.raises(ChillerConnectionError, match="exhausted after 2"):
        chiller.get_temperature()
    assert device.calls["connect"] == 3
    assert device.calls["get_temperature"] == 3
    calls = dict(device.calls)
    for _ in range(5):
        assert not chiller.get_status().connected
        assert "exhausted" in chiller.get_status().detail
        with pytest.raises(ChillerConnectionError, match="exhausted"):
            chiller.get_temperature()
        with pytest.raises(ChillerConnectionError, match="exhausted"):
            chiller.get_setpoint()
    assert dict(device.calls) == calls
    assert not chiller.is_connected
    device.faults["get_temperature"].clear()
    chiller.connect()
    assert chiller.get_temperature() == 20.0
    assert chiller.is_connected


def test_failed_initial_connection_exhaustion_persists_across_polls():
    chiller, device = recovering(attempts=2)
    device.faults["connect"].extend(TransportError("cannot open") for _ in range(20))
    with pytest.raises(ChillerConnectionError, match="exhausted"):
        chiller.connect()
    for _ in range(4):
        with pytest.raises(ChillerConnectionError, match="exhausted"):
            chiller.get_temperature()
        assert "cannot open" in chiller.get_status().detail
    assert device.calls["connect"] == 3
    assert device.calls["get_temperature"] == 0


def test_initial_reconnect_budget_survives_until_a_successful_read():
    chiller, device = recovering(attempts=2)
    device.faults["connect"].append(TransportError("initial open failed"))
    chiller.connect()  # One reconnect used; port ownership does not establish a read.
    device.faults["get_temperature"].extend(TransportError("unreadable") for _ in range(3))
    with pytest.raises(ChillerConnectionError, match="exhausted"):
        chiller.get_temperature()
    assert device.calls["connect"] == 3
    assert device.calls["get_temperature"] == 2


@pytest.mark.parametrize("stage", ["disconnect", "connect", "get_temperature"])
def test_each_failed_reconnect_stage_consumes_the_same_finite_budget(stage):
    chiller, device = recovering(attempts=2)
    chiller.connect()
    device.faults["get_temperature"].append(TransportError("initial outage"))
    device.faults[stage].extend(TransportError(f"failed {stage}") for _ in range(2))
    with pytest.raises(ChillerConnectionError, match="exhausted after 2"):
        chiller.get_temperature()
    assert device.calls["disconnect"] == 3  # Two attempts, then final cleanup.
    assert device.calls["connect"] == (1 if stage == "disconnect" else 3)
    assert device.calls["get_temperature"] == (3 if stage == "get_temperature" else 1)
    assert device.calls["set_setpoint"] == 0
    calls = dict(device.calls)
    for _ in range(3):
        with pytest.raises(ChillerConnectionError, match="exhausted"):
            chiller.get_temperature()
    assert dict(device.calls) == calls


@pytest.mark.parametrize("stage", ["connect", "get_temperature"])
def test_protocol_failure_during_reconnect_suspends_remaining_attempts(stage):
    chiller, device = recovering(attempts=2)
    chiller.connect()
    device.faults["get_temperature"].append(TransportError("initial outage"))
    device.faults[stage].append(ProtocolError("unknown protocol response"))
    with pytest.raises(ProtocolError, match="unknown protocol"):
        chiller.get_temperature()
    calls = dict(device.calls)
    with pytest.raises(ChillerConnectionError, match="suspended"):
        chiller.get_temperature()
    assert dict(device.calls) == calls
    assert device.calls["connect"] == 2
    assert device.calls["set_setpoint"] == 0


@pytest.mark.parametrize("error_type", [ProtocolError, ProtocolUnavailableError])
def test_protocol_errors_never_trigger_recovery_now_or_on_later_polls(error_type):
    chiller, device = recovering()
    chiller.connect()
    device.faults["get_temperature"].append(error_type("synthetic malformed/unknown format"))
    with pytest.raises(error_type):
        chiller.get_temperature()
    calls = dict(device.calls)
    with pytest.raises(ChillerConnectionError, match="suspended"):
        chiller.get_temperature()
    assert "suspended" in chiller.get_status().detail
    assert dict(device.calls) == calls
    assert device.calls["connect"] == 1


def test_malformed_numeric_read_suspends_without_repeating_it():
    chiller, device = recovering()
    chiller.connect()
    device.temperature = math.nan
    with pytest.raises(ProtocolError, match="invalid temperature"):
        chiller.get_temperature()
    assert device.calls["get_temperature"] == 1
    assert device.calls["connect"] == 1
    assert not chiller.is_connected


def test_missing_protocol_on_connect_is_not_retried():
    chiller, device = recovering()
    device.faults["connect"].append(ProtocolUnavailableError("manual missing"))
    with pytest.raises(ProtocolUnavailableError, match="manual missing"):
        chiller.connect()
    assert device.calls["connect"] == 1
    with pytest.raises(ChillerConnectionError, match="suspended"):
        chiller.get_temperature()


def test_unexpected_exception_is_not_hidden_or_retried():
    chiller, device = recovering()
    chiller.connect()
    device.faults["get_temperature"].append(RuntimeError("implementation defect"))
    with pytest.raises(RuntimeError, match="implementation defect"):
        chiller.get_temperature()
    assert device.calls["connect"] == 1
    assert device.calls["get_temperature"] == 1


def test_uncertain_write_is_not_replayed_when_a_later_read_recovers():
    chiller, device = recovering()
    chiller.connect()
    device.faults["set_setpoint"].append(ChillerTimeoutError("acknowledgement lost"))
    with pytest.raises(ChillerTimeoutError, match="acknowledgement lost"):
        chiller.set_setpoint(23)
    assert device.calls["set_setpoint"] == 1
    assert device.calls["connect"] == 1
    assert chiller.get_setpoint() == 23.0
    assert device.calls["connect"] == 2
    assert device.calls["set_setpoint"] == 1


def test_invalid_write_does_not_access_device_even_with_recovery_enabled():
    chiller, device = recovering()
    with pytest.raises(SetpointValidationError):
        chiller.set_setpoint(-2)
    assert dict(device.calls) == {}


def test_disconnect_cancels_pending_delay_and_polling_cannot_reverse_it():
    chiller, device = recovering(delay=5)
    chiller.connect()
    device.faults["get_temperature"].append(TransportError("outage"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(chiller.get_temperature)
        assert device.closed.wait(1), "read should enter reconnect delay"
        started = monotonic()
        chiller.disconnect()
        assert monotonic() - started < 1, "disconnect must interrupt the five-second delay"
        with pytest.raises(ChillerConnectionError, match="cancelled"):
            pending.result(timeout=1)
    calls = dict(device.calls)
    for _ in range(5):
        with pytest.raises(ChillerConnectionError, match="cancelled"):
            chiller.get_temperature()
        assert chiller.get_status().detail == "intentionally disconnected"
        assert not chiller.is_connected
    assert dict(device.calls) == calls
    assert device.calls["connect"] == 1
    chiller.connect()
    assert chiller.get_temperature() == 20


def test_disconnect_is_idempotent_after_success_and_exhaustion():
    chiller, device = recovering(attempts=1)
    chiller.connect()
    chiller.disconnect()
    chiller.disconnect()
    assert device.actual_closes == 1
    chiller.connect()
    device.faults["get_temperature"].extend(TransportError("offline") for _ in range(2))
    with pytest.raises(ChillerConnectionError, match="exhausted"):
        chiller.get_temperature()
    chiller.disconnect()
    chiller.disconnect()
    assert not chiller.is_connected


def test_concurrent_readers_share_one_recovery_and_no_duplicate_writes():
    chiller, device = recovering()
    chiller.connect()
    device.faults["get_temperature"].append(ChillerTimeoutError("one shared outage"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda _: chiller.get_temperature(), range(40)))
    assert values == [20.0] * 40
    assert device.calls["connect"] == 2
    assert device.calls["get_temperature"] == 41
    assert device.calls["set_setpoint"] == 0


def test_backend_connect_without_a_connection_does_not_reset_outage_budget():
    class SilentOpenFailure(FaultDevice):
        def connect(self):
            self._step("connect")

    chiller, device = recovering(SilentOpenFailure(), attempts=2)
    with pytest.raises(ChillerConnectionError, match="without an active connection"):
        chiller.connect()
    assert device.calls["connect"] == 3
    assert "exhausted" in chiller.get_status().detail


@pytest.mark.parametrize("interface", ["rs232", "rs485"])
def test_recovery_across_real_serial_shell_with_memory_endpoint_only(interface):
    device, endpoint = make_fake_device(interface=interface)
    chiller, _ = recovering(device)
    endpoint.inject_fault("connect", "disconnect")
    chiller.connect()
    assert endpoint.open_count == 2
    endpoint.inject_fault("get_temperature", "timeout")
    assert chiller.get_temperature() == pytest.approx(20)
    assert endpoint.open_count == 3
    assert endpoint.operation_counts["get_temperature"] == 2

    endpoint.inject_fault("set_setpoint", "ack_lost")
    with pytest.raises(ChillerTimeoutError):
        chiller.set_setpoint(24)
    assert endpoint.open_count == 3  # Writes have no reconnect path.
    assert chiller.get_setpoint() == 24
    assert endpoint.open_count == 4
    assert endpoint.operation_counts["set_setpoint"] == 1
    assert endpoint.applied_setpoints == 1

    endpoint.inject_fault("get_temperature", "malformed")
    with pytest.raises(ProtocolError):
        chiller.get_temperature()
    with pytest.raises(ChillerConnectionError, match="suspended"):
        chiller.get_temperature()
    assert endpoint.open_count == 4
    assert endpoint.operation_counts["get_temperature"] == 3
    chiller.disconnect()


def test_real_serial_shell_exhaustion_blocks_later_wire_requests():
    device, endpoint = make_fake_device()
    chiller, _ = recovering(device, attempts=2)
    chiller.connect()
    endpoint.inject_fault("get_temperature", "disconnect", count=20)
    with pytest.raises(ChillerConnectionError, match="exhausted"):
        chiller.get_temperature()
    counts = endpoint.operation_counts
    for _ in range(4):
        assert "exhausted" in chiller.get_status().detail
        with pytest.raises(ChillerConnectionError, match="exhausted"):
            chiller.get_setpoint()
    assert endpoint.operation_counts == counts
    assert endpoint.open_count == 3
    assert counts["get_temperature"] == 3
    chiller.disconnect()


def test_queued_setpoint_cannot_cross_disconnect_and_reconnect(monkeypatch):
    read_started, release_read = Event(), Event()
    write_queued, disconnected, reconnected = Event(), Event(), Event()

    class BlockingDevice(FaultDevice):
        def get_temperature(self):
            read_started.set()
            assert release_read.wait(2), "Test did not release the pending read"
            return super().get_temperature()

    class ObservedChiller(Chiller):
        def _new_intent(self, connected):
            token = super()._new_intent(connected)
            if connected and disconnected.is_set():
                reconnected.set()
            elif not connected:
                disconnected.set()
            return token

    device = BlockingDevice()
    chiller = ObservedChiller(device)
    chiller.connect()
    operation_lock = chiller._lock

    class ObservedLock:
        def __enter__(self):
            if current_thread().name.startswith("queued-setpoint"):
                write_queued.set()
            return operation_lock.__enter__()

        def __exit__(self, *args):
            return operation_lock.__exit__(*args)

    monkeypatch.setattr(chiller, "_lock", ObservedLock())
    with (
        ThreadPoolExecutor(max_workers=3) as lifecycle,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="queued-setpoint") as writer,
    ):
        pending_read = lifecycle.submit(chiller.get_temperature)
        try:
            assert read_started.wait(1)
            pending_write = writer.submit(chiller.set_setpoint, 10)
            assert write_queued.wait(1)
            pending_disconnect = lifecycle.submit(chiller.disconnect)
            assert disconnected.wait(1)
            pending_connect = lifecycle.submit(chiller.connect)
            assert reconnected.wait(1)
        finally:
            release_read.set()
        with pytest.raises(ChillerConnectionError, match="cancelled"):
            pending_read.result(timeout=2)
        with pytest.raises(ChillerConnectionError, match="cancelled"):
            pending_write.result(timeout=2)
        pending_disconnect.result(timeout=2)
        pending_connect.result(timeout=2)
    assert device.calls["set_setpoint"] == 0
    assert chiller.get_setpoint() == 20
    chiller.set_setpoint(10)  # A new request in the new connection intent is valid.
    assert device.calls["set_setpoint"] == 1
    chiller.disconnect()
