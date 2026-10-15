# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/).

## [0.2.1b3] - 2026-10-16

### Added
- Response-only `ae200.read_member_temperature_history` action for one
  explicitly selected entry, topology-validated IC address and Gregorian date.
  The data name is fixed to `ROOM_TEMP`; one minimal GET reads that selector.
- Independent raw history, sample times, safe errors and quality, with up to
  32 member/date samples retained by a local LRU policy. Exact 48-token
  sequences receive naive half-hour position labels, with no numeric
  temperature, verified unit, aggregation, timezone or statistics projection.
- Cached diagnostics export the room history without I/O. Room and runtime
  history actions share their controller-wide 15-second completion cooldown;
  no startup query, periodic poll, automatic day/channel scan or entity is added.

### Fixed
- A cancelled room or runtime history attempt records a safe cancellation
  error and retains previous raw evidence and success time as stale. The
  cancellation propagates and still starts the shared completion cooldown;
  it does not leave an earlier sample presented as a fresh successful attempt.

### Validation scope
- One minimal live `ROOM_TEMP` response contained one record and 48 non-zero
  numeric-looking tokens. Strict parsing produced 48 labelled positions
  without issues. HA action deployment acceptance remains pending.
- The vendor display expects Celsius, but the wire sample supplies neither a
  unit nor aggregation or collection-validity metadata. The diagnostic keeps
  `unit`, `aggregation` and `timezone` unknown, with no float conversion or
  inference of averages, current room temperature or valid measurement.
- The preceding `0.2.1b2` release passed deployed HA regression on the existing
  OLD installation, preserving entity identifiers, operating settings,
  permissions and automations. It does not provide live NEW acceptance;
  public NEW temperature writes remain blocked.

## [0.2.1b2] - 2026-10-16

### Added
- A pure offline profile and combined-command planner for an exact ordinary
  `IC` / empty `SubModel` / `NORMAL` / `NEW` / `ONE_SETPOINT` capability set.
  Complete reported ranges, recognised mode switches and a known Celsius step
  are required. The planner validates all inputs before returning immutable
  mode-specific attributes; it performs no I/O and is not connected to SET.

### Fixed
- Resolve a supported NEW group's cached target using its raw mode:
  `SetTemp1` for COOL/DRY, `SetTemp2` for HEAT and `SetTemp3` for AUTO.
  Invalid or unsupported profiles and values remain unknown, with no legacy
  `SetTemp` fallback, rounding or clamping. OLD reads are preserved.

### Validation scope
- Offline tests cover profiles, ranges, precision, field selection, immutable
  plans and HA/device read contracts. No representative live NEW sample,
  temperature write, readback or restoration has been accepted.
- Public NEW temperature writes remain blocked, including compound requests,
  and HA continues to hide their temperature-control feature. This release
  does not expand entity classification, advertised modes, TWO_SETPOINT,
  SETBACK, device families or physical running-state inference.
- The preceding `0.2.1b1` deployed HA runtime diagnostic has now passed explicit
  `FAN_TIME` and `THERMO_TIME` action/cache acceptance for one member and day.
  Each reported 48 zero tokens; collection and measurement validity remain
  unverified. Existing identifiers, permissions and automations were retained.

## [0.2.1b1] - 2026-10-16

### Added
- Response-only `ae200.read_member_runtime` action for one explicit entry,
  topology-validated IC address, Gregorian date and `FAN_TIME` or `THERMO_TIME`.
  Each invocation sends one minimal GET with a controller-wide 15-second
  cooldown measured from completion.
- Independent raw history, errors and freshness, with nullable projections
  for the IC member daily channel's 48 half-hour minute intervals. Up to
  32 selector samples are retained by a local LRU policy; this is not server
  storage capacity or history retention.

### Validation scope
- One selected live `FAN_TIME` response contained 48 zero tokens. This confirms
  the request and CSV shape, not measured idle time, collection validity,
  non-zero data or `THERMO_TIME` support. HA action acceptance remains pending.
- The single-record/attribute limits, 1970–2037 input window and nullable
  0–30-minute projection are conservative pilot policies, not vendor capacity,
  retention or wire-range guarantees. Raw evidence is retained without
  padding, truncation, zero-fill or clamping.
- No startup or periodic trend query, automatic day/channel scan, daily sum,
  cross-member aggregate, statistics entity, UTC conversion or operating-state
  inference is introduced. Existing identifiers and controls are preserved.

