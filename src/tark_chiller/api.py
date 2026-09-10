"""Small serialized public API shared by every device backend."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TypeVar

from .device import ChillerDevice, DeviceStatus
from .errors import (
    ChillerConnectionError,
    ChillerError,
    ProtocolError,
    SetpointValidationError,
    TransportError,
)
from .safety import DISTILLED_WATER, CoolantProfile, _finite_real

_ReadResult = TypeVar("_ReadResult")


@dataclass(frozen=True)
class RecoveryPolicy:
    """Opt-in read/connect budgets, not physical controller timing requirements.

    A reconnect attempt includes closing the old connection, a cancellable delay,
    and opening again. A successful read starts a new outage budget; merely
    opening a port does not. Exhaustion requires an explicit connect().
    """

    max_reconnect_attempts: int = 0
    delay_s: float = 0.05

    def __post_init__(self) -> None:
        if type(self.max_reconnect_attempts) is not int or not (
            0 <= self.max_reconnect_attempts <= 10
        ):
            raise ValueError("max_reconnect_attempts must be an integer from 0 to 10")
        try:
            delay = _finite_real(self.delay_s, "reconnect delay_s")
        except SetpointValidationError as exc:
            raise ValueError(str(exc)) from exc
        if not 0 < delay <= 60:
            raise ValueError("reconnect delay_s must be greater than 0 and no greater than 60")
        object.__setattr__(self, "delay_s", delay)


class Chiller:
    """Own application access to a backend with an immutable coolant policy.

    Read recovery is explicitly configured. Connecting never sets a target;
    disconnecting software does not stop a physical chiller.
    """

    def __init__(
        self,
        device: ChillerDevice,
        coolant: CoolantProfile = DISTILLED_WATER,
        *,
        recovery: RecoveryPolicy = RecoveryPolicy(),
    ) -> None:
        if not isinstance(coolant, CoolantProfile):
            raise SetpointValidationError("coolant must be an explicit CoolantProfile")
        if not isinstance(recovery, RecoveryPolicy):
            raise ValueError("recovery must be an explicit RecoveryPolicy")
        self._device = device
        self._coolant = coolant
        self._lock = threading.RLock()
        self._recovery = recovery
        self._remaining_attempts = recovery.max_reconnect_attempts
        self._recovery_error = ""
        self._recovery_blocked = False
        self._last_status = DeviceStatus(False, "unknown", "not yet polled")
        # Intent has its own short lock so disconnect can cancel a recovery wait
        # even while another thread holds the serialized device-operation lock.
        self._intent_lock = threading.Lock()
        self._cancel = threading.Event()
        self._want_connected = False

    @property
    def coolant(self) -> CoolantProfile:
        return self._coolant

    def connect(self) -> None:
        cancel = self._new_intent(True)
        with self._lock:
            self._check_cancel(cancel)
            self._remaining_attempts = self._recovery.max_reconnect_attempts
            self._recovery_blocked = False
            self._recovery_error = ""
            try:
                self._connect_device()
                self._check_cancel(cancel)
            except ProtocolError as exc:
                self._suspend_recovery(exc)
                raise
            except (ChillerConnectionError, TransportError) as exc:
                self._recover(self._connect_device, exc, cancel, connect_only=True)

    def _connect_device(self) -> None:
        self._device.connect()
        if not self._device.is_connected:
            raise ChillerConnectionError("Backend connect completed without an active connection")

    def disconnect(self) -> None:
        cancel = self._new_intent(False)
        with self._lock:
            # A newer explicit connect supersedes this intent. Polling never
            # creates intent, so it cannot undo an intentional disconnect.
            with self._intent_lock:
                if cancel is not self._cancel:
                    return
            self._recovery_error = ""
            self._recovery_blocked = False
            self._device.disconnect()

    def _new_intent(self, connected: bool) -> threading.Event:
        with self._intent_lock:
            self._cancel.set()
            self._cancel = threading.Event()
            self._want_connected = connected
            if not connected:
                self._cancel.set()
            return self._cancel

    @staticmethod
    def _check_cancel(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise ChillerConnectionError("Connection intent cancelled; call connect() explicitly")

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return (
                not self._cancel.is_set()
                and not self._recovery_blocked
                and self._device.is_connected
            )

    def _require_connected(self) -> None:
        self._check_cancel(self._cancel)
        if self._recovery_blocked:
            raise ChillerConnectionError(self._recovery_error)
        if not self._device.is_connected:
            raise ChillerConnectionError("Chiller is disconnected; call connect() explicitly")

    def _suspend_recovery(self, error: ProtocolError) -> None:
        self._recovery_blocked = True
        self._recovery_error = f"Protocol failure; automatic recovery suspended: {error}"
        try:
            self._device.disconnect()
        except ChillerError as cleanup_error:
            error.add_note(str(cleanup_error))

    def _recover(
        self,
        operation: Callable[[], _ReadResult],
        error: ChillerConnectionError | TransportError,
        cancel: threading.Event,
        *,
        connect_only: bool = False,
    ) -> _ReadResult:
        self._check_cancel(cancel)
        if (
            not self._want_connected
            or self._recovery_blocked
            or self._recovery.max_reconnect_attempts == 0
        ):
            raise error
        last_error: ChillerConnectionError | TransportError = error
        while self._remaining_attempts:
            self._check_cancel(cancel)
            self._remaining_attempts -= 1
            try:
                self._device.disconnect()
                if cancel.wait(self._recovery.delay_s):
                    self._check_cancel(cancel)
                self._check_cancel(cancel)
                if not connect_only:
                    self._connect_device()
                    self._check_cancel(cancel)
                result = operation()
                self._check_cancel(cancel)
                if not connect_only:
                    self._remaining_attempts = self._recovery.max_reconnect_attempts
                self._recovery_error = ""
                return result
            except ProtocolError as exc:
                self._suspend_recovery(exc)
                raise
            except (ChillerConnectionError, TransportError) as exc:
                self._check_cancel(cancel)
                last_error = exc
        self._recovery_blocked = True
        self._recovery_error = (
            f"Recovery exhausted after {self._recovery.max_reconnect_attempts} reconnect "
            f"attempt(s): {type(last_error).__name__}: {last_error}; call connect() explicitly"
        )
        try:
            self._device.disconnect()
        except ChillerError as cleanup_error:
            last_error.add_note(str(cleanup_error))
        raise ChillerConnectionError(self._recovery_error) from last_error

    def _read(self, operation: Callable[[], _ReadResult]) -> _ReadResult:
        # Caller holds the operation lock. Preserve the cancellation token even
        # if a competing explicit connect installs a new connection intent.
        cancel = self._cancel
        try:
            self._require_connected()
            result = operation()
            self._check_cancel(cancel)
        except ProtocolError as exc:
            self._suspend_recovery(exc)
            raise
        except (ChillerConnectionError, TransportError) as exc:
            return self._recover(operation, exc, cancel)
        self._remaining_attempts = self._recovery.max_reconnect_attempts
        self._recovery_error = ""
        return result

    @staticmethod
    def _reading(value: object, label: str) -> float:
        try:
            return _finite_real(value, label)
        except SetpointValidationError as exc:
            raise ProtocolError(f"Backend returned invalid {label}") from exc

    def get_temperature(self) -> float:
        with self._lock:
            return self._read(
                lambda: self._reading(self._device.get_temperature(), "temperature (°C)")
            )

    def get_setpoint(self) -> float:
        with self._lock:
            return self._read(lambda: self._reading(self._device.get_setpoint(), "setpoint (°C)"))

    def set_setpoint(self, value_c: object) -> None:
        # Validate before accessing even the backend's connection property.
        validated = self._coolant.validate(value_c)
        with self._intent_lock:
            cancel = self._cancel
        with self._lock:
            # Do not carry a queued write into a newer connection session.
            self._check_cancel(cancel)
            self._require_connected()
            try:
                self._device.set_setpoint(validated)
            except ProtocolError as exc:
                self._suspend_recovery(exc)
                raise

    def get_status(self) -> DeviceStatus:
        """Connection status remains available while disconnected."""
        with self._lock:
            if self._cancel.is_set() or self._recovery_blocked:
                detail = (
                    "intentionally disconnected" if self._cancel.is_set() else self._recovery_error
                )
                return replace(self._last_status, connected=False, detail=detail)
            if self._device.is_connected or (
                self._want_connected and self._recovery.max_reconnect_attempts > 0
            ):
                return self._read(self._status_read)
            return self._status_read()

    def _status_read(self) -> DeviceStatus:
        status = self._device.get_status()
        if not isinstance(status, DeviceStatus):
            raise ProtocolError("Backend did not return DeviceStatus")
        if (
            type(status.connected) is not bool
            or not isinstance(status.backend, str)
            or not status.backend.strip()
            or not isinstance(status.detail, str)
        ):
            raise ProtocolError("Backend returned invalid DeviceStatus fields")
        self._last_status = status
        return status
