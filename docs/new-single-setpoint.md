# NEW single-setpoint mapping and development boundary

Version `0.2.1b2` separates an implemented pure offline planner, corrected cached
temperature reads and a public control path. **Public temperature writes for
known `GroupType=NEW` groups remain blocked**, including combined commands
which also request a mode or power change. A pure plan or a corrected read
does not enable that control path.

No representative live NEW capability/state sample, NEW temperature write,
readback, restoration or NEW hardware acceptance has been established in this project.
Existing OLD control tests do not validate the NEW field mapping on hardware.
The implemented behaviour below is supported by static controller-client
evidence and offline validation; NEW hardware support requires separate
evidence. No live NEW control claim is made by this document.

The deployed `0.2.1b2` integration and bundled library passed regression on
the existing OLD installation. Original identifiers, operating settings,
local permissions and automations were preserved. That acceptance does not
exercise the NEW mapping on hardware or relax the public write guard.

## Three separate capabilities

| Layer | Scope | Evidence it cannot supply |
|---|---|---|
| Pure profile and planner | Validate supplied dictionaries and explicit inputs; return a bounded plan without I/O | Membership, freshness, controller support, acknowledgement or application |
| Cached typed read | Resolve a supported NEW profile's raw-mode-specific register, preserving unknowns | Permission to write, an active compressor, room temperature or a completed command |
| Public client and HA writes | Keep the existing NEW temperature guard and hidden HA temperature feature | The offline helper does not bypass either guard |

Existing identifiers, group entities, automations and legacy OLD temperature
behaviour are preserved. This read correction does not change entity-kind
classification, the advertised mode list, HA temperature bounds or UI step
fallbacks. An unchanged legacy mode fallback is not a verified NEW capability
list. A NEW group lacking the signals used by existing entity classification
still requires a separate discovery review; the getter does not manufacture
an AC entity from a non-empty `SetTemp1` field.

## Exact first profile

The initial offline profile requires every identity field to be present and
match exactly:

| Capability field | Required value |
|---|---|
| `Model` | `IC` |
| `SubModel` | Explicit empty string `""` |
| `IcKind` | `NORMAL` |
| `GroupType` | `NEW` |
| `AutoModeSWEx` | `ONE_SETPOINT` |

Missing, wildcard, unfamiliar or wrong identity fields do not establish this
profile. An absent `SubModel` is not an empty subtype. TWO_SETPOINT, HH, EOA,
other equipment families, SETBACK and unrecognised auto selectors are outside
this first profile. That scope is a conservative development boundary, not a
finding that other devices lack comparable vendor functions. The broader
Model=IC allowlist for daily runtime history does not grant NEW write support.

The first profile also requires recognised `ENABLE` or `DISABLE` values for
both `DryModeSW` and `FanModeSW`, a known `TempDetailC` step and all three
complete factory range pairs. A disabled optional mode is accepted as disabled;
a missing or unfamiliar flag rejects the profile even when another mode is
requested. Each raw interval and its inward step-aligned interval must have
distinct ordered endpoints. A single-point interval is outside this pilot.

`ONE_SETPOINT` means one active target for the selected mode. It does not mean
all modes share one temperature register. A NEW group can retain separate
cooling, heating and automatic-mode settings.

## Mode-specific field and factory range

| Raw target mode | Temperature field | Factory range | Additional mode gate |
|---|---|---|---|
| `COOL` | `SetTemp1` | `TempRange1Low1` / `TempRange1High1` | Exact first profile |
| `DRY` | `SetTemp1` | `TempRange1Low1` / `TempRange1High1` | `DryModeSW=ENABLE` |
| `HEAT` | `SetTemp2` | `TempRange1Low3` / `TempRange1High3` | `IcKind=NORMAL` |
| `AUTO` | `SetTemp3` | `TempRange1Low2` / `TempRange1High2` | `AutoModeSWEx=ONE_SETPOINT` |
| `FAN` | No temperature field | None | A mode-only plan requires `FanModeSW=ENABLE` |

The NEW automatic-mode selector is `AutoModeSWEx`. Legacy `AutoModeSW` must
not be used to override an exact NEW `ONE_SETPOINT` selection. DRY and FAN
still require their own recognised switches. Missing optional flags do not
authorise those optional modes or a broader fallback list.

FAN plus temperature is invalid as a complete plan. The planner must not drop
the temperature, select the cooling register or silently choose another mode.
AUTOCOOL, AUTOHEAT and SETBACK variants are not extra user-selectable modes
provided by this work.

## Correct cached target readings

For a known NEW group, the typed target-temperature getter uses the validated
profile and the exact raw `Mode` to select one register. COOL and enabled DRY
read `SetTemp1`; HEAT reads `SetTemp2`; single-setpoint AUTO reads `SetTemp3`.
An OFF group still uses its raw stored mode. HA's OFF state is not substituted
for a protocol mode or a new setpoint register.

