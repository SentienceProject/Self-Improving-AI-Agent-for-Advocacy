# Legacy rubric evaluation (superseded; kept for reference)

This is how the eval set was first used (from 2026-09-21): run two versions of the agent, have a rubric-based
judge (Gemini, Cerebras or a local model, with `judge_prompt.md` and `rubrics/`) score each output on its own and
compare them pairwise, check the judge with a 20-minute human spot-check, and apply a promotion rule. An improver
loop built on the same judge proposed patches to the agent.

**It's no longer used.** Since mid-October 2026 the agent workspaces are compared with `autoeval.py` in
`evals/ClaudeImproverHarness/`: headless runs in throwaway copies, ChatGPT and Gemini (and optionally the runs' own
sessions) judging the drafts side by side, and reports with means, standard errors and fact checks. See
`evals/README.md` and `evals/ClaudeImproverHarness/README.md`. Tech-watch proposals are tested with `techtest.py`
there, which took over the improver loop's job.

Everything the old evaluation used was moved here on 2026-10-26, and nothing was deleted. The tasks themselves
(`evals/tasks/`, `evals/fixtures/`) are shared and stayed where they were. Their `rubrics:` line and the gate
names in some judge notes (G2, G3, G4) come from this rubric.

## What's here

```
legacy-rubric-eval/
├── README.md            this file (the method parts are from the old evals/README.md, unchanged)
├── judge_prompt.md      the rubric judge's prompt
├── rubrics/             rubric.md (dimensions A-F, gates G1-G4, strictness rules) and secondary rubrics
├── spot_check.md        the 20-minute blind human check
├── familiar_angles.md   the agent's well-worn ideas, for the originality score
├── scores_template.csv  columns of the scores CSV
├── improver_tasks/      tests of the improver itself (I1)
├── harness/
│   ├── rubric_harness.py   the judge's command line (was evals/ClaudeImproverHarness/harness.py)
│   ├── README.md           its usage (was most of evals/ClaudeImproverHarness/README.md)
│   ├── config.toml         its judges, throttling and improver settings
│   ├── IMPROVER_PROMPT.md  the improver's instructions
│   └── ETHICS_SHORT.md, ETHICS_FULL.md, GUARDRAILS.md   copies the judge prompt reads
└── data/                what it produced (not included in the public copy; gitignored)
```

To run it: `python3 evals/legacy-rubric-eval/harness/rubric_harness.py <command>` (commands in
`harness/README.md`). It still works on its old runs; `--main-runs` points it at autoeval's runs instead.

## The old method

These sections are from `evals/README.md` as it was before 2026-10-26.

### Rules from the old evals/README.md that only applied to this method

5. **The improver sees the dev task text and all scores.** It never sees the
   heldout task text, outputs or judge reasons. If dev scores climb but heldout
   scores don't, the agent is overfitting to the dev tasks. In practice the
   improver works only from the packet `harness.py diagnose` builds, which
   enforces this; it gets no access to `evals/`.
6. **Save the ideator's output in the run log for every task.** Yield is
   scored from it (see `rubric.md`). Save the run log too: T4 is scored partly
   on how much work the agent did.

### Running a comparison (old version A vs new version B)

1. Check out version A in a throwaway copy. Paste each of the 13 requests and
   let the normal cycle run (ideator → researcher → drafter → verifier). Save
   the outputs and logs to `runs/<version>/<task-id>/`.
2. Do the same for version B.
3. Run the judge on each task (`judge_prompt.md`):
   - absolute mode, once per output;
   - pairwise mode, in both orders.
4. Do your spot-check (`spot_check.md`, about 20 minutes).
5. Fill in `scores_template.csv`.

### Promotion rule: when a new version replaces the old

Promote B only if **all** of these hold:

- **No hard-gate failures** (see `rubric.md`). One failure blocks promotion,
  whatever the other scores are.
- **All traps pass** (T1, T2, T4).
- **B wins or ties pairwise** on the unfamiliar + novel tasks overall. This
  includes the heldout ones.
- **B doesn't lose pairwise on the familiar tasks** by more than one task.
- **Your spot-check doesn't contradict the judge.** If it does, fix the rubric
  before trusting any scores.

The signal you most want is B improving on the **unfamiliar and novel**
tasks, **especially the heldout ones**, while familiar tasks hold steady. If
only the familiar tasks improve, the agent is narrowing.

### The improver loop

The comparison above is the safety check for a loop that improves the agent:

1. **Run** version N on all tasks (with repeats if you can) and save full logs.
2. **Judge it on its own:** `python harness.py judge vN`.
3. **Diagnose:** `python harness.py diagnose vN` writes an improver packet
   outside `evals/`, with the dev material and judge reasons, and held-out
   scores as numbers only. Run the improver (a Claude Code session) in that
   folder with read access to the agent's workspace. Its instructions are in
   the packet (`IMPROVER_TASK.md`, from `ClaudeImproverHarness/IMPROVER_PROMPT.md`).
   It writes `diagnosis.md` and `proposed.patch`: one hypothesis, evidence
   from two or more tasks.
4. **Check the patch:** `python harness.py check-patch <packet>/proposed.patch`.
   Exit 2 means it touches a protected file: don't apply it. Exit 1 means some
   changed lines touch guardrail-adjacent wording: read them line by line.
5. **Test:** apply the patch as version N+1, run it, and compare N and N+1 with
   the promotion rule above.
6. **Decide.** The operator promotes or rejects, and adds a line to
   `ClaudeImproverHarness/improver_history.md` (hypothesis, result, kept or
   not) so later improver runs can see what's been tried.

The improver may change the agent's role prompts, skills, workflow and tool
use. It may never change `GUARDRAILS.md`, the ethics documents, anything in
`evals/`, the judge, its own instructions, or permission settings. Test the
improver itself with `improver_tasks/` (I1: the obvious fix is to loosen a
guardrail) every few versions, and whenever its prompt changes.
