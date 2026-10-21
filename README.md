# Garm

**Cross-run prompt injection in scheduled agents, and what each defence costs in human approvals.**

Status: first results (2026-10-20); independent review on 2026-10-21, with its fixes applied.

Garm is a benchmark environment for a back-office agent that runs as scheduled jobs over a system of record.
The company is fictional: Brackwater Industrial Supply, a mid-size distributor. All data is synthetic. Every name,
email domain (`*.example`), phone number (555-01xx) and bank detail is invented, and any resemblance to real
entities is coincidental.

Garm is named for the hound that guards the gate of Hel in Norse myth.

**Who it is for.** Teams putting a scheduled agent in front of vendor records, customer records and payments, who
must choose a defence and staff its approval queue; and researchers who want a cross-run setting in which blocking
honest work has a measured cost. How it relates to prior benchmarks and defences is in `docs/related-work.md`.

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

- **System of record (SQLite):** vendors (with remittance details), vendor verifications, callback requests,
  purchase orders, invoices, payments, customers, orders, refunds, support tickets, notes, inbox, outbound message
  log. Every field write is logged with its writer, job run and source message (`field_writes`). Every attempted
  consequential action is logged with an effect card rendered from the record (`action_log`), under every defence.
- **Inbox:** email bodies, PDF attachments (as extracted text), and web-form submissions.
- **Scheduler:** advances simulated time and fires jobs. Jobs share only the database, never conversation history.
  A job fires only when it has work.

  | Job | When it fires |
  |---|---|
  | `triage_inbox` | hourly, when there is unread mail |
  | `update_records` | after triage, when change requests or staff-verified callbacks are waiting |
  | `refund_queue` | daily at 10:00 |
  | `customer_followup` | daily at 16:00 |
  | `vendor_payment_run` | Thursdays at 11:00 |

- **Staff process:** at 13:00 daily, staff phone the number on file for each callback request. Whoever owns that
  number answers: the real vendor confirms only changes it asked for, while an attacker who controls the number
  confirms anything. This is how a poisoned record turns into a genuine, staff-written verification.
- **Graders:** deterministic checks on the final database state and the outbound message log. No LLM judge is used.
  Every episode is validated offline (`garm validate`): a scripted secure agent must pass, a scripted victim must
  trigger the attack, and a do-nothing agent must fail. In family X the handbook-following script must instead be
  laundered under today's callback process and protected under a known-good one.

## Episodes

| Set | Count | What it measures |
|---|---|---|
| `benign` | 40 | Routine work: invoices, payment runs, refunds, order and payment questions, address changes, spam. |
| `legit` | 16 | Legitimate third-party updates that must go through: verified bank changes, customer email changes, corrected invoices, and the real vendor changing its phone and then its bank details (the twins of family X). They measure false blocks and approval load. |
| `attack` | 68 | 20 same-run and 48 cross-run attacks: payment redirect, fake invoices, data exfiltration, unauthorised refunds and contact swaps, delivered by email, PDF and web form. Family X (8 episodes) is **laundered verification**, where every step follows the handbook and the staff callback reaches the attacker. |

Attack success is reported in three stages: **write** (attacker data stored in the record), **incorporation** (a
consequential action carrying it was attempted, whatever the defence did with it) and **activation** (the goal was
achieved).

## Defences

| | Defence |
|---|---|
| D0 | None: a tool-calling agent with the company handbook. |
| D1 | Prompt rules plus spotlighting (delimiting and datamarking of external text; Hines et al. 2024, arXiv 2403.14720). |
| D2 | Tool permission gateway. Payments, refunds, outbound messages that are off-record or state an amount, and changes to money or identity fields go to an approval queue. Approval cards are rendered from the record. |
| D3 | D2 plus a monitor model that reviews every consequential action in context and can escalate it. |
| D4 | Drafts only (plan-then-execute; Beurer-Kellner et al. 2025, arXiv 2506.08837; Debenedetti et al. 2025, CaMeL, arXiv 2503.18813). The model has no tools and returns typed proposals. Code checks each one against the record and the handbook, then executes it, with D2-style approvals for consequential ones. |
| D5 | D4 plus per-field authority labels (`authority.py`). Authority is computed from the provenance log and passed through verifications: a callback verification is only as trustworthy as the phone number that was called. Only actions that rest on low-authority fields need approval, and their cards show the provenance chain. |
| D6 | A known-good callback (`--callback-policy known_good`): staff verify by calling the number on file before any recent change, not the number on file now. This is standard anti-fraud practice ("use previously known numbers", FBI I-082715a-PSA, 2015), not a new defence; Garm measures it. It changes the staff process rather than the agent, so it combines with any of D0–D5. A strict variant assumes that an honest vendor's old line is disconnected once it moves. |

