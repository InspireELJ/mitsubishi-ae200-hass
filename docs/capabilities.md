# Capability inventory and development roadmap

This document separates implemented Home Assistant features from capabilities
found in Mitsubishi Electric documentation or the controller's own web client.
It does not claim that every AE-200 installation supports every listed feature.
Equipment, controller software, group configuration and optional licences all
affect what is available.

Evidence was reviewed on 16 October 2026. Private controller addresses, room
names, device inventories, credentials and captured payloads are deliberately
excluded. Keep deployment evidence outside this public repository.

## Evidence and implementation status

Evidence levels describe what has been established, rather than an increasing
promise of support:

| Level | Meaning | What it does not establish |
|---|---|---|
| Manufacturer documentation | An official Mitsubishi Electric manual describes a function, its conditions or its operation. | Availability on a particular installation, or its XML wire format. BACnet documentation describes BACnet, not an interchangeable XML API. |
| Static protocol evidence | The official web client contains a model, request builder, parser or control algorithm for the function. | A populated response, effective write or working hardware feature on every controller. |
| Live read evidence | A bounded read from a controller returned a meaningful value or a capability flag. | A successful control operation, physical behaviour, or support on another device family or firmware. |
| Live control evidence | A deliberately chosen operation was acknowledged, read back and checked, with the original state restored where appropriate. | Reliability across other equipment and all operational conditions. |

Implementation status has a separate meaning:

- **Implemented**: present in the `0.2.0b10` source. Individual device support
  and end-to-end acceptance still apply. This is not a claim that every row has
  passed a live control test or that an installation has loaded this version.
- **Shared library only**: protocol support exists without a complete native HA
  entity implementation.
- **Planned**: a candidate requiring implementation, additional protocol
  evidence or a suitable device. It is not currently a supported HA feature.

## Scope of 0.2.0b10

The response-only `ae200.set_local_restrictions` action applies a strict
boolean patch to one explicitly selected configured `IC` / `NORMAL` / `OLD`
group. It covers local power, mode, temperature and filter-reset restrictions.
Omitted functions retain their freshly read effective settings. These are
local AC operating restrictions, not account permissions, hardware capability
flags or gates on central HA climate control.
See [per-function local restrictions](local-restrictions.md).

The shared write lock covers fresh prerequisite reads, a complete five-field
baseline, effective-state planning and one grouped five-field command. All
three re-read stop sources must be known false. Effective no-ops send no write.
Normalised targets avoid reviving dormant `CHK_ON` values hidden by `PERMIT`.
The action has a controller-wide 15-second cooldown from completion and does
not alter the legacy summary switch or existing identifiers.

Each invocation returns its own report with baseline, expected raw target,
status, errors and at most one independent post-send sample. Acknowledgement
and matching readback remain separate; an uncertain command is not replayed
or compensated. `after` is an observation with a sample time, not a continuing
claim about current permissions. A no-op has no post-sample. No new persistent
transaction cache, entity, automatic action or public raw-restore API is added.

HTTP write timeouts, lost responses and malformed acknowledgements can be
uncertain after sending begins. Redirects do not resend a POST, explicit
controller rejection remains failed, and cancellation is not returned as
success. A matching readback cannot replace a missing acknowledgement.

One idle ordinary group passed a local filter-reset restriction change and
private exact five-field restoration with acknowledgement/readback checks.
Other effective restrictions and operating settings were preserved. HA action
deployment acceptance remains pending, and physical wall-remote buttons were
not tested. A shared client lock cannot prevent external controller
writers; no server-side compare-and-set or rollback guarantee is claimed.

## Weekly-pattern scope introduced in 0.2.0b9

The response-only `ae200.read_weekly_pattern` action explicitly selects one
integration entry, configured group, season 1–5 and pattern 1–7. One minimal
`ScheduleControl/WPatternList` GET requests only that combination. There is no
automatic traversal of all 35 season/pattern slots, startup query, periodic
schedule poll, new entity, pattern activation, schedule write or automation
change. See [weekly-pattern diagnostics](weekly-pattern.md).

