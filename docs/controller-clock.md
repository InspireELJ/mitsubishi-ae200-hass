# On-demand controller-clock diagnostics

Version `0.2.0b8` adds the response-only `ae200.read_controller_clock` action to
the native HACS integration. An explicit request reads the controller-reported
calendar/clock fields and retains an independently timed diagnostic snapshot.
Existing entities, identifiers, central controls and HA automations are
preserved. One minimal live Clock GET confirmed the six-field response and
passed strict parsing without issues. HA deployment acceptance also confirmed
a fresh successful action response matching cached diagnostics exactly, with
existing entity identifiers and automations preserved.

There is no clock read at setup or reload, periodic clock poll, new clock
entity, clock write or time synchronisation. The snapshot does not establish
the controller's timezone, daylight-saving rules, clock accuracy or drift.
Whether these fields already include daylight adjustment remains unverified.
The reader does not add or subtract a shift or combine Clock with separate
daylight settings to manufacture an adjusted time.
The separate [raw daylight-settings action](controller-daylight-settings.md)
can inspect reported configuration without evaluating rules or changing Clock.

## Explicit controller selection

In **Tools → Actions**, select `ae200.read_controller_clock` and supply a loaded
integration entry. The `config_entry_id` field is required; there is no group
argument because the clock belongs to the selected controller.

The action requires a response. In a script, provide a response variable:

```yaml
action: ae200.read_controller_clock
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
response_variable: controller_clock
```

Replace the entry ID with the intended configured target. Each explicit read
uses one minimal request:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <Clock Year="*" Month="*" Day="*" Hour="*" Minute="*" Second="*" />
</DatabaseManager></Packet>
```

Only these six attributes are requested and retained. Normal controller
traffic and the action share the same request lock and pacing. Clock actions
also have a controller-wide 15-second cooldown measured from completion.
Calls during the cooldown raise an action error without another clock query.
An invalid or unloaded configuration entry is rejected before controller I/O.

## Raw fields and nullable local time

The parser requires a strict `getResponse` with one direct, leaf `Clock` record
under `DatabaseManager`. Error responses, duplicate or misplaced records and
non-whitespace content are rejected. Existing XML size, tree and DTD guards
apply. Additional attributes are discarded.

| Raw attribute | Typed component | Accepted range |
|---|---|---|
| `Year` | `components.year` | 1970–2037 |
| `Month` | `components.month` | 1–12 |
| `Day` | `components.day` | Valid Gregorian day for a known year/month |
| `Hour` | `components.hour` | 0–23 |
| `Minute` | `components.minute` | 0–59 |
| `Second` | `components.second` | 0–59 |

The year window comes from the inspected official settings editor. It is a
conservative interpretation boundary, not a universal claim about controller
calendar capacity. Raw strings remain unchanged, including accepted numeric
zero-padding. Missing, empty, wildcard, unfamiliar or invalid tokens produce
unknown typed components and issues. An impossible known calendar date makes
the typed day unknown. Valid independent components can still be reported;
they do not establish a complete date when another component is unknown.

`local_datetime` is produced only when all six components form a valid date
and time and the sample remains fresh and successful. It represents the
reported fields as an ISO string without an offset; it does not establish
daylight-adjusted local civil time. The response's `timezone` remains unknown. Neither
HA's configured timezone nor the browser's timezone is assigned to the sample.
The stored value is a sample, not a ticking clock extrapolated after reading.

Attempt and successful-read timestamps describe HA's observation of the
request. They are separate from the controller-reported calendar/clock fields and do
not establish its UTC offset, daylight-saving state or clock drift. This
action does not inspect a time synchronisation service or change the clock.

## Cache, failures and schedule boundaries

The response includes read status, quality, attempt and successful-read times,
safe errors, the six raw fields, nullable components, `local_datetime`, unknown
timezone and issues. A completed read returns an `updated` or `error`
diagnostic snapshot; validation and cooldown rejection are action errors.
Before a successful sample exists, its successful-read time remains unknown.

Clock freshness is independent of ordinary group polling. A failed optional
clock read does not change climate availability or group-state freshness.
After an error or expiry, historical raw fields and their sample time can
remain cached, while all typed components and `local_datetime` become
unknown. A successful XML response with unknown fields does not invent a valid
clock. Downloading diagnostics exports the existing cache without new I/O.

A clock request and `ae200.read_today_schedule` are separate operations. They
are not an atomic snapshot: the controller can cross midnight or its clock can
change between reads. Reading both does not bind a `TodayList` result to a
verified date, establish execution order or justify UTC calendar events or a
next-run prediction. The today snapshot keeps its own sample time and quality.

Static evidence establishes the six-field builder and conservative editor
ranges. One minimal live websocket GET returned a single Clock record with
all six raw fields. The parser produced matching typed components and a valid
offset-free local datetime without issues. This verifies that bounded protocol
read on the pilot controller. The deployed HA action has also passed
acceptance: its fresh successful response matched the downloaded cache
exactly, including raw fields, components, sample times and unknown timezone.

That response does not establish clock accuracy, timezone, daylight-saving
rules, synchronisation, drift or behaviour on other firmware. Private responses,
exact sample values and installation details remain outside the public
repository.
