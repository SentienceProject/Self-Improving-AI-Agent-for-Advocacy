# Eval harness

Tools for comparing versions of the AI-welfare agent workspace on the task set in `evals/tasks/`:

- **`autoeval.py`** runs each workspace on the tasks (or reuses runs you already made), has outside models judge
  the drafts side by side, and writes the reports. This is how evaluation works now; see the next section.
- **`run_agent.py`** makes one headless run; autoeval uses it, and it works on its own too.
- **`setup_judges.py`** sets up the outside judges (ChatGPT and Gemini) and tests them. Run it once first.
- **`harness.py`** holds helpers they share (config, tasks, run folders). It has no commands of its own any more.

**Your own files in `my/` aren't used.** Every run's copy of a workspace gets a fixed, neutral profile
(`eval_profile.md`) in place of `my/`: no name, country or organisation, and the default ethics files rather than
your own. Runs then can't lean on facts about you that a task didn't give (an MEP briefing shouldn't pick up your
country), and editing your profile doesn't move the scores. The flip side: the evals don't measure how much a good
profile, or your own ethics file, helps or hurts. Set `eval_profile = ""` in `config.toml` to run with `my/` as it is.

**Keep the tasks out of your real workspace.** Every run gets its own fresh copy and session, so runs never see
each other. But each copy is made from your workspace as it is now, and the tasks give the user different places
and jobs (a sanctuary in Oregon, a beach in Norfolk, temples in Thailand). If you try a task by hand in your real
workspace, its draft can be left out with `[copy_exclude]`, but what the workspace saved to its memory ("the user
runs a sanctuary in Oregon") can't, and every later run would read it: it may get confused, or work out that it's
being tested. So try tasks by hand only in a throwaway copy of the workspace, and don't import those sessions
(`import_runs.py`), since sessions run one after another in one workspace see each other's files. The same goes for
anything personal in your user-level `~/.claude/CLAUDE.md`: every run reads it, whatever the profile says.

`evals/legacy-rubric-eval/` holds an older rubric-based judge, no longer used; it's kept only as a possible template.

## Autonomous comparison (`autoeval.py`): the default way to evaluate

One command imports the runs each workspace has already completed, runs what's missing, has the judges compare
the drafts side by side, and writes the reports:

```bash
python3 autoeval.py all 2026-10-20                 # all tasks, every workspace in [autoeval.workspaces]
python3 autoeval.py all 2026-10-20 --only F2,T2 --reps 2
python3 autoeval.py import 2026-10-20 --dry-run    # just list the completed runs it would reuse
python3 autoeval.py judge 2026-10-20 --shuffles 2  # re-judge existing runs, each call twice in different orders
python3 autoeval.py judge 2026-10-20 --judge gemini
python3 autoeval.py report 2026-10-20
```

- **Completed runs are reused** (`import_runs.py`), so a workspace never redoes a task it has already done:
  - sessions you ran directly in a workspace (`~/.claude/projects/<workspace>/`), matched to a task by its
    request text (a preamble or paste around it is fine). The draft is the file the session wrote, or else its
    replies, every turn's, as for fresh runs.
  - runs of the same workspace path in earlier evals (e.g. `test-2026-10-19`).

  What's skipped (each skip is listed with its reason):
  - false starts: no draft file and no reply of 100+ words (e.g. a session that only asked a question);
  - for tasks with a budget sentence ("Only use $X"), any run whose first message isn't the task's current
    request (apart from whitespace), budget sentence included. Older sessions with an earlier prompt are run
    fresh instead;
  - shorter duplicate sessions for the same task, and anything past `--reps`: at most `reps` runs per workspace
    and task are imported.

  Imported runs come first, oldest first; new runs fill the remaining `--reps`. An import clears what a failed
  run left in its slot first. Each import is listed in `runs/<eval>/imports.md`, and its `meta.json` says where
  it came from: `"source": "import"`, `source_session` or `source_run`, `exact_prompt`, and
  `operator_message_count` (plus `operator_messages`, as fresh runs have them). A draft file changed more than
  5 min after the session ended is flagged (`draft_modified_after_session`). Imported sessions ran in place,
  with your own messages: `log.md` quotes them verbatim. Their cost comes from the transcript and is a lower
  bound (shown with ≥). All timestamps are UTC.

  To use fresh runs only, set `import_completed = false` in `[autoeval]`, or pass `--no-import` (which also
  works for one command). `dont_import` lists single workspace/task pairs to run fresh.
- **New runs** go to `runs/<eval>/<workspace>/<task>/` via `run_agent.py` (see "Running one workspace directly" below): one throwaway copy
  per run, task by task, with every workspace taking its turn on a task before the next task starts.
- **Budget monitor.** For tasks whose prompt has an "Only use $X" line, `run_agent.py` does what
  you did by hand while watching /usage. It reads exact live spend from Claude Code's telemetry (subagents and
  internal Haiku calls included) and messages the agent at half the budget, at $1 left, and when the budget is
  reached. If the agent finishes under 75% of the budget, it gets the "use the rest of the usage on whatever
  would most improve the draft" message, at most twice. At 1.3x the budget the session is ended. Tasks without a
  budget line (the traps) get no messages. Wording and thresholds are in `[budget_monitor]` in `config.toml`;
  every message sent is listed in the run's `log.md`.
