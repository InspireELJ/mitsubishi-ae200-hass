"""Constants and enum maps for the MELANS / AE-200 protocol.

The Mitsubishi Electric centralised controllers (G-50, G-50A, GB-50, GB-50A,
AE-50, AE-200, AE-200E, EW-50, EW-50E) all speak the same "MELANS" XML
protocol over HTTP (``/servlet/MIMEReceiveServlet``) and, on newer firmware,
over a websocket (``ws://<host>/b_xmlproc/``).

This module captures the small enum vocabularies used by the protocol so the
rest of the library can deal in plain Python strings rather than magic values.
"""

from __future__ import annotations

# Transport
DEFAULT_HTTP_PATH = "/servlet/MIMEReceiveServlet"
DEFAULT_WS_PATH = "/b_xmlproc/"
WS_SUBPROTOCOL = "b_xmlproc"
DEFAULT_PORT = 80

# The controller is sensitive to aggressive polling. CVE-2008-1546 documents a
# denial-of-service on the same servlet, so we are deliberately a well-behaved
# client: a floor on poll interval and a minimum spacing between writes.
MIN_POLL_INTERVAL = 15          # seconds; refuse to poll faster than this
DEFAULT_POLL_INTERVAL = 30      # seconds
MIN_WRITE_SPACING = 1.0         # seconds between consecutive setRequests
REQUEST_TIMEOUT = 8             # seconds per request
MAX_GROUPS = 50                 # AE-200 + AE-50 expansion tops out well under this

# Drive (power)
DRIVE_ON = "ON"
DRIVE_OFF = "OFF"

# Operating modes as the controller reports / accepts them
MODE_COOL = "COOL"
MODE_HEAT = "HEAT"
MODE_FAN = "FAN"
MODE_DRY = "DRY"
MODE_AUTO = "AUTO"
ALL_MODES = (MODE_COOL, MODE_HEAT, MODE_FAN, MODE_DRY, MODE_AUTO)

# Fan speeds. Controllers vary in how many steps they expose; we keep the
# superset and the device advertises which it supports.
FAN_LOW = "LOW"
FAN_MID = "MID"
FAN_HIGH = "HIGH"
FAN_AUTO = "AUTO"
ALL_FAN_SPEEDS = (FAN_LOW, FAN_MID, FAN_HIGH, FAN_AUTO)

# Vane / air direction
AIR_DIR_VALUES = (
    "HORIZONTAL",
    "DOWNWARD1",
    "DOWNWARD2",
    "DOWNWARD3",
    "DOWNWARD",
    "VERTICAL",
    "SWING",
    "AUTO",
)

# RemoCon (local remote-control permission) — a key building-manager feature.
REMOCON_PERMIT = "PERMIT"
REMOCON_PROHIBIT = "PROHIBIT"

# Ventilation (Lossnay / air handler) drive
VENT_OFF = "OFF"
VENT_LOW = "LOW"
VENT_HIGH = "HIGH"

# Ventilation modes (energy recovery / bypass / auto)
VENTMODE_VALUES = ("HEATEXCHANGE", "NORMAL", "AUTO")

# Boolean-ish attribute values the controller uses
SIGN_ON = "ON"
SIGN_OFF = "OFF"

# Unit "kind" we infer from which attributes a group exposes. This is what
# lets the integration be generic: an indoor AC unit, a Lossnay/air handler,
# or an air-to-water (HWHP) unit all come back through the same Mnet object
# but populate different attribute sets.
KIND_AC = "ac"
KIND_VENTILATION = "ventilation"   # Lossnay / air handler
KIND_ATW = "atw"                   # air-to-water / hydronic heat pump
KIND_UNKNOWN = "unknown"

# An InletTemp of roughly -40 means the return-air thermistor is faulted or
# the unit is offline; treat readings at/below this as "no value".
FAULT_TEMP_THRESHOLD = -39.0
