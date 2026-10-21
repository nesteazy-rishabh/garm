# Related work

Checked against the arXiv abstracts on 2026-10-21. Garm makes no claim to be first at any single piece below; what it
adds is the combination in the last section.

## Injection benchmarks for tool-using agents

- **AgentDojo** (Debenedetti et al. 2024, arXiv 2406.13352). A stateful environment with formal checks on the
  environment state, reporting utility and attack success for each defence. Garm's deterministic state graders and
  per-defence utility follow the same idea. Garm differs in what runs between attacks: scheduled jobs that share only
  a system of record, and a staff process that acts on it.
- **InjecAgent** (Zhan et al. 2024, arXiv 2403.02691) and **Agent Security Bench** (Zhang et al. 2024,
  arXiv 2410.02644). Indirect injection through tool outputs, single session, many tools and attack templates.
  Garm has far fewer attacks and tools; its attacks are templated in the same way.
- **CarryoverBench** (Xu et al., "After the Attacker Leaves", arXiv 2610.28425, 2026-10-20). A one-time injection
  planted in persistent memory or a scheduled task re-fires in later sessions; planting and triggering are scored as
  separate stages, over 101 tasks, 12 personal-assistant scenarios and five models. It does not evaluate approval or
  utility. **This is the closest work on "planted in one run, fires in a later one".** It appeared while Garm was
  being run. Garm's cross-run framing is therefore not new; Garm's write, mention, incorporation and activation
  stages play the role of CarryoverBench's planting and triggering stages. The persistent state differs (a relational
  record with field-level provenance, rather than agent memory), and on Garm's templated direct attacks the carried-
  over data never reached an action (0 of 1,260 runs), where CarryoverBench reports persistence without decay.

## Provenance, authority and information flow

- **CaMeL** (Debenedetti et al. 2025, arXiv 2503.18813) and the **design patterns** paper (Beurer-Kellner et al.
  2025, arXiv 2506.08837). Plan-then-execute with capabilities on data. Garm's D4 is the generic pattern; D5 adds
  per-field authority computed from a write log.
- **FIDES** (Costa et al., "Securing AI Agents with Information-Flow Control", arXiv 2505.23643). Confidentiality
  and integrity labels propagated through a planner. D5 is a narrow, record-level instance of integrity labelling.
- **AuthMem-Bench** (Zhan et al., "When Memory Becomes Authority", arXiv 2608.01679). Memory consolidation drops the
  source constraints on a claim ("authority collapse"); persisted authority labels cut unauthorised actions from 16.9%
  to 0% with benign success unchanged. D5 is the same kind of defence for a system of record. Garm's addition is the
  honest twin: when an outsider-written value is later verified through a channel the outsider also wrote, the
  honest and the hijacked case carry the same label, so labels alone cannot separate them.
- **PPMF** (Xu et al., "Memory Provenance Laundering in LLM Agents", arXiv 2607.29167). Consolidation rewrites an
  external observation as if it were the user's own history, so the low-trust origin is lost ("provenance
  laundering"); a middleware firewall gates tool calls by the authority of the memories behind them. **Terminology:**
  Garm's "laundered verification" is a different mechanism. Nothing is lost from the provenance log; a genuine staff
  verification is obtained through a callback number the attacker set earlier under a policy that allows it. D5
  keeps that chain visible, and it still does not decide the case.

## Approval and oversight

- **"Optimizing Agent Planning for Security and Autonomy"** (Kolluri et al., arXiv 2602.11416). Argues that system
  defences should be credited for the human oversight they save, and measures autonomy as the share of
  consequential actions completed without approval, on AgentDojo and WASP. **Garm's approvals per 100 tasks is the
  same kind of metric.** Garm adds approver models that turn those approvals into expected attack success and false
  blocks, and honest twins that make blocking honest work count.
- **"Oversight Has a Capacity"** (Turan, arXiv 2606.08919). Reviewers disagree on what is risky, and a fatiguing
  reviewer makes more escalation less safe past a point. Garm's fatigued-human model takes this form, fitted to
  published catch rates (`approver-models.md`).
- **"Approve What, Exactly?"** (Kodathala et al., arXiv 2610.21063). Approval cards must be rendered from a canonical
  descriptor that the agent cannot narrate; LLM approvers catch far more attacks from trusted cards than from agent
  narration. Garm renders every card from the record for the same reason.
- **Loopjacking** (Kumar, arXiv 2609.21081). The human approves one operation and the system executes another.
  Garm binds each approval to the action it executes and does not test this path.

## The known-good callback is existing practice

D6 is not a new defence. Verifying a payment change by calling a number already known, not one supplied with the
request, is standard business-email-compromise guidance: the FBI's 2015 public service announcement I-082715a-PSA
(https://ic3.gov/PSA/2015/PSA150827_1.pdf, retrieved 2026-10-21; the site's TLS certificate had
expired that day, so some clients refuse it) advises, "When using phone verification as part of the two-factor
authentication, use previously known numbers, not the numbers provided in the e-mail request." What Garm adds is a
measurement: under its assumptions, how much of the laundered chain this control removes for each agent-side
defence, what it costs honest vendors, and how it changes the approval load.

## What Garm adds

Taken together, Garm's contribution is a measurement setup rather than a defence:

1. defences compared on attack success, utility and human approvals per 100 tasks in one table, with approver
   models that turn approvals into expected outcomes;
2. paired honest twins, so that blocking honest third-party updates is a measured cost;
3. a relational system of record with field-level provenance and a staff process, in a finance back-office setting,
   in which a policy-compliant chain of steps produces a genuine verification for the attacker;
4. an episode-level account of how a process control (D6) and record-level authority labels (D5) combine.
5. a measured result about LLM approvers: they act on provenance correctly (removing it roughly halves their
   rejections, and trusted provenance gets approved every time), and still cannot separate the honest twin from the
   attack, because the record gives both the same chain (`results-main.md`, provenance ablation).

Its empirical findings are limited by design choices described in `../README.md` (What it doesn't show).