An 18-field allowlist preserves raw record values. The official editor's
24-row limit supplies a conservative receive protection: oversized responses
fail rather than being truncated. This is not proof of server capacity or a
server-side pagination bound. Invalid selectors, mismatched directories and
controller errors must not become successful single-pattern snapshots.

Each selected combination has independent sample times, errors and freshness.
An optional failed read retains historical raw records with their age and
quality; it does not alter climate availability or renew another pattern's
sample. The controller-wide 15-second cooldown is measured from completion
across all weekly-pattern selectors. Downloading diagnostics adds no I/O.

An empty action is no control of that field; wildcard and unfamiliar tokens
remain unknown. Plans, indices and row order do not prove execution order or
physical operation. A selected season/pattern slot does not establish its
activation, weekday, date, exception handling or precedence. Separate Clock,
TodayList and weekly-pattern reads are not atomic and do not establish a
verified calendar date or next execution.

Static evidence establishes the builder and record model. One minimal live
websocket GET for one group/season/pattern combination returned zero records
and passed strict parsing without issues. Deployed HA acceptance confirmed
a fresh successful empty action response matching its cached selector exactly,
with existing identifiers and automations preserved. Non-empty actions,
activation and execution need their own
evidence. An accepted empty response does not establish that other slots are
empty or scheduling is disabled.

The existing default-disabled schedule diagnostic keeps its `schedule_on`
identifier, original `Schedule` state source and creation rule. Its name is
corrected to *Controller schedule flag*. Exact `Schedule=ON/OFF` remains its
state; it is not renamed internally or switched to a different data source.
The separate `schedule_available` attribute interprets exact
`ScheduleAvail=ON/OFF` only while the entity is available. Missing, unfamiliar,
stale or failed data, or a failed coordinator update, makes this attribute
unknown. `raw_schedule_flag` and `raw_schedule_available` expose only limited
safe raw tokens. These are cached attributes, not new entities or requests;
neither flag establishes pattern activation, today's records or execution.

## Clock scope introduced in 0.2.0b8

The response-only `ae200.read_controller_clock` action reads six local-clock
fields for one explicitly selected integration entry. It retains independent
attempt and successful-read times, errors and freshness, with a controller-wide
15-second cooldown from completion. There is no setup read, periodic clock
poll, new entity, clock write or time synchronisation.
See [controller-clock diagnostics](controller-clock.md).

Only a fresh, successful sample representing a valid Gregorian date and time
produces `local_datetime`, without an offset. Expired or failed samples retain
historical raw values while the typed datetime becomes unknown. No UTC offset,
daylight-saving rule or clock drift is inferred. One minimal live Clock GET
returned all six fields and passed strict parsing without issues; HA action
deployment acceptance also confirmed a fresh successful action response
matching cached diagnostics exactly. Existing entity identifiers and
automations were preserved.
A clock read and a separate `TodayList` read are not atomic and do not establish
that a schedule snapshot belongs to a verified date.

## Today-schedule scope introduced in 0.2.0b7

The response-only `ae200.read_today_schedule` action reads one configured
group's `ScheduleControl/TodayList` on explicit request. It retains an independent
snapshot, sample times, errors and raw plan fields. There is no setup query,
periodic schedule poll, new entity, schedule write or automation change.
See [today-schedule diagnostics](today-schedule.md).

Only the empty-list response structure has live acceptance. Non-empty records
are covered by offline fixtures but have not been accepted on live equipment.
The parser uses the official web editor's 24-record limit as a receive boundary,
rejecting an oversized response rather than silently truncating it. That
boundary is not proof of server capacity or a server-side record-count limit.

Hours and minutes can be validated, but the response supplies no verified date
or timezone from which to compute UTC events, execution order or a next run.
Empty actions mean no control of that field; wildcard and future action tokens
remain unknown. An empty list is zero returned records, not proof that
scheduling is disabled or unsupported. Plans do not establish running state.

## Control-context scope introduced in 0.2.0b6

