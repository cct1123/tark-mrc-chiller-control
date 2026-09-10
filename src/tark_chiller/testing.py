"""SYNTHETIC TEST-ONLY protocol and in-memory serial fixtures.

Nothing in this module describes Tark hardware. The JSON envelopes, version tag,
numeric settings and terminator are an arbitrary software test language. Never
use FakeProtocol with a physical serial port. make_fake_device always injects
an in-memory factory and cannot construct an OS/pySerial port.
"""

import json
import math
import threading
import time
from collections import Counter, deque
from typing import Any, Literal

from .device import DeviceStatus
from .errors import ChillerTimeoutError, ProtocolError, SetpointValidationError
from .hardware import SerialDevice
from .protocol import Operation
from .safety import _finite_real
from .simulator import SimulatedDevice, SimulatedOperation
from .transport import RS232Transport, RS485Mode, RS485Transport, SerialSettings

_TAG = "synthetic-test-only-v1"
_OPERATIONS = ("get_temperature", "get_setpoint", "set_setpoint", "get_status")
FakeFault = Literal[
    "timeout",
    "disconnect",
    "malformed",
    "wrong_id",
    "wrong_operation",
    "invalid_number",
    "ack_lost",
    "truncated",
    "oversized",
]
_FAULTS = (
    "timeout",
    "disconnect",
    "malformed",
    "wrong_id",
    "wrong_operation",
    "invalid_number",
    "ack_lost",
    "truncated",
    "oversized",
)


