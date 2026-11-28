---
name: backlog
description: Keep a backlog of ideas, unfinished work and things to look into that aren't worth usage now, and work through it with the usage left before the plan's weekly reset, so it isn't wasted. Use when someone wants to save something for later ("put that on my backlog", "remind me to look into this when I have usage left"), asks to see, add to or work their backlog, wants to set up the weekly backlog check, or when a scheduled backlog check or check-in starts. Also offer it when a conversation turns up something worth doing but not now.
license: PolyForm Noncommercial 1.0.0 (see LICENSE)
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
    - backlog run 2026-11-27: read the 2025 review; draft summary in backlog-results/B4-2026-11-27.md; left: one paywalled paper

Ids count up and are never reused. `by` is `me` (the person) or `claude`. Size: small (minutes), medium (about an
hour of work), large (several hours). The lines under an item, newest last:

- `- progress <date>: ...`: work done in an ordinary conversation (what it adds; what's left).
- `- working since <YYYY-MM-DD HH:MM>`: a backlog run is on it now. Write it when you start the item, and replace
  it when you stop, so two runs never take the same item.
- `- backlog run <date>: ...`: how a backlog run ended (what it did; what's left), or why it stopped.

Finished and dropped items move to the `# Done` section at the end, one line each:
`- B4 · <title> · done 2026-11-27 → backlog-results/B4-2026-11-27.md` or `- B4 · <title> · dropped 2026-11-27: why`.

**Where it lives.** In Claude Code or Claude Desktop: `BACKLOG.md` in the folder the person works in (ask once;
`~/backlog/BACKLOG.md` if they have no preference). In claude.ai, a plain file doesn't last between
conversations, so, in this order of preference:

1. If you can publish an artifact (or a document) and read it again in a later conversation, keep `BACKLOG.md`
   there: publish it once, put its link in the scheduled check-in's prompt, and read and update it from there each
   time. Check that a fresh conversation can read it before relying on it.
2. If a notes or documents connector (Google Drive, Notion, ...) is connected and the person prefers it, keep it
   there.
3. Otherwise the person keeps `BACKLOG.md` (in a Project's files, or on their computer) and gives it to you;
   whenever you change it, hand back the whole updated file (as a downloadable file if you can create files,
   otherwise in one code block) and say what changed.

**Adding.** When the person asks, add the item with a clear one-line goal, size and enough detail for a session
with no memory of today. When you notice something worth doing but not worth usage now, offer to add it (one
line: what and why); add it without asking only if the settings say `Claude adds items: yes` and you can write the
file.

## Setting up the weekly check (first use)

Ask, in one message, what's missing from the settings:

- **Weekly reset**: the day and time Settings → Usage shows for the weekly limit, written like `Tuesday 09:00`
  (day in English, 24-hour local time).
- **Quiet hours**: hours they usually don't use Claude, e.g. 00:00-06:00. Unattended work starts only then.
  Write them as `HH:MM-HH:MM` (24-hour, local time): in Claude Code or Desktop, convert what they said with
  `python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py --normalize-quiet "<their words>"`; in claude.ai, convert it
  yourself ("10pm to 6am" → `22:00-06:00`). If it's unclear, ask: "10-6" could be 10am or 10pm, and the
  script refuses it rather than guess.
- **Usage cap**: how much of the weekly usage the backlog may take (default 90%), leaving the rest for them.
- **Check-in**: how long before the reset to ask them (default 1 day).

Then find out whether usage can be read here: in Claude Code or Desktop, run
`python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py`. In claude.ai it can't be (there's no Claude Code login in chat's
sandbox), so don't try; say so in one line.

- **Usage readable → automatic.** Write the settings into `BACKLOG.md` first, then get the schedule from
  `python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py --cron --backlog <path to BACKLOG.md>`. It prints cron lines (local
  time) for every hour of the last day before the reset, and for only the quiet hours in that day; don't work
  them out by hand (a reset close to the quiet hours is easy to get wrong). The task's prompt: *"Please run a
  scheduled backlog check with the backlog skill. You're welcome to decline or stop; if you do, note it in
  BACKLOG.md. Thank you!"*
  - A terminal scheduler (cron; Task Scheduler on Windows): use the whole-day lines, and run the script first, so
    Claude starts only on `GO` and the hours that must wait cost nothing: `cd <folder> && python3 <skill
    folder>/scripts/usage.py --check --backlog BACKLOG.md > /dev/null && claude -p "<the prompt>"` (on Windows,
    `> NUL`). The script reads `This week` itself, so "use it all" can start any hour of that day.
  - Claude Desktop: Scheduled → a local task (folder = where `BACKLOG.md` is), schedule **Custom**, one task per
    cron line. Every Desktop run is a session, so offer the choice: the quiet-hours lines (fewest sessions; "use it
    all" then starts in the conversation where they answer) or the whole-day lines (about 24 short sessions a week,
    most of which only check and stop).

  Plus the check-in below, if they want to be asked first. Tell them the computer must be on and awake in their
  quiet hours, and that the task's permission mode decides what it can do unattended (it must never need to ask:
  anything that would is skipped). The saved Claude Code login expires a few hours after Claude Code last ran; the
  script renews it when needed (`claude auth status`, or else a one-line Haiku reply), so it needs `claude` on the
  scheduler's PATH. Run one check by hand from the scheduler's environment to see that it works.
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
  (quiet hours, up to the cap), and "use it all" means any hour, up to 98% of the weekly and 5-hour limits.
- one warning line: if paid extra usage is turned on (Settings → Usage), the plan's limit doesn't stop work, so
  "use it all" would go on into paid usage. In chat, offer a number of items instead; in automatic mode the script
  sees it and keeps the normal caps.

If you can't see `BACKLOG.md`, ask them to attach it. In check-in mode, when they answer, work the items as
below, one at a time, in this conversation, and hand back the updated list at the end; check in briefly between
items only if they asked you to. In automatic mode, write their answer into the settings as
`This week: use it all | skip | B3, B5 | 2 items (reset <YYYY-MM-DD>)`, with the date of the coming reset, for
the scheduled checks to follow; if they chose "use it all" and the reset is less than a day away, also start
right away, as a scheduled check would.

## A scheduled backlog check (automatic mode)

1. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/usage.py --check --backlog <path to BACKLOG.md>`. It reads the
   settings itself, including `This week` (only if its reset date is the coming reset: `skip` waits, `use it all`
   means any hour with both caps at 98%, or the normal caps if paid extra usage is on). Anything but `GO` (a
   `WAIT`, or `CAN'T READ`): stop at once. Each check costs usage, so do nothing else. A `This week` line for an
   earlier reset is last week's: blank it when you next write the file.
2. Pick the next item: the ones named in a current `This week` line if any; otherwise theirs before yours, oldest
   first. Skip:
   - an item with a `working since` line under 4 hours old: another run is on it. An older one means a run died:
     replace it with `- backlog run <date>: stopped without recording anything (found later)` and go on;
   - an item with two `backlog run` lines and nothing from the person since: it waits for them to look (tell them
     the next time they're there);
   - anything once a `This week: N items` count is done.
3. Write `- working since <now>` under the item and work it (below). Every 15 minutes or so, and before each big
   step (a round of research, a long draft), run the check again. On anything but `GO` (the weekly cap or the
   5-hour share reached, quiet hours over, the reset passed so the next one is days away, or usage unreadable):
   finish the current step, save what you have, replace the `working since` line with a `backlog run` line, and
   end.
4. If the check still says `GO`, take the next item.

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
- Update `BACKLOG.md`: finished → move the item to `# Done` (format above); partly done → a `backlog run` line in
  a backlog run, a `progress` line otherwise (what it adds; what's left); something new worth doing → add an item
  (`by claude`). Remove your `working since` line either way.
- You may decline an item or stop partway; if you do, write that in its progress line.

The next time the person is there, tell them in a line or two what the backlog did and where the results are.
