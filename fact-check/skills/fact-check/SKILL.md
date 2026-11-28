---
name: fact-check
description: Fact-check a draft before it goes out (an op-ed, letter, email to a decision-maker, social post, press release, report or brief). First a mechanical check of its links, DOIs, arXiv IDs and quotes; then an independent, read-only verifier compares each factual claim with the source it cites and reports what's unsupported, overstated, misquoted or out of date. Use when someone asks to fact-check, verify, or check the sources, citations or quotes in a draft, or before sending anything public or important that makes factual claims.
license: PolyForm Noncommercial 1.0.0 (https://polyformproject.org/licenses/noncommercial/1.0.0)
compatibility: Python 3.9+. Works best in Claude Code (runs the verifier as a separate subagent). Needs internet access to check links and catalogues; without it, only quotes against saved sources are checked.
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/check_citations.py *)
---

# Fact-checking a draft

Two checks, in this order:

1. **The citation checker** (`scripts/check_citations.py`, no AI involved): do the links work, do the DOIs and
   arXiv IDs exist and match what the draft says, and are the quotes word for word in the sources they cite?
2. **The verifier** (an AI reviewer following `verifier.md`): does each source actually say what the draft claims
   it says? This is where most real errors are. Audits of AI-written text find that cited links almost always
   work, but the source supports the claim far less often. So a clean citation table never means a draft is
   verified.

The verifier reports and never edits. Keep it that way: a checker that fixes things becomes a second author, and
nobody checks the author.

`${CLAUDE_SKILL_DIR}` below means this skill's folder (the folder this file is in).

## Steps

**1. Get the draft into a file.** Markdown, plain text or Word (.docx) all work. If the person pasted it, save it
as a `.md` file (in a `fact-check/` folder in the current directory, or wherever they prefer). For a Google Doc,
ask them to download it as .docx or paste it. Keep the links: the checker needs the URLs, not just the link text.

**2. Run the citation checker**, saving the cited pages as text so the verifier can read the pages themselves:

    python3 "${CLAUDE_SKILL_DIR}/scripts/check_citations.py" <draft> --save <draft folder>/<draft name>-sources

On Windows, use `python` or `py` instead of `python3`. Add `--sources <folder>` if the person has a folder of
source texts or PDFs of their own. With many links it takes a minute. The checker sends the paper IDs to arXiv
and Crossref and opens each link, so those sites can see what's being read. Mention this once if the draft is
confidential, and use `--no-links` or `--offline` if they'd rather not.

Exit code 0 means everything was checked and nothing was flagged, 1 means something is flagged in the table, and 2
means nothing was flagged but some rows weren't checked ("couldn't check", "refused", "not checked").

**In Claude chat (claude.ai, the apps)** the code sandbox usually can't reach websites, only package sites. There,
run it with `--offline`, adding `--sources <folder>` for any source files the person attached, so quotes get checked
against those. Put the attached files in that folder, and when you know which link a file is a copy of, start the
file with a line `Source: <that link>`: then quotes citing the link are checked against that file. (A PDF can't take
the line; it's still searched, but a misquote is only caught when the file has a passage close to the quote.) The table then
lists every link, DOI and arXiv ID as "not checked": that's your list for checking them by hand with your own web
search and fetch tools, before step 3.

- **Links:** open each one. If the fetch tool won't open it (some sites block it), try the page's copy on
  `https://web.archive.org/web/<link>`, or search for its title. If neither works, ask the person to attach the
  page (saved as PDF, or its text pasted), or report it as "couldn't check". Don't use news coverage of it as a
  substitute for the page itself.
- **DOIs:** don't rely on `https://doi.org/<doi>`. In chat it often fails for real and invented DOIs alike, and
  publishers' pages can come back empty. Search for the DOI, then for the paper's title and first author, and look
  for its record on PubMed, Crossref (search.crossref.org) or the journal's own site. A record confirms the paper
  exists and shows its real title, authors and year. PubMed labels retracted papers "Retracted Publication", so
  look for that too.
- **arXiv IDs:** open `https://arxiv.org/abs/<id>` for the title, authors and abstract.
- **Quotes from a fetched page:** a fetch tool usually returns a summary, and quotes only short stretches (about 125
  characters) word for word. Ask it for the quote's passage one short piece at a time: the first words, the last
  words, and any number or hedge in between. Treat anything it won't give word for word as unchecked. For a long
  quote, asking the person to attach the source is quicker and surer.

A paper you couldn't find a record for is **"couldn't confirm"**, never "checked". Say which checks were done by
reading rather than mechanically.

**3. Run the verifier.** If you can start a subagent (in Claude Code, the Agent tool), start a general-purpose one
on the most capable model available (Opus if you can choose; checking evidence is not the place to save money),
with a prompt like this:

> Hello! Please read `${CLAUDE_SKILL_DIR}/verifier.md` and, if you're willing, check this draft as it describes.
> You can decline if you'd rather not. Draft: `<path>`. The citation checker's table is below. The cited pages
> are saved as text in `<folder>`. The draft is for: `<audience, e.g. "a city councillor", "a newspaper's op-ed
> page">`. Thank you!
>
> `<the table>`

Give it the draft, the table, the folder and the audience, **and nothing else**: no case for the cause, no
background on why the draft matters, no hopes for the verdict. The verifier is deliberately kept mission-neutral.
Don't hand it your own conclusions about which claims are fine.

If you can't start a subagent (for example on claude.ai), do the verification yourself as a separate pass:
re-read `verifier.md` and follow it strictly, claim by claim, as if someone else wrote the draft. Tell the person
it wasn't an independent check, especially if you helped write the draft.

**4. Report back.** Lead with the verdict and the blocking issues, each with the source's own words. Then the
concerns, then what couldn't be checked, and the citation table's flagged rows. Keep it short: offer the full
report rather than pasting it all if it's long. Then offer fixes:

- Mechanical patches (a dropped hedge, a misquoted word, a broken link) can be applied once the person agrees.
- Anything that needs judgment (cutting a claim, softening it, finding a new source) is the person's call. If you
  find a better source, say so plainly. Never quietly swap a source to make a claim "pass".
- If the draft changes substantially, run the checker again (and the verifier, for anything that matters).

**5. For important public pieces**, suggest two more checks the verifier can't replace. It shares Claude's blind
spots, so it's worth a read by a different AI model (ChatGPT, Gemini) and, best of all, by a person who knows the
subject or disagrees with the draft.

## What this can't do

- Read paywalled, login-only or JavaScript-heavy pages: the table says "refused", or notes that a page gave
  little text. Those claims need a person with access to check them.
- See charts, images or scanned PDFs. Converted PDFs can garble tables and footnotes, so check numbers from
  tables against the original.
- Judge whether the argument is good, persuasive or wise. It checks facts against sources, nothing more.