def _envelope(fields: dict[str, object]) -> bytes:
    return json.dumps(fields, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"


def _parse(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Malformed synthetic test envelope") from exc
    if not isinstance(parsed, dict) or parsed.get("test_protocol") != _TAG:
        raise ProtocolError("Invalid synthetic test envelope identity")
    if type(parsed.get("id")) is not int or parsed["id"] <= 0:
        raise ProtocolError("Invalid synthetic request ID")
    if parsed.get("operation") not in _OPERATIONS:
        raise ProtocolError("Invalid synthetic operation")
    return parsed


class FakeProtocol:
    """Explicitly synthetic JSON codec for exercising the real SerialDevice path.

    One codec belongs to one serialized device. Response identity, operation,
    value type and finite Celsius values are checked before returning results.
    """

    def __init__(self) -> None:
        self._request_id = 0
        self._pending: tuple[int, Operation] | None = None

    def ensure_available(self) -> None:
        pass

    def encode(self, operation: Operation, value: float | None = None) -> bytes:
        if operation not in _OPERATIONS:
            raise ProtocolError("Unknown synthetic operation")
        if operation == "set_setpoint":
            try:
                value = _finite_real(value, "Synthetic setpoint")
            except SetpointValidationError as exc:
                raise ProtocolError("Invalid synthetic setpoint") from exc
        elif value is not None:
            raise ProtocolError("Synthetic read operations accept no value")
        self._request_id += 1
        self._pending = (self._request_id, operation)
        return _envelope(
            {"test_protocol": _TAG, "id": self._request_id, "operation": operation, "value": value}
        )

    def is_complete(self, buffer: bytes) -> bool:
        return buffer.endswith(b"\n")

    def decode(self, operation: Operation, response: bytes) -> float | DeviceStatus | None:
        pending, self._pending = self._pending, None
        fields = _parse(response)
        if pending is None or (fields["id"], fields["operation"]) != pending:
            raise ProtocolError("Synthetic response does not match the outstanding request")
        if operation != fields["operation"] or set(fields) != {
            "test_protocol",
            "id",
            "operation",
            "result",
        }:
            raise ProtocolError("Invalid synthetic response fields or operation")
        result = fields["result"]
        if operation in ("get_temperature", "get_setpoint"):
            try:
                return _finite_real(result, "Synthetic Celsius response")
            except SetpointValidationError as exc:
                raise ProtocolError("Invalid synthetic Celsius response") from exc
        if operation == "set_setpoint":
            if result is not None:
                raise ProtocolError("Invalid synthetic write acknowledgement")
            return None
        if (
            not isinstance(result, dict)
            or set(result) != {"connected", "detail"}
            or result["connected"] is not True
            or not isinstance(result["detail"], str)
        ):
            raise ProtocolError("Invalid synthetic status response")
        return DeviceStatus(True, "hardware", result["detail"])


class FakeSerialEndpoint:
    """Serial-compatible memory endpoint with finite fault scripts and bounded trace.

    Counts include attempted wire operations; applied_setpoints counts successful
    target changes, including ack_lost (an applied write whose reply is lost).
    timeout/disconnect occur before dispatch. Faults are consumed by matching
    operations only. Closing and reopening clears unread bytes, never the target.
    """

    def __init__(self, simulator: SimulatedDevice | None = None, *, trace_limit: int = 128) -> None:
        if type(trace_limit) is not int or trace_limit <= 0:
            raise ValueError("trace_limit must be a positive integer")
        self.simulator = simulator if simulator is not None else SimulatedDevice()
        self.port: str | None = None
        self.is_open = False
        self.timeout = 0.05
        self.write_timeout = 0.05
        self.rts = False
        self.dtr = False
        self.rs485_mode: dict[str, object] | None = None
        self.open_count = 0
        self.close_count = 0
        self.factory_count = 0
        self.write_count = 0
        self.read_count = 0
        self.applied_setpoints = 0
        self._counts: Counter[str] = Counter()
        self._trace: deque[str] = deque(maxlen=trace_limit)
        self._faults: dict[str, deque[tuple[FakeFault, int]]] = {}
        self._response = bytearray()
        self._lock = threading.RLock()

    @property
    def operation_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    @property
    def trace(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._trace)

    def inject_fault(
        self, operation: SimulatedOperation, fault: FakeFault, *, count: int = 1
    ) -> None:
        if operation not in (*_OPERATIONS, "connect") or fault not in _FAULTS:
            raise ValueError("Unknown synthetic operation or fault")
        if operation == "connect" and fault not in ("timeout", "disconnect"):
            raise ValueError("Connect supports timeout/disconnect faults only")
        if fault == "ack_lost" and operation != "set_setpoint":
            raise ValueError("ack_lost applies only to set_setpoint")
        if type(count) is not int or count <= 0:
            raise ValueError("Fault count must be a positive integer")
        with self._lock:
            self._faults.setdefault(operation, deque()).append((fault, count))

    def _next_fault(self, operation: str) -> FakeFault | None:
        self._counts[operation] += 1
        self._trace.append(operation)
        pending = self._faults.get(operation)
        if not pending:
            return None
        fault, count = pending.popleft()
        if count > 1:
            pending.appendleft((fault, count - 1))
        return fault

    def serial_factory(self, **settings: Any) -> "FakeSerialEndpoint":
        """Injected factory only; deliberately has no OS serial fallback."""
        with self._lock:
            if settings.get("port") is not None or self.is_open:
                raise ValueError("Synthetic factory requires an unopened endpoint")
            self.factory_count += 1
            self.timeout = float(settings["timeout"])
            self.write_timeout = float(settings["write_timeout"])
            self.port = None
            return self

    def open(self) -> None:
        with self._lock:
            self.open_count += 1
            fault = self._next_fault("connect")
            if fault == "timeout":
                raise ChillerTimeoutError("Synthetic serial open timeout")
            if fault == "disconnect":
                raise OSError("Synthetic serial unavailable")
            self.simulator.connect()
            self.is_open = True

    def close(self) -> None:
        with self._lock:
            self.close_count += 1
            self.is_open = False
            self._response.clear()
            self.simulator.disconnect()

    def _dispatch(self, operation: str, value: object) -> object:
        if operation == "get_temperature":
            return self.simulator.get_temperature()
        if operation == "get_setpoint":
            return self.simulator.get_setpoint()
        if operation == "get_status":
            status = self.simulator.get_status()
            return {
                "connected": status.connected,
                "detail": "SYNTHETIC TEST ONLY; " + status.detail,
            }
        validated = self.simulator.coolant.validate(value)
        self.simulator.set_setpoint(validated)
        self.applied_setpoints += 1
        return None

    def write(self, request: bytes) -> int:
        with self._lock:
            if not self.is_open:
                raise OSError("Synthetic serial closed")
            self.write_count += 1
            fields = _parse(request)
            if set(fields) != {"test_protocol", "id", "operation", "value"}:
                raise ProtocolError("Invalid synthetic request fields")
            operation = fields["operation"]
            fault = self._next_fault(operation)
            self._response.clear()
            if fault == "disconnect":
                self.close()
                raise OSError("Synthetic connection lost")
            if fault == "timeout":
                return len(request)
            result = self._dispatch(operation, fields["value"])
            if fault == "ack_lost":
                return len(request)
            response: dict[str, object] = {
                "test_protocol": _TAG,
                "id": fields["id"],
                "operation": operation,
                "result": result,
            }
            if fault == "wrong_id":
                response["id"] = fields["id"] + 1
            elif fault == "wrong_operation":
                response["operation"] = (
                    "get_setpoint" if operation != "get_setpoint" else "get_temperature"
                )
            elif fault == "invalid_number":
                response["result"] = "not-a-number"
            encoded = _envelope(response)
            if fault == "malformed":
                encoded = b"{invalid synthetic JSON}\n"
            elif fault == "truncated":
                encoded = encoded[:-1]
            elif fault == "oversized":
                encoded = b"x" * 4097
            self._response.extend(encoded)
            return len(request)

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            self.read_count += 1
            if not self.is_open:
                raise OSError("Synthetic serial closed")
            if self._response:
                result = bytes(self._response[:size])
                del self._response[:size]
                return result
            delay = self.timeout
        # Honor the transport's finite remaining budget without a busy loop.
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Synthetic serial requires finite nonnegative timeout")
        time.sleep(delay)
        return b""


def make_fake_device(
    *,
    simulator: SimulatedDevice | None = None,
    interface: Literal["rs232", "rs485"] = "rs232",
    transaction_timeout_s: float = 0.05,
) -> tuple[SerialDevice, FakeSerialEndpoint]:
    """Build SerialDevice → FakeProtocol → real transport → memory endpoint.

    Settings are arbitrary fixture inputs, never MRC hardware defaults. Both
    serial construction and RS485 settings construction are injected locally.
    """
    endpoint = FakeSerialEndpoint(simulator)
    settings = SerialSettings(
        port="SYNTHETIC-MEMORY-ONLY",
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        rts=False,
        dtr=False,
    )
    transport: RS232Transport
    if interface == "rs232":
        transport = RS232Transport(
            settings,
            transaction_timeout_s=transaction_timeout_s,
            serial_factory=endpoint.serial_factory,
        )
    elif interface == "rs485":
        transport = RS485Transport(
            settings,
            RS485Mode(True, False, False, None, None),
            transaction_timeout_s=transaction_timeout_s,
            serial_factory=endpoint.serial_factory,
            rs485_settings_factory=lambda **fields: fields,
        )
    else:
        raise ValueError("Synthetic interface must be rs232 or rs485")
    return SerialDevice(transport, FakeProtocol(), endpoint.simulator.coolant), endpoint