The selected value must be a valid finite Celsius temperature, inside the
corresponding reported factory range and on the known step. Missing, empty,
wildcard, malformed, non-finite, out-of-range or off-grid values remain
unknown. So do FAN, unknown modes, incomplete profiles and unsupported NEW
branches such as TWO_SETPOINT. Raw evidence remains available for diagnosis.

There is no fallback to legacy `SetTemp`, another mode's register, a UI default
or the nearest range endpoint. A placeholder zero is not replaced with a
plausible setpoint. The getter does not round or clamp a value, change the
cache, send requests or let profile-validation errors escape as a fabricated
temperature. OLD and existing legacy read branches keep their prior behaviour.

The target is a stored setting, not a measurement. `current_temperature`
continues to use the return-air/inlet reading; it is not taken from a setpoint.
The existing HA getter delegates to the typed device view. Even when that
view resolves a valid NEW target, HA's TARGET_TEMPERATURE feature remains
hidden for all known NEW groups and the public client write guard remains.

## Offline planning without transport

`new_single_setpoint_profile()` returns a frozen profile,
`read_new_single_setpoint_temperature()` projects a cached value, and
`plan_new_single_setpoint_climate()` returns a frozen plan. They accept supplied
capability/state mappings and do not read a transport or alter a client cache.

The pure planner validates one group identifier and an explicit subset of
mode, power and temperature inputs. At least one setting must be supplied.
Power is strictly boolean; temperature must be a valid finite numeric input.
Boolean temperatures and truthy strings are not numeric requests.

An explicit requested mode determines the temperature field and range. A
temperature-only plan requires a recognised current raw mode supplied by its
caller. Missing mode context must not become COOL. OFF plus temperature also
needs valid mode context; a power request does not choose the register.

The requested value is checked against the target mode's reported factory
endpoints and Celsius grid. `TempDetailC=ENABLE` provides a 0.5 °C step and
`DISABLE` a 1 °C step; unknown precision does not authorise a fallback step.
Missing, unordered, non-finite or unusable ranges and empty allowed grids
reject the plan. Vendor UI defaults and HA fallback bounds are not factory
capability evidence. Local remote restrictions are separate from those limits.

A plan contains only requested fields: optional `Mode`, optional `Drive` and
the one selected `SetTemp1`, `SetTemp2` or `SetTemp3`. It must not add power,
rewrite a mode, change another setpoint or copy a cached temperature into a
mode-only request. Invalid combined input rejects the complete plan.

For a synthetic example, assume COOL permits 19–30 °C and HEAT 17–28 °C.
An explicitly requested HEAT/power-on/18 °C plan uses the HEAT range and
`SetTemp2`, even if the current mode is COOL. It produces one prospective
attribute set rather than a preliminary mode or power write. This example is
offline planning only: the public NEW temperature API still rejects it.

Returned plans and profile data must not expose mutable caller or cache
references. Their output dictionaries do not establish that anything was
sent, accepted, read back or restored. Capability snapshots also lack a new
independent freshness guarantee simply because current group state is fresh.

## Work required before public NEW writes

A future control path needs a representative real NEW/ONE profile and state
sample, an explicit capability-lifetime policy and a bounded acceptance and
restoration procedure. The client must prove the configured target and fresh
state, revalidate after waiting for its write lock and preserve normal pacing.
A pure helper cannot perform these checks on the caller's behalf.

Temperature-only mode selection must be protected from another local command
changing mode while the operation waits. External BMS actors can still race;
the XML interface has no verified compare-and-set guarantee. Confirmation
must distinguish a stored inactive register change from a change to the
currently selected mode. It must not add an unrequested mode write merely to
force a matching acknowledgement or observation.

Readback needs the correct selected register and relevant mode context from
one properly ordered post-acknowledgement sample. Finite numeric equality can
recognise `20` and `20.0`, but a matching legacy `SetTemp` or wrong NEW register
cannot confirm the request. Acknowledgement, readback and uncertain outcome
remain distinct, with no replay or automatic compensation after sending begins.

HA control exposure would require separate feature and service validation.
The entity-wide temperature envelope must allow valid combined target-mode
requests before the client applies the exact target-mode limits. A group's
temporary FAN state must not accidentally block a future valid FAN-to-COOL
combined call. None of that control exposure is enabled by this read correction.

Do not alter OLD capability flags, relabel an OLD group as NEW or send NEW
registers to OLD hardware to manufacture live acceptance evidence. Unknown
or unconfigured devices are not candidates for an address scan or a write test.

## Deferred separately

TWO_SETPOINT requires paired cooling/heating values, correct endpoint order,
reported deadband and explicit policy for one-sided updates. Even COOL, DRY
or HEAT can require paired registers on that branch; the ONE planner must not
be reused merely because the requested mode is not AUTO. No default deadband,
averaged target, automatic opposite-endpoint movement or range feature is added.

SETBACK and `SetTemp4`/`SetTemp5`, other device families, broader classification,
mode-list changes, public NEW write services and physical running-state
inference remain separate work. See the [capability inventory](capabilities.md)
for the current public scope and the distinction between source availability,
offline evidence and live acceptance.
