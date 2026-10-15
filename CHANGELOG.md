# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/).

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
  without issues. HA action deployment acceptance remains pending. Clock values
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