The existing local-remote switch now has four cached effective prohibition
attributes for local power, mode, temperature and filter reset. Its identifier,
legacy `RemoCon` on/off interpretation and single-field writes are preserved.
The on state does not establish that every local function is prohibited, and
the b6 attributes themselves remain read-only. A separate scoped write action
is introduced in b10. Effective attributes become
unknown if the switch is unavailable because the group is stale, its read
failed or the coordinator update failed; old cached values do not establish
current local permissions.

The response-only `ae200.read_control_context` action requires an explicit
integration entry and configured group. It performs two sequential minimal
reads: global `SystemData.AllTurnOff`, then group `TurnOff` and `BACnetTurnOff`.
The controller web client labels these as emergency-stop and energy-saving
stop flags; they are not measurements of physical running state or proof of a
particular cause. Independent errors and freshness allow partial responses.
The action has a controller-wide 15-second cooldown from completion, adds no
automatic query or periodic polling, and does not gate central HA commands.
See [control-context diagnostics](control-context.md) for the interpretation.

## Metadata scope introduced in 0.2.0b5

The native integration attempts a minimal `SystemData` read and a separate
`FunctionControl/FunctionList` read once at setup or reload. Cached diagnostics
retain only the controller's display model and version strings, and function
index, raw status, raw time-limit token and a limited active-state interpretation.
The reads have independent errors, reuse the existing request pacing and add
no periodic metadata poll. See [controller metadata](controller-metadata.md).

Live reads confirmed the expected structures, without establishing that any
licensed XML endpoint or equipment feature works. An active licence is not a
hardware capability, a valid measurement or proof of endpoint availability.
No keys, activation, fault-history scan or new licensed control is implemented.

If a websocket write loses its connection or response after sending starts,
the result is `outcome_unknown` and the write is not replayed over HTTP. A later
bounded state read can show what the controller currently reports, but does
not supply the missing acknowledgement. This condition remains distinct from
an acknowledged command awaiting confirmation or an explicit rejection.

## Membership scope introduced in 0.2.0b4

The native integration now reads configured membership once after the first
successful group refresh. Its on-demand `ae200.read_member` action reads one
allowlisted indoor member, with independent freshness and a controller-wide
15-second cooldown. There is no automatic member polling or new operating-state
entity. See [member diagnostics](member-diagnostics.md) for the action and its
limits. The websocket transport now waits through bounded notifications and
KeepAlive frames for the actual request response.

Live evidence includes a configured directory and one member's complete
ErrorSign, AlarmCode, ThermoStatus and FanStatus response. The pilot firmware
rejected DefrostStatus; default requests omit it. Status strings remain raw,
and no hvac_action or compressor/energy inference is introduced.

## Climate scope introduced in 0.2.0b3

The native HACS integration loads a bounded, optional capability table at setup
or reload. For verified ordinary indoor AC groups it narrows operating modes,
fan choices and vertical vane positions, and uses mode-specific temperature
ranges and Celsius setpoint steps. Related mode, power and optional temperature
settings are validated before being sent together in one checked request. A
controller acknowledgement is still distinct from a confirmed state readback.

The release also adds two kinds of diagnostic entity, disabled by default:
the controller's last command status and each group's controller-schedule
flag where a recognised flag exists. Both use cached data and add no
controller requests. The schedule flag is read-only; this release neither
reads full schedule contents nor writes or replaces controller schedules or
HA automations.

The capability-based mode mapping is verified for ordinary `IC` / `NORMAL`
indoor AC groups using the legacy group representation. A known `NEW` group
requires different setpoint attributes. All temperature writes to a group
identified as `NEW` are rejected before any request is sent, including a
compound command which also asks to turn it on. Dual-setpoint and other `NEW`
group temperature control are not implemented. Such groups do not receive a
claimed verified mode list from this release, and their temperature-control
feature is hidden in HA.

One ordinary indoor AC group has passed live temperature, fan and fixed-vane
changes while off, then a brief compound fan-only/on command. Each change was
read back, and the original mode, temperature, fan, vane and off state were
restored and confirmed. This validates those selected operations on that group;
other vane positions and equipment families still require acceptance.

HA's entity-wide temperature bounds cover all selectable temperature modes so
that a combined mode/temperature service call can pass HA's initial validation.
The client then checks the requested mode's exact limits before writing. The
current mode's precise Celsius bounds are also exposed as state attributes.

