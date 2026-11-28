# Tech watch

What tech watch looks for and what it may suggest. Claude reads this before every scan. Scans add proposals; only
you (with Claude, in a conversation) change this file's decisions.

## Settings

- Runs: when I ask
<!-- when I ask · scheduled (Claude Code: in the background on this computer) · weekly check-in (Claude chat) -->
- Scans: weekly
<!-- Scheduled runs only. weekly (about $5-15 of usage a month) · twice a week (a weekly scan plus one quick one,
     a few days later) · daily (a weekly scan plus quick ones; about $15-40: raise Monthly cap to match, or scans
     stop mid-month). On a subscription these are estimates against your usage limits, not charges. -->
- Model: opus
<!-- opus finds more; sonnet costs about a third -->
- Budget per scan: $3
<!-- A scheduled scan stops here; quick scans get half. -->
- Monthly cap: $15
<!-- No scheduled scan starts once this month's scans could pass this; they start again next month. When that
     happens, `status` and the start of a Claude Code session say so. -->
- Setup to watch:
<!-- Claude Code: the files that make up your Claude setup, comma-separated, e.g.
     ~/.claude/CLAUDE.md, ~/.claude/skills, ~/.claude/agents, ~/projects/campaign/CLAUDE.md
     A scheduled scan can read these and nothing else outside this folder. It also reads web pages, and a page
     could trick it into sending what it read to a website, so treat anything you list here as something that
     could leave this computer. List only what you'd be fine with that for. Settings files, prompt history, past
     conversations and credentials stay unreadable even inside a folder you list.
     Claude chat: leave blank and describe your setup below. -->

## About my work

<!-- A few lines: your cause or field, what you mostly do (research, writing, campaigns, organizing, coding...),
     who you work with, and what takes the most time or goes wrong most often. This decides what "helpful" means. -->

## My setup

<!-- What you use now: Claude plan (Free/Pro/Max/Team), Claude chat or Claude Code or both, custom instructions or
     Project instructions, skills, connectors (Google Drive, Notion...), other AI tools, and the other apps and
     services your work depends on. Your computer (Windows/Mac/Linux, how powerful). -->

## Limits

<!-- Anything tech watch should respect: money you'll spend on tools a month, accounts you won't create, privacy
     limits (e.g. "nothing that sends my documents to another company"), time you'll put into setting things up. -->

## Never propose

<!-- Things you don't want suggested at all, e.g. "switching away from Google Docs", "anything that posts for me". -->

## Watched pages

<!-- The sensor (Claude Code) and the weekly check (Claude chat) look at these for what's new. One per line:
     - URL — why it's watched        (or - [name](URL) — why)
     Add "· first N lines" to read only the top of a long page (a changelog). Feeds (Atom/RSS) work best; a page
     that needs JavaScript gives nothing, and the sensor says so. Add your field's own sources: a newsletter's feed,
     an organization's news page, a tool's changelog. -->

- https://www.anthropic.com/news — Claude model launches and announcements
- https://claude.com/blog — new Claude features (chat, Claude Code, connectors, skills)
- https://simonwillison.net/atom/entries/ — independent coverage of new AI tools from every company
- https://hnrss.org/newest?q=Claude&points=50&search_attrs=title — Hacker News stories with Claude in the title, 50+ points
- https://hnrss.org/newest?q=%22AI+agents%22&points=40&search_attrs=title — Hacker News stories with "AI agents" in the title, 40+ points

<!-- Claude Code only (leave these out if you use only Claude chat): -->
- https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md — every Claude Code release · first 300 lines
- https://code.claude.com/docs/llms.txt — a new Claude Code docs page usually means a new feature

## Decisions

<!-- One line per proposal you've decided on; scans read these and don't re-propose what you turned down.
     - 2026-12-01 · 2026-11-30-project-memory · yes · applied; check 2026-12-31 whether it helped
     - 2026-12-01 · 2026-11-30-notion-connector · no, not this kind · I don't want more connectors
     - 2026-12-01 · 2026-11-30-new-model · no, not as it is · too expensive; a cheaper variant is welcome
     - 2026-12-01 · 2026-11-30-transcription-app · later · after the campaign ends in January -->

## Scan notes

<!-- Claude chat only: the date of the last scan, the newest thing seen on each watched page (so next week can
     tell what's new), and what's being watched for later. Claude Code keeps these in reports/ instead. -->
