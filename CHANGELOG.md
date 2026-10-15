# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/).

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