If capability data is unavailable or not understood, monitoring remains usable
and legacy mode/fan choices remain as a compatibility fallback. Unknown vane
capabilities do not enable the new vane control. The shared library contains
the new capability parser and checked setters, but the MQTT add-on does not yet
load this capability table or expose native-HA feature parity. The new setup
behaviour and diagnostic entities described here apply to HACS.

## Capability matrix

| Area | Implementation status | Evidence available | Next acceptance requirement |
|---|---|---|---|
| Configured group discovery and names | Implemented | Static protocol and live reads of the configured group directory. | Preserve identifiers and user-assigned names when metadata is refreshed. |
| Power, operating mode and target temperature | Implemented; capability filtering and compound commands added in 0.2.0b3 | Existing XML setters; live state and capability reads; offline validation and compound-write contracts. | Test commands against a suitable group, check acknowledgement and readback, and restore its original state. Known `NEW` group temperature writes remain blocked. |
| Fan speed | Implemented; per-group choices added in 0.2.0b3 | Live `3STAGES` and `4STAGES` capability responses; official client enumerations and transition algorithms. | Test a representative supported change and preserve unfamiliar read values in diagnostics. Missing capability data retains legacy choices. |
| Vertical vane / air direction | Implemented in native HA climate in 0.2.0b3 | Official client enum and capability algorithm; live enabled capability, stage count, auto and swing flags. | Verify a supported position or swing operation and restoration. The HA swing-mode selector also carries fixed vertical-vane positions; it does not control a separate horizontal vane. |
| Temperature limits and Celsius step | Implemented in 0.2.0b3 for the supported AC mapping | Official client range mapping and step algorithm; meaningful live range and precision flags; offline range/step contracts. | Test a valid half-degree setpoint where supported. Missing range data cannot establish a new hardware limit. |
| Return-air temperature | Implemented | Live measurements; fault sentinel handling in the library. | Continue returning unknown for invalid or fault values. |
| Humidity and outdoor temperature | Implemented conditional sensors | Protocol fields exist; field presence alone is insufficient evidence of a sensor. | Require plausible measurements and appropriate device capabilities. Zero humidity and empty temperature fields are not measurements. |
| Filter and fault signs | Implemented | Live flag values and existing problem sensors. | Retain unknown for unrecognised values; do not clear service signs as part of discovery. |
| Controller health and per-group freshness | Implemented | Cached success times, failed groups and offline failure tests. | Confirm fresh, degraded and offline behaviour without presenting old samples as new observations. |
| Cached diagnostics and command confirmation | Implemented; capability details and optional command-status entity added in 0.2.0b3 | Cached exporter and checked write acknowledgement/readback implementation. | Keep diagnostics free of additional controller requests and distinguish acceptance from confirmed state. The new status sensor is disabled by default. |
| Controller display model and versions | Cached read-only diagnostics in 0.2.0b5 | Official `SystemData` builder and live minimal response structure. | Preserve raw version strings and unknown fields; exclude serial numbers and network settings. This is a setup snapshot, not a periodic firmware monitor. |
| Licence-status summary | Cached read-only diagnostics in 0.2.0b5 | Official `FunctionList` model and live directory response. | Verify each desired endpoint and connected hardware independently. Active licence status alone proves neither capability nor usable data; raw time limits are not computed expiry dates. |
| Local remote prohibition summary | Implemented legacy switch; clarified in 0.2.0b6 | Existing `RemoCon` setter and state parser. | Preserve the old identifier and single-field write. Its on state alone does not prove that every local control is prohibited. |
| Per-function local remote prohibitions | Read-only effective attributes from 0.2.0b6; scoped four-function write action in 0.2.0b10 | Official effective-state rule, one live filter-reset restriction change and private exact five-field restoration; HA action pending. | Accept the HA action, preserve unrequested effective values and exact restore evidence, and keep ACK/readback/uncertainty distinct. Other functions and physical wall-remote behaviour need separate acceptance. No central-command permission or CAS claim. |
| Global and group stop context | Explicit read-only diagnostic action in 0.2.0b6 | Official emergency-stop and energy-saving labels, strict minimal requests and live response structures. | Keep global/group freshness and errors separate. Stale typed values remain unknown, and no physical operation, cause, stop clearing or BACnet availability is inferred. |
| Group members, M-NET addresses and equipment family | Implemented as cached read-only diagnostics in 0.2.0b4 | Official `MnetGroupList` model and live configured membership. | Broaden representative multi-member hardware acceptance before creating additional devices or changing entity relationships. |
| Detailed equipment identity and capacities | Planned | Address-level models include model, capacity, location and system codes. | Confirm which codes are populated and obtain an authoritative decoding before presenting a model or capacity as fact. |
| Actual Thermo-ON, fan and defrost state | Raw on-demand member diagnostics in 0.2.0b4; operational entities planned | Complete live ThermoStatus/FanStatus strings and a fan-only test. DefrostStatus rejected by pilot firmware. | Validate XML value semantics and group aggregation. Power on and a cooling mode do not prove active cooling or compressor operation. |
| Active fault codes and fault history | Planned | Official address-level alarm fields and `AlarmList` request/parser models. | Confirm active versus recovered records, timestamps, bounded history size and code interpretation. Keep reset/clear operations separate from monitoring. |
| Controller schedule flag and availability | Existing read-only diagnostic from 0.2.0b3; label corrected and separate availability attribute added in 0.2.0b9, disabled by default | Cached `Schedule` and `ScheduleAvail` values, with independent raw and nullable availability attributes. | Preserve the existing identifier and state source. Unknown or unavailable data stays unknown; neither flag proves activation, execution, today's records or next-run timing. No schedule write is implemented. |
| Single-group today schedule | Explicit read-only diagnostic action in 0.2.0b7 | Official `TodayList` model; empty live response; non-empty offline fixtures only. | Accept non-empty plans on suitable equipment before interpreting execution. Preserve unknowns and sample times; no next-run calculation or schedule write. |
| Controller local clock | Explicit read-only diagnostic action in 0.2.0b8 | Official six-field model, one minimal live GET and deployed HA action/cache acceptance. | Preserve local fields and unknowns. Validate Gregorian date/time before producing an offset-free local datetime; no timezone, DST, drift or atomic schedule-date inference. |
| Single-group weekly pattern | Explicit read-only diagnostic action in 0.2.0b9 | Official `WPatternList` builder, one single-selector empty live response and deployed HA action/cache acceptance. | Preserve unknowns and independent freshness, and verify non-empty actions separately. No automatic 35-slot scan, activation, execution, clock-date binding or server-capacity claim. |
| Yearly patterns, season activation and schedule editing | Planned | Official season, annual-pattern and yearly-directory models. | Establish separately bounded request scopes and activation/precedence semantics. A today or single weekly-pattern sample is not the complete controller schedule. Design editing, backup and recovery separately after read-only acceptance. |
| Runtime and Thermo-ON time | Planned | Official runtime/account and trend models. | Confirm units, counter resets, bucket boundaries and aggregation before creating HA statistics. |
| Electrical power, energy, COP and apportionment | Planned | Official energy models and documented licensed functions. | Establish installed metering and licences, data provenance, units and reset semantics. Keep measured, estimated and apportioned energy distinct. |
| Lossnay and ventilation | Shared library only for partial parsing; complete HA control planned | Official ventilation capability flags and enum algorithms. | Obtain meaningful data from a ventilation installation. AC placeholder fields cannot establish Lossnay support. |
| ATW, hot-water heat pumps and chillers | Partial legacy classification/sensors exist; verified device-specific HA support planned | Official separate operating modes, temperature ranges and system models. | Validate a suitable device before claiming device-family control. The existing generic climate entity is not proof of a verified hydronic mode mapping. |
| AI/PI measurement, DIDO and interlocked equipment | Planned | Official product documentation and separate web-client measurement/I/O models. | Establish connected hardware, channel types, units and permissions. Implement per-device models rather than treating every record as an AC group. |
| Expansion and multiple controllers | Implemented independent integration entries; richer topology planned | Official controller expansion configurations and multi-controller client models. | Verify how parent/expansion addresses are represented and retain controller-scoped identifiers. Do not enlarge group-number limits speculatively. |
| BACnet | Planned optional investigation, not the current transport | Official documented interface requiring the relevant licence and configuration. | Confirm an explicit need and installed licence. Do not enable or reconfigure a building's BACnet network merely to discover XML capabilities. |

