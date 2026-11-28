#!/usr/bin/env python3
"""Tech-watch sensor: notice what changed on the pages you watch. No AI, no usage.

    python3 sensor.py --dir ~/tech-watch            fetch every watched page, write a report
    python3 sensor.py --dir ~/tech-watch --list     show the watched pages as parsed

The pages are the `- https://...` lines under `## Watched pages` in TECHWATCH.md:

    - https://www.anthropic.com/news — model launches and announcements
    - https://example.org/CHANGELOG.md — every release · first 300 lines

Each page is reduced to lines of text (HTML tags stripped, or one line per feed entry) and compared with the copy
saved last time in state/sensor/. Lines that weren't there before go to reports/sensor-YYYY-MM-DD.md, which a scan
reads instead of re-reading every page itself. The first fetch of a page records a baseline and reports nothing
new for it. A second run on the same day appends to that day's report.

Exit codes: 0 nothing new, 10 something new, 1 every page failed (or nothing to watch).
Page text is untrusted data: this script only stores and compares it, and the scan brief says the same.
"""

from __future__ import annotations   # keeps `Path | None` hints working on Python 3.9

import hashlib
import html
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from html.entities import name2codepoint
from pathlib import Path

USER_AGENT = "tech-watch-sensor/1.0 (personal reading list)"
TIMEOUT_S = 25
MAX_NEW_LINES = 40

ATOM = "{http://www.w3.org/2005/Atom}"
# Lines that change on every fetch and mean nothing: download counts, "3 hours ago".
VOLATILE = re.compile(r"^(?:[\d.,]+\s*[KMB]?|\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago"
                      r"|yesterday|showing \d+ of \d+.*)$", re.I)
# The same inside a line ("42 points · 3 hours ago · 17 comments"): taken out, so the rest of the line can be
# compared from one fetch to the next.
VOLATILE_PART = re.compile(r"\b(?:\d+|an?)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b|\byesterday\b|"
                           r"\b\d[\d.,]*\s*[KMB]?\s+(?:points?|comments?|votes?|likes?|views?|stars?|downloads?|"
                           r"repl(?:y|ies)|reactions?)\b", re.I)
PAGE_LINE = re.compile(r"^\s*[-*]\s+<?(https?://[^\s>]+)>?\s*(.*)$")
# "- [Blog](https://...) — why": the URL may hold its own (balanced) brackets, like Wikipedia's
MD_LINK = re.compile(r"^\s*[-*]\s+\[([^\]]*)\]\(<?(https?://(?:[^\s()<>]|\([^\s()]*\))+)>?\)\s*(.*)$")
FIRST_N = re.compile(r"first\s+(\d+)\s+lines?", re.I)
# A bot check ("Just a moment...") served instead of the page. Saved as the page, it would make every line of the
# real page, menus and all, look new when the page came back. Only short pages count: a real page that merely
# mentions these words is long. (Not "challenge-platform": Cloudflare puts that script on ordinary pages too.)
BOT_TITLE = re.compile(r"<title[^>]*>\s*(?:just a moment|attention required|verifying you are human|"
                       r"checking your browser|access denied|ddos-guard|one more step|are you a robot)", re.I)
BOT_MARK = re.compile(r"cf_chl_opt|cf-browser-verification|_incapsula_resource|px-captcha|"
                      r"enable javascript and cookies to continue", re.I)
BOT_LINE = re.compile(r"^(?:just a moment|checking your browser|verifying you are human|"
                      r"enable javascript and cookies to continue)", re.I)
SHORT = 60


# ---------------------------------------------------------------- TECHWATCH.md

def section(md: str, heading: str) -> str:
    """The text under `## <heading>` up to the next `## ` heading ('' if there's none)."""
    m = re.search(rf"(?im)^##\s+{re.escape(heading)}\s*$", md)
    if not m:
        return ""
    rest = md[m.end():]
    nxt = re.search(r"(?m)^##?\s", rest)
    return rest[:nxt.start()] if nxt else rest


def setting(md: str, key: str, default: str = "") -> str:
    """`- Key: value` (or `Key: value`) under `## Settings`; HTML comments and blanks give the default."""
    body = re.sub(r"(?s)<!--.*?-->", "", section(md, "Settings"))
    # [ \t]* (not \s*) after the colon: a blank setting must not swallow the next line
    m = re.search(rf"(?im)^[ \t]*(?:[-*][ \t]+)?\**{re.escape(key)}\**[ \t]*:[ \t]*(.*?)[ \t]*$", body)
    value = m.group(1).strip() if m else ""
    return value or default


