# Controller metadata and command outcomes

Version `0.2.0b5` adds optional controller identity and licence-status snapshots
to the native HACS integration. Existing config entries, device identifiers,
entity unique IDs and automations remain unchanged. These snapshots do not
establish support for every equipment family or licensed endpoint.

## Reads and retained fields

At setup or integration reload, the client attempts each of these reads once,
using the same controller request lock, minimum spacing and response timeout as
ordinary requests:

| Read | Requested structure | Fields retained in cached diagnostics |
|---|---|---|
| Controller identity | `DatabaseManager/SystemData` | `Model`, `Version`, `VersionIF` |
| Function-status directory | `DatabaseManager/FunctionControl/FunctionList` | Function `Index`, `Status`, `TimeLimit` and derived `active` |

The identity read requests only its three display fields. It does not request
the controller number, serial number, network configuration or account data.
The function read uses the controller's directory operation. Unverified option
fields, licence keys and activation material are discarded rather than stored
in the parsed result. No registration, trial activation or licence change is
performed.

Each operation has its own attempt time, successful-read time and bounded
error type/code. A failed optional read leaves normal climate monitoring and
group freshness unchanged, and is not retried on every poll. Metadata is not
refreshed periodically. Reloading the integration starts a new client and a
new single attempt for each optional directory.

Downloading diagnostics copies the existing cache. It does not issue either
request, fetch historical faults or refresh member state. The sections are
named `controller_metadata` and `functions` in the controller diagnostics.

## Unknown values and version strings

The parsers require a valid, bounded `getResponse` envelope and the exact
expected structure. Controller error responses, notifications, malformed XML,
duplicate identity records and structural limits are rejected. Function
indices are bounded and contradictory duplicates are isolated rather than
resolved by XML order.

Only bounded printable tokens from the selected fields are retained. Missing,
blank, wildcard or invalid fields remain unknown and produce diagnostic issues.
Model and version values are preserved as strings. A version string is not
converted to a floating-point number or replaced by a guessed software version.
The meanings of `Version` and `VersionIF` remain their reported field names;
they are not automatically assigned to unverified firmware components.

## Licence status has a limited meaning

The reported status is retained unchanged. The limited `active` interpretation
follows the inspected controller web client:

| Status | `active` |
|---|---|
| `ENABLE`, `TRIAL` | `true` |
| `DISABLE`, `RESERVED` | `false` |
| Missing or another token | unknown |

An active licence is a reported entitlement state. It does not show that a
particular XML endpoint is available, that connected hardware supports the
function, or that a returned value is a valid measurement. Energy, runtime,
apportionment, BACnet and other optional functions still need independent
protocol and hardware acceptance before being exposed as working HA features.

`TimeLimit` remains a raw token. The observed `9999` token is not converted to
an expiry timestamp, an unlimited-duration promise or a date calculated from
the HA clock. No timezone, renewal behaviour or future entitlement is inferred.
Unknown status tokens do not become disabled licences automatically.

## Writes with an uncertain result

A command acknowledgement and a state readback provide different evidence.
An acknowledgement says that the controller accepted the request. A subsequent
fresh group response can confirm that its reported settings match. A cached
sample collected before acknowledgement cannot confirm that command.

If a websocket connection fails before sending starts, the existing transport
fallback may send the request over HTTP. Read requests can also use HTTP
fallback. Once sending a websocket write has started, a missing response could
mean that the controller acted and the reply was lost. Replaying that write
could apply it twice, so the integration reports `outcome_unknown` and raises
the operation error without repeating the write over HTTP.

The client still permits a bounded readback through its normal group-reading
path. That read may show the requested settings, different settings or no
fresh data. It does not fabricate an acknowledgement or silently change
`outcome_unknown` into an acknowledged or confirmed command. Check the current
state and the desired operation before issuing another command.

This release adds no automated retries of uncertain writes, fault resets,
history clearing, schedule editing or licence activation.

## Evidence and privacy

The identity and function-directory structures were checked against the
controller-supplied web-client builders and minimal live read responses.
Malformed responses, unknown fields, duplicate conflicts, optional-read
failures and transport failures are covered by offline tests. This evidence
does not certify every controller revision or optional function.

Real response files, private controller identities, licence inventories,
addresses, names and credentials remain outside this public repository. Use
the cached export for review and keep any site-specific evidence internal.
