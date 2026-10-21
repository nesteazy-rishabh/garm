# On building a stronger attacker

Garm has one soft spot I want to be honest about. Its direct-injection results are a null: across 1,260 templated
runs, no attack reached a consequential action, even with no defence at all. A null is a real finding, and I'm glad
to report it. The price is that when every defence scores zero, I can't tell them apart on direct injection. The
only attack that pulls them apart is the laundered chain (family X), and that one works because I built the
environment so it would, not because the attacker outwitted anything.

So the benchmark needs an attacker that some defences stop and others let through. The question was how to get one.

## Two ways to get there

I had two ideas. The first was to recruit a few AI-security researchers and have them attack the thing properly,
by hand, the way a red team would. That is the right way to do it, and I'd like to do it one day. It is also not
something I can stand up in an afternoon: the people who are good at this are busy, and a serious engagement is a
project of its own.

The second was to lean on work that already exists. Autonomous pen-testing has had a loud year on GitHub, and some
of those projects are genuinely good. If a tool can already find real bugs in real software, maybe I could point it
at Garm, or at least learn how it is built and borrow the shape. That I could start on the same day, so that is
what I did.

## What I looked at, and what I wanted from each

I went looking for projects that were trending, had real traction behind them, and shipped something that ran
rather than a paper. Two stood out, both with tens of thousands of stars.

**Strix** (`usestrix/strix`, Apache-2.0). A swarm of agents that do recon, exploit, and chain findings together on
web apps and APIs, with an interception proxy, a browser, and a sandbox to run exploit code in. What drew me to it
was the insistence on a working proof. It doesn't report a vulnerability it hasn't actually fired. I wanted to see
whether that discipline, demanding a demonstrated exploit before anything counts, was something I could hold my own
attacker to.

**Shannon** (`KeygraphHQ/shannon`, AGPL-3.0). An autonomous pen-tester that reads your source first, maps the trust
boundaries and the data flows, and only then goes after the running app. Its rule is blunt and I like it: "no
exploit, no report." What I wanted from Shannon was its front half, the part where it reasons about where trust
crosses a line in the system before it tries anything. That is the same move family X makes by hand, and I wanted
to know whether a tool could make it on its own.

I also looked at the tools built specifically to attack models, PyRIT (Microsoft) and garak (NVIDIA). They matter
to the space, so I read them, but I'll say below why they answer a different question than mine.

## What struck me

The first thing that struck me is that neither Strix nor Shannon attacks an AI agent at all. They attack ordinary
software: injection, cross-site scripting, server-side request forgery, broken authentication. My target is a model
following a handbook, which is a different animal. So the code doesn't transfer. The method does, and three habits
are worth taking:

- They derive their attacks from the target's own code and configuration, not from a canned list. Garm's version of
  that is the handbook and the defence policies, which my attacker already gets to read.
- They refuse to trust themselves. A finding is nothing until an exploit proves it. Garm already works this way: an
  attack counts only when the harmful state is still true at the end, checked against the database, never on the
  attacker's say-so. Seeing two mature tools converge on the same rule made me more confident the grader is right.
- They chain small, individually-allowed steps into something harmful. That is exactly what laundering is. Strix and
  Shannon find those chains on their own. In Garm I wrote the chain by hand and the open question is whether a model
  can discover one unaided.

The tools aimed at models, PyRIT and garak, struck me differently. They are good at what they do, which is to
generate adversarial text and throw it at an endpoint, then score what comes back. I could wire Garm's agent up as
a target and let them drive. The trouble is that the text-generation step is the one that already stalled on me: the
capable models refused the attacker role about half the time in my one adaptive run. Pointing these tools at Garm
would mostly measure how willing one model is to jailbreak another, which is a real question but not the one Garm is
asking. And building a stockpile of injection or persuasion payloads is both the wrong direction for this benchmark
and something I'm not going to do.

## Where that leaves the attacker

The honest version of "stronger attacker" for Garm doesn't need any of these tools bolted on. It needs the loop I
already have (`src/garm_bench/adaptive.py`) run properly. That loop lets an attacker rewrite the messages it
controls and, with `--feedback trace`, see exactly what the agent did and why the validator refused a proposal.
That is a white-box attacker in everything but the model's weights, and I have never managed a clean run of it,
because the attacker model kept refusing (`results-adaptive.md`).

So the to-do list is short and specific:

1. Run the trace-feedback loop with an attacker that will actually engage, whether that is an open-weight model
   without the refusals or a person writing the rewrites by hand. Turn the current lower bound into a measurement.
2. Run it against every defence, not just D0. The whole point is to watch D2 through D5 come apart, if they do.
3. Keep the proof exactly as strict as Strix and Shannon keep theirs. Success stays defined as end-state activation
   on the database. That is the one thing from those projects worth copying line for line.

And a few things I'm deliberately not doing: no automated search over sequences of requests that ends in a payment
or a leak, because that is a fraud generator and it's out of scope; no payload library; and no attacker whose wins
are judged by a model instead of by the record.

## The other half of the gap

A stronger attacker is one of the two things the reviews kept flagging. The other is that my human-approver numbers
are modelled rather than measured (`approver-models.md`). The clean fix is a small study: put the approval cards
Garm has already logged in front of real people and record what they'd wave through. I haven't done it, because it
takes recruited participants rather than a script, but the cards are sitting in the release archive for anyone who
wants to pick it up.
