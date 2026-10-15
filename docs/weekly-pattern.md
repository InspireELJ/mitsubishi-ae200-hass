# On-demand weekly-pattern diagnostics

Version `0.2.0b9` adds the response-only `ae200.read_weekly_pattern` action.
It reads one weekly pattern for an explicitly selected controller and
configured group. Concrete season and pattern selectors keep each request
scoped to one combination. The result is an independently timed diagnostic
snapshot of the reported plan.

This feature does not activate, enable, edit or execute a pattern. It adds no
startup read, periodic schedule poll, new entity, calendar event or automation
change. Existing entity identifiers and central controls are preserved.

## Explicit target and selectors

Supply all four fields for a loaded integration entry:

| Field | Required target |
|---|---|
| `config_entry_id` | One explicitly selected, loaded AE-200 integration entry |
| `group_id` | Integer 1–50, also present in that controller's configured-group allowlist |
| `season` | One concrete integer 1–5 |
| `pattern` | One concrete integer 1–7 |

Numeric ranges do not authorise unconfigured groups. Wildcards, omitted
selectors and multi-group selection are not a route to a broader query.
Invalid selectors must be rejected before controller I/O.

The action requires a response. A script can use:

```yaml
action: ae200.read_weekly_pattern
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  group_id: 1
  season: 1
  pattern: 1
response_variable: weekly_pattern
```

Replace every target and selector with the intended configured selection.
One invocation sends one minimal GET, using the common packet envelope:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <ScheduleControl>
    <WPatternList Group="1" Season="1" Pattern="1" />
  </ScheduleControl>
</DatabaseManager></Packet>
```

Only this group/season/pattern combination is requested. `MultiGroup`, date
ranges, wildcard selectors and extra records are not sent. The official web
client can iterate five seasons and seven patterns, but this diagnostic action
does not copy that 35-request scan or fetch adjacent combinations.

The selectors identify protocol slots. This read alone does not establish
that a slot is enabled, selected today, associated with a particular civil
weekday or currently executing. Season activation dates, exceptions and
schedule precedence require separate evidence.

## Retained fields and interpretation

The expected structure is one strict `getResponse` with the requested
`DatabaseManager/ScheduleControl/WPatternList` and its `WPatternRecord`
children. The returned selectors must match the request. An ERROR or error
response must fail the read even if it also carries partial plan records.
Ambiguous directories are rejected. Selectors must match their canonical
requested strings; alternate padding is not silently treated as a match.
An `Address` or `MultiGroup` attribute anywhere in the response, including an
empty or wildcard value, rejects the snapshot.

Only these 18 record fields are retained:

| Category | Fields |
|---|---|
| Record and time | `Index`, `Hour`, `Minute` |
| Drive and mode | `Drive`, `Mode`, `VentMode` |
| Setpoints | `SetTemp`, `SetTemp1`, `SetTemp2`, `SetTemp3`, `SetTemp4`, `SetTemp5` |
| Air and humidity | `AirDirection`, `FanSpeed`, `Humid` |
| Local-control items | `DriveItem`, `ModeItem`, `SetTempItem` |

Raw strings remain available without inventing an action from an unfamiliar
token. An empty action means no control of that field; it is not an OFF,
default or reset command. Wildcards and future action values remain unknown.
Rows describe a plan rather than current power, mode, temperature, local
permissions or physical operation. Reading a planned local-control item does
not change the existing local-remote switch or its effective attributes.

Record indices and returned row order do not establish execution order.
Conflicting or invalid indices need explicit issue reporting and isolation
rather than a last-record-wins interpretation. Identical duplicates must not
be counted as additional execution events. Raw hour/minute values are retained;
nullable typed values are limited to hour 0–23 and minute 0–59. Missing,
wildcard or invalid values remain unknown with issues. These values do not
establish a calendar date, timezone or next execution time.

## The 24-record receive boundary

The official editor's 24-row limit is used as a conservative receive
protection. A response exceeding 24 rows must fail in full rather than being
truncated into an apparently complete pattern. XML size, tree and attribute
guards apply in addition to the record cap.

This is not a guarantee about server storage capacity or a server-side
pagination or count limit. The controller workload is constrained by the
concrete request selectors; a local receive cap does not justify an unbounded
history, yearly-directory or whole-controller scan.

Zero returned rows means that this response contains no records for the
selected combination. It does not prove that weekly scheduling is disabled,
unsupported, empty in other slots or inactive today. The separate cached
schedule flag and its reported availability do not establish pattern contents
or execution.

The existing schedule diagnostic keeps its `schedule_on` identifier,
`Schedule`-based state, creation rule and default-disabled status. Its name is
*Controller schedule flag*. The separate cached `schedule_available` attribute
maps exact `ScheduleAvail=ON` to true and `OFF` to false while the entity is
available. Other tokens or an unavailable, stale or failed group/coordinator
make availability unknown; this does not switch the entity's state source.
`raw_schedule_flag` and `raw_schedule_available` retain only `ON`, `OFF`, `*`
or empty-string tokens, including historical safe raw evidence while
unavailable. Neither flag proves activation, execution or a next run.

## Cache, errors and freshness

The cache is independent of climate polling, today-schedule snapshots
and controller-clock samples. Each selected combination retains its own
attempt time, successful-read time, errors, quality, raw records and issues.
An optional read failure must not change climate availability or refresh
another pattern's evidence.

Requests reuse normal controller serialisation and pacing. Weekly-pattern
actions also share a controller-wide 15-second cooldown measured from
completion, including calls for other groups, seasons or patterns. Invalid
targets and cooldown rejection are action errors without extra requests.
There is no automatic retry loop to fill missing slots.

A completed failed read can return an error diagnostic snapshot. Previous
raw records may remain available with their original sample time and stale
quality; typed hour/minute values become unknown. Missing evidence is unknown,
not a fresh empty pattern. Downloading diagnostics must copy the existing
cache without controller I/O.

From `0.2.1b7`, cancelling a started attempt records a safe `CancelledError`,
retains old raw records and successful-read time as stale, and leaves a cold
selection unknown. Typed hour/minute values become unknown. Cancellation
propagates without retry and starts the completion cooldown. Cancellation
while waiting for the pattern lock makes no new attempt or cache change.
This cancellation coverage is offline, without live failure injection.

Reading this pattern together with a controller Clock or TodayList response
does not make the samples atomic, associate the pattern with a verified date,
prove activation or predict execution. No implicit clock query, season
directory read or additional schedule query accompanies this action.

## Evidence and remaining acceptance

Static evidence establishes the official `WPatternList` request builder,
single-combination selectors, record fields and editor boundaries. One
minimal live websocket GET for one group/season/pattern combination returned
zero records and passed strict parsing without issues. It did not read all
slots or a complete directory. Deployed HA acceptance also confirmed a fresh
successful empty action response matching the selected cache exactly, with
existing entity identifiers and automations preserved.

Only the empty-list response structure has live protocol evidence. It does
not establish that scheduling is disabled, inactive or empty in other slots.
Non-empty records, their actions, activation and execution still require
additional acceptance. Verify selector matching, unknown and conflicting
records, receive limits, cooldown, independent stale/error caches and the HA
response-only contract before widening support. Keep private addresses, room
names, exact sample times, complete plan inventories and captured responses
outside the public repository.
