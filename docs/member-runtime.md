# On-demand member daily runtime diagnostics

Version `0.2.1b1` adds the response-only `ae200.read_member_runtime` action.
Deployed HA acceptance has passed for one explicitly selected member and day
on both `FAN_TIME` and `THERMO_TIME`.

The diagnostic reader targets one topology-validated IC member,
one explicitly selected Gregorian date and one fixed runtime data name.
It preserves a controller-reported raw sequence and can project recognised
values into 48 nullable half-hour buckets. It creates no entity, daily total,
cross-member aggregate or Home Assistant statistics.

One minimal live `FAN_TIME` request returned one record containing 48 literal
zero tokens. Deployed HA actions subsequently returned fresh successful
`FAN_TIME` and `THERMO_TIME` snapshots, each with one record and 48 literal
zero tokens, matching their cached selectors exactly. This establishes the
request, sequence and HA/cache path for those samples. It does not establish
non-zero runtime, enabled history collection, licence validity, measured idle
time or the physical meaning of zero.

## One explicit member, day and runtime name

The action requires all four fields:

| Selection | Boundary |
|---|---|
| `config_entry_id` | One explicitly selected loaded integration entry |
| `address` | One canonical address 1–250, already allowed by validated IC membership |
| `date_iso` | Strict Gregorian `YYYY-MM-DD` within the pilot's 1970–2037 input window |
| `data_name` | Exactly `FAN_TIME` or `THERMO_TIME` |

An address range alone does not prove membership. Unknown, conflicted,
unconfigured or non-IC identities must be rejected before I/O. A group's first
member is not a substitute for every member or a group-wide runtime sample.
The client must validate membership; the pure builder cannot establish it.

The runtime allowlist uses verified `Model=IC` membership. It does not apply
the explicitly empty `SubModel` gate used by the separate local-restrictions
write action. The daily-channel grammar is not a submodel-specific branch;
actual HH/EOA hardware and history collection remain subject to their own
live acceptance.

The year window is a conservative local pilot policy. It is not a verified
endpoint history range, retention period, backend capacity or evidence that
records exist on a selected date. The controller clock's settings window
does not establish this endpoint's historical limits.

The action requires a response. A script can use:

```yaml
action: ae200.read_member_runtime
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  address: 1
  date_iso: "2026-01-01"
  data_name: FAN_TIME
response_variable: member_runtime
```

Replace the example with the intended loaded entry, configured member and
explicit date. There is no default date or automatic choice of controller,
member or second channel. The requested date is independent of HA's current
date and the controller-clock diagnostic.

The following request is schematic, with a generic example selector:

```xml
<Packet><Command>getRequest</Command><DatabaseManager>
  <EnergyManagement>
    <IcTrendDList Address="1" Year="2026" Month="1" Day="1" DataName="FAN_TIME" />
  </EnergyManagement>
</DatabaseManager></Packet>
```

The request has one address, one date and one data name. Month and day use the
canonical unpadded request form. It contains no `Group`, `MultiGroup`, address
wildcard, runtime-value wildcard or second selector. Empty, rejected or unknown
`FAN_TIME` data must not trigger an automatic `THERMO_TIME` request or a scan
of adjacent days. A second data name requires a separate explicit invocation.

## Retained fields and strict response structure

The expected structure is a strict `getResponse` with one direct
`DatabaseManager/EnergyManagement/IcTrendDList`, matching all five requested
selector attributes exactly. At most one direct leaf `IcTrendDRecord` is
accepted. Additional records are rejected rather than selected, merged or
summed. `Group`, `MultiGroup`, misplaced selectors, ambiguous lists, nested
records, ERROR payloads and non-whitespace mixed content reject the sample.

Only the requested runtime field is retained:

| Data name | Selected raw field |
|---|---|
| `FAN_TIME` | `FanTimeData` |
| `THERMO_TIME` | `ThermoTimeData` |

Unrequested energy, temperature, names and other metadata are discarded.
The pure `MemberRuntime` result contains the selected address, ISO date,
data name, record count, raw string, raw tokens, buckets and issues.
`raw_attributes()` returns an independent dictionary for the selected field.
The action exports only safe selectors, sample status and times, quality,
errors, retained raw evidence, record count, issues and bucket projections.
Its unit label is `min`, interval length is 30 minutes and timezone is unknown.
Those labels describe intended controller-reported history, not validated
physical measurement.

Zero records or an absent selected attribute can produce an unknown sample
with an issue. They are not converted into a zero-runtime sequence or an
affirmative statement of endpoint support. The parser does not silently
borrow another runtime field.

## Raw sequence and 48 nullable buckets