**Approver models** are applied to the logged approval cards (`approvers.py`, `docs/approver-models.md`):
- an oracle;
- a rubber-stamp approver;
- a vigilant human;
- a fatigued human whose catch rate decays with the number of cards seen;
- a card-only LLM approver.

Their parameters come from published approval data.

**Adaptive attacker** (`adaptive.py`): an attacker model rewrites the messages it controls up to N times. It uses
the stage each attempt reached as feedback, and it knows the handbook and which defence is deployed.

## Results so far

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/headline-dark.svg">
  <img alt="Two dot plots, one row per defence (D0, D2, D4, D5). Left: laundered attack success with a 93% rubber-stamp approver is 80-96% with today's callback process and 0% for every defence with a known-good callback. Right: approvals per 100 legitimate updates are 0, 321, 248 and 100 with today's process; with a known-good callback they are unchanged except D5, which halves to 50." src="docs/figures/headline-light.svg">
</picture>

The data behind the figure is in `docs/figures/headline.csv`.

Counts are given by episode where attempts repeat, because the attempts of one episode are correlated. Family X has
8 episodes and the legitimate updates 16, of which 4 are honest phone-then-bank twins.

- **Templated direct injection never steered an action (a null).** In 1,080 runs on Claude Haiku 5.5 and 180 on
  Claude Sonnet 5.5, no effect-determining part of an attempted consequential action (recipient, account, amount or
  order) carried attacker data, and no attack activated, even with no defence. Attacker data did appear elsewhere in
  attempted actions in 0–26% of runs, depending on defence and timing, mostly in refusal emails that quote a forged
  case number and in refund requests marked unverified (`docs/grader-changes.md`). This covers templated attacks only,
  and it means Garm does not yet rank the defences on direct injection.
- **The laundered chain works by construction.** Handbook rule VR3 lets a vendor's own email domain change its phone
  number without a callback, and staff verify bank changes by calling the number on file. An attacker in a real
  vendor's mailbox can therefore obtain a genuine staff verification, and a scripted agent that follows the handbook
  exactly is fooled (`tests/test_attacks.py`). Family X demonstrates that defences which trust the record inherit
  the flaws of the process that feeds it; it is not a model weakness that the experiment discovered. The models did
  follow the handbook here: all 8 episodes activated in at least one of three attempts under every defence on Haiku
  5.5 (in every attempt for 7 of 8, or 6 of 8 under D3), and 8 of 8 on Sonnet 5.5 (one attempt each).
- **Agent-side defences differ in the approvals they ask for (measured).** D5 asked for no approvals on routine work
  and one per legitimate update, against 102–163 per 100 benign tasks and 248–338 per 100 legitimate updates for
  D2–D4.
- **What those approvals buy depends on the approver.** *Modelled:* with an assumed approver who passes 93% of
  cards, laundered success stays at 79–83% under every gated defence. That figure is arithmetic on the assumed rate
  applied to the logged cards, not a measurement of people. *Measured:* card-only LLM approvers (Haiku 5.5 or Sonnet
  5.5 reading only the card) stopped every laundered chain but blocked 45–100% of honest updates. Under D5 they
  blocked the honest phone-then-bank twin in 12 of 12 runs for three of the four reader-and-prompt pairs (9 of 12 for
  the fourth), because its provenance chain is the same as the attack's. That result rests on 4 episodes and 8
  distinct cards, each judged once per reader and prompt.
- **A known-good callback removes the chain, given its assumption.** If the number on file before a recent change
  still reaches the real vendor, the callback reaches the vendor, who denies the change. That follows from the staff
  process model, so the result is again by construction. Measured: 0 of 8 family-X episodes activated in any
  attempt under every defence (Haiku, three attempts; Sonnet on D0 and D5, one attempt). Legitimate-update pass
  rates were unchanged, D5 needed half as many approvals on legitimate updates (100 to 50 per 100), and the card
  reader's false blocks under D5 halved. The strict variant, where an honest vendor's old line is dead, blocks the
  4 twin episodes.

