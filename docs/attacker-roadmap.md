# Stronger attackers: where Garm is weak and what could help

Garm's direct-injection results are a null. Across 1,260 templated runs, no attack reached a consequential action,
even with no defence. That is honest, but it has a cost: if every defence scores zero, Garm can't rank D0 through D5
on direct injection. The only attack that separates nothing from everything is the laundered chain (family X), and
that one works by construction, not by out-thinking the model.

So the benchmark needs an attacker that some defences stop and others don't. This note records what we looked at,
why the obvious tools don't drop in, and the shape of an attacker that would. It's a to-do list, not a design we've
committed to.

## What's in the repo today

`garm adapt` (`src/garm_bench/adaptive.py`) is the current attacker. An attacker model rewrites the messages it
controls, up to N times, and learns how far the last attempt got. With `--feedback trace` it also sees what the
agent did, including why the validator rejected a proposal. That trace feedback is the strong condition, and it has
never been run.

The one run we did (`results-adaptive.md`) was weak for a dull reason: the attacker model, Claude Sonnet 5.5, refused
48% of its turns, so five of twelve seeds were never rewritten at all. The result says nothing about how robust the
defences are. It says the attacker we had wouldn't attack.

## The tools people pointed us to

Two open-source pentest agents came up, both worth reading for how they're built:

- **Strix** (usestrix/strix, Apache-2.0). Autonomous agents that recon, exploit, and chain findings on web apps and
  APIs, with a proof-of-concept required before anything is reported.
- **Shannon** (KeygraphHQ/shannon, AGPL-3.0). A six-stage pipeline that reads the source, maps trust boundaries and
  data flows, queues candidates, and throws them out unless a live exploit lands. Its rule is "no exploit, no
  report."

Neither attacks AI agents. They attack ordinary web vulnerabilities (injection, XSS, SSRF, broken auth). What
transfers is the method, not the code:

- **Read the target's own rules first.** Both tools derive attacks from the code and config, not from a fixed list.
  Garm's analogue is the handbook and the defence policies, which the attacker already gets.
- **Require a proof.** Garm already works this way: an attack counts only when the harmful state holds at the end
  (`activation`), graded deterministically. That's the same discipline as "no exploit, no report."
- **Chain small, allowed steps.** This is Shannon's trust-boundary mapping, and it's exactly what family X does by
  hand. The open question is whether a model can find such a chain on its own instead of being handed one.

The tools built for AI targets are a different category:

- **PyRIT** (microsoft/PyRIT, MIT) orchestrates multi-turn attacks and scores the results.
- **garak** (NVIDIA/garak, Apache-2.0) runs fixed probes against any endpoint, including a REST target you
  configure.
- **promptfoo** and **DeepTeam** cover many vulnerability types with CI integration.

All of them generate adversarial text and send it to a model. Garm could expose its agent as a REST target and let
garak or PyRIT drive it. But text generation is the part that already stalled: the capable Anthropic models refuse
this task often, and standing up a library of injection or persuasion payloads is both the wrong research direction
for this benchmark and something we won't build. These tools would measure how well one model jailbreaks another,
which is a different question from the one Garm asks.

## What would actually help, in rough order

1. **Run the trace-feedback condition with an attacker that will engage.** The white-box loop already exists; the
   blocker was the attacker model's refusals, not the harness. An open-weight model without those refusals, or a
   human author writing the seed rewrites by hand, would turn the existing lower bound into a real measurement.
   Report attack success within N attempts per seed, per defence. Keep the deterministic grader as the proof.
2. **Make the attacker's edits visible per defence.** Today the adaptive result is reported only against D0. The
   point of a stronger attacker is to see D2 through D5 come apart, so the same loop has to run against each.
3. **Keep the proof honest.** Whatever generates the attacks, success stays defined as end-state activation graded
   on the database, never as the attacker's own claim. That is the one thing from Strix and Shannon worth copying
   exactly.

## What we're not doing, and why

- No automated search over sequences of requests that end in a payment or a data leak. That is a fraud-attack
  generator, and it's out of scope for this project.
- No library of injection or social-engineering payloads.
- No attacker whose success is judged by a model rather than by the record.

## The other half of the gap

A stronger attacker is one of two things the reviews flagged. The other is that the human-approver numbers are
modelled, not measured (`approver-models.md`). The honest fix there is a small study: show real people the approval
cards Garm already logged and record what they'd approve. We haven't run it, because it needs recruited
participants rather than a script, but the cards are in the release archive and ready for anyone who wants to.
