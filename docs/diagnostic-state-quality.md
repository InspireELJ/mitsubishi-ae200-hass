# Cached diagnostic state quality

Version `0.2.1b5` corrects cached HA diagnostic presentation without adding
controller requests or control operations. Existing entity unique IDs,
standard climate state and temperature readings, actions and automations are
preserved. Deployed HA normal-site regression has passed for fresh climate
flags against the cached raw data. Unavailable/error branches and command
field filtering have offline evidence rather than live fault injection.

## Climate flags follow entity availability

The existing climate extra attributes are:

| Attribute | Cached protocol source |
|---|---|
| `filter_sign` | `FilterSign` |
| `error_sign` | `ErrorSign` |
| `local_remote_prohibited` | `RemoCon` summary |

These three values are nullable booleans. They retain the device's recognised
boolean interpretation only while the climate entity is available and the
group quality is exactly `fresh`. A stale
sample, failed group read, failed coordinator update or missing device makes
all three `null`/unknown. Fresh missing, wildcard or unrecognised tokens also
remain unknown. A previously cached false value must not imply a current
absence of a filter reminder, fault flag or local prohibition after a read
has failed or the sample has expired.

This change does not clear a flag, release a restriction, refresh a sample or
modify the existing current/target temperature getters. Safe historical raw
evidence can remain in cached diagnostics with its quality and observation
time. Unknown is not a newly observed false value.

`local_remote_prohibited` still describes only the legacy `RemoCon` summary.
It does not establish that every wall or handset remote function is blocked.
The existing switch's separate effective per-function attributes retain their
own freshness rules; see [local restrictions and context](control-context.md).

## Last-command item expectations

The existing default-disabled *Last command status* sensor keeps its unique
ID and status semantics. Its allowlisted `expected_attributes` can now also
retain the four individual local restriction fields already written by the
per-function action:

| Expected field | Accepted raw tokens |
|---|---|
| `DriveItem` | `CHK_ON`, `CHK_OFF` |
| `ModeItem` | `CHK_ON`, `CHK_OFF` |
| `SetTempItem` | `CHK_ON`, `CHK_OFF` |
| `FilterItem` | `CHK_ON`, `CHK_OFF` |

Missing fields, wildcards and invalid or future tokens are omitted rather than
published as guessed booleans or extra protocol values. Existing allowlisted
command fields remain available. No connection details, arbitrary attributes
or raw transport errors are added by this extension.

The expected values describe a saved command, which may be historical. They
do not establish current effective restrictions or fresh physical behaviour.
`RemoCon` and item fields must still be interpreted together, and a matching
expectation is not an acknowledgement or a successful readback. Command
status continues to distinguish acceptance, confirmation, failure and uncertain
outcome using the existing client rules; no retry or compensation is added.

This is diagnostic visibility for an existing bounded control operation,
rather than a new permission-writing capability. See
[per-function restrictions](local-restrictions.md) for profile gates, fresh
prerequisites and the limited live filter-reset/restoration evidence.

## Bounded failure details

The saved command can expose `error_type` only when its status is `failed` or
`outcome_unknown` and its type exactly matches one of these known classes:

- `MelansError`
- `MelansConnectionError`
- `MelansCommandUncertainError`
- `MelansProtocolError`
- `MelansAuthError`
- `CancelledError`

Only with a recognised type can an explicitly present `error_code` survive:
either `null`, or a 1–32-character string made solely of ASCII letters,
digits, `_` and `-`. A missing or invalid code is omitted. An unknown or
malformed type, or any other command status, suppresses both fields.

These are bounded diagnostic tokens, not error messages. Arbitrary exception
text, URLs, connection details and controller responses are not published.
No status is fabricated from the type or code; native status options,
including unknown outcome handling, remain unchanged. This adds no request,
retry, confirmation or new control capability.

## Group-poll error export isolation in 0.2.1b7

Stored group-poll error codes use the existing bounded error formatter.
Codes must be ASCII letters, digits, `_` or `-`, with length 1–32; missing or
invalid codes become null. This changes error metadata, not polling timing,
the wire request or controller-side behaviour.

The cached `read_errors` export then applies an independent filter:

| Element | Export rule |
|---|---|
| Group key | Canonical decimal string 1–50; malformed keys are omitted, not coerced |
| `type` | ASCII letters, digits, `_` or `-`, length 1–64; otherwise null |
| `code` | Same character set, length 1–32; otherwise null |
| Other record keys | Omitted, including message, URL or arbitrary extra fields |

Valid historical group keys are retained even if absent from the current
configured-name list. A non-dictionary record becomes `{type: null, code: null}`;
a non-dictionary outer cache exports an empty dictionary. Future valid short
type tokens can survive here. Type and code are filtered independently, so an
invalid type does not discard an otherwise valid code. This is separate from the last-command sensor's
six-class allowlist above.

Every export creates new outer and inner dictionaries. Changing returned
diagnostic errors therefore cannot change the live client's error records.
The original cache is not mutated by filtering. Other raw diagnostic field
rules are unchanged, and exporting diagnostics still performs no I/O.

Synthetic offline cases established that unbounded error-code strings could
previously be exported and that returned error dictionaries shared references
with client state. The fix does not assert an actual site credential leak,
external attack or live fault-injection result.

## Validation boundary

Offline HA contracts cover fresh/unknown and unavailable states, allowlisted
item values, retained identifiers and no-I/O behaviour. These checks do not
create a live outage, change a controller flag, exercise a wall-remote button
or widen hardware support. The actual HA integration and bundled library
loaded `0.2.1b5`; all groups were fresh and their three climate flags matched
cached raw values, including reported filter-on values. Existing identifiers,
operating settings, local permissions and the enabled automation were retained.
No live outage was injected, no control was sent for this regression and the
default-disabled last-command sensor was not enabled. This normal-site check
does not extend the unavailable/error or command-field tests into a claimed
live fault/command acceptance.
