---
name: backlog
description: Keep a backlog of ideas, unfinished work and things to look into that aren't worth usage now, and work through it with the usage left before the plan's weekly reset, so it isn't wasted. Use when someone wants to save something for later ("put that on my backlog", "remind me to look into this when I have usage left"), asks to see, add to or work their backlog, wants to set up the weekly backlog check, or when a scheduled backlog check or check-in starts. Also offer it when a conversation turns up something worth doing but not now.
license: PolyForm Noncommercial 1.0.0 (see LICENSE in the repository)
compatibility: Works in Claude chat (claude.ai and the Claude apps) and in Claude Code. Automatic runs need Claude Code or Claude Desktop with a Claude Code login on the person's computer, plus Python 3.9+, because only there can usage be read (scripts/usage.py). In claude.ai the weekly check is a check-in message instead.
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py *)
---

# The backlog

A Pro or Max plan's weekly usage is lost if it isn't used before the reset. The backlog is a list of things worth
doing but not worth usage now; near the reset, whatever usage is left goes to them. `${CLAUDE_SKILL_DIR}` means
this skill's folder.

## The list

One Markdown file, `BACKLOG.md`, made from `${CLAUDE_SKILL_DIR}/template.md`: a few settings at the top, then one
`## ` heading per item. Keep the format so any session can read it:

    ## B4 · Find out whether the 2023 shrimp stunning trial was replicated
    added 2026-11-20 by me · size small
    Details, links, what a good result looks like.
    - progress 2026-11-22: found two follow-up papers (notes in drafts/shrimp.md); still need the 2025 review

Ids count up and are never reused. `by` is `me` (the person) or `claude`. Size: small (minutes), medium (about an
hour of work), large (several hours). Finished and dropped items move to a `# Done` section at the end, with the
date and where the result is.

**Where it lives.** In Claude Code or Claude Desktop: `BACKLOG.md` in the folder the person works in (ask once;
`~/backlog/BACKLOG.md` if they have no preference). In claude.ai, Claude can't keep a file between conversations:
the person keeps `BACKLOG.md` (in a Project's files, or on their computer) and gives it to you, and whenever you
change it, you hand back the whole updated file (as a downloadable file if you can create files, otherwise in one
code block) and say what changed. If a notes or documents connector (Google Drive, Notion, ...) is connected and
the person prefers it, keep it there instead.

**Adding.** When the person asks, add the item with a clear one-line goal, size and enough detail for a session
with no memory of today. When you notice something worth doing but not worth usage now, offer to add it (one
line: what and why); add it without asking only if the settings say `Claude adds items: yes` and you can write the
file.

## Setting up the weekly check (first use)

Ask, in one message, what's missing from the settings:

- **Weekly reset**: the day and time Settings → Usage shows for the weekly limit.
- **Quiet hours**: hours they usually don't use Claude, e.g. 00:00-06:00. Unattended work starts only then.
- **Usage cap**: how much of the weekly usage the backlog may take (default 90%), leaving the rest for them.
- **Check-in**: how long before the reset to ask them (default 1 day).

Then find out whether usage can be read here: in Claude Code or Desktop, run
`python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py`. In claude.ai it can't be (there's no Claude Code login in chat's
sandbox), so don't try; say so in one line.

- **Usage readable → automatic.** Set up a scheduled task on this computer that runs hourly (Claude Desktop:
  Scheduled → a local task, folder = where `BACKLOG.md` is; or, from a terminal, the system's scheduler running
  `claude -p` in that folder). Its prompt: *"Please run a scheduled backlog check with the backlog skill. You're
  welcome to decline or stop; if you do, note it in BACKLOG.md. Thank you!"* Plus the check-in below, if they want
  to be asked first. Tell them the computer must be on and awake in their quiet hours, and that the task's
  permission mode decides what it can do unattended (it must never need to ask: anything that would is skipped).
- **Usage not readable → check-in.** Set up a scheduled task (claude.ai: Scheduled, or ask Claude to schedule it)
  that runs weekly at the check-in time, outside quiet hours, with the prompt: *"Please run the weekly backlog
  check-in with the backlog skill. Thank you!"* Tell them, plainly and up front, that in claude.ai the backlog
  can't watch their usage, so it never runs on its own: it asks them, and works while they're there to answer. If
  they later use Claude Desktop or Claude Code, the automatic mode becomes possible.

Write the settings into `BACKLOG.md` (`Mode: automatic` or `Mode: check-in`).

## The weekly check-in

Send one short message:

- when the weekly limit resets, and that they can see their usage in Settings → Usage;
- the items waiting (id, one line each, size), oldest of theirs first;
- the choices: **use it all** (work items until the plan's limit stops you; best in the last day before the
  reset, since a limit hit days earlier leaves them without Claude until it resets), **a number of items**, **only
  some items** (they name them), or **skip this week**. In automatic mode, add: no answer means the usual plan
  (quiet hours, up to the cap).

If you can't see `BACKLOG.md`, ask them to attach it. In check-in mode, when they answer, work the items as
below, one at a time, in this conversation, and hand back the updated list at the end; check in briefly between
items only if they asked you to. In automatic mode, write their answer into the settings as
`This week: use it all | skip | B3, B5 | 2 items (reset <date>)`, for the scheduled checks to follow.

## A scheduled backlog check (automatic mode)

1. Read only the settings at the top of `BACKLOG.md`. A `This week: skip` line for the coming reset: stop. Then
   run `python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py --check --cap <cap> --window 24 --quiet <quiet hours>`
   (with `This week: use it all`: leave out `--quiet` and use `--cap 98`). Anything but `GO`: stop at once. Each
   check costs usage, so do nothing else.
2. Pick the next item: the ones named in `This week` if any; otherwise theirs before yours, oldest first. Skip any
   marked `waiting` or tried twice already, and stop once a `This week: N items` count is done.
3. Work it (below). Every 15 minutes or so, and before each big step (a round of research, a long draft), run the
   check again; on `WAIT` for the cap, stop: record what you have as progress and end. Quiet hours ending also
   means: finish the current step, record, end.
4. If usage is still under the cap and the quiet hours aren't over, take the next item.

## Working an item

Nobody may be there to answer, so:

- Don't send, post, share, buy or contact anyone; draft instead, and say what you'd send. Anything that would
  normally ask the person first is skipped and noted.
- Where the item is unclear, choose the most sensible reading and say which you chose.
- Build on the item's progress lines instead of starting over. Work at the size the item calls for, with the
  usual care: check facts against sources, and say what you couldn't check.
- Save the result where the person keeps such things (a `backlog-results/` folder beside `BACKLOG.md` by default;
  in claude.ai, in the conversation or as a file). Start it with three lines: what you did, what you found or
  made, and what's left.
- Update `BACKLOG.md`: finished → move the item to `# Done` with the date and the result's place; partly done →
  add a progress line (what it adds; what's left); something new worth doing → add an item (`by claude`).
- You may decline an item or stop partway; if you do, write that in its progress line.

The next time the person is there, tell them in a line or two what the backlog did and where the results are.
