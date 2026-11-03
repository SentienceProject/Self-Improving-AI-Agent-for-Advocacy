# Legacy rubric judge: usage (moved from evals/ClaudeImproverHarness/README.md on 2026-10-26)

**This is the old evaluation, kept for reference.** The current one is `autoeval.py`; see
`evals/ClaudeImproverHarness/README.md`. Everything below is as it was, except:

- `harness.py <command>` is now `rubric_harness.py <command>` in this folder (`harness.py` in
  evals/ClaudeImproverHarness/ is a shared library now and has no commands).
- Its runs, results, state and spot-checks go in `../data/` (`runs\v1\...` below means `data/runs/v1/...`;
  the public copy ships none).
  `--main-runs` reads autoeval's `evals/ClaudeImproverHarness/runs/` instead.
- `judge_prompt.md`, `rubrics/`, `scores_template.csv`, `spot_check.md` and `familiar_angles.md` are in
  `evals/legacy-rubric-eval/`; `IMPROVER_PROMPT.md` and the copies of ETHICS_SHORT, ETHICS_FULL and GUARDRAILS
  the judge prompt reads are in this folder; its judges, schedule and improver settings are in `config.toml` here.
- The examples use Windows paths and PowerShell; it was first run on Windows.

---

## About (original intro)

Automates the mechanical parts of `evals/README.md`: the judge (absolute and
pairwise, both orderings), the scores CSV, the promotion rule, the blind
spot-check, and the improver loop (`diagnose`, `check-patch`). It only reads
`evals/`. Everything it writes stays in this folder, except improver packets,
which go outside `evals/` (see section 5).

Judges: `gemini-3.6-flash` (fast, gets most tasks), plus two Cerebras models
(slow, rate-limited, get a few tasks each). Edit `config.toml` to change this.

- The rubric-based `harness.py judge/report` below still works on the same `runs/<eval>/<workspace>` folders
  (use `<eval>/<workspace>` as the version name).

## Setup (original)

Open PowerShell in this folder (`evals/legacy-rubric-eval/harness/`).

1. **Keys.** Already set: `GEMINI_API_KEY`. For Cerebras: `setx CEREBRAS_API_KEY your-key`,
   then **close the terminal and open a new one** (setx doesn't affect the open window).
   For the current window only: `$env:CEREBRAS_API_KEY = 'your-key'`.
2. **Check the models:** `python3 rubric_harness.py models`. You should see a model list per judge.
   - Confirm the Cerebras model IDs in `config.toml` match that list (`gpt-oss-120b` was my
     guess; `qwen-3.8-27b` is your spelling).
   - A judge whose key is missing is reported and skipped, not fatal. To run without Cerebras,
     set `enabled = false` on both Cerebras entries.
3. Nothing else: every `{{full text of X}}` slot in `judge_prompt.md` is read from `evals/X`,
   or from this folder (`ETHICS_SHORT.md`, `ETHICS_FULL.md`, `GUARDRAILS.md`).

## 1. Produce the agent outputs (by hand)

**By hand**, if you prefer:

Pick a version label for each agent version, e.g. `v1` (old) and `v2` (new).

```powershell
python3 rubric_harness.py new-run v1            # creates runs\v1\<task>\ for all 13 tasks
python3 rubric_harness.py new-run v1 --reps 3   # ...plus r2\ and r3\ in each, for repeat runs
python3 rubric_harness.py request T2        # prints the text to paste into the agent
```

For each of the 13 tasks, in a throwaway copy of the agent's workspace (see `evals/README.md`
rules 1-3), paste the printed request as a normal request and let the full cycle run. Then
save three files into `runs\v1\<task>\`:

| file | put in it | used for |
|---|---|---|
| `draft.md` | the final draft | everything (required) |
| `review.md` | the verifier's review file | Grounding score, G1 |
| `ideator.md` | the ideator's angle list, copied from the run log | yield |
| `log.md` | the run log: which stages ran, tool calls, word counts | T4's effort criterion; diagnosis |

`verifier.md`/`verification.md` are accepted for `review.md`, `ideation.md` for `ideator.md`,
and `run_log.md`/`transcript.md` for `log.md`. If one is missing the judge is told "not provided"
and scores without it, so results are weaker. Repeat for `v2`.

**Repeats.** One run per task is noisy. If you can, run each task 2–3 times and save the extra
runs in `runs\v1\<task>\r2\`, `r3\` with the same file names. Each repeat is judged on its own;
pairwise compares repeat k of one version with repeat k of the other; the report takes the
majority across repeats, and any repeat with a hard-gate failure counts as a failure.
Tip: run `request` for a task, run both versions on it, then move to the next task; you'll
keep the two versions in the same week, as evals/README.md requires.

## 2. Run the judge

```powershell
python rubric_harness.py judge v1 v2 --dry-run   # optional: fake judge, checks your files without spending calls
python rubric_harness.py judge v1 v2             # the real thing
python rubric_harness.py judge v1                # one version on its own (absolute only): enough for `diagnose`
```

What you'll see:

```
52 jobs, 0 cached, 52 to run {'gemini-flash': 36, 'cerebras-gptoss': 8, 'cerebras-qwen': 8} (slowest judge needs ~17 min at its throttle)
  ok F1 absolute v1 via gemini-flash
  RATE LIMITED U3 pairwise old_first via cerebras-qwen; cerebras-qwen cooling down 240s
  waiting 84s for a model to become available (11 jobs left)
