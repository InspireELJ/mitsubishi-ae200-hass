# Raw member room-temperature history

Version `0.2.1b3` adds the response-only
`ae200.read_member_temperature_history` action. It explicitly reads one
topology-validated IC member and one calendar date, with the data name fixed
to `ROOM_TEMP`. Deployed HA acceptance has passed for one selected member/day
and an immediate-repeat cooldown check.

The diagnostic retains raw strings. It does not publish Celsius values,
averages, totals, temperature entities or statistics. `unit`, `aggregation`
and `timezone` remain unknown. A successful history read does not update the
current room-temperature entity or prove collection or measurement validity.

## One member and one explicit day

All three fields are required:

| Field | Accepted selection |
|---|---|
| `config_entry_id` | One explicitly selected loaded AE-200 integration entry |
| `address` | One ASCII integer address 1–250 allowed by validated IC membership |
| `date_iso` | Strict Gregorian `YYYY-MM-DD` within the pilot's 1970–2037 window |

The action does not accept `data_name`, `group_id`, `Group`, address lists,
wildcards, units, aggregation settings or additional control fields. Even an
explicit `data_name: ROOM_TEMP` is rejected; the reader itself fixes the
channel. Missing date or entry never selects a default controller or today.

Membership is a separate prerequisite from the numeric address range.
Unknown, conflicted, unconfigured and non-IC members are rejected before I/O.
The IC allowlist does not adopt the empty-SubModel gate used by the separate
local-restrictions write action. Other actual hardware and history collection
conditions still require representative acceptance.

The date window is a local pilot policy, not a server-retention, history
availability or capacity guarantee. This action does not read the controller
clock, bind its sample to that clock or substitute HA's current date.

An explicit action with a response variable can use:

```yaml
action: ae200.read_member_temperature_history
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  address: 1
  date_iso: "2024-01-01"
response_variable: member_temperature_history
```

Use the intended configured member and date in place of these generic
examples. One invocation sends one minimal request:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <EnergyManagement>
    <IcTrendDList Address="1" Year="2024" Month="1" Day="1" DataName="ROOM_TEMP" />
  </EnergyManagement>
</DatabaseManager></Packet>
```

There is no `RoomTempData="*"`, Group selector, wildcard address, second
channel, adjacent-date scan or automatic retry. An empty or failed sample
does not trigger a runtime-history request or another endpoint.

## Strict structure and retained evidence

The parser expects one strict `getResponse` envelope and one direct
`DatabaseManager/EnergyManagement/IcTrendDList`, matching all five selector
attributes exactly. At most one direct leaf `IcTrendDRecord` is accepted.
It retains only that record's `RoomTempData`. Additional records, ERROR
responses, conflicting selectors, group fields, nesting, ambiguous lists or
mixed non-whitespace content reject the whole sample.

Shared XML protections bound payload size, tree depth, nodes, attributes and
value lengths. The 1 MiB payload limit, 512-character attribute limit,
zero-or-one record policy and calendar window are local protections, not
server-capacity or history-retention claims. Oversized responses are rejected
in full rather than truncated.

The result includes safe member/date selectors, fixed `data_name: ROOM_TEMP`,
record count, raw attributes, raw tokens, sample times, read status, quality,
safe errors and issues. Unknown extra fields are discarded. Zero records,
a missing selected field or a non-48 token count retain an appropriate issue;
they do not become zero-filled or successful numeric measurements.

## Forty-eight positions without numeric temperatures

Only an exact 48-token sequence receives position labels based on the vendor
daily graph's nominal half-hour layout:

| Index | Naive graph interval label |
|---|---|
| 0 | `00:00`–`00:30` |
| 1 | `00:30`–`01:00` |
| … | Following half-hour positions |
| 47 | `23:30`–`24:00` |

Each slot contains only `index`, `start_time`, `end_time` and `raw_token`.
`interval_minutes: 30` describes this positional layout; it does not prove
the acquisition interval, averaging method or exact instant of a measurement.
`24:00` is the final label, not a converted event timestamp.

Tokens remain strings even when numeric-looking, empty, wildcard or
unfamiliar. There is no float conversion, trimming, sentinel decoding,
rounding, clamping, padding or zero-fill. A different token count preserves
the original sequence and returns no labelled slots; it is not truncated
or shifted to fit a day.

The vendor display expects Celsius, but the captured wire response does not
report a unit, aggregation method or collection-validity flag. That display
convention does not verify all firmware or prove that each position is an
instantaneous value, average, minimum or maximum. Therefore `unit`,
`aggregation` and `timezone` stay `null`. No `reported_celsius` or other
numeric projection is exported. Numeric-looking zero or non-zero strings do
not establish measured conditions or a valid statistics source.

Local graph labels are not UTC timestamps. No timezone assignment, DST
adjustment, clock-drift correction or date association with a separate Clock
or schedule sample is inferred.

## Cache, cooldown and failure isolation

Room-temperature, runtime and setpoint-history actions share a controller-wide lock
and a 15-second cooldown measured from completion across members, days and
their channels. Normal request pacing remains in force. There is no startup
history read, periodic poll, automatic query or new entity.

At most 32 room-history member/date samples are retained by a local LRU
policy. This is client memory retention, not controller storage capacity.
Downloading cached diagnostics exports `member_temperature_histories`
without I/O, refreshing a sample or allocating an absent selector.

Each sample has independent attempt and success times, error and quality.
Fresh means a recent successful query of the explicitly selected historical
date, rather than a current room measurement. An error or expiry can retain
raw evidence and positional labels with the original sample time, clearly
marked stale or unknown. A cancelled attempt records a safe cancellation
error, preserves any prior raw sample and success time, starts the shared
completion cooldown and propagates cancellation without retry.

A completed action returns an `updated` or `error` snapshot. An empty valid
response can still have unknown content with issues. Cooldown raises an HA
action error without another request. Invalid selectors and unverified
membership are validation errors. Optional history failure does not renew
normal group/member freshness, change climate availability or confirm a
control command.

## Evidence and next steps

One minimal live `ROOM_TEMP` GET returned one record containing 48 non-zero
numeric-looking tokens. Strict parsing produced 48 positions without issues,
including the first and final interval labels. This proves that selected
request and response shape, rather than units, aggregation, collection
validity or cross-firmware measurement support. Public documentation retains
no private address, selected site date, raw values or inventory.

The deployed HA action returned a fresh successful snapshot with 48 raw tokens
and positions matching both cached diagnostics and the selected protocol
sample exactly. Unit, aggregation and timezone remained unknown. An immediate
repeat raised the expected cooldown error without changing the cached attempt
or success time. Original entity identifiers, operating settings, local
permissions and the enabled scheduled automation were preserved.

Numeric historical temperatures,
statistics, means, minima/maxima, missing-data rules, sampling semantics and
DST handling require separate evidence and implementation. The existing
current-temperature entity, control paths, identifiers and automations are
preserved. See [runtime history](member-runtime.md) for the separate minute
channel, [setpoint history](member-setpoint-history.md) for cooling/heating raw
series, and [capability boundaries](capabilities.md) for the broader scope.
