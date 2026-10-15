# Per-function local remote restrictions

Version `0.2.0b10` adds the response-only `ae200.set_local_restrictions` action.
A limited live protocol change and private exact restoration have passed
acknowledgement/readback checks. Deployed HA action acceptance remains pending;
physical wall-remote button behaviour was not tested.

The action changes selected functions on one group's local wall or
handset remote. It can prohibit local power, operating-mode, temperature or
filter-reminder-reset control. These are AC operating restrictions. They are
not account permissions, authentication controls, hardware capabilities or a
guarantee that every local interface is locked.

The existing local-remote summary switch and its identifier are preserved,
including its legacy single-field `RemoCon` writes. Its four cached effective
attributes remain read-only. The new per-function action does not replace
that switch, change central climate commands or rewrite existing automations.
The legacy switch's on state alone does not prove that all four functions are
prohibited.

## Initial scope and explicit input

The initial implementation is deliberately limited to one explicitly selected
integration entry and one configured group. The group must be in the 1–50
range and in that controller's configured-group allowlist. There is no all-group,
address-level or wildcard target.

All four reported capability fields must be present and match exactly:

| Capability field | Required value |
|---|---|
| `Model` | `IC` |
| `SubModel` | Explicit empty string `""` |
| `IcKind` | `NORMAL` |
| `GroupType` | `OLD` |

An absent, wildcard or unknown `SubModel` is not treated as an empty value.
`NEW`, missing capabilities, other indoor-unit variants and other equipment
families are outside this conservative pilot scope and need their own
implementation and acceptance. Exclusion does not establish incompatibility.
The selected group's normal state
must also be fresh and successful. A partial permissions read does not
establish fresh power, mode, temperature or other climate state.

The four optional HA fields map to the logical keys in the returned report:

| HA field | Report key | Protocol item | Meaning of true |
|---|---|---|---|
| `power_prohibited` | `power` | `DriveItem` | Prohibit local power control |
| `mode_prohibited` | `mode` | `ModeItem` | Prohibit local mode control |
| `temperature_prohibited` | `temperature` | `SetTempItem` | Prohibit local temperature changes |
| `filter_reset_prohibited` | `filter_reset` | `FilterItem` | Prohibit local filter-reminder reset |

Values must be actual booleans. True prohibits the chosen function and false
permits it. False is an explicit request, not an omitted value. At least one
and at most four recognised functions must be supplied. Nulls, numbers,
boolean-like strings, extra fields and raw protocol attributes are rejected.
Omitted functions retain their freshly read effective settings.

The action requires a response. In a script, provide `response_variable`:

```yaml
action: ae200.set_local_restrictions
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  group_id: 1
  filter_reset_prohibited: true
response_variable: local_restrictions
```

Both `config_entry_id` and `group_id` are required; replace them with the
intended configured target. The public action does not accept arbitrary
`RemoCon` or `CHK_*` values, saved raw snapshots, extra local
items, stop flags or a restore instruction. Prohibiting local filter reset
does not reset a filter reminder or clear a fault.

## Preconditions re-read inside the write lock

The client transaction uses the same write lock as ordinary central
commands. The required checks and fresh baseline read belong inside that
transaction, rather than being prepared by the HA service before awaiting the
client. All requests continue to use the common I/O lock and request pacing.
Changes also share a controller-wide 15-second cooldown from completion,
including calls for different groups and completed no-op attempts.

Before a write, the transaction must:

1. Validate the explicit target, strict patch, supported capabilities and
   current group freshness.
2. Re-read the three stop-context flags with bounded minimal reads: global
   `SystemData.AllTurnOff`, and the group's `TurnOff` and `BACnetTurnOff`.
   All three must have fresh, successful and known false values. An ON,
   missing, unknown, failed or stale source blocks this new action. A previous
   cached control-context result is not substituted for this check.
3. Re-read that group's complete five-field baseline: `RemoCon`, `DriveItem`,
   `ModeItem`, `SetTempItem` and `FilterItem`. Every field must be present and
   use a recognised token. An incomplete, ambiguous or unknown baseline must
   fail without a write.
4. Compute effective restrictions, merge only the requested boolean functions
   and validate the complete target before deciding whether to send anything.

The raw summary must be `PERMIT` or `PROHIBIT`; each item must be `CHK_ON` or
`CHK_OFF`. These stricter prerequisites apply to the new write workflow. They
do not change the more permissive unknown handling of existing read-only
attributes or impose new gates on legacy climate and summary-switch controls.

## Preserve effective settings when merging

For a complete known baseline, the effective rule is:

| Raw combination | Effective prohibition |
|---|---|
| `RemoCon=PERMIT` | False, even if an individual item is `CHK_ON` |
| `RemoCon=PROHIBIT` and item `CHK_OFF` | False |
| `RemoCon=PROHIBIT` and item `CHK_ON` | True |

The patch merges against those effective values. It must not copy dormant
raw `CHK_ON` bits and then flip the summary to `PROHIBIT`: that could revive
unrequested restrictions which were hidden by `PERMIT`.

For example, if `PERMIT` hides four `CHK_ON` items, all four effective
restrictions are false. Requesting only a local filter-reset prohibition must
keep local power, mode and temperature permitted. The planned target
normalises the three unrequested items to `CHK_OFF`, sets the requested item
to `CHK_ON` and uses `PROHIBIT` for the summary.

If the effective before and target states are identical, the result is
`no_change` and no set request is sent. A different normalised raw tuple alone
must not trigger a write on an effective no-op. Dormant raw values are not
rewritten merely to tidy them.

