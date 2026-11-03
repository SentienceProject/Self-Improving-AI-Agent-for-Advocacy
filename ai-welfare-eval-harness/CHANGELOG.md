# Changelog

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