### Release sequence
- Start the `0.2.1b1` prerelease patch series without changing existing tags.
  HACS installation instructions require explicit target-tag verification.

## [0.2.0b10] - 2026-10-16

### Added
- Response-only `ae200.set_local_restrictions` action for one explicit entry
  and configured `IC` / `NORMAL` / `OLD` group with an explicitly empty
  `SubModel`. Missing, wildcard and unknown submodels are rejected rather than
  being interpreted as ordinary IC. A strict boolean patch changes
  selected local power, mode, temperature or filter-reset permissions while
  preserving the unrequested effective restrictions.
- Shared-write-lock prerequisite reads, a complete five-field baseline and
  one grouped five-field command. All three freshly read stop flags must be
  known false. Effective no-ops send no write and do not fabricate a post-read.
- A report for this invocation separates acknowledgement, one bounded
  post-send sample and uncertainty. It adds no persistent transaction cache,
  automatic retry, compensation, public raw-restore service or new entity.

### Fixed
- Treat HTTP write timeouts, lost responses and malformed acknowledgements as
  uncertain where a write may have begun. Do not follow redirects that could
  resend a POST or replay an uncertain command. Explicit rejection remains a
  failure, and cancellation propagates without being presented as success.

### Validation scope
- One idle ordinary group passed a local filter-reset restriction change with
  acknowledgement and matching five-field readback. Unrequested effective
  restrictions and operating settings were preserved, and the original five
  raw fields and operating state were restored and checked. Deployed HA actions
  also confirmed the selected filter-reset restriction and exact restoration,
  with identifiers and automations retained. Physical wall-remote buttons were
  not tested.
- Shared locks do not provide a controller-side compare-and-set guarantee
  against external writers.
- A separate minimal capability read confirmed the four-field pilot profile.
  Other submodels remain outside this release's scope pending their own
  implementation and acceptance; this is not an incompatibility finding.
- The legacy `RemoCon` switch, cached effective attributes, existing identifiers
  and central climate controls are preserved. Changing a local filter-reset
  restriction does not reset the filter reminder or clear a fault.

## [0.2.0b9] - 2026-10-16

### Added
- Response-only `ae200.read_weekly_pattern` action for one explicitly selected
  integration entry, configured group, season 1–5 and pattern 1–7. Each call
  sends one minimal `WPatternList` GET for that exact combination.
- Independent per-selection diagnostic snapshots, raw plan fields, errors and
  sample times, with a controller-wide 15-second cooldown from completion.
- An 18-field record allowlist and a 24-record receive boundary. Oversized
  responses are rejected in full rather than silently truncated.
- A separate cached `schedule_available` attribute on the existing schedule
  diagnostic, based on recognised `ScheduleAvail` values while it is available.
  Safe raw flag attributes retain limited historical evidence without I/O.

### Fixed
- Rename the existing schedule diagnostic to *Controller schedule flag* so
  `Schedule` is no longer presented as a verified enabled-state signal. Keep
  its identifier, state source, creation rule and default-disabled status.
  Unknown or unavailable availability data remains unknown.

### Validation scope
- One minimal live websocket GET for one group/season/pattern combination
  returned zero records and passed strict parsing without issues. Deployed HA
  acceptance also confirmed a fresh successful empty response matching the
  cached selector exactly, with identifiers and automations preserved.
  Non-empty actions, activation and execution have not passed live acceptance.
- The receive cap is a local protection based on the official editor; it is
  not a server-capacity or pagination guarantee. Zero returned records do not
  prove that scheduling is disabled, unsupported or inactive today.
- No startup query, periodic poll, automatic 35-combination scan, new entity,
  schedule write, pattern activation, clock query or automation change is
  introduced. Clock, TodayList and weekly-pattern reads are separate samples
  and do not bind a plan to a verified calendar date.

## [0.2.0b8] - 2026-10-16

### Added
- Response-only `ae200.read_controller_clock` action for an explicitly selected
  configuration entry. One minimal six-field clock read supplies an independent
  diagnostic cache with a controller-wide 15-second cooldown from completion.
- Raw local-clock fields and a nullable `local_datetime` for a fresh, valid
  Gregorian date and time. Errors and expiry preserve historical raw fields
  while making the typed value unknown.

### Validation scope
- One minimal live Clock GET returned all six fields and passed strict parsing
  without issues. HA deployment acceptance also confirmed a fresh successful
  action response matching cached diagnostics, with existing identifiers and
  automations preserved. Clock values
  do not establish a UTC offset, daylight-saving rules, clock drift or a verified
  time synchronisation source.