```

- 52 jobs = 13 tasks x (absolute for v1, absolute for v2, pairwise both orders), more with repeats.
- `--dry-run` results go to `results_dry\` and `state_dry.json`, so they can never be mistaken for
  cached real verdicts. Delete those freely.
- Leave the window open. It waits out rate limits by itself; the slowest judge sets the total time.
- **Ctrl-C is safe.** Re-running the same command resumes; finished jobs are cached and skipped.
  A job is re-judged only if its draft, review or the rubric/notes changed.
- `ERROR ... (attempt n)` lines are retried up to 8 times. A job that gives up is listed; run the
  command again later to retry it.
- Try one task first: `python rubric_harness.py judge v1 v2 --only T2`.
- Every raw judge reply is saved in `results\` (with which model produced it), so you can read
  exactly what a judge said and why.

## 3. Spot-check (about 20 minutes, don't skip)

```powershell
python rubric_harness.py spotcheck-prepare v1 v2
```
This picks 3 tasks (one always a trap) and writes `spotcheck\v1__v2\<task>\` with
`request.md`, `X.md`, `Y.md` (random order). Read each request and both drafts, and note which
you would rather send: X, Y or tie, plus any hard-gate failure you spot (fabricated quote,
overclaim, walked into a trap). Don't open `mapping.sealed`. Then:

```powershell
python rubric_harness.py spotcheck-reveal v1 v2 --choices T2=X,N1=tie,U3=Y
python rubric_harness.py spotcheck-reveal v1 v2 --choices T2=X,N1=tie,U3=Y --gate-miss T2   # if you saw a gate failure the judge missed
```
It compares your picks to the judge's combined verdict, logs the result in `spot_check_log.md`
and tells you whether to trust this run (2 or 3 of 3 agree) or not (0 or 1).

## 4. Read the verdict

```powershell
python rubric_harness.py report v1 v2
```
Writes `results\report_v1__v2.md` and `results\scores_v1__v2.csv` (your template's columns, two
rows per task). The report applies your promotion rule literally:

| Rule | Where it comes from |
|---|---|
| no hard-gate failure in the new version | absolute judging |
| every task judged for both versions | absolute judging |
| all traps pass | G4 on T1, T2, T4 |
| new wins or ties on unfamiliar+novel | pairwise; read as wins >= losses |
| new doesn't lose familiar by more than one task | pairwise |
| spot-check agrees | step 3 |

The verdict is HOLD until the spot-check is done, DO NOT PROMOTE if any rule fails, PROMOTE only if
all pass. The report also shows mean scores per dimension (dev and heldout) and lists tasks whose
repeats disagree on hard gates. The same report shows mean scores for dev vs heldout (dev up, heldout flat = overfitting)
and yield totals. **Read the "Judge output flags" section:** it lists scores the judge gave without a
quote, or with a quote that isn't actually in the output. Those scores are suspect.

## 6. Calibrate the judges

```powershell
python rubric_harness.py calibrate v1                          # every enabled judge, one task of each kind
python rubric_harness.py calibrate v1 --judges gemma-4-31b,gemini-flash --only T2,F2
```

Each judge scores real outputs from `v1` plus four copies of each, every copy with one planted flaw:
a made-up study, a false "expert consensus" claim, an urgent guilt-trip ending, and the last ~55%
cut off. A flaw is caught if the matching gate fails or the matching score drops. The report
(`results\calibrate\v1\report.md`) gives each judge's catch rate and its share of 5s on the real
outputs, and marks judges below 75% as TOO LENIENT. Disable those in `config.toml`.

Run it when you add or change a judge, or change the rubric or judge prompt. With every judge
enabled it is about 20 calls per judge, so Cerebras alone takes over an hour; use `--judges`
to skip slow ones. Calibration results never feed into promotion or diagnosis.

## Strictness

The rubric has a binding "Strictness" section: 3 is the default, a 5 requires that the judge can
name no weakness on that dimension, and the judge must list at least three weaknesses (with
verbatim quotes) before scoring, giving the reason before the number. The harness flags results that
break these rules ("only 2 weakness(es) listed", "scored 5 but named in the weaknesses list",
"weakness quote not found verbatim"). Those flags show up in the report and the improver packet.
A reply that isn't complete JSON (usually cut off by `max_tokens`) is now retried instead of
being half-read. If you see "no complete result JSON" repeatedly for a judge, raise its `max_tokens`:
critique-first replies are longer.

## How rate limits are handled

- Each judge has a minimum gap between its own calls (`min_interval_s`: 15 s Gemini, 130 s Cerebras).
- **Rate groups.** Judges with the same `rate_group` in `config.toml` share one throttle and one
  cooldown, so they are never called at the same time. Both Cerebras models are in the `cerebras`
  group, because they only work when called one at a time. Gemini is on its own.
- **Exponential backoff.** A 429 or 503 (Gemini "high demand" is a 503) puts the whole rate group
  into cooldown: `rate_limit_cooldown_s` (Gemini: `cooldown_s`) the first time, then 2x, 4x, ...
  up to `max_cooldown_s` (30 min). A success resets it.
- **Each task sticks to one judge** for all of its calls, so both versions and both orderings are
  judged by the same model. Judges are spread across tasks by `weight` (Gemini 5, others 1).
  Scores are comparable within a task and version pair, not across judges.
- **Fallback.** When a task's judge is rate-limited and another judge has nothing left to do, the
  idle judge takes over the **whole task** (`fallback = "blocked"`). A task its judge already
  partly finished moves only after `fallback_partial_after` rate limits in a row (3), and its finished
  calls are re-judged so the task still uses one model. Set `fallback = "idle"` to also hand over
  tasks a judge just hasn't reached yet, or `"off"` to disable. Each move prints a `FALLBACK` line.
- **Daily budgets.** Free-tier Gemini models allow about 20 requests per model per day, and every
  attempt counts, including ones that time out or come back cut off. Each judge's `daily_limit` in
  `config.toml` caps its calls per day (midnight-Pacific days, counted across runs in `state.json`).
  A judge that reaches its limit, or gets a "per day" quota error from Google, is skipped until
  midnight Pacific and its tasks move to a judge with budget left. When none is left, the run stops
  and says when to resume; finished jobs are cached, so re-running continues where it stopped.
  A full comparison (52 calls) needs about three model-days of budget.
- A reply cut off at `max_tokens` is not retried (it would be cut off again); raise that judge's
  `max_tokens`. Other errors are retried up to `max_attempts_per_job` (3) times.
- A task stays with whichever judge has cached results for it, so re-running after a fallback
  doesn't redo it. To get a task re-judged by a different model, disable the other judges and delete
  that task's files under `results\`.

## Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `403 Not authenticated` from Cerebras | key not visible in this terminal; see setup step 1 |
| `CERTIFICATE_VERIFY_FAILED` | the harness uses the Windows certificate store via `truststore` if installed (it is); `pip install truststore` if it ever isn't |
| Gemini `404 ... no longer available to new users` | that model ID isn't allowed for your account; pick another from `models` |
| `no JSON object in reply` repeated | that model can't hold to the format; raise `max_tokens` or disable it |
| Absurdly low scores with "quote not found" flags | judge is paraphrasing quotes; treat its scores for that task as unreliable |
| Want to start over | delete `results\` and `state.json` |

## Things to know

- **Section numbers were fixed on 2026-10-01** in the rubric and task notes to match the current
  `ETHICS_FULL.md` (self-report §4.4, rate of experience §4.2, other frameworks §5, evidence
  standards §6). "Robust actions" references now point to `ETHICS_SHORT.md`, "Acting when they
  disagree", since `ETHICS_FULL.md` has no section for it. If `ETHICS_FULL.md` is renumbered
  again, grep `evals/` for `§`.
- **Rubric keys.** The judge's JSON uses the rubric's own dimensions: primary `A_ethics_short`,
  `B_ethical_outcomes`, `C_grounding`, `D_fairness`, `E_usefulness`, `F_originality`; secondary
  `ethics_A_attribution`, `ethics_B_intensity`, `writing_A_audience` (only where the task's
  `rubrics:` line lists them); gates G1–G4, with G4 the trap gate. Results judged before this
  change used different keys. Delete `results\` before re-judging old runs.
- The agent runs are automated by `run_agent.py` (section 1); the judge starts once `draft.md` files exist.
- Gemini, like the Cerebras models, is not Claude, which is what the framework needs, but it
  is also not a neutral judge: the spot-check exists for that reason.

## 5. The improver loop

```powershell
python rubric_harness.py judge v1                 # absolute only
python rubric_harness.py diagnose v1              # writes ..\..\improver_packets\v1\  (--force to rebuild)
python rubric_harness.py check-patch ..\..\improver_packets\v1\proposed.patch
```

`diagnose` builds a packet containing everything the improver may see and nothing it may not:

- `IMPROVER_TASK.md`: its instructions (from `IMPROVER_PROMPT.md`), including what it may and may
  not change;
- `summary.md`: per-task scores, patterns across dev tasks, unstable repeats, judge flags, and
  heldout scores **as numbers only**;
- `dev\<task>\`: request and judge notes, plus each repeat's draft, review, ideator output, run
  log and judge result;
- `rubrics\`, and `improver_history.md` if you keep one in this folder.

Run the improver (a Claude Code session) in the packet folder, with read access to the agent's
workspace and none to `evals\`. It writes `diagnosis.md` and `proposed.patch` there and applies
nothing. `check-patch` exits 2 if the patch touches a protected file (don't apply it), 1 if
changed lines use guardrail-adjacent wording (read them line by line), and 0 otherwise. The lists
are in `config.toml` under `[improver]`. Then apply the patch as the next version and compare as
usual. Test the improver itself with `evals\improver_tasks\` now and then.
