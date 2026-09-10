"""Typed failures at controller, policy, protocol and transport boundaries."""


class ChillerError(Exception):
    """Base class for expected controller failures."""


class ChillerConnectionError(ChillerError):
    """An operation cannot complete with the current connection."""


class SetpointValidationError(ChillerError, ValueError):
    """A temperature or coolant policy is not valid for a requested write."""


class ProtocolError(ChillerError):
    """A request or response does not meet the documented device contract."""


class ProtocolUnavailableError(ProtocolError):
    """Authoritative device communication information has not been supplied."""


class TransportError(ChillerError):
    """A bounded serial operation failed; uncertain writes must not be retried."""
