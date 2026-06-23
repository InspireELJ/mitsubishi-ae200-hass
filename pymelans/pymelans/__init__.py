"""pymelans — async client for Mitsubishi Electric MELANS / AE-200 controllers.

Supports the G-50 / G-50A / GB-50 / GB-50A / AE-50 / AE-200 / AE-200E /
EW-50 / EW-50E centralised controllers over their MELANS XML protocol
(websocket ``b_xmlproc`` preferred, HTTP ``MIMEReceiveServlet`` fallback).
"""

from __future__ import annotations

from . import const
from .client import MelansClient
from .device import MelansDevice
from .exceptions import (
    MelansAuthError,
    MelansConnectionError,
    MelansError,
    MelansProtocolError,
)

__version__ = "0.1.0"
__all__ = [
    "MelansClient",
    "MelansDevice",
    "MelansError",
    "MelansConnectionError",
    "MelansProtocolError",
    "MelansAuthError",
    "const",
]