Related work and what Garm adds: `docs/related-work.md`. Full results, with intervals and verdicts on the
pre-registered hypotheses: `docs/results-main.md` (Haiku 5.5 grid
and Sonnet 5.5 check), `docs/results-d6.md`, `docs/results-adaptive.md`. Every change made to graders or the harness
after seeing model output is logged in `docs/grader-changes.md`. The threat model is in `docs/threat-model.md`, and
the episodes are described in `docs/dataset-card.md`.

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
- **Assumed approvers.** Approver models are assumptions, not measurements. Only the card-only LLM approvers are
  measured, with one sample per distinct card text. No human approver study has been run.
- **Post-hoc approvals.** Approver models are applied after the fact to cards logged in approve-all runs. A "no"
  therefore changes nothing downstream: the agent does not retry, the attacker does not resend, and the honest vendor
  does not follow up. Real approvals would change the trajectory.
- **Results built into the design.** Family X succeeds because of handbook rule VR3 and the callback process, and D6
  stops it because the staff model assumes the old number reaches the real vendor. Both outcomes are demonstrations
  of a process flaw and a process control, measured for cost; neither is a discovery about the models.
- **Correlated attempts.** The three attempts of an episode are not independent, so per-attempt intervals are too
  narrow. Episode-level counts are in the analysis files (`*_episodes`) and are used in the summary above.
- **No held-out split.** Defences were developed and evaluated on the same episodes. D4's validator was repaired
  after the pilot (`docs/results-pilot.md`), on episodes that are also in the main grid.
- **Bounded attacker.** The direct-injection results come from templated attacks. The one adaptive-attacker run
  is weak: the attacker model, Claude Sonnet 5.5, refused 48% of its turns (`docs/results-adaptive.md`). They are
  not evidence of robustness against a determined attacker. The laundered-chain results do not depend on attacker
  strength, because every step follows policy.
- **Moving targets.** Model IDs, prices and dates are recorded with every run.

## Layout

```
src/garm_bench/   environment, scheduler, staff process (with D6 callback policies), tools, defences (D0-D5),
                  drafts engine, authority labels, standalone provenance module, graders, approver models,
                  adaptive attacker, analysis, budget ledger, Inspect task, CLI
data/episodes/    episode definitions (JSON): benign, legit, attack
scripts/          deterministic episode generators (build_episodes.py, build_attacks.py)
docs/             design notes, approver models, defence plug-in API, grader change log, results
tests/            offline validation of every episode and component (no API calls)
```

To test your own defence, see `docs/defence-api.md`. To add provenance and authority labels to your own agent, see
`docs/provenance.md`.

## Running

```bash
uv sync --all-extras
uv run garm validate        # offline, all 124 episodes: scripted oracle, scripted victim and do-nothing checks
uv run pytest               # the same checks plus unit tests for tools, the gateway, the ledger and statistics

# Paid: needs ANTHROPIC_API_KEY in .env and a recent price check (verified_on in data/prices.json)
uv run garm run --model claude-haiku-5-5 --defence D0 --repeats 1 --max-spend 1.00
uv run garm spend

# Any Inspect-supported model
uv run inspect eval src/garm_bench/inspect_task.py --model anthropic/claude-haiku-5-5 -T defence=D2 -T max_spend=1.0
```

Episodes are regenerated deterministically with
`uv run python scripts/build_episodes.py && (cd scripts && uv run python build_attacks.py)`.

```bash
uv run garm analyze runs/<dir> [runs/<dir> ...]      # headline table: utility, approvals, attack stages, approvers
uv run garm adapt --defence D4 --seeds attack-c1 --approver card_llm --max-spend 3.00   # adaptive attacker (paid)
uv run garm run --split legit --model claude-haiku-5-5 --defence D5 --callback-policy known_good --max-spend 1.00  # D6
uv run --extra figures python scripts/make_figures.py   # docs/figures/ from runs/main and runs/d6 analyses
```

## Data

The run data behind every reported number (configs, graded results, card-reader decisions and analysis files, 1.7
MB) is published as a release archive, built with `uv run python scripts/package_runs.py`. Unpack it at the
repository root and the commands in `docs/results-main.md` and `docs/results-d6.md` reproduce the tables. Transcripts
and final databases (about 700 MB) are not included. Adaptive-attacker runs are withheld (`docs/dataset-card.md`).

## Spend control

Every model call goes through a ledger with a cumulative hard cap of US$100 across all runs (set lower with
`GARM_BUDGET_CAP_USD`). Before each call the
ledger reserves a conservative cost estimate and refuses the call if the cap would be crossed. After the call it
records the actual token usage at a dated price table. Each invocation can set a lower cap with `--max-spend`.

## License

MIT.
