# Offline candidate scope and temporary timer plan

## Status and scope

Timer remains design-only. The implementation scope of this candidate is bounded
command-attempt diagnostics and local integration brand assets. Neither feature
implements a countdown, a scheduler or controller-native timer support.
This candidate must register no timer jobs, timer services, helpers, automations
or scheduling callbacks, and must add no polling or controller requests.
Attempt records describe client software attempts, not physical operation,
a complete transport ledger or activity from other clients. Keep them bounded,
in memory and restricted to safe fields; never include credentials or raw payloads.
Brand assets must not change entity identities, service options or configuration.

Phases A to C below are proposed follow-up work, not completed functionality.
Their test matrices are acceptance requirements, not claims of timer test results.
Preserve existing entity IDs, unique IDs, device links, permissions and automations.

## Phase A: isolated model and verifiable persistence

Build an offline-only job model using fake clocks, storage and dispatch adapters.
Do not register it with integration setup, services, the event bus or a real client.
Initialise an empty store with no jobs; loading the candidate must not arm anything.
Use one job per configuration-entry/group identity, independent of mutable names.
Record a schema version, job ID, revision, UTC deadline, action, state and outcome.
Bound retained terminal records; do not retain addresses, room names or raw responses.
Use aware UTC instants and render local time separately; do not infer controller DST.
Serialise arm, rearm, cancel and expiry with a task lock and revision checks.
Rearming replaces the previous revision; cancellation identifies the current job.
An old callback must never dispatch for a replacement job.

Audit the fixed Home Assistant storage contracts before choosing an adapter.
A completed `Store.async_save()` await alone is not proof that data reached storage:
some write errors are logged internally, and shutdown can defer a final write.
Require a reviewed write-success mechanism or verification of the saved revision.
Do not use a possibly cached `async_load()` as proof of an on-disk write.
Reject new reservations during shutdown and never report unverified changes as saved.
A failed cancellation write must not be reported as a durable cancellation.
Document the remaining crash and filesystem limits; do not promise exactly-once control.

## Phase B: command-correlated result interface

Design an additive internal result interface without changing stable service values.
The existing power setter returns no receipt; global `last_command` can be replaced
by another group's command and must not serve as a future timer's receipt.
Capture an immutable attempt ID, group, intended attributes and outcome inside the
existing shared write boundary. Preserve public callers and diagnostic compatibility.
Recheck revision, target, freshness and lateness after queue waits before submission.
Define the cancellation boundary explicitly: cancellation before commitment prevents
submission; after commitment it reports too late or unknown, without reverse control.
Persist a verified dispatching intent before any future controller submission.
Restore a dispatching job as outcome unknown, never as an automatically replayable job.
Keep acknowledged acceptance, later observed OFF, explicit rejection and uncertain
delivery distinct. A later OFF observation proves neither attribution nor compressor stop.
Use fake clients only in this phase; no real dispatch adapter or registered scheduling.

## Phase C: explicit business approval before real scheduling

Only after the execution policy and operational acceptance scope are approved may a
separate change connect a scheduler, expose timer actions or permit real dispatch.
The following are proposals requiring confirmation, not previously approved policies:

| Decision | Proposed first version |
|---|---|
| Scope | One future OFF per configured group; reservation does not start equipment. |
| Duration | Integer delay of 1 to 1440 minutes; a product limit, not a hardware claim. |
| Missed deadline | Mark missed; no implicit catch-up after downtime or long delays. |
| Manual OFF in HA | Cancel an unsubmitted job; retain the manual command's own outcome. |
| Mode or temperature changes | Keep the deadline; changes do not extend the timer. |
| External manual operation | State limitations; do not infer a reliable operator identity. |
| Offline or stale target | Mark blocked or missed-unavailable; no automatic retry on recovery. |
| Cancel after submission | Report too late or unknown; never send a compensating ON. |

Restore future jobs at the original deadline only after their original entry and
target are usable. Deleted, disabled, missing or replaced targets must not be retargeted.
A fresh already-OFF target completes without a write; unknown is not equivalent to OFF.
Allow at most one logical OFF submission attempt per job, through the existing client.
Preserve its shared I/O lock, write pacing and uncertain-write protections.
Arming, rearming, cancelling, viewing and countdown display require zero controller I/O.
Observe existing normal cache updates passively; add no collector, demand listener,
per-second reads or recovery polling. Existing acknowledgement/readback traffic remains
separate, so one logical submission must not be advertised as one network transmission.
Respect later schedules and manual commands; do not repeatedly force equipment OFF.
Other processes and building controllers are outside this client's lock scope.

## Offline acceptance matrix

| Phase | Cases | Required evidence |
|---|---|---|
| Candidate | Load, diagnostics, brand assets | Bounded safe records; no timer registration or added polling. |
| A | Empty, corrupt, unknown-schema and old storage | No executable unknown job; explicit failure, no silent success. |
| A | Silent write failure, deferred shutdown write, failed cancel save | No false durable acknowledgement; restart limitations recorded. |
| A | Future/past recovery, clock correction, sleep and delayed callback | Original UTC deadline; proposed lateness policy applied explicitly. |
| A | Rearm/cancel/expiry races and stale callbacks | Revision isolation; no duplicate or wrong-target dispatch intention. |
| B | Shared queue wait, concurrent groups and overwritten global state | Job-specific immutable receipt; final pre-submission checks. |
| B | Rejection, lost acknowledgement, cancellation and crash boundaries | Distinct outcomes; no blind resend or dispatching replay. |
| C | Missing/disabled/unloaded target, stale data and manual changes | Approved policy; no retargeting, automatic recovery retry or force-off loop. |
| All | Existing identities, options, permissions, cache and I/O demand | No compatibility change; zero extra reads from timer management. |

Use fake time and transport counters, not wall-clock sleeps or live fault injection.
Verify the chosen APIs against fixed Core 2025.12.0 and 2026.10.1 contracts:
[storage](https://github.com/home-assistant/core/blob/2025.12.0/homeassistant/helpers/storage.py),
[time events](https://github.com/home-assistant/core/blob/2025.12.0/homeassistant/helpers/event.py),
[2026.10.1 storage](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/storage.py).
Require focused tests and independent review of persistence, result association and races
before any runtime integration. No new timer has been implemented or live-tested here.