## Verified climate capability semantics

The official web client calls the per-group capability model `MnetGroupKind`.
It is queried separately from changing operating state. In 0.2.0b3, the HACS
coordinator makes one bounded capability request for already discovered groups
at setup or reload, through the same serialised request path and spacing limit
as normal reads and writes. There is no periodic capability poll or separate
metadata-refresh entity. Rejected reads are recorded as optional capability
errors rather than stopping normal monitoring.

### Operating modes and precision

Use `AutoModeSW` and `AutoModeSWEx` to distinguish unavailable, single-setpoint
and dual-setpoint automatic operation. `DryModeSW` and `FanModeSW` describe
those modes. A stored automatic-mode temperature range does not override a
disabled automatic-mode capability.

The official client's `modeToRangeTag_` establishes this mapping:

| Climate mode | Capability range fields |
|---|---|
| Cool and dry | `TempRange1Low1`, `TempRange1High1` |
| Automatic, single setpoint | `TempRange1Low2`, `TempRange1High2` |
| Heat | `TempRange1Low3`, `TempRange1High3` |

The same numeric suffixes are reused for other device families with different
mode names. They must not be applied to hydronic or chiller equipment using an
unqualified AC mapping. Dual-setpoint operation also requires its deadband and
the correct setpoint attributes; storing `SetTemp1` through `SetTemp5` does not
by itself establish a supported dual-setpoint HA implementation. The
`0.2.0b3` legacy `SetTemp` writer therefore rejects all temperature settings
when the group's known capability metadata says `GroupType=NEW`.

