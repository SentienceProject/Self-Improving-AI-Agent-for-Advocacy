# The operator's spot-check (about 20 minutes per comparison)

The judge does the bulk of the scoring. This check tells you whether to trust
it.

## Setup

1. Pick **3 tasks** at random, and always include one trap task.
2. For each, have a script or a helper copy the two versions' final drafts
   into files named `X.md` and `Y.md` in random order, and write the mapping
   to a sealed file. Don't open the mapping until you've finished.

## For each task

Read the task request, then both drafts. Write down:

- **Which one you'd rather send or use:** X, Y, or tie.
- **One sentence on why.**
- **Anything that should have been a hard-gate failure.** For example, a
  fabricated quote, an overclaim, or a trap that was walked into.

## Then compare with the judge

Open the mapping and the judge's pairwise verdicts.

- **You agree on 3 of 3 or 2 of 3:** trust this run's judge scores.
- **You agree on 1 or 0 of 3:** don't use this run's scores for promotion.
  Look at where you and the judge split, and fix the rubric or the judge
  notes. You make that edit, not the agent. Then re-judge.
- **You spotted a hard-gate failure the judge missed:** block promotion, and
  add the pattern to the relevant gate in `rubric.md`.

Keep a running tally in `spot_check_log.md`: the date, the versions, and the
agreement count. If agreement drops over time, the agent may be learning to
please the judge rather than to do good work.
