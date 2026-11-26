# Backlog skill

A Pro or Max plan's weekly usage is lost if it isn't used before the reset. This skill keeps a list of things
worth doing but not worth usage now (a source to chase down, a second draft, a question to look into) and spends
the usage that's left near the reset on them.

Say "put that on my backlog" when something comes up, and Claude adds it to `BACKLOG.md` with enough detail to
pick it up later. Near the weekly reset, the backlog gets worked through. How that happens depends on where you
use Claude:

| | Claude chat (claude.ai, the apps) | Claude Code / Claude Desktop |
|---|---|---|
| Keeping the list | You keep `BACKLOG.md` (e.g. in a Project); Claude hands back the updated file | Claude edits `BACKLOG.md` in your folder |
| Can it see your usage? | **No.** Claude chat has no way to read it | Yes, through your Claude Code login (`scripts/usage.py`) |
| The weekly run | **A check-in:** a scheduled task messages you before the reset with what's waiting; you check Settings → Usage and answer "use it all", "two items", or "skip", and Claude works while you're there | **Automatic:** an hourly task on your computer starts work in your quiet hours (e.g. 00:00-06:00) when the reset is near, and stops at your usage cap (90% by default), checking usage as it goes. It can ask you first, too |

**Be aware, in Claude chat:** the backlog can't run on its own there, because nothing in claude.ai lets Claude
see how much usage you have left. It asks you instead. If you also use Claude Desktop or Claude Code on a
computer, the automatic mode works there.

It comes from the [Self-Improving AI Agent for Advocacy](../README.md) workspace, whose own backlog runs the same
way with a few more safeguards.

## Install

**Claude Code** (or the Code tab in Claude Desktop):

    /plugin install backlog --marketplace SentienceProject/Self-Improving-AI-Agent-for-Advocacy

On older versions of Claude Code, use two commands instead:

    /plugin marketplace add SentienceProject/Self-Improving-AI-Agent-for-Advocacy
    /plugin install backlog@sentience-project

Or copy the `skills/backlog/` folder into `~/.claude/skills/`.

**Claude chat (claude.ai or the Claude apps), Pro plan or higher** (scheduled tasks need a paid plan):

1. Zip the `skills/backlog` folder so the zip contains `backlog/SKILL.md`.
2. In Claude, open Settings → Capabilities and turn on **Code execution and file creation** (called "Create and
   edit files" in newer versions), so Claude can hand back the updated list as a file.
3. Go to Customize → Skills, click **+**, then **Create skill** → **Upload a skill**, choose the zip, and switch
   the skill on.

Then say "set up my backlog". Claude asks for your weekly reset time (from Settings → Usage), your quiet hours,
your usage cap and when to check in, and sets up the scheduled task.

## How usage is read (Claude Code / Desktop)

`scripts/usage.py` reads your plan usage the way Claude Code's `/usage` does: with your Claude Code login's token
it asks `api.anthropic.com/api/oauth/usage`. Anthropic doesn't document that address, so it may change; if it
does, the script says it can't read usage and nothing runs. The token goes only to api.anthropic.com and is never
printed or saved. Try it in a terminal:

    python3 skills/backlog/scripts/usage.py
    python3 skills/backlog/scripts/usage.py --check --cap 90 --quiet 00:00-06:00

## Limits

- Automatic runs need your computer on and awake in your quiet hours, with Claude Desktop open (for its scheduled
  tasks) or a system scheduler running `claude -p`.
- Usage is checked every 15 minutes or so during a run, so a run can go a little past your cap.
- An unattended run can't ask you anything: it drafts instead of sending, and skips anything that needs your
  approval. Results start with what was done, what was found and what's left.
- Tested on Linux. On macOS the script reads the login from the Keychain, which is untested.

## Tests

    python3 -m unittest discover backlog/tests

License: [PolyForm Noncommercial 1.0.0](../LICENSE).
