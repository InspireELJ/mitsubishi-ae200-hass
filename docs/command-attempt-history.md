# Bounded command-attempt diagnostics

This is an unreleased offline candidate based on `0.2.1b8`. It has not been
deployed or accepted against live equipment.

The cached diagnostics add `command_history`: up to 32 attempts, oldest first,
held only in one existing client's memory. Starting a new client loses the
history. `last_command` retains its existing schema and behaviour.

Each record contains a client-local increasing `sequence`, numeric `group_id`,
UTC observation time `at`, allowlisted `intent`, write-stage `outcome`, and a
bounded machine-readable `error` or null. Unknown fields, arbitrary payloads,
responses, exception messages, credentials and connection details are omitted.
An intent can therefore be partial or empty. Exported records are deep copies.

An attempt starts at the existing pending-command boundary, after write-lock
acquisition and write-spacing wait. Validation failures and cancellations before
that boundary do not create records. The request may still be waiting for the
I/O lock or request spacing: a record, its sequence, `pending`, or
`outcome_unknown` does not prove transmission. This is not a wire request ledger.

The outcome mirrors only the existing write stage: `pending`,
`accepted_unconfirmed`, `failed`, or `outcome_unknown`. An unclassified exception
can leave `pending`, as it did before this candidate. No history outcome asserts
physical operation. Later readback continues to update `last_command` through
its existing rules; it does not revise past history records. In particular, an
uncertain attempt cannot acquire an acknowledgement because a later state
matches, or because a command for another group receives an acknowledgement.

The history adds no transport call, polling, retry, lock, timer, persistence or
control path. It does not change command returns, write pacing, exception
propagation, pending-write bookkeeping or the interpretation of readback.
