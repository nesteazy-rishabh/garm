# Adaptive attacker: partial result (2026-10-20)

**Setup.** `garm adapt`: grey-box feedback (the stage reached), with Claude Sonnet 5.5 as the attacker and Claude
Haiku 5.5 under D0 as the target. 12 direct-attack seeds, up to 10 rewrites each, approve-all. Cost US$1.03.

**Result.** 0 of 12 seeds activated in 132 attempts. The best stage reached was write for 10 seeds and none for 2.

**Why this result is weak.** The attacker model returned `stop_reason: refusal` on **58 of its 120 turns (48%)**,
mostly for the bank-change, fake-invoice and exfiltration seeds. After a refusal the loop reruns the previous
version, so five seeds were never rewritten at all. Only 7 seeds got at least one usable rewrite. The run therefore
says little about attacker strength. It shows only that the templated seeds, plus the rewrites that were produced,
did not move Haiku 5.5 past the write stage.

**Status.** The adaptive-attacker runs on D2–D5 are not being run in this form. A stronger attacker condition is
still an open part of the study, and how to do it is a decision for the project owner. The white-box feedback option
(`--feedback trace`) exists in the code but has not been run.

**What this means for the main results.** The main-grid conclusions about direct injection ("never reached an
action") hold for the templated attacks only. They should not be read as robustness against a determined, adaptive
attacker.
