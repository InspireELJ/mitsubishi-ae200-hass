# On-demand today-schedule diagnostics

Version `0.2.0b7` adds the response-only `ae200.read_today_schedule` action to the
native HACS integration. It reads a single configured group's `TodayList` and
preserves the returned plan as an independently timed diagnostic snapshot.
Existing entities, identifiers, controller schedules and HA automations remain
unchanged.

There is no TodayList read at setup or reload, no periodic schedule poll and no
new schedule entity. This action does not edit, enable, disable or execute a
controller schedule.

## Explicit group selection

Use **Tools → Actions → Mitsubishi AE-200 / EW-50: Read today schedule** and
select a loaded configuration entry and one configured group. Both
`config_entry_id` and `group_id` are required. The group must be an integer
from 1 to 50 and belong to that controller's configured-group allowlist.
The numeric range alone does not authorise an unconfigured group.

The action requires a response. In a script, provide a response variable:

```yaml
action: ae200.read_today_schedule
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  group_id: 1
response_variable: today_schedule
```

Replace both values with the intended configured target. Each explicit read
uses one minimal `getRequest`:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <ScheduleControl><TodayList Group="1" /></ScheduleControl>
</DatabaseManager></Packet>
```

Only the selected `Group` is sent. No wildcard group, `MultiGroup`, date range,
history scan or write operation is added. Normal controller traffic and the
action share the same request lock and pacing. Today-schedule reads also have
a controller-wide 15-second cooldown measured from completion, including calls
for different groups. Repeated requests do not bypass this limit.

## Retained fields and unknown values

The expected response is a strict `getResponse` containing one
`DatabaseManager/ScheduleControl/TodayList` for the requested group and its
`TodayRecord` children. Error responses are rejected even if they also contain
partial records. Ambiguous directories or a concrete multi-group selection
are not accepted as the requested group's snapshot.

Only these 18 record fields are retained:

| Category | Fields |
|---|---|
| Record and time | `Index`, `Hour`, `Minute` |
| Drive and mode | `Drive`, `Mode`, `VentMode` |
| Setpoints | `SetTemp`, `SetTemp1`, `SetTemp2`, `SetTemp3`, `SetTemp4`, `SetTemp5` |
| Air and humidity | `AirDirection`, `FanSpeed`, `Humid` |
| Local-control items | `DriveItem`, `ModeItem`, `SetTempItem` |

Raw hour/minute strings are preserved. Valid typed values are limited to hour
0–23 and minute 0–59; missing, wildcard or invalid values remain unknown and
carry issues. No date or timezone has been established from this response, so
the integration does not convert these values to UTC events or calculate the
next execution time. The name `TodayList` is the controller's protocol label,
not proof that a cached response still belongs to the HA calendar's current day.

Action fields remain raw strings. An empty action string means that the plan
does not control that field, rather than requesting its OFF or default value.
Wildcard and future action tokens remain unknown. A planned mode, temperature
or power action is not an observed current state, and record data does not
establish that an action has executed or will execute.

Invalid or conflicting indices are isolated with issues instead of choosing
one contradictory record. Raw records remain available for diagnosis. Neither
an index nor XML row order is interpreted as a verified execution order.

## The 24-record receive limit

The inspected controller web editor limits a today plan to 24 records. This
integration uses that evidence as a local receive boundary: a response with
more than 24 rows is rejected in full, rather than truncated into an apparently
complete plan. XML size, nesting, node and attribute limits apply as well.

This is not a guarantee about every controller's storage capacity, and no
unverified server-side count or pagination parameter is sent. The request is
scoped to one configured group's today list; the receive limit must not be
reused as justification for an unbounded history or whole-controller query.

An empty list means that the controller returned zero records in this
response. It does not prove that schedules are disabled, unsupported or empty
in weekly/yearly patterns, and does not replace the separate cached
schedule flag or its separately reported availability. Neither flag proves
that a particular plan is active or executing.

## Cache, failures and evidence

The action response includes the selected group, read status, quality, attempt
and successful-read times, safe errors, records and issues. A completed read
can return an `updated` or `error` diagnostic response. Invalid targets and
cooldown calls raise an action error instead. Before a successful sample
exists, its successful-read time remains unknown.

From `0.2.1b7`, a started cancelled attempt records a safe `CancelledError`,
preserves the previous raw records and success time as stale, and leaves a cold
sample unknown. Typed hour/minute values become unknown. Cancellation
propagates without retry and starts the completion cooldown; cancellation
while waiting for the schedule lock makes no attempt or cache change. Offline
tests cover this behaviour without live outage or cancellation injection.

The snapshot has its own freshness. Normal climate polling does not refresh
it. A failed optional read does not change climate availability or group-state
freshness. Historical raw
records may remain cached after an error or expiry, but must be read with their
sample time and quality instead of being presented as today's latest plan.
Unknown or stale data does not become a fresh empty schedule.

Downloading integration diagnostics copies the existing schedule cache and
does not issue this request. The integration does not schedule repeated reads
or a background task to fill missing plans. The action creates no calendar
event, next-run sensor or new schedule automation.

Live evidence currently confirms only the empty-list response structure.
Non-empty plan records, action semantics across equipment families and actual
execution have not passed live acceptance. Offline fixtures cover parsing,
unknown values, conflicts, boundaries and failure isolation. Private response
files, addresses, room names and plan inventories remain outside the public
repository.