When an effective change is required, one `setRequest` carries exactly the
five permission fields for the selected group. The summary is `PROHIBIT` if
any target restriction is true and `PERMIT` otherwise; all four target items
are explicit. This is one grouped command, not five independent setters.
It contains no power, operating-mode, temperature, fan, vane, filter-sign or
fault-reset command.

## Acknowledgement, readback and uncertainty

Sending the five fields together does not prove that the controller applied
them. A checked acknowledgement and a matching state readback are separate
evidence. After an acknowledged or uncertain send, the client performs one
additional bounded five-field readback using the same serialisation and
pacing. A definitive failed write does not trigger that readback. There is no repeated
confirmation poll or HA-triggered duplicate refresh.

A confirmed result requires the acknowledgement and complete matching
five-field readback. Acknowledged but absent, failed or mismatching readback
must remain unconfirmed. Effective equality alone is not a substitute for
matching the expected raw target.

If a connection or acknowledgement is lost after writing begins, the outcome
is unknown. Do not replay the write over HTTP, retry it automatically or send
an automatic compensating write. A single bounded readback may show current
reported values, but even a match cannot supply the missing acknowledgement
or upgrade an uncertain command to confirmed.

HTTP write timeouts, loss of the response and malformed acknowledgements can
also leave an uncertain result. Writes do not follow redirects which could
resend a POST. An explicit controller rejection remains a failed command.
Cancellation propagates rather than being returned as success, and no
transport failure starts an automatic retry or compensation.

## Response and sampled evidence

The HA action returns the report from this invocation, including failed or
uncertain write results. Invalid input, unsupported or stale prerequisites
and cooldown rejection are action errors; a failed prerequisite network read
also fails the action before writing. HA does not issue another request,
refresh or write after the client returns.

| Field | Meaning |
|---|---|
| `group_id`, `at` | Selected group and report time after the baseline read |
| `status` | This operation's result, as listed below |
| `requested` | Only the supplied logical function keys and boolean values |
| `before_raw`, `before` | Fresh five-field baseline and its four effective restrictions |
| `expected_raw` | Complete expected five-field target; the original raw tuple for a no-op |
| `after_raw`, `after` | Independent post-send sample and its effective values, if one was obtained |
| `readback_at`, `readback_matches` | Observation time and complete raw-target comparison, or unknown |
| `error`, `write_error`, `readback_error` | Safe summary and independent write/readback errors |

`after` is a timestamped post-send observation, not a claim about current
permissions when someone later opens a saved response. It can be present even
when the write outcome remains unknown. No additional persistent transaction
cache or new entity is introduced. The generic last-command diagnostic still
tracks the five requested attributes and command status; it is not a substitute
for this report, and a no-op does not create a new last command.

| Status | Evidence |
|---|---|
| `no_change` | Effective baseline already matches the patch; no write or post-read occurred |
| `confirmed` | Checked acknowledgement and complete matching five-field readback |
| `accepted_unconfirmed` | Acknowledged write, but no valid post-send sample was obtained |
| `not_yet_confirmed` | Acknowledged write with a valid, mismatching post-send sample |
| `outcome_unknown` | A write may have been applied without a usable acknowledgement; readback cannot supply it |
| `failed` | A definitive failed or explicitly rejected write |

For `no_change`, `after` and `readback_at` are unknown, `after_raw` is empty,
`readback_matches` is unknown and `expected_raw` equals `before_raw`. The
baseline is not copied into a fabricated post-read. Read the returned status
and both error fields rather than treating a non-error HA invocation as proof
of a confirmed change.

The common write lock serialises operations through this client. It does not
lock wall remotes, other controllers, external BMS clients or a separate HA
client. The XML interface has no verified compare-and-set or transactional
rollback. State can change after a prerequisite read and before a write or
readback. Fresh reads and a single grouped command narrow that window; they
do not provide a race-free compare-and-set guarantee.

## Live acceptance and restoration boundary

One idle, OFF, configured ordinary group passed a small live protocol test.
Its capabilities and freshly read stop prerequisites were checked before
changing only the local filter-reset prohibition from permitted to prohibited.
The five-field command was acknowledged and its complete readback matched.
The other three effective restrictions and operating settings were unchanged.
This did not reset a filter reminder or test physical wall-remote buttons.
A separate minimal read confirmed all four capability fields, including the
explicitly empty `SubModel`, for that pilot profile.

The private acceptance tool retained the exact original five raw permission
fields, their four effective values and full relevant operating-state evidence.
After fresh stop and baseline checks, it restored the original five raw fields,
received an acknowledgement and matched the complete restoration readback.
A full operating-state check also matched the original evidence. This
validates the selected protocol operation and restoration on that group,
not the behaviour of every local interface, function or equipment family.

Exact restoration is a separate, private acceptance operation, not a public
raw-write service or an automatic error handler. It must restore the captured
five-field raw tuple, including any originally dormant `CHK_ON` values.
Setting all four effective functions to false is not exact raw restoration.
Before a planned restore, re-check current prerequisites and compare a fresh
complete baseline with the expected just-written target. An external change,
mismatch or uncertain result must not be blindly overwritten or compensated.
Any restoration write also needs its own acknowledgement and one bounded
five-field readback; it is not a retry of an uncertain command.

Keep original raw tuples, actual room or group inventories, private targets
and full control captures outside public documentation. Publish only the
operations and evidence which actually pass protocol and HA acceptance.
