"""The sole insertion point for a documented controller wire protocol.

Operation names below are internal API labels, never serial commands. No Tark
commands, framing, addresses, acknowledgements, or settings are known here.
"""

from typing import Literal, NoReturn, Protocol

from .device import DeviceStatus
from .errors import ProtocolUnavailableError

Operation = Literal["get_temperature", "get_setpoint", "set_setpoint", "get_status"]


class ProtocolCodec(Protocol):
    """Normalize documented responses to Celsius, status, or write success.

    decode returns a finite real number for temperature/setpoint, DeviceStatus
    for status, and None after a successful setpoint transaction. It must validate
    response identity, integrity and errors according to the future manual.
    """

    def ensure_available(self) -> None: ...

    def encode(self, operation: Operation, value: float | None = None) -> bytes: ...

    def is_complete(self, buffer: bytes) -> bool: ...

    def decode(self, operation: Operation, response: bytes) -> float | DeviceStatus | None: ...


class MissingProtocol:
    """Fail closed until the matching controller communication manual exists."""

    def ensure_available(self) -> NoReturn:
        raise ProtocolUnavailableError(
            "Controller communication manual is required; no serial protocol is configured"
        )

    def encode(self, operation: Operation, value: float | None = None) -> bytes:
        self.ensure_available()

    def is_complete(self, buffer: bytes) -> bool:
        self.ensure_available()

    def decode(self, operation: Operation, response: bytes) -> float | DeviceStatus | None:
        self.ensure_available()