- No setup read, periodic clock poll, entity, clock write or time synchronisation
  is added. A separate clock read and `TodayList` read are not an atomic sample
  and do not bind a schedule snapshot to a verified calendar date.

## [0.2.0b7] - 2026-10-16

### Added
- Response-only `ae200.read_today_schedule` action for one explicitly selected
  configuration entry and configured group. One minimal `TodayList` request
  supplies an independent cached snapshot with a controller-wide 15-second
  cooldown measured from completion.
- Strict single-group schedule parsing with an 18-field allowlist, raw action
  tokens, nullable validated hour/minute values and conflict isolation. Responses
  exceeding the web editor's 24-record limit are rejected rather than truncated.

### Validation scope
- A minimal live request confirmed the empty `TodayList` response structure.
  Non-empty plans and their execution have not passed live acceptance.
- Offline parsing, cache-isolation and HA action contracts cover unsafe or
  conflicting records, unknown fields and failures. The receive limit is not
  a claim about server storage capacity or server-side pagination.
- An empty result means zero records were returned, not that schedules are
  disabled or unsupported. This release adds no setup read, periodic schedule
  poll, next-run calculation, new entity, schedule write or automation change.

## [0.2.0b6] - 2026-10-16

### Added
- Four cached effective local-prohibition attributes on the existing remote-lock
  switch, distinguishing power, mode, temperature and filter reset. The legacy
  switch identifier, reported on/off state and single-field writes are preserved.
  Unavailable, stale or failed group data makes the effective attributes unknown.
- Response-only `ae200.read_control_context` action for an explicitly selected
  integration entry and configured group. Two sequential minimal reads retain
  independent global/group stop-flag samples, errors and freshness.
- Controller-wide control-context cooldown, measured from completion, without
  automatic context reads, periodic polling or new entities. Partial and failed
  reads retain their diagnostic response and historical raw samples.

### Fixed
- Describe `RemoCon` as a local-prohibition summary rather than proof that all
  remote functions are locked. Per-function values remain read-only and do not
  gate central HA commands or rewrite automations.

### Validation scope
- Bounded global and group stop-field reads confirmed the response structures.
  Unknown and stale flags remain unknown in typed output, while cached raw
  tokens are retained. No stop-clearing or new prohibition write is introduced.
- Protocol, isolation, cooldown and HA contract tests cover partial failures and
  unknown values. Reported stop flags do not establish physical operating state,
  identify the cause of a stop or prove an active BACnet installation.

## [0.2.0b5] - 2026-10-16

### Added
- Optional, once-per-setup controller identity and function-status directory
  reads, with independent errors and cached diagnostics. Only display model,
  version and licence-status fields are retained; no keys or activation controls.
- Strict metadata XML parsing, bounded function indices and conflict isolation.
  Unknown status tokens remain unknown; raw time limits are not expiry dates.

### Fixed
- A websocket write which loses its response after sending begins is no longer
  repeated over HTTP. Report `outcome_unknown`, preserve a bounded state-readback
  opportunity, and never turn an observed matching state into a missing command
  acknowledgement. Read fallback and failures before sending remain separate.

### Validation scope
- Live minimal controller identity and function-status directory reads confirmed
  the response structures. Captured identities and licence inventories remain
  private and are not included in fixtures or documentation.
- Offline metadata, optional-read isolation and transport tests cover malformed
  responses, unknown fields, duplicate conflicts and ambiguous write outcomes.
- An active licence does not establish that its associated endpoint, equipment
  or measurement is supported. This release adds no fault-history query,
  licence activation, schedule write or energy inference.

## [0.2.0b4] - 2026-10-16

### Added
- One optional configured-membership directory read after the first successful
  HA group refresh. Preserve all members without replacing existing entities.
- Separate, bounded Address parsing and cached member diagnostics. The
  `ae200.read_member` action reads one explicitly selected configured indoor
  member, with a controller-wide 15-second cooldown and no automatic member poll.
- Membership and member data have their own sample times, errors and quality.
  Optional failures do not change group-state freshness or controller health.

### Fixed
- Wait for the actual websocket get/set response when unsolicited notifications
  arrive first. Ignore bounded notifications and KeepAlive frames under one
  response deadline, without resending the request or treating a notification
  as a complete sample or command acknowledgement.

### Validation scope
- Live configured-member directory and a single indoor-member status read.
- A control-assisted fan-only test observed an unsolicited FanStatus notification
  before the expected response. The corrected transport received the complete
  getResponse with FanStatus ON and ThermoStatus OFF; the original group settings
  and OFF state were restored and read back.
