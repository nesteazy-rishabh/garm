# Provenance and authority for your own agent

`src/garm_bench/provenance.py` is a single standard-library file. Copy it into any project where an agent writes to
a system of record. It is the logic behind Garm's D5. An equivalence test (`tests/test_provenance.py`) checks that it
gives the same authority level as D5's own code for every vendor field, in the end states of all 24 laundered and
legitimate-update episodes, under both callback policies.

## Wiring it in

1. **Log every write.** In your tool layer, after each change to a record field, call
   `log.record(entity, field, value, writer_kind, writer, source, at)`. Writer kinds are your own vocabulary, for
   example "seed" for imported data, "staff" for people and "agent" for the model. For agent writes, `source` names
   the outside input the change came from, such as an email address and message id.
2. **Log verifications as attestations.** When a person confirms a value out of band, call
   `log.attest(entity, field, value, by, via, at)`. If they used a channel stored in the record, such as a phone
   number on file, pass it as `via=(entity, field, value)`. That is what lets the module see that a callback reached
   a number an outsider had just written.
3. **Gate on authority.** Before a consequential action, compute
   `Policy(trusted=..., attestable=...).authority(log, entity, field)` for each field the action depends on, and
   `combine()` them. Let high-authority actions run; send low-authority ones to approval, with `card()` on the card.

## What the Garm results say about using it

- **It cuts approval load sharply.** In the main grid, D5 asked for no approvals on routine work and about one per
  legitimate update, against 1–3 per task for per-action gating (`results-main.md`).
- **It makes laundering visible, not decidable.** The card shows that a verification rests on a number an outside
  email wrote. An honest vendor that changes its number and then its bank produces the same chain. A reading
  approver therefore rejects about half of honest updates while stopping the attacks.
- **Pair it with an out-of-band control.** A known-good callback, which calls a number that was on file before any
  recent change, closes that gap at the process level (D6, `design-d6.md`). Under that policy, honest verified
  changes come out as high authority and need no approval.
- **Timestamps must be comparable.** The module compares ISO 8601 strings as text, so use one format and one
  timezone throughout.
