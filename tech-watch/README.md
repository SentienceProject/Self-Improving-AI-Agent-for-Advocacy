# Tech watch skill

New AI models, Claude features, research tools and apps come out every week, and most of them don't matter for
your work. This skill keeps an eye out for the few that would help, and brings you a short list of specific
proposals. Each one says what it would change, the evidence, the strongest case against it, the cost and how
you'll know whether it helped. Nothing changes until you say yes.

It looks broadly, not only at AI: a data source for your field, a translation tool, a newsletter service, a way of
working, or a built-in feature that lets you drop part of your setup. What counts as helpful comes from what you
tell it about your work, and your limits (money, accounts, privacy, things never to suggest) bind every scan.

| | Claude chat (claude.ai, the apps) | Claude Code / Claude Desktop's Code tab |
|---|---|---|
| Your settings | `TECHWATCH.md`, which you keep (e.g. in a Project); Claude hands back the updated file | `TECHWATCH.md` in `~/tech-watch/` |
| How it runs | **A weekly check-in:** a scheduled task starts a scan; Claude searches, checks your watched pages and brings up to three proposals | **When you ask, or in the background:** an hourly job scans weekly (or more often) within a budget and a monthly cap |
| Knowing what's new | From the dates and the "newest item seen" notes in your file | A sensor (no AI, no usage) saves each watched page and reports only the lines that are new |
| The proposals | In the conversation, with the exact text to paste (instructions, a skill, a connector) or steps | Files in `proposals/` with the exact edit to your `CLAUDE.md`, skills or agents; Claude applies one after you've seen it and said yes |
| Cost | One conversation with searches a week | About $1-4 of usage per full scan on Opus (about a third on Sonnet); $5-15 a month weekly by default |

**Be aware, in Claude chat:** tech watch can't run in the background there or remember anything except through
`TECHWATCH.md`, so keep the file up to date (Claude hands it back after each check-in).

It comes from the [Self-Improving AI Agent for Advocacy](../README.md) workspace, whose own tech watch adds tests
on real tasks, outside reviewers and automatic options. If you use that workspace, you don't need this skill:
its maintainer runs tech watch and ships the changes through its updates.

## Install

**Claude Code** (or the Code tab in Claude Desktop):

    /plugin install tech-watch --marketplace SentienceProject/Self-Improving-AI-Agent-for-Advocacy

On older versions of Claude Code, use two commands instead:

    /plugin marketplace add SentienceProject/Self-Improving-AI-Agent-for-Advocacy
    /plugin install tech-watch@sentience-project

Or copy the `skills/tech-watch/` folder into `~/.claude/skills/` (then you won't get the one-line note at session
start when proposals are waiting; that comes from the plugin's hook).

**Claude chat (claude.ai or the Claude apps):** the scan works on any plan with web search; the weekly scheduled
check-in needs a paid plan.

1. Zip the `skills/tech-watch` folder so the zip contains `tech-watch/SKILL.md`.
2. In Claude, open Settings → Capabilities and turn on **Code execution and file creation** (called "Create and
   edit files" in newer versions), so Claude can hand back `TECHWATCH.md` as a file, and make sure web search is
   on.
3. Go to Customize → Skills, click **+**, then **Create skill** → **Upload a skill**, choose the zip, and switch
   the skill on.

Then say "set up tech watch". Claude asks about your work, your setup and your limits, suggests pages to watch for
your field, and makes `TECHWATCH.md`.

## Scheduled scans (Claude Code)

`scan.py schedule on` adds an hourly job (cron on Linux and macOS, Task Scheduler on Windows). Each hour it checks
whether a scan is due (`Scans:` weekly, twice a week or daily; at most one a day) and whether it fits this
month's cap, and if so runs one `claude -p` scan with a hard budget. That scan:

- can read only the tech-watch folder and the paths you list under `Setup to watch:`, with your keys, credential
  folders and Claude Code's private files (settings, prompt history, past conversations) denied even there;
- can search and fetch web pages, and write only in `reports/` and `proposals/`; no shell, no MCP servers, and
  none of your other Claude Code settings or plugins;
- can't change your setup or mark a proposal accepted: decisions live in `TECHWATCH.md`, which it can't write.

The job calls a copy of the scripts in `~/tech-watch/.engine/`, so a plugin update can't break it; the copy is
refreshed when you start a session (with the plugin) or run `schedule on` again. Logs are in `state/`.

    python3 skills/tech-watch/scripts/scan.py status       # last scans, spend, what's waiting
    python3 skills/tech-watch/scripts/scan.py run --now    # a scan now, in the background
    python3 skills/tech-watch/scripts/sensor.py --dir ~/tech-watch   # just see what changed (free)

## Limits

- **Web pages can try to mislead.** A scan reads arbitrary pages, so a page could push a bad proposal or try to
  get your files' text into a request. The scan can't run commands or write outside its folders, the brief tells
  it never to put your files' text into searches or URLs, and every change waits for you. Still, treat anything
  you list under `Setup to watch:` as something that could leave your computer. Read the evidence labels. Anything
  that would run code on your computer (hooks, MCP servers, scheduled tasks, new programs) is spelled out before
  you're asked.
- **Claude judging Claude.** Scans may lean toward giving models more tools or autonomy; each proposal has a
  conflict-of-interest line, and the decisions stay with you.
- **Background scans need the computer on.** Hourly checks catch up on missed days. When the monthly cap is
  reached they stop until next month; `status` and the session start say so. Tested on Linux; macOS and
  Windows scheduling are untested.
- **Pages that need JavaScript** give the sensor nothing (it says so); a feed or plain-text version works.

## Tests

    python3 -m unittest discover tech-watch/tests

License: [PolyForm Noncommercial 1.0.0](../LICENSE).