For Celsius setpoint control, `TempDetailC=ENABLE` enables **0.5 °C steps** in
this official client's `S03050CtrlSetTempView`. Its initialisation disables
fractional adjustment when that flag is not enabled, and its
`changeTempConvertFarenheit` method uses a 0.5 increment when fractional
adjustment is enabled. A disabled flag normally gives 1 °C adjustment from an
integer value. Room-temperature display precision is handled separately and
must not be mistaken for a setpoint step. `TempDetail_C` is present in the
model but no separate control semantics have been established for it.

### Fan stages

The official client's stage transitions establish the following ascending
protocol values. The labels in HA may differ, but the protocol values and order
must remain intact.

| Capability | Ascending manual protocol stages |
|---|---|
| `4STAGES` | `LOW`, `MID2`, `MID1`, `HIGH` |
| `3STAGES` | `MID2`, `MID1`, `HIGH` |
| `2STAGES`, no extra-low flag | `MID2`, `HIGH` |
| `2STAGES`, extra-low enabled | `LOW`, `MID2`, `HIGH` |
| `2STAGES`, extra-low and extra-high enabled | `LOW`, `MID2`, `MID1`, `HIGH` |

Add `AUTO` only when `FanAutoSW=ENABLE`. `MID` is retained in the library's
historical vocabulary, but the inspected official client does not use it as a
manual stage. Stage numbers are not calibrated percentages or airflow rates.

### Vane positions

| Capability | Fixed positions, from horizontal towards vertical |
|---|---|
| `4STAGES` | `HORIZONTAL`, `MID1`, `MID2`, `VERTICAL` |
| `5STAGES` | `HORIZONTAL`, `MID0`, `MID1`, `MID2`, `VERTICAL` |

Vane control requires `AirDirectionSW=ENABLE`. Offer `AUTO` only when
`AirAutoSW=ENABLE`, and `SWING` only when `SwingSW=ENABLE`. The official client
displays position icons rather than specifying physical angles. Historical
`DOWNWARD` vocabulary has not been confirmed for this client and must not be
silently substituted for `MID0`, `MID1` or `MID2`.

