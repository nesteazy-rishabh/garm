# Plugging a defence into Garm

Garm measures a defence on four axes at once: same-run versus cross-run attack success (by stage), utility including
legitimate third-party updates, approvals per 100 tasks, and effective attack success under modelled approvers. You
can plug a new defence in by writing one class; you do not need to fork the environment.

## The interface

A defence subclasses `garm_bench.defences.Defence` and overrides any of these hooks:

| Hook | Layer | Called | Default |
|---|---|---|---|
| `system_addendum() -> str` | prompt | when each job's system prompt is built | `""` |
| `mark_external(text) -> str` | context | on every piece of outside text in a tool result (email bodies, attachments, ticket text) | unchanged |
| `async review(world, tool, args, context) -> Gate or None` | action gate | before every tool call, with the recent run context | calls `gate()` |
| `gate(world, tool, args) -> Gate or None` | action gate (synchronous) | from `review()` | `None` (allow) |
| `mode = "tools"` or `"drafts"` | architecture | drafts: the model gets no tools and returns typed proposals, which code checks and executes (`drafts.py`) | `"tools"` |

A returned `Gate(reason, card)` sends the action to the approval queue. The `card` is what the approver sees. Render
it from the record rather than from the agent's own description; `defences.py` has helpers, and D5 appends a
provenance chain.

`world` is the system of record, a SQLite database. Inside a hook you can read any table, including:
- `field_writes`, the provenance log: every field write with its writer, job run and source message;
- `action_log`, every consequential action attempted.

`garm_bench.authority` computes per-field authority from that log and propagates it through staff verifications.
Use it to build authority-aware gates, or as a reference when you build your own.

## Minimal example

```python
from garm_bench.defences import DEFENCES, Defence, Gate, effect_card


class AmountCap(Defence):
    """Approve anything except payments above $5,000."""

    name = "X1"
    description = "payments above $5,000 need approval"

    def gate(self, world, tool, args):
        if tool == "pay_invoice":
            inv = world.one("SELECT amount_cents FROM invoices WHERE id = ?", (args["invoice_id"],))
            if inv and inv["amount_cents"] > 500_000:
                return Gate("large payment", effect_card(world, tool, args))
        return None


DEFENCES["X1"] = AmountCap
```

Save it as, say, `my_defences.py` on your Python path. Then run
`GARM_PLUGINS=my_defences garm run --defence X1 ...` on each split, followed by `garm analyze` on the run folders.

## Defences that call a model

If your defence calls a model (as D3's monitor does), take a client with
`async generate(system, tools, messages, max_tokens)` in your constructor. Wrap it with the budget ledger so its
spend is capped and counted with everything else; see `models/anthropic_model.py`. Monitor calls appear in the cost
and latency columns alongside the agent's own calls.

## Checking that your policy actually fires

Run the offline suite before any paid run:

```bash
uv run pytest tests/test_attacks.py -k "victim"      # scripted attacks must activate under D0
uv run garm validate --split attack                 # oracle, victim and do-nothing agent checks
```

Then run your defence with the scripted victim (see `tests/test_drafts.py` for the pattern) and confirm it stops
the attacks it is meant to stop. A defence that never fires looks robust on a static suite. AutoDojo found one
published configuration that had been running with no policies loaded.