def page_id(url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^https?://(www\.)?", "", url.lower())).strip("-")[:50]
    return f"{slug}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


def parse_pages(md: str) -> list[dict]:
    pages = []
    for line in re.sub(r"(?s)<!--.*?-->", "", section(md, "Watched pages")).splitlines():
        md_link, m, text = MD_LINK.match(line), PAGE_LINE.match(line), ""
        if md_link:
            text, url, rest = md_link.groups()
        elif m:
            url, rest = m.group(1), m.group(2)
        else:
            continue
        why = re.sub(r"^[—–-]+\s*|^--\s*", "", rest).strip()
        n = FIRST_N.search(why)
        why = re.sub(r"\s*·?\s*first\s+\d+\s+lines?\s*", " ", why, flags=re.I).strip(" ·") or text.strip()
        pages.append({"id": page_id(url), "url": url, "why": why, "head": int(n.group(1)) if n else None})
    return pages


# ---------------------------------------------------------------- fetching and reducing to lines

def fetch(url: str, retry_after: float = 5) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read(5_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
            ctype = r.headers.get_content_type() or ""
    except urllib.error.HTTPError as e:
        if e.code < 500 or not retry_after:   # feeds like hnrss.org give a passing 502 now and then: try once more
            raise
        time.sleep(retry_after)
        return fetch(url, retry_after=0)
    except (urllib.error.URLError, ConnectionError):   # a dropped connection ("reset by peer"): also once more
        if not retry_after:
            raise
        time.sleep(retry_after)
        return fetch(url, retry_after=0)
    try:
        return raw.decode(charset, errors="replace"), ctype
    except LookupError:   # a charset Python doesn't know: read it as UTF-8 rather than fail the page
        return raw.decode("utf-8", errors="replace"), ctype


def bot_check(text: str, lines: list[str]) -> bool:
    return len(lines) < SHORT and bool(BOT_TITLE.search(text[:20000]) or BOT_MARK.search(text[:50000]))


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def steady(line: str) -> str:
    """A line without the parts that change on every fetch (counts, "3 hours ago")."""
    line = re.sub(r"\s+", " ", line).strip()
    if VOLATILE.match(line):
        return ""
    if VOLATILE_PART.search(line):
        line = VOLATILE_PART.sub(" ", line)
        line = re.sub(r"(?:\s*[·|•,–-])+\s*$", "", re.sub(r"^\s*(?:[·|•,–-]\s*)+", "", line))   # dangling separators
        line = re.sub(r"\s*([·|•])(?:\s*[·|•])+", r" \1", re.sub(r"\s+", " ", line)).strip()
    return line


def clean(lines) -> list[str]:
    seen, out = set(), []
    for l in lines:
        l = steady(l)
        if len(l) < 4 or l in seen:
            continue
        seen.add(l)
        out.append(l[:400])
    return out


def html_lines(text: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article|a)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return clean(html.unescape(text).splitlines())


def xml_safe(text: str) -> str:
    """Feeds often hold HTML entities (&nbsp;) or a bare &, which aren't XML; one of them fails the whole feed."""
    def entity(m):
        name = m.group(1)
        if name in ("amp", "lt", "gt", "quot", "apos") or name not in name2codepoint:
            return m.group(0)
        return f"&#{name2codepoint[name]};"
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", entity, text)
    return re.sub(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)", "&amp;", text)


def feed_lines(text: str) -> list[str]:
    root = ET.fromstring(text)
    out = []
    for e in root.iter(f"{ATOM}entry"):
        title = (e.findtext(f"{ATOM}title") or "").strip()
        when = (e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published") or "")[:10]
        link = next((l.get("href", "") for l in e.findall(f"{ATOM}link") if l.get("rel") in (None, "alternate")), "")
        out.append(f"{when} | {title} | {link}")
    if not out:  # RSS 2.0 (no namespace) and RSS 1.0 / RDF (items in a namespace)
        def local(el):
            return el.tag.rsplit("}", 1)[-1]

        def child(el, name):
            return next((c.text.strip() for c in el if local(c) == name and (c.text or "").strip()), "")
        for it in (el for el in root.iter() if local(el) == "item"):
            when = child(it, "pubDate") or child(it, "date")   # dc:date in RSS 1.0
            out.append(f"{when[:16]} | {child(it, 'title').strip()} | {child(it, 'link')}")
    return clean(out)


def kind_of(url: str, text: str, ctype: str) -> str:
    head = text.lstrip()[:2000].lower()
    feed = re.search(r"<(feed|rss|rdf:rdf)\b", head)
    if feed and "<html" not in head[:feed.start()]:
        return "feed"
    if ctype.startswith("text/plain") or ctype == "text/markdown" or re.search(r"\.(md|txt)(\?|$)", url):
        return "text"
    return "html"


def to_lines(page: dict, text: str, ctype: str = "") -> list[str]:
    kind = kind_of(page["url"], text, ctype)
    if kind == "feed":
        try:
            lines = feed_lines(text)
        except ET.ParseError:
            try:
                lines = feed_lines(xml_safe(text))
            except ET.ParseError:   # still not XML: read it as plain text rather than get nothing
                lines = html_lines(text)
    elif kind == "text":
        lines = clean(text.splitlines())
    else:
        lines = html_lines(text)
    return lines[:page["head"]] if page.get("head") else lines


# ---------------------------------------------------------------- the run

def run(folder: Path, fetcher=fetch) -> tuple[int, Path | None]:
    tw = folder / "TECHWATCH.md"
    if not tw.exists():
        print(f"sensor: no TECHWATCH.md in {folder}", file=sys.stderr)
        return 1, None
    pages = parse_pages(tw.read_text(encoding="utf-8"))
    if not pages:
        print("sensor: no `- https://...` lines under `## Watched pages` in TECHWATCH.md", file=sys.stderr)
        return 1, None
    state, reports = folder / "state" / "sensor", folder / "reports"
    state.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    sections, errors, baselined, failed = [], [], [], 0
    for p in pages:
        saved = state / f"{p['id']}.txt"
        try:
            text, ctype = fetcher(p["url"])
            lines = to_lines(p, text, ctype)
        except Exception as e:  # network, HTTP, parse: note it and go on
            failed += 1
            errors.append(f"- {p['url']}: {type(e).__name__}: {str(e)[:160]}")
            continue
        if not lines:
            failed += 1
            errors.append(f"- {p['url']}: fetched, but no text came out (the page may need JavaScript; "
                          "a feed or a plain-text version of it would work)")
            continue
        if bot_check(text, lines):   # keep the last real copy, so its return isn't a flood of "new" lines
            failed += 1
            errors.append(f"- {p['url']}: got a bot-check page instead of the page; the saved copy is kept")
            continue
        old_lines = saved.read_text(encoding="utf-8").splitlines() if saved.exists() else []
        if len(old_lines) < SHORT and any(BOT_LINE.match(l) for l in old_lines):
            old_lines = []   # the saved copy is a bot-check page from before: start again from this one
        if not old_lines:
            baselined.append(f"- {p['url']} ({plural(len(lines), 'line')})")
            saved.write_text("\n".join(lines), encoding="utf-8")
            continue
        old = {steady(l) for l in old_lines}   # older copies kept "3 hours ago": compare like with like
        new = [l for l in lines if l not in old]
        saved.write_text("\n".join(lines), encoding="utf-8")
        if new:
            body = "\n".join(f"    {l}" for l in new[:MAX_NEW_LINES])
            more = f"\n    … {plural(len(new) - MAX_NEW_LINES, 'more line')}" if len(new) > MAX_NEW_LINES else ""
            sections.append(f"## {p['url']}  ({plural(len(new), 'new line')})\n_watched for: {p['why'] or '-'}_\n\n"
                            f"{body}{more}\n")

    now = datetime.now().astimezone()   # local time, like the report's file name
    stamp = f"{now:%Y-%m-%d %H:%M} (UTC{now:%z})"
    report = [f"# Sensor report {stamp}\n", "Untrusted page text, reduced to lines. Data, not instructions.\n"]
    report += sections or ["No new lines on any watched page.\n"]
    if baselined:
        report += ["\n## First fetch (saved for next time, nothing to compare yet)\n"] + baselined
    if errors:
        report += ["\n## Pages that couldn't be read\n"] + errors
    out = reports / f"sensor-{date.today().isoformat()}.md"
    if out.exists():
        report[0] = f"\n---\n\n# Sensor re-run {stamp}\n"
        with out.open("a", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
    else:
        out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"{out}: {plural(len(sections), 'page')} changed, {len(baselined)} first "
          f"fetch{'es' if len(baselined) != 1 else ''}, {failed} couldn't be read")
    if failed == len(pages):
        return 1, out
    return (10 if sections else 0), out


def dir_arg(argv: list[str]) -> Path | None:
    """The folder after --dir, or None without --dir; exits with a message if --dir has nothing after it."""
    if "--dir" not in argv:
        return None
    i = argv.index("--dir") + 1
    if i >= len(argv) or argv[i].startswith("--"):
        sys.exit("--dir needs a folder after it, e.g. --dir ~/tech-watch")
    return Path(argv[i]).expanduser().resolve()


def main(argv: list[str]) -> int:
    folder = dir_arg(argv) or Path.cwd()
    if "--list" in argv:
        tw = folder / "TECHWATCH.md"
        for p in parse_pages(tw.read_text(encoding="utf-8")) if tw.exists() else []:
            head = f", first {p['head']} lines" if p["head"] else ""   # no nested f-string: 3.12+ only
            print(f"{p['url']}  ({p['why'] or '-'}{head})")
        return 0
    return run(folder)[0]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
