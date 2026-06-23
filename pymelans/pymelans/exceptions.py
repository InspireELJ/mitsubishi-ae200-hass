"""Exceptions raised by :mod:`pymelans`."""

from __future__ import annotations


class MelansError(Exception):
    """Base class for all pymelans errors."""


class MelansConnectionError(MelansError):
    """Could not reach the controller (network, timeout, refused)."""


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