`DriveItem`, `ModeItem`, `SetTempItem`, `AirDirItem` and `FanSpeedItem` are
local-remote prohibition settings. The official client treats administrator
authority separately when applying them. Do not use these settings as a
substitute for the capability table or hide a central controller's supported
hardware functions solely because local controls are prohibited.

## Development sequence

1. Accept the capability discovery, climate choices, mode-specific temperature
   validation, compound commands, vane control and cached diagnostic entities
   implemented in `0.2.0b3`. Keep the `ae200` domain, config entry and existing
   unique IDs unchanged. Treat deployment acceptance separately from source
   availability before widening the protocol surface.
2. Add read-only group membership and address-level status. Use this to develop
   fault detail and actual operating-state entities with explicit unknown
   handling. Define aggregation where several indoor units share one group.
3. Accept the controller identity and licence summaries added in `0.2.0b5` and
   the explicit today snapshot added in `0.2.0b7`, local-clock snapshot in
   `0.2.0b8` and single weekly pattern in `0.2.0b9`, then develop yearly-pattern
   and activation inspection and bounded fault history. Clock and schedule reads are separate samples. Refresh static metadata
   separately from operational state. A server-side history bound
   must be established before introducing a history request; local truncation
   alone does not bound controller work.
4. Implement runtime and energy only after confirming valid data and its
   semantics. Decide whether data belongs in a current sensor, a diagnostic
   export or a historical import rather than pushing every trend into a
   frequently polled entity.
5. Add device-specific ventilation, hydronic and I/O platforms with representative
   fixtures and hardware acceptance. Keep HACS and the MQTT add-on consistent
   where they share an implemented capability; document any remaining gaps.
6. Consider schedule editing and additional building-manager controls after
   read-only support is dependable. Each operation needs acknowledgement,
   readback, predictable failure handling and a recovery path.

All stages retain the shared controller request limit and minimum polling
interval. Avoid repeated writes of unchanged values, full address scans and
unbounded history requests. A rejected optional capability read should leave
core monitoring usable and record the limitation in cached diagnostics.

## Source register

- [Official AE-200E, AE-50E and EW-50E product and manual catalogue](https://www.mitsubishi-electric.co.nz/commercial/c/11706/ae-200e-ae-50e-ew-50e): product family, expansion and official manual entry points.
- [Licence Classification List, WT07300X02](https://www.mitsubishi-electric.co.nz/materials/PRODUCT/Controls/AE-200E_AE-50E_EW-50E/02_Manuals/1_Operation/OM_AE-200E_AE-50E_WT07300X02_License_Classification_List.pdf): optional functions require the appropriate licence. It does not establish which licences a site owns.
- [BACnet Function, WT07919X09](https://www.mitsubishi-electric.co.nz/materials/PRODUCT/Controls/AE-200E_AE-50E_EW-50E/02_Manuals/1_Operation/OM_AE-200E_AE-50E_EW-50E_WT07919X09_BACnet_Function.pdf): official functional reference, including Thermo-ON and apportionment. Its transport, objects and licence conditions are specific to BACnet.
- [Integrated Centralized Control Web, WT07996X13](https://www.mitsubishi-electric.co.nz/materials/PRODUCT/Controls/AE-200E_AE-50E_EW-50E/02_Manuals/1_Operation/OM_AE-200E_AE-50E_EW-50E_WT07996X13_Integrated_Centralized_Control_Web.pdf): official web-interface operation reference, not a published XML API contract.
- The controller-supplied official web asset `/control/js/newweb.min.js`, inspected as text without execution: `MnetGroupKindModel`, `attrMnetGroupKind`, `createXmlMnetGroupKind`, `adjustAirDirection`, `getFanSpeedSW`, `S03050CtrlFanSpeedView`, `S03050CtrlAirDirectionView` and `S03050CtrlSetTempView` provide the static protocol evidence described above. These symbol names are more useful for future revisions than offsets in a minified file. The vendor asset is not redistributed here.
- Private live cached diagnostics and bounded read-only capability responses establish that the relevant indoor-AC fields can return meaningful values. Captures remain outside the repository. They do not establish successful writes for planned features or universal device-family support.