The official IC-member daily runtime channel splits the selected field on commas and
displays 48 half-hour intervals in minutes. The diagnostic projection retains
each original token and its position:

| Index | Controller wall-clock interval |
|---|---|
| 0 | `00:00`–`00:30` |
| 1 | `00:30`–`01:00` |
| … | Following half-hour positions |
| 47 | `23:30`–`24:00` |

These are local interval labels, not timezone-aware timestamps. The final
`24:00` is an interval end label; it is not an inferred UTC event or a promise
about daylight-saving transitions. The sample date remains the explicit
request date. No controller-clock request, HA timezone assignment, DST
adjustment, clock drift calculation or date substitution accompanies it.

Buckets are produced only for exactly 48 comma-separated tokens. Another
token count retains the raw sequence and tokens with an
`unexpected_token_count` issue and no typed buckets. Nothing is truncated,
zero-filled, padded or shifted to manufacture a complete day. The chart UI's
own extra plotting marker is not a 49th wire bucket or total.

Each bucket contains its index, local start/end labels, raw token and nullable
`minutes`; the HA export names this field `reported_minutes`. Numeric projection requires a complete unsigned ASCII decimal
token, without trimming, prefix parsing, signs, exponents, infinities or
trailing text. Unfamiliar or missing tokens, including empty, `*` and `@m`,
become unknown with issues. The implementation's handling of those tokens
does not establish that they have appeared as wire sentinels in live data.

The typed 0–30-minute range is a defensive client policy for a nominal
half-hour slot, not a verified vendor wire limit. An out-of-range or
unrepresentable value becomes unknown while its raw token survives; values
are never clamped. Decimal precision and permitted wire ranges still need
representative evidence.

A numeric zero means the controller returned a token which projects to zero.
Its measurement validity remains unverified. An all-zero day cannot
distinguish idle history from uncollected, default or placeholder data. A
fresh successful read does not turn it into a verified zero operating time.

## Protective limits and failure isolation

The current pure parser retains shared protections: at most 1 MiB XML, bounded
tree/depth/attributes and 512 characters per attribute value. The zero-or-one
record rule, raw-value limit and calendar window are local pilot boundaries,
not server capacity or retention guarantees. Oversized or ambiguous payloads
are rejected rather than truncated. A longer valid-looking sequence needs
separate evidence and a deliberate bounded-parser change.

The client read reuses normal request locking and pacing and a
controller-wide 15-second cooldown measured from completion across selectors.
There is no startup query, periodic trend poll, automatic retry, day sweep,
licence activation or write. An optional failure must not change current
climate/member availability, renew their freshness or confirm a command.

Up to 32 member/date/data-name samples are retained in the controller's local
client cache using least-recently-used eviction. This is a memory-retention
policy, not the controller's storage capacity, date retention or query bound.
Eviction does not trigger a refill or a background request.

Each member/date/data-name sample retains independent attempt and
success times, quality and safe errors. On failure or expiry, historical raw
evidence can remain available with its observation time while typed minute
claims become unknown. Downloading cached diagnostics must not issue I/O.
A fresh quality means a recent successful query of the selected historical
day; it does not mean the member is currently running or that logging is valid.

A completed read returns an `updated` or `error` diagnostic snapshot.
Cooldown rejection raises an HA action error and issues no additional query.
Invalid entries, addresses, dates or data names are validation errors. The
action does not conceal missing data as a successful zero-filled day.

Licence `active` does not prove endpoint, hardware, history or measurement
support. A rejected or unavailable query remains an error or unknown; the
reader does not change licences, infer a substitute value or try another
endpoint automatically.

## Evidence and remaining work

The saved official request builder and graph decoder establish the address/
date/data-name syntax and the intended IC-member 48-slot display grammar.
The selected protocol sample and deployed HA actions confirm a one-record,
48-zero sequence for `FAN_TIME` and `THERMO_TIME` on one member and day.
Both action responses were fresh, had no issues and matched their cached
snapshots exactly; bucket positions, labels and nullable projections were
checked. Existing entity identifiers, original local permissions, off states
and the enabled scheduled automation were preserved.

This does not establish non-zero durations, collection validity, resets,
partial-day or DST-day behaviour, other hardware, physical measurement or a
valid statistics source. Pure parser tests, accepted request shapes and
HA/cache acceptance remain distinct evidence from measurement validation.

This diagnostic does not calculate a daily sum, create a duration/statistics
entity, expose an increasing counter, combine multiple members or derive
`hvac_action`, current FanStatus/ThermoStatus, compressor operation, power,
energy or COP. Those features require independent provenance, units,
collection-validity and aggregation evidence. Private addresses, selected
site dates, raw captures and inventories remain outside public documentation.
