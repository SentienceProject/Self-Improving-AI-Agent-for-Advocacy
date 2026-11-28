# Fact-check skill

Check a draft before it goes out: an op-ed, a letter or email to a decision-maker, a social post, a press release,
a report. Ask Claude "please fact-check this draft" and it does two things:

1. **A mechanical citation check** (no AI involved): it opens every link and reports broken ones, looks up DOIs and
   arXiv IDs in the public catalogues (catching wrong, invented and retracted papers), and checks that every quote
   is word for word in the source it cites, showing the closest real passage when it isn't.
2. **An independent verifier**: a separate Claude reviewer reads each cited source and reports claims the source
   doesn't support, dropped hedges ("may", "in this study") and overstatements, misattributed views, out-of-date
   facts, wrong numbers and misquotes. It reports and never edits, and it's deliberately not told what cause the
   draft argues for, so it judges every claim the same way.

You get a short verdict, each problem with the source's own words, and suggested fixes. Nothing in your draft
changes unless you agree.

It comes from the [Self-Improving AI Agent for Advocacy](../README.md) workspace. There it caught reversed quotes,
dropped hedges and a wrong legal citation, and the one run that skipped it sent out an "official" email address
nobody could find.

## Install

**Claude Code** (recommended: the verifier runs as a separate reviewer). In a Claude Code session, type:

    /plugin install fact-check --marketplace SentienceProject/Self-Improving-AI-Agent-for-Advocacy

On older versions of Claude Code, use two commands instead:

    /plugin marketplace add SentienceProject/Self-Improving-AI-Agent-for-Advocacy
    /plugin install fact-check@sentience-project

Or copy the `skills/fact-check/` folder into `~/.claude/skills/` (all your projects) or into a project's
`.claude/skills/` folder.

**Claude chat (claude.ai or the Claude apps), any plan including Free:**

1. Make a zip of the `skills/fact-check` folder, not the whole plugin: the zip must have `fact-check/SKILL.md` at
   its top. On Windows, right-click `skills/fact-check` → Send to → Compressed (zipped) folder. On a Mac, right-click
   it → Compress. Or, in a terminal in this folder:
   `cd skills && zip -r ../fact-check.zip fact-check -x '*__pycache__*'`.
2. In Claude, open Settings → Capabilities and turn on **Code execution and file creation** (called "Create and
   edit files" in newer versions).
3. Go to Customize → Skills, click **+**, then **Create skill** → **Upload a skill**, choose the zip, and switch
   the skill on.

On Team and Enterprise plans, an owner may need to allow skills first. In chat, the verifier can't run as a
separate reviewer, so Claude does a second, stricter pass itself and tells you so. Chat's code sandbox can't open
websites, so Claude checks the links and paper IDs by reading them with its web tools instead of the script.

You need [Python](https://www.python.org/downloads/) 3.9 or newer. For PDFs, `pdftotext` (from poppler) or the
`pypdf` package helps but isn't required.

## Use

Ask in your own words: "fact-check my op-ed in draft.md", "check the sources in this letter before I send it",
"are these quotes right?". Paste the draft or point to the file (Markdown, text or Word .docx). For a Google
Doc, download it as .docx first, so the links come along.

You can also run the citation check on its own, in a terminal:

    python3 skills/fact-check/scripts/check_citations.py my-draft.md --save my-draft-sources

It prints a table and saves each linked page as text in `my-draft-sources/`. Run it with `--help` for the options.

## Limits

- Paywalled, login-only and JavaScript-heavy pages can't be read automatically. The table marks them, and those
  claims need a person with access to check them.
- It can't see charts, images or scanned PDFs, and converted PDFs can garble tables: check numbers from tables
  against the original.
- The verifier is Claude checking Claude's kind of mistakes. For anything important and public, also ask a
  different AI model, and ideally a person who knows the subject, to read it.
- The checker opens your draft's links and sends paper IDs to arXiv and Crossref, so those sites can see what's
  being read. Ask Claude to skip the link check (`--no-links`) for a confidential draft.

## Tests

    python3 -m unittest discover fact-check/tests

License: [PolyForm Noncommercial 1.0.0](../LICENSE).
