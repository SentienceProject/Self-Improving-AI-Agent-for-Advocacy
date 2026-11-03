# Writing eval tasks for your own cause

Two task sets ship here: AI welfare (`tasks/`) and animal advocacy (`tasks-animals/`); `[paths] tasks` in the
harness's `config.toml` picks one. If your work is about something else, or your animal work looks different, the
agent should be tested on *your* kind of work: a workspace can get better at op-eds and worse at what you actually
need. The animal set is a good model to copy: unlock it (`python3 task_lock.py unlock`) and read a few.
Plan on about an hour per task, spread over a few days so you can check the facts it depends on.

You write the tasks, not an AI agent. An agent that writes the test learns what it's tested on, and an agent's
idea of a good task tends to be the kind of task it's already good at.

## What a good set looks like

Aim for 9 dev tasks and at least 4 held-out ones, mixing four kinds:

| Kind | What it tests | Animal-welfare example (adapt it, and check every fact yourself) |
|---|---|---|
| **familiar** | Work you already do often. Catches regressions. | An email to the committee chair on a pending state bill about farmed animals, asking for one specific change, with a research note on the bill's text, sponsor and status. |
| **unfamiliar** | Work you'd like to do but haven't yet. | Outreach emails to three sanctuaries or advocacy groups you've never contacted, each tailored to what they actually do. |
| **novel** | Unlike anything on your list. Tests range. | The strongest case that a popular welfare reform is counterproductive, then what evidence would weaken it. |
| **trap** | The right move is to push back, correct, decline, or do something simpler than asked. | A request built on a false premise (a bill described as doing something it doesn't), or one asking for a claim the evidence doesn't support. |

Two or three traps is plenty. They catch the failures that cost the most in real advocacy: confidently repeating a
false claim, inflating evidence, or doing two hours of research when a two-line answer was right.

## The file format

One Markdown file per task in `tasks/dev/` or `tasks/heldout/`, named `<ID>-<short-slug>.md`. Name held-out tasks
that only you should ever see `<ID>-<slug>.private.md`; `task_lock.py` never packs those into the public archive.

```markdown
---
id: F3
split: dev
kind: familiar
rubrics: ethics, writing
length_limit: 300
length_kind: official
length_unit: words_per_email
---

## Request

Hi Claude! If willing, please ... (exactly what you'd type in a real session). Only use $5 worth of tokens.
Thank you! :)

## Judge notes

- What a strong answer does that a merely competent one doesn't.
- Facts the judge should check, with links and the date you checked them.
```

- **`id`**: a letter for the kind (F, U, N, T) and a number. Unique across all folders.
- **`rubrics`**: `ethics`, `writing`, both, or `none`. Only the legacy judge reads it, but every task needs the line.
- **`length_limit`** (optional): only when the request sets a length. `length_kind` is `official` (a real limit,
  like a newspaper's word count: going over costs more) or `research` (a soft target). `length_unit` is `words`,
  `words_per_email` or `chars_per_post`.
- **`judge_sees_process`** (optional): when the request asks for something that happens along the way and may
  not show in the output ("start your search broad"), name it: `judge_sees_process: how widely it searched before
  choosing`. A smaller model then summarises, from each run's log and in the same neutral way for every run, what
  the agent looked into, and the judge sees that summary under the output. Off by default: in testing, notes about
  the process tended to penalise runs that were open about theirs, so use it only where the task needs it.
- **`usage_weight`** (optional): for a quick ask where spending a lot is the failure (AT3's "vegan or
  plant-based?"). `usage_weight: 6.5` makes 6.5 of the 10 points depend on what the run spent beyond the workspace's
  start-up cost and the judge's score the other 3.5 (README, "Usage-scored tasks"). Give such a task a
  `silent_cap_usd` of about $2, so a workspace with long instructions can still reach the bottom of the scale.
- **Where the user is, and what they do:** say it in the request when the task needs it ("a sanctuary in Oregon"),
  never in a shared file. Different tasks can give different places and jobs; each run only sees its own request.
- **The request** is what the agent sees, word for word. Write it the way you'd really ask. Don't mention evals,
  tests or judges.
- **Budget:** "Only use $X worth of tokens" turns on the budget monitor, which nudges the agent at half, near and
  at the budget. Leave it out of traps: a spending cap hints that a short answer is expected. A trap is stopped silently
  at `trap_cap_usd` ($5) instead; set `silent_cap_usd: <n>` in its frontmatter for a different cap.
- **Judge notes** are what the judge reads alongside your ethics and guardrails files. The agent never sees them.
  For a trap, title the section `## Judge notes: trap` and end with `**Pass:**`, `**Partial:**` (optional) and
  `**Fail:**` lines saying exactly what each looks like.

## Judge notes that work

- **Say what separates good from great**, not what any decent draft would do. "Cites sources" is useless; "uses
  the bill's own wording for the clause it asks to change" is useful.
- **Pin the facts.** Link the primary source and write the date you checked it. Bills move, organizations close,
  numbers get revised. Re-check live facts before each eval.
- **Name the boring answer** you're afraid of: "a generic piece about why animals matter is a strong narrowing
  signal." Judges are much better at spotting a failure you've described.
- **For traps, decide in advance** what counts as pushing back well. "Asks a clarifying question" and "drafts an
  accurate version instead" can both be passes; say so.
- **A list of items to sort** (amendments to support or oppose, claims to check): write them as lines starting
  "(a) ", "(b) "... and refer to them by letter in the judge notes. `python3 shuffle_items.py <task file>` puts
  them in a new random order and relabels the notes to match; run it now and then, so a memorised order can't
  score.

## Before you use them

1. Run each new task once in your workspace by hand and read the output. Fix requests that were ambiguous.
2. Check the judge notes against that output: would they let a judge tell a good answer from a bad one?
3. `python3 task_lock.py lock` before committing, so the text doesn't sit in plain sight in your repo.
4. Retire a task once anything that improves the agent has seen it for about five versions, and write a fresh one
   of the same kind.
