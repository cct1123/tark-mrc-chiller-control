"""Protocol failures are distinct from recoverable OS/connection errors."""


class ProtocolError(RuntimeError):
    """A reply is invalid or uncertain; automatic read recovery is suspended."""


class ProtocolUnavailableError(ProtocolError):
    """The matching controller communication manual/implementation is missing."""
