# Changelog

## Unreleased

- techtest freezes the workspace when it builds a test: the baseline runs in that frozen copy and the arms are
  built from it. Editing the workspace mid-test no longer stops the test with "instruction files changed in the
  workspace"; the report says what changed since. A prepared change that would undo an edit made after it was
  prepared is refused at build (re-stage it) instead of being tested.
- techtest waits while tech watch's check run is still reviewing a prepared change (it may change it).
- Fix: Python bytecode caches (`__pycache__/`, `.pyc`) no longer count as instruction files. Running any workspace
  script stopped a test after its baselines with "instruction files changed in the workspace".

- `config.local.toml` beside `config.toml`: your own settings over the shared ones, never committed.
- `[autoeval] off_limits`: extra folders no run may read (old workspaces holding earlier attempts at the tasks).
- `[autoeval] eval_my_ethics`: keep your own `my/ETHICS_*.md` in eval copies (off by default).
- `HARNESS_TASKS=tasks-animals` picks the task set for one command.
- Fix: eval runs kept the workspace's sandbox exceptions (`excludedCommands`); in hardened mode, corpus search and
  the Docker analyst failed inside eval copies without them.
- The unlocked `evals/tasks*/heldout-drafts/` folder is ignored by git like the other task folders.

## 0.1.0 — 2026-11-03

First public version, tested with Claude Code 2.1.317 on Linux (WSL2); macOS and Windows are untested. Ships in one
repository with ai-welfare-workspace 0.1.0, and its config points at that workspace by default.

- Two locked task sets: AI welfare (9 dev, 4 held-out) and animal advocacy (13 dev, 6 held-out); `[paths] tasks`
  picks one.
- `autoeval.py`: runs workspaces in throwaway copies, blind side-by-side judging by ChatGPT and Gemini (a Claude
  stand-in for one that isn't set up), position effects, trap verdicts, length limits, and fact checks where judges
  disagree. Usage scoring for quick asks (`usage_weight`, measured against each workspace's start-up cost);
  `judge_sees_process` for tasks that ask for something that happens along the way.
- Eval runs see a fixed, neutral profile (`eval_profile.md`) instead of the user's `my/` folder.
- `techtest.py`: tests a tech-watch proposal before it's applied, with a noise check, results stated in plain terms
  (the smallest difference a test could tell from noise), deeper tests, and a periodic checkup on held-out tasks.
  Test arms can't run unread hooks or MCP servers; the planner can only make a test stricter; results name the exact
  bundle tested; agent runs don't get your other API keys.
- `evals/shuffle_items.py` reorders a task's lettered items and relabels its judge notes to match.
