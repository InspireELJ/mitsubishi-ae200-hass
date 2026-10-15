"""pymelans — async client for Mitsubishi Electric MELANS / AE-200 controllers.

Supports the G-50 / G-50A / GB-50 / GB-50A / AE-50 / AE-200 / AE-200E /
EW-50 / EW-50E centralised controllers over their MELANS XML protocol
(websocket ``b_xmlproc`` preferred, HTTP ``MIMEReceiveServlet`` fallback).
"""

from __future__ import annotations

from . import const
from .client import MelansClient
from .device import MelansDevice
from .diagnostics import cached_snapshot
from .exceptions import (
    MelansAuthError,
    MelansCommandUncertainError,
    MelansConnectionError,
    MelansError,
    MelansProtocolError,
)

__version__ = const.VERSION
__all__ = [
    "MelansClient",
    "MelansDevice",
    "MelansError",
    "MelansCommandUncertainError",
    "MelansConnectionError",
    "MelansProtocolError",
    "MelansAuthError",
    "const",
    "cached_snapshot",
]
