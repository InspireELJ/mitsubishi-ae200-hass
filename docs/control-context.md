# Local restrictions and control-context diagnostics

Version `0.2.0b6` adds cached per-function local-prohibition attributes and an
explicit read-only control-context action to the native HACS integration.
Existing entity identifiers, central climate commands and automations are
preserved. No new entity, automatic context query or periodic context poll is
created.

## The existing remote switch

The existing *Local remote lockout* switch keeps its unique ID, legacy on/off
state and write behaviour. Its state reports the `RemoCon` summary. Turning it
on still writes only `RemoCon=PROHIBIT`; turning it off writes only
`RemoCon=PERMIT`. Neither operation changes the individual restriction items.
An on state alone does not show that all wall or handset remote functions are
locked.

Four additional read-only attributes interpret the existing cached group data:

| Attribute | Individual protocol item |
|---|---|
| `local_power_prohibited` | `DriveItem` |
| `local_mode_prohibited` | `ModeItem` |
| `local_temperature_prohibited` | `SetTempItem` |
| `local_filter_reset_prohibited` | `FilterItem` |

Each follows the controller web client's combined summary/item rule:

| Reported combination | Effective prohibition |
|---|---|
| `RemoCon=PERMIT`, or the individual item is `CHK_OFF` | `false` |
| `RemoCon=PROHIBIT` and the individual item is `CHK_ON` | `true` |
| Any other combination, including missing or unrecognised values | unknown |

These boolean interpretations apply only while the existing switch is
available. A stale group, failed group read or failed coordinator update makes
all four effective attributes unknown. Historical cached flags are not current
local permissions. The legacy switch's own `is_on` interpretation remains
unchanged and follows its existing HA availability handling.

These attributes do not send requests or make per-function changes. They
describe local-control restrictions, rather than hardware capabilities or
permissions for central HA commands. The integration does not use them to gate
HA services, change existing automations or promise that all local controls
will behave identically on another equipment family.

## Explicitly read a group's control context

Use **Tools → Actions → Mitsubishi AE-200 / EW-50: Read control context** with a
loaded integration entry and one configured group. Both `config_entry_id` and
`group_id` are required. The group must be an integer from 1 to 50 and present
in that controller's configured-group allowlist; the numeric range alone does
not make an unconfigured group eligible.

The action is response-only. A script example is:

```yaml
action: ae200.read_control_context
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  group_id: 1
response_variable: control_context
```

Replace the entry ID and group with the intended configured values. Calling
the action explicitly performs these two small `getRequest` operations in
order, using the same client request lock and pacing as normal traffic:

1. `DatabaseManager/SystemData` with only `AllTurnOff="*"`.
2. `DatabaseManager/Mnet` for the selected `Group`, with only `TurnOff="*"` and
   `BACnetTurnOff="*"`.

They do not read identity fields, enumerate other groups, reset faults, clear
stops or modify controller settings. The controller-wide minimum gap between
control-context actions is 15 seconds measured from completion, including
requests for different groups. Calls during that cooldown raise an action
error without issuing another query. Invalid or unloaded entries and groups
are rejected before controller I/O.

## Response, freshness and partial failures

The global and selected-group results have independent attempt times,
successful-read times, errors, quality and raw attributes. If either request
fails, the other is still attempted. Completed action responses use
`read_status` values `updated`, `partial` or `error`; partial and failed reads
return their diagnostic context rather than concealing the available result.
Validation errors and cooldown rejection remain action errors.

From `0.2.1b7`, cancellation differs from a completed read failure: it
propagates immediately and no later GET starts. Only the currently attempted
global or group record receives a safe `CancelledError`; its old raw evidence
and success time remain stale, or unknown when no success exists. An existing
unattempted record is not overwritten. A successful global sample stays fresh
if the group attempt is cancelled; the cached invocation status is partial
after one successful part and error after none. This metadata remains visible
in cached diagnostics, rather than a fabricated successful action response.

The completion cooldown still applies. Cancellation while waiting for the
context lock makes no new attempt or metadata change. Another independently
fresh true stop flag can still make `control_inhibited=true`; an unknown source
does not become an all-clear false. Tests cover these branches offline, without
live outage or cancellation injection.

Typed values are interpreted only from a fresh successful sample. Exact `ON`
means `true` and exact `OFF` means `false`. Missing, blank, wildcard and other
tokens remain unknown. An errored or aged sample can retain its historical raw
attributes and successful-read time, while its typed values become unknown.
Normal group polling does not renew these optional-context samples.

| Typed field | Raw source | Label used by the controller web client |
|---|---|---|
| `global.global_emergency_stop` | Global `AllTurnOff` | Emergency stop |
| `group.bacnet_emergency_stop` | Selected-group `BACnetTurnOff` | Emergency stop associated with BACnet |
| `group.energy_saving_stop` | Selected-group `TurnOff` | Energy-saving stop |

The names describe reported control flags. They do not identify an electrical
input or the cause of a stop, establish an active BACnet installation, prove
that a unit has physically stopped, or indicate compressor operation.

The response's `control_inhibited` combines only these three fresh typed
values:

- Any one is `true`: `control_inhibited=true`.
- All three are `false`: `control_inhibited=false`.
- Otherwise: `control_inhibited` is unknown.

An unknown source does not conceal another fresh affirmative stop flag, and
three old raw values cannot establish a current negative result. This summary
is diagnostic context, rather than a new gate on HA central commands. The
action never releases a stop or changes local prohibitions.

## Cache and evidence

Downloaded diagnostics copy the current context cache without controller I/O.
They retain separate global and group records, freshness and safe errors.
No automatic action repeats a read to replace a missing result. Existing
climate availability and command acknowledgement/readback remain independent.

The minimal global/group response structures were checked with live read-only
requests. Protocol, failure-isolation, cooldown and HA boundary tests use
offline fixtures. This establishes the selected read behaviour, rather than
successful stop control or every equipment family's restriction semantics.
Private response payloads, addresses and group inventories remain outside the
public repository.
