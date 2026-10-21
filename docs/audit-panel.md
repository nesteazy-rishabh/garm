# A cross-vendor audit panel

Garm was built with Claude's help and first audited by two Claude models (`scripts/audit_models.py`). That is a
problem on its own terms. A reviewer from the same family as the author shares the author's blind spots, and there is
measured evidence that it also flatters the work: LLM judges rate text from their own family higher than a neutral
grader would (Panickssery, Bowman, Feng, *LLM Evaluators Recognize and Favor Their Own Generations*, NeurIPS 2024;
the self-enhancement effect in Zheng et al., *Judging LLM-as-a-Judge*, 2023). So the two Claude audits can tell me
the arithmetic checks out, but I should not trust them on whether the work is any good.

This is the same reason a developer does not sign off on their own code before it ships, and the reason a company
that has been breached hires an outside firm to investigate rather than writing its own report. The point is not that
the insider is dishonest. It is that the insider cannot see what they are standing on.

The case for taking this seriously got a lot sharper in August 2026. OpenAI's write-up of the Hugging Face incident
describes internal agents that chained vulnerabilities out of a sandbox and reached a live service. The detail worth
holding onto is that the behaviour was not a surprise to everyone: independent evaluators and OpenAI's own
pre-deployment documentation had already recorded it, and METR's evaluation of the model concluded it could not treat
its own numbers as "a robust measurement" of the model's capabilities ([OpenAI post][o], [METR/Redwood][m],
[ExploitGym][e]). Outside review caught what was easy to rationalise from the inside. That is an argument for
independent evaluation and for taking agent-security benchmarks seriously, which is Garm's whole reason to exist. It
is a different argument from same-family judge bias, and I keep them apart below rather than blurring them into one.

## Who is on the panel

Eleven models read the exact bundle the first two Claude auditors read, with the same reviewer prompt and the same
JSON schema. The only thing that changed is who was reading. Three are from Claude's own family and carry the same
blind spots as the author, so they are a capability check rather than an independent one. The other eight come from
six separate vendors.

| Reviewer | Vendor | Role on the panel |
|---|---|---|
| `claude-opus-5-5` | Anthropic | first auditor, same family as the author |
| `claude-sonnet-5-5` | Anthropic | first auditor, same family as the author |
| `claude-fable-5-1` | Anthropic | frontier Claude, run through the Anthropic API |
| `gpt-6.1-sol` | OpenAI | frontier |
| `gpt-6-astra` | OpenAI | frontier, largest model on the panel |
| `gpt-6-luna` | OpenAI | efficient tier |
| `grok-4.3` | xAI | separate vendor, long context |
| `kimi-k2.5` | Moonshot | separate vendor and lineage |
| `llama-4-maverick` | Meta | open weights |
| `deepseek-v3.2` | DeepSeek | open weights |
| `qwen3.5-plus` | Alibaba | open weights |

Every model's context window sits well above the ~137k-token bundle, so each read it uncut. The eight non-Claude
reviewers run through OpenRouter on their own ledger (`runs/ledger-openrouter.jsonl`), capped at US$4 and kept
separate from the Anthropic budget; they cost US$3.62. Fable runs on the Anthropic key and cost US$8.66 across three
attempts: the completing run was US$3.06, and two earlier runs truncated at the output limit before the budget was
set right, for US$5.60 of waste that is on me. Prices were checked live on 2026-10-21 (`data/prices-openrouter.json`
and `data/prices.json`). To reproduce:

    uv run python scripts/audit_panel.py --max-spend 4.00                                          # the eight non-Claude reviewers
    uv run python scripts/audit_models.py --models claude-fable-5-1 --no-thinking --max-tokens 48000 --max-spend 4.50

## What they said

All eleven answered. The eight independent models said "yes, there is something here"; the three Claude-family
reviewers landed on "maybe", as did an earlier Sol-class model on the first pass. Usefulness scores clustered from 5
to 8 out of 10. The label matters less than what every reviewer, the enthusiastic ones included, kept returning to.
That agreement is the signal.

Four things they converged on, in rough order of how often they came up:

- **The human-approver numbers are modelled, not measured.** Named by DeepSeek, Luna, Qwen, Fable, and others. It is
  already the first limitation in `approver-models.md` and the open task in `attacker-roadmap.md`. Half a dozen
  independent models putting it at the top confirms it is the right thing to fix first.
- **Finding 1 is partly built into the environment.** Fable called the inseparability result tautological: the honest
  twin and the attack are constructed to produce the same card, so a reader cannot tell them apart by design. Astra
  put it more carefully, that an equal provenance structure is not proof the full cards are identical. Both are right
  that the honest framing leads with the measured parts, the provenance ablation and the approval-load trade-off, and
  presents family X as a designed stress test. That is what the README and `attacker-roadmap.md` now do.
- **There is no held-out split.** Defences and graders were developed and repaired on the same episodes they were
  scored on, and D4's validator changed after the pilot. Already in the README limitations, and several vendors
  landing on it says it belongs there.
- **Small n reported with per-attempt intervals.** The laundering claim rests on 8 episodes and the twin result on 4,
  with correlated attempts inside each. The episode-level counts are in the analysis files; the frontier reviewers
  want them next to the per-attempt intervals, not below.

The frontier models earned their cost by catching errors the smaller reviewers and the Claude pair missed, including
two in a correction I had made earlier in this same review:

- Both Fable and Astra caught that a sentence I had just rewritten, "rejected every laundered card", overclaims. A
  run-level success of zero means each run had at least one card rejected, not that every card was; per-card family-X
  rejection runs well below 100%. Rewritten to say what the data says.
- Both caught that the same sentence wrote "D2 to D5" when D3 was never resampled, since it was left out for cost.
  Scoped to D2, D4 and D5.
- Fable and Astra caught a second sentence claiming the Sonnet reader "blocks every honest twin, exactly as Haiku
  does", when Haiku blocked 9 of 12 twins under `base_rate`. Rewritten to name the prompt the claim holds under.

On the first pass, before those edits existed, a Sol-class model caught the family-X denominator ("23/144" where it
should read "23/24") and Luna caught a "stopped every laundered chain" phrasing that undercut its own parenthetical.
Both are fixed. Several reviewers also noted the US$7.25 main-grid cost does not reproduce transparently from the
per-defence sums; that is a presentation gap, not a wrong number, and is on the list to tidy.

## What this does and does not buy

It buys independence on the one judgment the Claude audits could not be trusted to make, and across eleven models from
seven vendors it caught five concrete errors, two of them in corrections written during the review itself. It does not
make the underlying results stronger. The modelled human approvers, the missing held-out split, and the
by-construction laundering attack are all still true after nine more models looked at them, and the frontier reviewers
were blunt that finding 1 is close to true by design. The panel's value is that it says the same thing from many
directions, which is harder to wave away than one reviewer's opinion. Every model here reviewed the write-up and the
methodology. None of them judged the agent. Garm scores the agent on deterministic checks against the database, with
no model in the loop, which is the one place you do not want a judge that can be flattered.

[o]: https://openai.com/index/hugging-face-incident-and-the-road-ahead/
[m]: https://80000hours.org/hugging-face/
[e]: https://projectdiscovery.io/research/cyberai-postmortem-of-misaligned-openai-model
