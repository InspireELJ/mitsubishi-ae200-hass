# Raw member cooling and heating setpoint history

Version `0.2.1b4` adds the response-only `ae200.read_member_setpoint_history`
action. One explicit invocation reads one topology-validated IC member,
one calendar date and either `COOL_SET_TEMP` or `HEAT_SET_TEMP`.
Deployed HA acceptance has passed for both selected channels on one member/day.

The result preserves raw strings rather than numeric temperatures. Unit,
aggregation and timezone remain unknown. It changes no setpoint, does not
update the current target-temperature entity and creates no history entity,
average, total or HA statistic. Historical values do not confirm a command,
prove an active mode or establish that the member is currently heating or cooling.

## Four explicit selectors

| Required field | Accepted selection |
|---|---|
| `config_entry_id` | One explicitly selected loaded AE-200 integration entry |
| `address` | One ASCII integer address 1–250 allowed by validated IC membership |
| `date_iso` | Strict Gregorian `YYYY-MM-DD` within the pilot's 1970–2037 window |
| `data_name` | Exactly `COOL_SET_TEMP` or `HEAT_SET_TEMP` |

No field has a default. The action rejects aliases, combined names, ROOM_TEMP,
runtime channels, Group selectors, wildcards, units, aggregation settings and
all extra parameters. It does not infer a channel from the member's current
mode or request both channels automatically. The existing ROOM_TEMP action
continues to accept only its entry, address and date, with no data-name field.

The numeric address range does not establish membership. Unknown, conflicted,
unconfigured and non-IC members are rejected before I/O. The verified IC-member
allowlist is separate from the empty-SubModel gate used by the local-restrictions
write action. Representative equipment and collection conditions still need
their own acceptance.

The date window is a local protective policy, not a server-retention or
history-availability guarantee. The reader does not select today, read the
controller clock, assign a timezone or bind the result to a separate clock
or schedule sample.

An explicit action can use:

```yaml
action: ae200.read_member_setpoint_history
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  address: 1
  date_iso: "2024-01-01"
  data_name: COOL_SET_TEMP
response_variable: member_setpoint_history
```

Replace the generic member/date with the intended configured selection.
One invocation sends one minimal GET for that exact selector:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <EnergyManagement>
    <IcTrendDList Address="1" Year="2024" Month="1" Day="1" DataName="COOL_SET_TEMP" />
  </EnergyManagement>
</DatabaseManager></Packet>
```

There is no value-field wildcard, Group selector, second channel, adjacent-date
query, automatic retry, collection activation or write.

## Selected raw field and strict parsing

| Data name | Retained field |
|---|---|
| `COOL_SET_TEMP` | `CoolSetTempData` |
| `HEAT_SET_TEMP` | `HeatSetTempData` |

The parser requires one strict `getResponse` and one direct
`DatabaseManager/EnergyManagement/IcTrendDList`, with all five selector
attributes matching exactly. At most one direct leaf `IcTrendDRecord` is
accepted. Only the requested raw field survives; another field is not a fallback.
ERROR responses, extra records, ambiguous directories, nested content,
group selectors and non-whitespace mixed text reject the complete sample.

Shared XML protection bounds payload size, tree depth, nodes, attributes and
attribute lengths. The 1 MiB payload cap, 512-character value cap, zero-or-one
record policy and date window are local protections, not controller capacity
or retention promises. Oversized samples are rejected rather than truncated.

The safe snapshot includes address/date/data-name selectors, read status,
quality, attempt/success times, safe error, record count, raw attributes,
raw tokens, slots and issues. It reports `unit: null`, `aggregation: null`,
`timezone: null` and `interval_minutes: 30`. The interval is a graph-layout
label, not a verified averaging or acquisition period.

## Empty strings and forty-eight positions

Exactly 48 tokens receive naive positional half-hour labels, from
`00:00`–`00:30` at index 0 to `23:30`–`24:00` at index 47. Each slot has only
`index`, `start_time`, `end_time` and `raw_token`. There is no float, Celsius,
mean, minimum, maximum, total or timestamp conversion.

Numeric-looking values, empty strings, wildcards and unfamiliar tokens all
remain strings. In particular, 47 commas encode **48 empty tokens**, with
48 corresponding positions. This differs from an absent record, a missing
attribute, or an empty attribute containing a single empty token. Missing or
non-48-token samples retain issues and original evidence; they are not padded,
shifted, clamped or zero-filled.

An empty heating sequence does not mean zero temperature, no heating, disabled
collection or unsupported history. Numeric-looking cooling strings do not
prove measured conditions or current setpoints. Vendor display conventions
do not establish wire units, aggregation, collection validity or semantics
across firmware. These require independent evidence before numeric history
or statistics can be exposed.

The final `24:00` is a position label, not an event timestamp. No UTC offset,
timezone assignment, DST adjustment, clock-drift correction or date binding
to a separate Clock sample is inferred.

## Shared pacing and independent cache

Runtime, room-temperature and setpoint-history actions share one
controller-wide lock and a 15-second cooldown measured from completion.
Alternating action, member, date or data name does not bypass it. Normal
request pacing remains in force. No startup history query, periodic poll,
automatic channel/day scan or new entity is introduced.

Setpoint history has an independent cache of at most 32 member/date/data-name
samples under a local LRU policy. This is client memory retention, not server
storage capacity. Cached diagnostics export `member_setpoint_histories`
under address/date/data-name without I/O, refreshing a sample or allocating
an absent selector.

Each sample has independent attempt and success times, error and quality.
Fresh describes a recent successful query of the selected historical day,
rather than a current target temperature or valid measurement. An error or
expiry retains earlier raw evidence and labels with their sample time and
stale/unknown quality. Cancellation after an attempt starts records a safe
error, preserves the old sample and success time, propagates cancellation and
starts the shared completion cooldown. Cancellation while waiting for the
action lock makes no new attempt or cache entry.

Completed actions return `updated` or `error` snapshots; unknown contents
can retain issues after a structurally valid response. Cooldown raises an HA
action error with no additional query. Invalid selectors or membership produce
validation errors. Optional history failure does not alter climate/member
availability, renew their freshness or confirm a write.

## Evidence and next steps

Two separate minimal live requests returned one record each: a selected cooling
history contained 48 numeric-looking strings, and a selected heating history
contained 48 empty strings. Those selected wire shapes were accepted; they
do not establish units, aggregation, collection state or other hardware.
Public documentation retains no private address, site date, raw values or inventory.

Deployed HA actions for both channels returned updated, fresh, one-record,
48-slot snapshots matching cached diagnostics and prior protocol samples
exactly. All 48 empty heating strings were preserved. Original entity
identifiers, operating settings, local permissions and the enabled scheduled
automation were retained. Unit, aggregation and timezone remained unknown.

Numeric projection, current-state
interpretation, sampling semantics, missing-data policies, DST handling and
HA statistics remain separate work. Existing identifiers, controls, schedules
and automations are preserved. See [room history](member-temperature-history.md),
[runtime history](member-runtime.md) and the [capability inventory](capabilities.md).