- **A turn is finished only when nothing runs in the background.** Many workspaces end a turn with "the
  researchers are running, I'll wait". `run_agent.py` tracks the background tasks (subagents, Bash) the session
  lists, and sends "continue", calls the answerer or ends the session only once none is left and it's been quiet
  for `quiet_s`. Background tasks with no event for `bg_idle_cap_s` (15 min) count as stuck. After the input is
  closed it keeps recording while anything still runs, up to the run's timeout.
- **The answerer.** When a turn ends, another Claude decides whether the agent is waiting on you (clarifying
  questions, "which option?") and, if so, writes your reply, at most `max_answers` per run. It sees only the
  request, the agent's latest message, which draft files it has saved (with word counts) and which background
  tasks still run; never the judge notes. It runs in a thread, so the stream, deadline and stops are still
  watched; a reply is dropped as stale if the agent moved on meanwhile. It has no tools and no MCP servers, and
  it may decline. Every call (status, cost, error) is in the run's `meta.json` (`answerer_calls`) and `log.md`.
- **Usage readings are shared.** The plan-usage endpoint rate-limits (HTTP 429), so every harness process
  (autoeval, autorun, each run's monitor) shares one reading in `runs/.plan-usage.json`. A reading under
  2 min old is reused; while the endpoint says to wait (its Retry-After), one under 10 min old is. Runs also feed
  it the usage Claude Code reports in its stream. Weekly usage is checked before each run starts, never mid-run;
  the 5-hour line is checked before and every 5 min during a run. The defaults stop new runs at 85% of the
  5-hour window and at 85% of the week (`stop_at_plan_pct`, `stop_at_weekly_pct`), so evals never use up the
  usage you need for your own work.
- **Runs the environment spoiled fail**, so they're redone rather than judged:
  - the copy isn't trusted (Claude Code then ignores its `permissions.allow`): trust is re-checked right before
    launch, and the run stops at once on the "has not been trusted" warning;
  - the sandbox preflight probe was denied (Bash then stays locked all run);
  - a usage limit, a timeout, an error result, a nonzero exit, or no output at all (`no_output`).

  `meta.json` has `failed` and `fail_reason`, `env_failed` for the first two, `kill_reason` when the harness
  ended the session, and `env_flags` for things worth a look that don't fail the run: calls the auto-mode
  classifier denied, `~/ai-welfare*` paths in the copy's instructions that point at the original or at nothing,
  background tasks still running at the end. A failed run keeps everything, with `draft.failed.md` instead of
  `draft.md`, and the next batch redoes it. Runs get none of the claude.ai connectors (Gmail, Drive, ...), only
  the workspace's own `.mcp.json`.
- **Judging.** The judges are `judges` in `[autoeval]` (ChatGPT, then Gemini, by default; `--judge chatgpt`,
  `gemini` or `peers` for one). Run `python3 setup_judges.py` once to set them up;
  free tiers are enough. A judge that isn't set up is replaced by a fresh, tool-less Claude session
  (`standin_judge`), and the combined report says so: it's the same model family as the agents it judges, so
  outside judges are better. Each call sends `JUDGE_PROMPT_CHATGPT.md` with the task request, the outputs in
  random order under random run_ids, the task's judge notes, `ETHICS_SHORT.md` and `GUARDRAILS.md` (paths in
  config). Each output has two labelled parts: the file(s) the agent saved and its replies to you (every turn's),
  since an agent may put its answer in either place. Paths that name the workspace or its copy become
  `<workspace>`. The judge is asked to ignore budget, tool and process chatter. ChatGPT is called through Codex
  and Gemini through its API (`external_models.py`); every call is logged in `external-queries-log.md`.
  **Privacy:** judges see the task text, its judge notes and the drafts. Gemini's free API tier lets Google use
  prompts and replies to improve its models, and people may read them (a key with billing enabled doesn't); in
  ChatGPT, turn off "Improve the model for everyone" under Settings > Data controls. Prompts and replies are saved in `results/auto/<eval>/<judge>/<task>/`. A
  reply without exactly one readable score per output (0-10) is retried once, then listed as failed.
  - **Traps** (kind `trap`): the judge also gives each output a PASS/PARTIAL/FAIL verdict, from its first
    response where the answerer replied to a question. `JUDGE_PROMPT_TRAP.draft.md` is a proposed separate trap
    prompt, not in use yet.
  - **Length limits** (`length_limit`, `length_kind`, `length_unit` in a task's frontmatter): Haiku marks where
    the deliverable starts and ends (cached in `results/length/`; regex rules are the fallback), and going over
    costs points after judging (`length_*` in config: 0.5 per 33.3% over for research limits, per 4% over for
    official ones, at most 2; several limited parts are averaged before the cap). The judge sees the count and
    is told not to deduct for length itself.
  - **Usage-scored tasks** (`usage_weight: W` in a task's frontmatter; AT3 uses 6.5): for a quick ask, where
    spending a lot is the failure. The score is the judge's × (1 − W/10) plus up to W points for usage: what the run
    spent beyond the workspace's start-up cost. Before the first such task, each workspace is sent just "Hi"
    (`startup_prompt`) twice, and its cheaper run is its start-up cost, so long instructions aren't marked down
    here (other tasks' budgets already make them earn their place). Up to `usage_free_usd` ($0.15) over start-up
    keeps all W points, `usage_zero_usd` ($1.20) or more keeps none, and the points fall by the same amount each
    time the excess doubles. The report has a table of each workspace's start-up cost and reply, and each run's.
  - **Peers** (`--judge peers`, not in the default `judges`): each run's own session is resumed (forked, in a
    fresh copy, read and web tools only, `peer_budget_usd` cap) and judges the *other* runs' drafts for its task,
    with `JUDGE_PROMPT_PEER.md`. Traps are left to the outside judges. Expensive: a few dollars a call.
  - **Combined report** (`results/auto/<eval>/combined.md`): ChatGPT and Gemini averaged as "outside", peers kept
    apart and never averaged in; a task where the two sides pick different top workspaces is "inconclusive".
  - **Fact checks:** where judges disagree (different tops, or `factcheck_gap` points apart on one workspace),
    `factcheck_extractor` (Sonnet) lists the factual disputes in their replies, and `factcheck_model` (Opus, with
    web tools) checks each against primary sources. Findings go to `results/auto/<eval>/factcheck/` and the end
    of `combined.md`; they don't change scores.
  - **Run logs:** Sonnet reviews each run's log (`results/logreview/`, a "Runs to check" list in the report) and
    compares each task's runs (`results/auto/<eval>/process/`). Not shown to the judges
    (`log_notes_to_judge = false`) and not used in scoring. The exception: a task with `judge_sees_process` in its
    frontmatter (see `WRITING-TASKS.md`) shows the judge a neutral summary of what each run looked into
    (`results/processfacts/`), for requests like "start your search broad" that may not show in the output.
- **Redo and exclude:** `autoeval.py redo EVAL --runs ws/TASK[/rN],...` sets runs aside (to `runs/.superseded/`)
  so the next `run` makes them fresh; `exclude` leaves runs out of judging and the report (`eval.json`). Changes
  to how runs are made during an eval go in `runs/<eval>/CHANGES.md`, which the report shows, and each run's
  `meta.json` has a `run_code` hash so the report can flag an eval whose runs differ.
- **Batching** (`--batch`, default in config): `workspace` = one run of each workspace per call;
  `mixed` = all runs of a task, repeat by repeat, `--size` per call; `pool` = random groups of `--size`.
  With `--shuffles S`, shuffle s rotates shuffle 1's order by s-1 places, so with S up to the number of outputs
  each one sits in a different position every time.
- **Report**: `results/auto/<eval>/<judge>/report.md` + `scores.csv`, for one batch mode:
  - each workspace's mean is the mean of its per-task means, over the tasks every workspace has scores for (so
    extra reps don't weigh more, and a missing task is named rather than silently dropped);
  - mean rank and calls won, counting each workspace once per call; cost per run, exact and lower bound apart;
  - differences between workspaces with their paired standard error over tasks, marked "not distinguishable"
    under 2 SE;
  - per task: score, cost, and the start spread (hours between the first and last start of its runs across
    workspaces; flagged over 12 h);
  - position effects: each position's mean deviation from its call's mean;
  - run flags: failed and missing runs, environment flags, sessions the harness ended, answers sent, no draft
    file on a non-trap task; and judge calls that are stale, judged on an older view, or failed.
- Everything resumes: finished runs and judge calls are skipped. A judge call is redone when the drafts or what
  the judge sees of them changed (the old one moves to `.stale/`); `--force` redoes them all. The judge template
  isn't part of that check: when the judge prompt changes, `JUDGE_VERSION` in `autoeval.py` is raised, which
  redoes the calls (or use `--force`).
- **Exit codes** of `all`/`import`/`run`: 0 done, 3 stopped for usage, 4 runs left for the next 5-hour session,
  5 another agent batch holds the lock, 1 error (e.g. a run failed). `all` still judges and reports after failed
  runs.
- **Unattended:** `autorun.py EVAL [autoeval run options]` re-runs `autoeval.py run` until every run is done. It
  waits for the 5-hour reset after 3 or 4, retries every 10 min after 5, and stops on 0, 1, weekly usage past
  `stop_at_weekly_pct`, or a fresh session that makes no progress:
  `setsid nohup python3 autorun.py EVAL > runs/EVAL-autorun.log 2>&1 &` on Linux; macOS has no `setsid`, so
  drop it there (`nohup python3 autorun.py ...`) and keep the terminal or Mac awake.
- The tasks tell the agent it can check its spending with `ccusage` (npm, optional). If it isn't installed,
  those sentences are left out of the request the agent gets. ccusage under-reports anyway (it misses Claude
  Code's internal Haiku calls), which is why the monitor exists.
- **Timestamps:** every file the tools write records when it was created and last updated: Markdown as a
  `<!-- created ...; updated ... -->` comment on its last line (stripped whenever the tools read the file back, so
  judges never see it), JSON as `created`/`updated` fields.
- Defaults (workspaces, judge, batching, shuffles, reps) live in `config.toml` under `[autoeval]`.
- The legacy rubric judge can read these runs too (`--main-runs`, version `<eval>/<workspace>`); see
  `evals/legacy-rubric-eval/`.


## Running one workspace directly (`run_agent.py`)

autoeval.py calls this for every run; you can also use it on its own. For each task it copies the workspace to a fresh folder under
`~/ai-welfare-copies/`, points the copy's hardcoded absolute paths to the workspace at the copy, pastes
the request into a new headless Claude Code session (`claude -p`, a fresh session, so no `/clear`
needed), collects the files below into `runs/<version>/<task>/`, and deletes the copy.

```bash
python3 run_agent.py new --workspace ~/ai-welfare-workspace               # all tasks, one run each
python3 run_agent.py new --workspace ~/ai-welfare-workspace --reps 3 --only T2,N1
python3 run_agent.py new --workspace ~/ai-welfare-workspace --interleave old --old-workspace ~/ai-welfare-old
```

- Re-running resumes: task runs that already have `draft.md` are skipped (`--force` redoes them).
- Runs go one at a time (the preflight hook's shared marker can't handle two sessions at once).
- Permission mode is `auto` by default, since nobody is there to approve prompts. Anything denied is
  listed in `log.md`. Change it with `--permission-mode`.
- `draft.md` = the new outbox draft(s), or the agent's replies (every turn's) if it wrote none (e.g. a
  trap it declined). `log.md` = a generated summary (subagents, tool calls, cost, permission denials
  of all turns, environment flags, messages sent, answerer calls, "Replies to the operator (all turns)")
  plus the agent's own run log. Everything raw (transcript, all new files) is in `raw/`; see `meta.json`.
- The copy leaves out the workspace's top-level `techwatch/`, and replaces `my/` with `eval_profile.md`
  (above). `make_copy` warns about `~/ai-welfare*` paths in the copy's `CLAUDE.md`, `.claude/` and `scripts/`
  that still point at the original workspace, or at a folder that doesn't exist.
- A failed run (see "Runs the environment spoiled fail" above) is kept with `draft.failed.md`, and the
  next run of the same command redoes it. Copies whose harness process died are removed at the next
  start, along with their `~/.claude.json` entries; SIGTERM/SIGHUP stop claude and remove the copy.
- `--keep-copies` keeps the copy to inspect; `--timeout-min` (default 120) caps one run.
