# Configured members and on-demand diagnostics

Version `0.2.0b4` adds read-only membership and member diagnostics to the native
HACS integration. Existing group climate entities and their identifiers remain
unchanged. The MQTT add-on does not automatically load these new diagnostics.

After its first successful group refresh, HA makes one optional request for the
configured membership directory. It does not scan the M-NET address space.
Download integration diagnostics to inspect `controller.topology`: every
configured membership record is retained, including multiple members of one
group. Invalid, zero, unexpected or conflicting records are excluded from the
address-read allowlist and carry explicit issues. Family codes such as `IC`
are protocol classifications, not manufacturer model numbers.

Use **Tools → Actions → Mitsubishi AE-200 / EW-50: Read member** to select a
loaded integration entry and a concrete indoor-member address from that
directory. The action performs a single read and returns its cached diagnostic
record. Its YAML form is:

```yaml
action: ae200.read_member
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  address: 1
```

Use the actual entry ID and an address present in its directory. An M-NET bus
address is not the controller's IP address. The action requires response data;
in scripts, add a `response_variable`. The controller-wide minimum gap between
member reads is 15 seconds from completion, including attempts for different addresses. Calls
within that cooldown, unknown addresses, unloaded entries and failed reads
raise an action error. They do not silently substitute an earlier sample.

No member status is polled automatically. `controller.members` in downloaded
diagnostics contains only members whose reads were attempted. Each has its own
quality, attempt time, success time and error information. Group polling does
not refresh an old member sample. A failed member read leaves group climate
control usable and preserves the historical member sample with stale quality.
Downloading diagnostics performs no controller I/O.

From `0.2.1b7`, cancellation after a member attempt starts records a safe
`CancelledError`, retains old raw evidence and successful-read time as stale,
and leaves a cold member unknown. Cancellation propagates without retry and
starts the completion cooldown. Cancellation while waiting for the member lock
does not allocate a new attempt or cache entry. Group health, polling and
commands remain unchanged. These cancellation cases are tested offline;
no live outage or cancellation injection is claimed.

Member responses preserve raw `ErrorSign`, `AlarmCode`, `ThermoStatus` and
`FanStatus` strings. Empty, missing and wildcard values remain unknown. The
pilot firmware accepted these fields; it rejected `DefrostStatus`, which is
therefore omitted from the request. A normal-looking alarm code must not be
interpreted without its documented vocabulary. A fan-only test observed
`FanStatus=ON` and `ThermoStatus=OFF`, but this does not establish compressor
operation, whole-group HVAC action or energy measurement. No new status entity
or derived `hvac_action` is created by this pilot.

The websocket transport distinguishes complete get/set responses from
unsolicited `notifyRequest` and `KeepAlive` frames. Notifications do not complete
requests, refresh samples or confirm writes. A shared response deadline and
frame cap bound the wait; the request is not resent to bypass a notification.

The next stage is to verify detailed fault history and controller metadata,
then establish status vocabularies and group aggregation before promoting raw
member information into operational entities.