- The pilot firmware rejected DefrostStatus as an unknown attribute. It is not
  requested by the new action. AlarmCode remains an uninterpreted string; no
  compressor operation, energy measurement or whole-group hvac_action is inferred.

## [0.2.0b3] - 2026-10-16

### Added
- One optional, bounded capability query after discovery, sharing the existing
  request lock and pacing. Unsupported queries leave normal monitoring usable.
- Per-group HA mode and fan choices from reported hardware capabilities, native
  vertical vane/swing controls, temperature precision and reported limits.
- Cached controller command-status and schedule-enabled diagnostic entities,
  disabled by default. Schedule-enabled is a read-only controller flag.
- A capability matrix separating implemented behaviour, live evidence and future
  topology, alarms, schedules, ventilation, water and licensed energy work.

### Fixed
- Combine mode and power, or mode/power/temperature, in one validated command.
  Reject unsupported known capabilities and invalid temperatures before writing.
- Use the controller's actual MID0/MID1/MID2 vane vocabulary. Preserve existing
  entity identifiers, fan labels and raw direction attributes.
- Guard temperature writes to known NEW groups until their separate setpoint
  fields and auto-mode semantics have been validated.
- Track the latest command's readback independently of older pending settings.

### Validation scope
- Offline protocol, transport, HA contract, diagnostics and packaging tests.
- Live capability reads for configured indoor AC groups. A single ordinary AC
  group confirmed temperature, fan and vane changes while off, then a brief
  compound fan-only/on command. Original mode, temperature, fan, vane and off
  state were restored and read back successfully.
- Other equipment families, schedule writes and licensed energy/accounting
  functions remain outside this release's verified control scope.

## [0.2.0b2] - 2026-10-16

### Added
- A HACS release workflow restricted to version changes on the company repository's
  main branch, with CI gates, version validation, release notes, archive checksums
  and a manual retry entry point.
- HACS migration, beta update, authentication and rollback instructions.

### Fixed
- Preserve the distinct MID1/MID2 fan readings as medium_1/medium_2 in HA and MQTT;
  keep MID/medium compatibility. These are protocol labels, not fan percentages
  or a per-unit capability discovery result. Live write acceptance remains pending.
- Direct installation, issue and maintenance links to the company fork; preserve
  original licence and authorship attribution.

### Validation scope
- Read-side fan enumeration evidence from a successful controller probe. Added
  synthetic HA command mapping and release-validation tests. Site acceptance of
  the installed beta and its control commands remains required.

## [0.2.0b1] - 2026-10-16

### Added
- Per-group freshness, controller health and communication-problem entities.
- Cached HA diagnostics containing redacted raw Mnet attributes and field warnings.
- A modern-browser read-only probe independent of the Java plug-in.
- A self-contained HACS library bundle, deterministic sync check, pilot archive
  builder and reversible HA file installer.
- Deployment, rollback and no-Java diagnostics guides.

### Fixed
- Failed polls no longer make historical cached values appear freshly available.
- Missing status flags remain unknown, and unverified zero humidity is withheld.
- Controller write errors are checked; accepted writes require state readback
  rather than optimistic cache updates.
- Discovery/read/write requests are paced and serialised; connection failures
  do not trigger a timeout for every group; WebSocket fallback retries after cooldown.
- HA coordinator/options flow compatibility, controller device registration,
  and per-group/field MQTT availability.

### Validation scope
- Synthetic protocol tests, loopback transport tests, HA API boundary contract
  tests and reversible installer tests. Browser-probe tests use simulated APIs.
- Site acceptance on a real controller and running HA remains required.

## [0.1.0] - 2026-06-22

Initial release.

### Added
- `pymelans` async client for the MELANS protocol (G-50/GB-50/AE-50/AE-200/
  EW-50). Websocket transport with HTTP servlet fallback; auto-discovery of
  groups and names; typed device model spanning AC / ventilation / ATW units;
  rate-limited, CVE-aware writes.
- HACS custom integration `ae200`: UI config flow, `DataUpdateCoordinator`,
  and `climate`, `sensor`, `binary_sensor` and `switch` platforms.
- Home Assistant add-on: MQTT Discovery bridge with watchdog, autostart,
  settings page, and auto-detected MQTT broker.
- World-class README, add-on docs, offline test suite (real captured
  fixtures), and CI (lint, tests, hassfest, HACS validation).
