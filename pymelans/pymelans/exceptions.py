"""Exceptions raised by :mod:`pymelans`."""

from __future__ import annotations


class MelansError(Exception):
    """Base class for all pymelans errors."""


class MelansConnectionError(MelansError):
    """Could not reach the controller (network, timeout, refused)."""


class MelansCommandUncertainError(MelansError):
    """A command may have been sent, but no acknowledgement was received.

    This is deliberately not a connection error: automatically falling back
    to another transport could send the same command to the controller twice.
    A later state read may establish the result; this exception does not prove
    either that the controller applied the command or that it rejected it.
    """


class MelansProtocolError(MelansError):
    """The controller returned a malformed or error response.

    ``code`` and ``point`` mirror the ``<ERROR Code=... Point=.../>`` element
    the MELANS controller emits inside a ``getErrorResponse``/``setErrorResponse``.
    """

    def __init__(self, message: str, *, code: str | None = None, point: str | None = None):
        super().__init__(message)
        self.code = code
        self.point = point


class MelansAuthError(MelansError):
    """The controller requires a maintenance/admin login we do not have."""
