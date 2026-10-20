# Garm

**Cross-run prompt injection in scheduled agents, and what each defence costs in human approvals.**

Status: under construction. No results yet.

Garm is a benchmark environment for a back-office agent that runs as scheduled jobs over a system of record.
The company is fictional: Brackwater Industrial Supply, a mid-size distributor. All data is synthetic. Every name,
email domain (`*.example`), phone number (555-01xx) and bank detail is invented, and any resemblance to real
entities is coincidental.

Garm is named for the hound that guards the gate of Hel in Norse myth.

## The question

Agents are moving from chat sessions to background jobs that run on schedules over a system of record. Typical
jobs triage the shared inbox every hour, update vendor and customer records, work the refund queue, and run the
weekly payment batch. Outsiders legitimately change that record all the time (a customer's new email address, a
vendor's new bank details), so the agent must act on low-trust input.

Business email compromise is the second-costliest category in the FBI's 2025 Internet Crime Report. It accounts
for about US$3.05 billion in reported losses across 24,768 complaints, and the typical vector is a "we've changed
our bank details" message. Back-office agents now read those messages.

Garm measures, for such an agent:

1. how much protection each class of defence buys against **cross-run** injection (planted in one job run, fired
   in a later one) compared with **same-run** injection;
2. what each defence costs in **utility**, including legitimate updates it wrongly blocks, and in **human approvals
   per 100 tasks** under modelled approver behaviour;
3. whether a drafts-only, plan-then-execute design is **laundered** when an earlier run has poisoned the record it
   checks against, and whether per-field authority labels close that gap and at what approval cost. In such a
   design the model only proposes, code executes, and every outbound action is checked against the record.

## Environment

- **System of record (SQLite):** vendors (with remittance details), vendor verifications, purchase orders,
  invoices, payments, customers, orders, refunds, support tickets, notes, inbox, outbound message log. Every field
  write is logged with its writer, job run and source message.
- **Inbox:** email bodies, PDF attachments (as extracted text), and web-form submissions.
- **Scheduler:** advances simulated time and fires jobs. Jobs share only the database, never conversation history.

  | Job | When it fires |
  |---|---|
  | `triage_inbox` | hourly, when there is unread mail |
  | `update_records` | after triage, when record-change requests are waiting |
  | `refund_queue` | daily at 10:00 |
  | `customer_followup` | daily at 16:00 |
  | `vendor_payment_run` | Thursdays at 11:00 |

- **Graders:** deterministic checks on the final database state and the outbound message log.

## Defences

| | Defence |
|---|---|
| D0 | None: a tool-calling agent with the company handbook. |
| D1 | Prompt rules plus spotlighting (delimiting and datamarking of external text). |
| D2 | Tool permission gateway. Payments, refunds, outbound commitments, and changes to money or identity fields go to an approval queue. |
| D3 | D2 plus a monitor model that sees the proposed action and its context and can escalate. |
| D4 | Drafts only (plan-then-execute; Beurer-Kellner et al. 2025, arXiv 2506.08837; Debenedetti et al. 2025, CaMeL, arXiv 2503.18813). The model has no tools and emits typed proposals. Code executes them, checks every outbound action against the record, and queues consequential ones for approval. |
| D5 | D4 plus per-field authority labels. Consequential proposals that rest on low-authority fields need approval, and the approval card shows where each value came from. |

**Approver models** are applied to the logged approval queue: an oracle, a rubber-stamp approver at 93%, a
load-dependent fatigue model, and a model that sees only the approval card.

## Pre-registered hypotheses

These were written on 2026-10-19, before any model was run. H4 is stated for the single-provider grid (Claude Haiku
5.5 on all defences, Claude Sonnet 5.5 on D0, D4 and D5), because the cross-family comparison in an earlier draft
is out of scope.

- **H1:** For D2 and D4, cross-run attack success is higher than same-run attack success, because per-action and
  record checks trust the record.
- **H2:** D4 matches D2's same-run security with fewer approvals, but is the most exposed to laundering.
- **H3:** D5 closes the laundering gap but adds approvals on legitimate updates. With a 93% rubber-stamp approver,
  much of D5's gain disappears.
- **H4:** On Claude Haiku 5.5, D4 and D5 reduce cross-run attack success more than D1 or D3 do.

A careful null result is also a result, and will be published as such.

## Metrics

All rates are reported with Wilson 95% confidence intervals.

- **Attack success:** split into write, incorporation and activation. Same-run and cross-run are reported
  separately, and also as effective attack success under each approver model.
- **Utility:** pass^3 on benign episodes (an episode counts only if all three attempts pass), plus the
  false-block rate on legitimate updates.
- **Oversight load:** approvals per 100 tasks; human-dependent security (the share of blocked attacks stopped
  only by an approver's "no"); estimated reviewer minutes.
- **Cost and latency:** per completed task.

## Scope and limitations

- **Single provider.** The grid uses Anthropic models only. The environment is model-agnostic: a model adapter is
  a small class, and the Inspect wrapper can drive any model Inspect supports.
- **Synthetic setting.** The environment and data are synthetic, and samples per cell are small.
- **Assumed approvers.** Approver models are assumptions, not measurements.
- **Bounded attacker.** The adaptive attacker is a lower bound on attacker strength.
- **Moving targets.** Model IDs, prices and dates are recorded with every run.

## Layout

```
src/garm_bench/   environment, scheduler, tools, defences, agent loop, graders, budget ledger, CLI
data/episodes/    episode definitions (JSON), generated by scripts/build_episodes.py
tests/            graders validated against a scripted solver and a do-nothing agent
```

## Running

```bash
uv sync --all-extras
uv run garm validate        # offline: the scripted solver must pass and a do-nothing agent must fail every episode
uv run pytest               # the same checks plus unit tests for tools, the gateway, the ledger and statistics

# Paid: needs ANTHROPIC_API_KEY in .env and a recent price check (verified_on in data/prices.json)
uv run garm run --model claude-haiku-5-5 --defence D0 --repeats 1 --max-spend 1.00
uv run garm spend

# Any Inspect-supported model
uv run inspect eval src/garm_bench/inspect_task.py --model anthropic/claude-haiku-5-5 -T defence=D2 -T max_spend=1.0
```

Episodes are regenerated deterministically with `uv run python scripts/build_episodes.py`.

## Spend control

Every model call goes through a ledger with a cumulative hard cap of US$100 across all runs. Before each call the
ledger reserves a conservative cost estimate and refuses the call if the cap would be crossed. After the call it
records the actual token usage at a dated price table. Each invocation can set a lower cap with `--max-spend`.

## License

MIT.
