# On-demand raw controller daylight settings

Version `0.2.1b6` adds the response-only `ae200.read_daylight_settings` action.
It reads one controller's `SummerTime` configuration and retains eleven
allowlisted raw fields with independent read/cache metadata. Deployed HA
acceptance has passed for one selected controller and an immediate-repeat
cooldown check.

The bounded purpose is to inspect reported clock-adjacent configuration without
adjusting the clock. The result does not establish that daylight adjustment is
enabled, currently active, correct or applied to the separate Clock response.
It provides no timezone, UTC offset, evaluated transitions or adjusted datetime.
No clock/rule write, automatic query, periodic poll or new entity is added.

## One explicit controller

The action requires only `config_entry_id`, selecting one loaded AE-200 entry.
It does not default to the only configured controller or fall back to another
entry. Unknown entries, wrong integration domains and unloaded entries fail
before a client read.

```yaml
action: ae200.read_daylight_settings
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
response_variable: controller_daylight_settings
```

There are no group, address, date, country, enable, timezone, refresh or Clock
arguments. All extra fields are rejected. Each explicit invocation requests
only the eleven known fields through the normal shared client transport:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <SummerTime CountryCode="*" Month1="*" Day1="*" Hour1="*" Minute1="*"
              ShiftMin1="*" Month2="*" Day2="*" Hour2="*" Minute2="*" ShiftMin2="*" />
</DatabaseManager></Packet>
```

The wildcards request these known attributes on a single controller record.
They do not enumerate groups, members, dates or undocumented fields. No
automatic Clock read follows this request, or vice versa.

## Eleven fields, retained as strings

| Field set | Retained fields |
|---|---|
| Reported configuration selector | `CountryCode` |
| First indexed fields | `Month1`, `Day1`, `Hour1`, `Minute1`, `ShiftMin1` |
| Second indexed fields | `Month2`, `Day2`, `Hour2`, `Minute2`, `ShiftMin2` |

Only present allowlisted attributes survive. Missing fields remain absent
and receive `missing_attribute` issues identifying the field; no default is
invented. A present empty string is different from a missing field.

Raw means XML-decoded attribute strings, rather than original response bytes.
Normal XML attribute-value and character-reference handling still applies,
including literal line-break normalisation. The diagnostic adds no further
trimming, numeric conversion, alias mapping or default substitution.

Present empty strings, `*`, negative strings and unfamiliar future tokens
remain exactly those strings. No integer, boolean, country-name, unit,
timezone or transition-date interpretation is applied. The official client
uses literal `CountryCode="000"` for its custom selector; it is not an empty
token or integer zero. Other country tokens are vendor lookup keys, not
verified ISO countries or IANA timezone identifiers.

A record with no recognised attribute is rejected. A record containing all
eleven attributes as empty strings can be a structurally accepted raw sample;
it does not mean enabled=false, valid rules or no daylight adjustment.
A partial response with recognised attributes can be updated/fresh while
reporting missing-field issues. Freshness describes a successful recent read,
not the configuration's correctness or application.

## Strict structure and safe snapshot

The parser requires one strict `getResponse` with one direct leaf
`DatabaseManager/SummerTime`. Duplicate records, misplaced attributes, nested
content, group/member identities, ERROR responses and mixed non-whitespace
text reject the entire sample. Shared payload, XML, tree, depth and attribute
limits apply before field filtering. Foreign attributes are discarded rather
than included in diagnostics.

The cache-only snapshot has seven top-level fields:

| Field | Meaning |
|---|---|
| `read_status` | Latest invocation status: unknown, updated, error or cooldown |
| `quality` | Unknown, fresh or stale read quality |
| `last_attempt` | Attempt observation timestamp, or null |
| `last_successful_read` | Successful sample observation timestamp, or null |
| `error` | Safe error type/code, or null; no exception messages or connection details |
| `raw_attributes` | Only present allowlisted raw strings |
| `issues` | Structural issue kind and field key |

Each export returns independent containers. Downloading diagnostics exports
this sample under `daylight_settings` without network I/O or a new attempt.
Before any explicit read, it is unknown with an empty raw dictionary.

There are deliberately no derived keys for enabled, current_dst, timezone,
UTC offset, unit, effective dates, local_datetime or numeric month/day/shift,
including null placeholders. The source fields do not justify those claims.

## Independent cooldown and failure handling

This action has its own controller-wide lock and 15-second cooldown measured
from the completion of a started attempt, including slow, failed and cancelled
attempts. It also uses the shared I/O lock and normal request pacing.
Its cooldown is independent of Clock and the three daily-history actions;
those existing readers and their limits remain unchanged.

A cooldown raises an HA action error without another query or changing the
sample and its observation times. Completed `updated` or `error` reads return
the safe snapshot. Partial raw evidence is retained with its issues rather
than concealed or converted into defaults. An invalid result is an action
error, not a fabricated successful configuration.

On failure or cancellation after an attempt starts, prior raw fields, issues
and successful-read time remain available as stale. No previous successful
sample means unknown quality. A cancelled attempt records a safe cancellation
error and propagates cancellation without retry. Cancellation while waiting
for the action lock makes no new attempt or cache change. An age-expired
sample keeps historical raw evidence with its time rather than being labelled
current.

These optional reads do not renew group/member freshness, change climate
availability, modify pending commands or last-command status, refresh entities
or alter automations. No extra fallback or retry is invented by this reader.

## Clock relationship and evidence

The controller's official web client requests Clock and SummerTime separately
and displays the reported Clock fields without a demonstrated SummerTime
adjustment in the reviewed path. That does not establish the firmware's Clock
time basis or whether the returned fields already include daylight adjustment.
The integration adds no shift, timezone conversion or transition arithmetic.

Clock and SummerTime reads are not atomic. A later explicit Clock action can
be independently sampled, but the two caches must not be merged into a claimed
current DST state or adjusted timestamp. They also do not establish the time
basis of historical graph positions or schedule execution.

One prior minimal live GET returned the eleven requested fields, confirming
that selected request/response shape. It contained no enable, current-DST or
timezone field. This does not validate active transitions, clock accuracy,
rule correctness, all firmware or other controllers. Private addresses and
configuration values remain outside public documentation.

The deployed HA integration and library loaded `0.2.1b6`. Its action returned
updated/fresh with all eleven XML-decoded raw fields matching cached diagnostics
and the prior protocol sample. An immediate repeat within 15 seconds raised
the cooldown error. The cache then recorded `read_status: cooldown`, while
the other six snapshot fields, sample and attempt/success times were unchanged.
Existing identifiers, operating settings, permissions and automations were
preserved. No Clock or rule write, derived timezone or current-DST claim was
introduced. Partial, error and cancellation cases retain offline evidence.

See
[controller-clock diagnostics](controller-clock.md) for the separate six-field
reader, [diagnostic state quality](diagnostic-state-quality.md) for cached HA
flags and [capability boundaries](capabilities.md) for wider evidence limits.
