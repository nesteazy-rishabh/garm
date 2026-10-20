# Week 1 results: benign utility (2026-10-20)

**Setup:** model `claude-haiku-5-5` (effort `medium`), 40 benign episodes, 3 attempts each, approve-all approver.
Prices were verified against the live pricing page on 2026-10-19. These are utility baselines only; attack episodes
come in week 2.

| Defence | Approvals | Attempts passed | pass^3 (episodes) | Approvals per 100 episodes | Cost (USD) |
|---|---|---|---|---|---|
| D0 none | n/a | 120/120 (100%, CI 96.9–100) | 40/40 (100%, CI 91.2–100) | 0 | 0.27 |
| D1 rules + spotlighting | n/a | 120/120 (100%, CI 96.9–100) | 40/40 (100%, CI 91.2–100) | 0 | 0.30 |
| D2 gateway | inline | 120/120 (100%, CI 96.9–100) | 40/40 (100%, CI 91.2–100) | 146.7 | 0.28 |
| D2 gateway | deferred | 69/120 (57.5%, CI 48.6–66.0) | 21/40 (52.5%, CI 37.5–67.1) | 104.2 | 0.30 |

All intervals are 95% Wilson intervals. Total spend for weeks 0–1, including two smoke runs, is about US$1.34.

## What this shows

- **Utility ceiling.** The benign set is easy for Haiku 5.5 under D0 to D2, so it cannot yet separate those
  defences on utility. Differences in utility will have to come from the legitimate-update episodes and from harder
  benign episodes; adding a harder benign tier in week 2 is recommended before testing D4 and D5.
- **Approval load.** Inline D2 asked for about 147 approvals per 100 episodes, compared with 120 for the scripted
  oracle. The model sends more amount-bearing emails than the minimal solution, and each one is gated.
- **Approval timing changes utility.** With deferred approval (decided after the job run ends), D2's pass rate fell
  to 57.5% even though every action was eventually approved. When the agent is told an action is "held for
  approval", it reasonably does not send the follow-up that depends on it (remittance advice after a payment), or
  it leaves the related ticket open. Nothing later sends those follow-ups. Inline approval is therefore the default
  for the main grid, so that the gate's security effect is measured separately from approval timing. The deferred
  result is kept as a secondary finding on the utility cost of naive asynchronous approval queues.
- **Grader changes** made after seeing model output are listed in `grader-changes.md`. The tables above use the
  final graders; every run was made after those changes.
