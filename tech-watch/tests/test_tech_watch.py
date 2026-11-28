"""sensor.py and scan.py with canned pages and a fake `claude`: no network, no usage.

    python3 -m unittest discover tech-watch/tests
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1] / "skills" / "tech-watch"
sys.path.insert(0, str(SKILL / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = load("sensor")
C = load("scan")

FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>New model</title><updated>2026-11-20T00:00:00Z</updated><link href="https://x/1"/></entry>
{extra}</feed>"""


def tw_file(folder: Path, settings="", decisions="", pages=None):
    pages = pages if pages is not None else [
        "- https://example.org/feed.atom — a feed",
        "- https://example.org/CHANGELOG.md — releases · first 3 lines",
        "- <https://example.org/news> -- news page"]
    text = (SKILL / "template.md").read_text(encoding="utf-8")
    text = text.replace("- Runs: when I ask", "- Runs: scheduled\n" + settings)
    head, _, rest = text.partition("## Watched pages")
    _, _, after = rest.partition("## Decisions")
    text = head + "## Watched pages\n\n" + "\n".join(pages) + "\n\n## Decisions\n" + decisions + "\n" + after
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "TECHWATCH.md").write_text(text, encoding="utf-8")
    return text


class TestParsing(unittest.TestCase):
    def test_template_parses(self):
        md = (SKILL / "template.md").read_text(encoding="utf-8")
        pages = S.parse_pages(md)
        self.assertGreaterEqual(len(pages), 5)
        self.assertTrue(all(p["url"].startswith("https://") for p in pages))
        cl = [p for p in pages if "CHANGELOG" in p["url"]][0]
        self.assertEqual(cl["head"], 300)
        self.assertEqual(cl["why"], "every Claude Code release")
        self.assertEqual(len({p["id"] for p in pages}), len(pages))

    def test_settings_and_defaults(self):
        md = (SKILL / "template.md").read_text(encoding="utf-8")
        self.assertEqual(S.setting(md, "Model"), "opus")
        self.assertEqual(S.setting(md, "Setup to watch", "none"), "none")   # blank, comment below ignored
        self.assertEqual(C.money(S.setting(md, "Monthly cap"), 0), 15.0)

    def test_page_line_forms(self):
        md = "## Watched pages\n- <https://a.org/x> -- why A\n* https://b.org/y — why B · first 10 lines\n" \
             "- not a page\n<!-- - https://c.org/hidden — no -->\n## Decisions\n- https://d.org — not watched"
        pages = S.parse_pages(md)
        self.assertEqual([p["url"] for p in pages], ["https://a.org/x", "https://b.org/y"])
        self.assertEqual(pages[0]["why"], "why A")
        self.assertEqual((pages[1]["why"], pages[1]["head"]), ("why B", 10))

    def test_kinds(self):
        self.assertEqual(S.kind_of("https://x/f", FEED.format(extra=""), "application/atom+xml"), "feed")
        self.assertEqual(S.kind_of("https://x/C.md", "# Changelog\n- 1.0", "text/plain"), "text")
        self.assertEqual(S.kind_of("https://x/", "<html><body><p>rss<feed is a word</p>", "text/html"), "html")
        lines = S.to_lines({"url": "https://x/f", "head": None}, FEED.format(extra=""), "")
        self.assertEqual(lines, ["2026-11-20 | New model | https://x/1"])
        html_ = "<html><head><title>t</title></head><body><p>Hello there</p><script>x()</script>" \
                "<li>3 hours ago</li><li>Second item</li></body></html>"
        self.assertEqual(S.html_lines(html_), ["Hello there", "Second item"])


class TestSensor(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        tw_file(self.dir)
        self.pages = {"https://example.org/feed.atom": (FEED.format(extra=""), "application/atom+xml"),
                      "https://example.org/CHANGELOG.md": ("## 2.0\n- a\n- b\n- c\n- d\n", "text/plain"),
                      "https://example.org/news": ("<p>Old story</p>", "text/html")}

    def fetch(self, url):
        if url not in self.pages:
            raise OSError("unreachable")
        return self.pages[url]

    def run_sensor(self):
        with redirect_stdout(io.StringIO()):
            return S.run(self.dir, fetcher=self.fetch)

    def test_baseline_then_only_new_lines(self):
        rc, report = self.run_sensor()
        self.assertEqual(rc, 0)
        self.assertIn("First fetch", report.read_text())
        self.pages["https://example.org/feed.atom"] = (FEED.format(
            extra='<entry><title>Skills in chat</title><updated>2026-11-25</updated><link href="https://x/2"/>'
                  '</entry>'), "")
        self.pages["https://example.org/CHANGELOG.md"] = ("## 2.1\n- new\n## 2.0\n- a\n- b\n", "text/plain")
        rc, report = self.run_sensor()
        text = report.read_text()
        self.assertEqual(rc, 10)
        self.assertIn("Skills in chat", text)
        self.assertNotIn("New model", text.split("Sensor re-run")[-1])
        self.assertIn("## 2.1", text)          # head 3: '## 2.1', '- new', '## 2.0'
        self.assertIn("Sensor re-run", text)   # same day: appended, not replaced

    def test_failures_reported_not_fatal(self):
        del self.pages["https://example.org/news"]
        rc, report = self.run_sensor()
        self.assertEqual(rc, 0)
        self.assertIn("couldn't be read", report.read_text())
        self.pages.clear()
        rc, _ = self.run_sensor()
        self.assertEqual(rc, 1)

    def test_no_techwatch_file(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(S.run(self.dir / "nowhere")[0], 1)

    # Found in the second claude.ai test (2026-11-27)

    def test_bot_check_page_keeps_the_saved_copy(self):
        news = "https://example.org/news"
        self.pages[news] = ("<ul>" + "".join(f"<li>Menu item {i}</li>" for i in range(30)) + "</ul><p>Story one</p>",
                            "text/html")
        self.run_sensor()
        self.pages[news] = ("<html><head><title>Just a moment...</title></head><body>Enable JavaScript and cookies "
                            "to continue<script>window._cf_chl_opt={}</script></body></html>", "text/html")
        _, report = self.run_sensor()
        self.assertIn("bot-check page", report.read_text())
        self.assertIn("Menu item 7", (self.dir / "state" / "sensor" / f"{S.page_id(news)}.txt").read_text())
        self.pages[news] = ("<ul>" + "".join(f"<li>Menu item {i}</li>" for i in range(30)) + "</ul><p>Story two</p>",
                            "text/html")
        _, report = self.run_sensor()
        last = report.read_text().split("Sensor re-run")[-1]
        self.assertIn("Story two", last)
        self.assertNotIn("Menu item 7", last)                       # not a flood of "new" lines
        self.assertIn("(1 new line)", last)                          # and not "(1 lines)"

    def test_a_saved_bot_check_page_from_before_is_replaced(self):
        news = "https://example.org/news"
        (self.dir / "state" / "sensor").mkdir(parents=True)
        (self.dir / "state" / "sensor" / f"{S.page_id(news)}.txt").write_text(
            "Just a moment...\nEnable JavaScript and cookies to continue")
        self.pages[news] = ("<ul>" + "".join(f"<li>Menu item {i}</li>" for i in range(30)) + "</ul>", "text/html")
        _, report = self.run_sensor()
        self.assertNotIn("Menu item", report.read_text().split("First fetch")[0])   # a fresh start, not "new"

    # Found in the third claude.ai test (2026-11-28)

    def test_times_and_counts_inside_a_line_are_not_news(self):
        news = "https://example.org/news"
        self.pages[news] = ("<li>Claude 5 is out</li><li>Claude 5 is out · 42 points · 3 hours ago · 17 comments</li>"
                            "<li>Posted an hour ago by sam</li>", "text/html")
        self.run_sensor()
        self.pages[news] = ("<li>Claude 5 is out · 97 points · 5 hours ago · 40 comments</li>"
                            "<li>Posted 2 hours ago by sam</li>", "text/html")
        self.assertEqual(self.run_sensor()[0], 0)                    # nothing new
        self.assertEqual(S.steady("Claude 5 is out · 97 points · 5 hours ago · 40 comments"), "Claude 5 is out")
        self.assertEqual(S.steady("Launched 3 days ago, 2.1K stars"), "Launched")

    def test_saved_copies_from_before_this_update_do_not_flood(self):
        news = "https://example.org/news"
        (self.dir / "state" / "sensor").mkdir(parents=True)
        (self.dir / "state" / "sensor" / f"{S.page_id(news)}.txt").write_text(
            "\n".join(f"Story {i} · {i} points · 3 hours ago" for i in range(10)))
        self.pages[news] = ("".join(f"<li>Story {i} · {i + 5} points · 9 hours ago</li>" for i in range(10)),
                            "text/html")
        self.assertEqual(self.run_sensor()[0], 0)

    def test_markdown_links_in_watched_pages(self):
        md = ("## Watched pages\n- [Blog](https://a.org/blog) — new posts\n"
              "- [Shrimp welfare](https://en.wikipedia.org/wiki/Shrimp_(food)) · first 20 lines\n"
              "- https://en.wikipedia.org/wiki/Krill_(animal) — bare URL with brackets\n")
        pages = S.parse_pages(md)
        self.assertEqual([p["url"] for p in pages], ["https://a.org/blog", "https://en.wikipedia.org/wiki/Shrimp_(food)",
                                                     "https://en.wikipedia.org/wiki/Krill_(animal)"])
        self.assertEqual(pages[0]["why"], "new posts")
        self.assertEqual((pages[1]["why"], pages[1]["head"]), ("Shrimp welfare", 20))

    def test_long_page_mentioning_a_bot_check_is_still_read(self):
        page = "<title>Just a moment of silence</title>" + "".join(f"<p>Paragraph {i}</p>" for i in range(80))
        self.assertFalse(S.bot_check(page, S.html_lines(page)))

    def test_feed_with_html_entities_or_bad_xml(self):
        nbsp = FEED.format(extra='<entry><title>Claude&nbsp;5 &amp; more &mdash; R&D</title><updated>2026-11-25'
                                 '</updated><link href="https://x/2"/></entry>')
        lines = S.to_lines({"url": "https://x/feed.atom"}, nbsp)
        self.assertIn("2026-11-25 | Claude\xa05 & more \u2014 R&D | https://x/2".replace("\xa0", " "),
                      [l.replace("\xa0", " ") for l in lines])
        broken = '<?xml version="1.0"?><rss><channel><item><title>Kept anyway</title><foo></item></rss>'
        self.assertIn("Kept anyway", " ".join(S.to_lines({"url": "https://x/rss"}, broken)))


class TestScan(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.setup_dir = self.dir / "claude-setup"
        self.setup_dir.mkdir()
        (self.setup_dir / "CLAUDE.md").write_text("# mine")
        self.tw = self.dir / "tw"
        self.md = tw_file(self.tw, settings=f"- Setup to watch: {self.setup_dir}, ~/does-not-exist-xyz\n"
                                            "- Monthly cap: $10\n- Budget per scan: $4\n- Scans: twice a week\n",
                          decisions="- 2026-11-01 · 2026-10-30-old · no, not this kind · nope\n")

    def entry(self, days_ago, kind="full", outcome="ok", cost=1.0):
        C.record(self.tw, {"kind": kind, "outcome": outcome, "cost_usd": cost})
        rs = (self.tw / "state" / "scans.jsonl").read_text().splitlines()
        last = json.loads(rs[-1])
        last["day"] = (date.today() - timedelta(days=days_ago)).isoformat()
        last["ts"] = f"{last['day']}T12:00:00+00:00"
        rs[-1] = json.dumps(last)
        (self.tw / "state" / "scans.jsonl").write_text("\n".join(rs) + "\n")

    def test_due(self):
        self.assertEqual(C.due(self.tw, self.md), "full")           # never scanned
        self.entry(2)
        self.assertEqual(C.due(self.tw, self.md), "")               # twice a week: 3 days apart
        self.entry(3)
        (self.tw / "state" / "scans.jsonl").write_text("")
        self.entry(3)
        self.assertEqual(C.due(self.tw, self.md), "quick")
        self.entry(8, outcome="retry", cost=0)                        # retries don't count as scans
        self.assertEqual(C.due(self.tw, self.md), "quick")

    def test_attempts_a_day_are_capped(self):
        for _ in range(C.MAX_ATTEMPTS_A_DAY):
            self.entry(0, outcome="retry", cost=0)
        self.assertEqual(C.due(self.tw, self.md), "")

    def test_settings_lock_down_the_scan(self):
        s = C.scout_settings(self.tw, self.md)["permissions"]
        self.assertEqual(s["defaultMode"], "dontAsk")
        self.assertIn(f"Read({C.rule_path(self.setup_dir.resolve())}/**)", s["allow"])
        self.assertFalse(any("does-not-exist" in r for r in s["allow"]))
        edits = [r for r in s["allow"] if r.startswith("Edit(")]
        self.assertEqual(len(edits), 2)
        self.assertTrue(all("/reports/" in r or "/proposals/" in r for r in edits))
        self.assertIn("Bash", s["deny"])
        self.assertNotIn("Grep", s["allow"])     # a bare Grep/Glob rule let Grep search anywhere (tested live)
        self.assertNotIn("Glob", s["allow"])
        self.assertTrue(any(".credentials.json" in r for r in s["deny"]))
        self.assertEqual(C.rule_path("C:\\Users\\Ana\\tw"), "//c/Users/Ana/tw")
        self.assertEqual(C.rule_path("/home/ana/tw"), "//home/ana/tw")

    def test_waiting_ignores_decided_and_examples(self):
        (self.tw / "proposals").mkdir()
        for stem in ("2026-10-30-old", "2026-11-30-new-model", "2026-11-30-project-memory"):
            (self.tw / "proposals" / f"{stem}.md").write_text("# x")
        # 2026-11-30-project-memory appears only in the template's commented example: still waiting
        self.assertEqual(C.waiting(self.tw), ["2026-11-30-new-model", "2026-11-30-project-memory"])

    def test_waiting_matches_whole_names_only(self):
        (self.tw / "proposals").mkdir()
        for stem in ("2026-11-30-memory", "2026-11-30-memory-v2", "2026-10-30"):
            (self.tw / "proposals" / f"{stem}.md").write_text("# x")
        md = (self.tw / "TECHWATCH.md").read_text()
        (self.tw / "TECHWATCH.md").write_text(md.replace(
            "## Decisions\n", "## Decisions\n- 2026-12-01 · 2026-11-30-memory-v2 · yes\n"))
        self.assertEqual(C.waiting(self.tw), ["2026-10-30", "2026-11-30-memory"])

    def test_money(self):
        self.assertEqual(C.money("$1,000", 0), 1000.0)
        self.assertEqual(C.money("$12.50 a month", 0), 12.5)
        self.assertEqual(C.money("about $3, maybe 4", 0), 3.0)
        self.assertEqual(C.money("", 15.0), 15.0)

    def test_dir_without_a_folder(self):
        for argv in (["--dir"], ["--dir", "--now"]):
            with self.assertRaises(SystemExit) as e:
                C.folder_from(argv)
            self.assertIn("needs a folder", str(e.exception.code))
        self.assertEqual(C.folder_from(["--dir", str(self.tw)]), self.tw.resolve())

    def test_sensor_list(self):
        out = io.StringIO()
        with redirect_stdout(out):
            S.main(["--dir", str(self.tw), "--list"])
        self.assertIn("https://example.org/CHANGELOG.md  (releases, first 3 lines)", out.getvalue())

    def fake_claude(self, cost=0.8, is_error=False, rc=0):
        fake = self.dir / "claude"
        fake.write_text("#!/usr/bin/env python3\nimport sys, json, pathlib\n"
                        "args = sys.argv[1:]\nprompt = sys.stdin.read()\n"
                        f"pathlib.Path('args.json').write_text(json.dumps(args))\n"
                        "pathlib.Path('prompt.txt').write_text(prompt)\n"
                        f"print(json.dumps({{'total_cost_usd': {cost}, 'is_error': {is_error}}}))\n"
                        f"sys.exit({rc})\n")
        fake.chmod(0o755)
        return str(fake)

    def run_scan(self, kind="full", **kw):
        with mock.patch.object(C, "claude_bin", return_value=self.fake_claude(**kw)), \
                mock.patch.object(C.sensor, "run", return_value=(0, self.tw / "reports" / "sensor-x.md")), \
                redirect_stdout(io.StringIO()) as out:
            rc = C.scan(self.tw, kind, self.md)
        return rc, out.getvalue()

    @unittest.skipIf(os.name == "nt", "fake claude is a shebang script")
    def test_scan_runs_locked_down_and_records_cost(self):
        rc, _ = self.run_scan("quick")
        self.assertEqual(rc, 0)
        args = json.loads((self.tw / "args.json").read_text())
        self.assertIn("-p", args)
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "2.0")    # quick: half of $4
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertEqual(args[args.index("--tools") + 1], C.TOOLS)
        self.assertIn("--strict-mcp-config", args)
        prompt = (self.tw / "prompt.txt").read_text()
        self.assertIn("Scan: quick", prompt)
        self.assertIn("The tech-watch scan", prompt)
        self.assertIn(str(self.setup_dir.resolve()), prompt)
        last = C.runs(self.tw)[-1]
        self.assertEqual((last["kind"], last["outcome"], last["cost_usd"]), ("quick", "ok", 0.8))

    @unittest.skipIf(os.name == "nt", "fake claude is a shebang script")
    def test_failed_scan_outcomes(self):
        self.run_scan(cost=0.01, is_error=True, rc=1)
        self.assertEqual(C.runs(self.tw)[-1]["outcome"], "retry")
        self.run_scan(cost=1.5, is_error=True, rc=1)
        self.assertEqual(C.runs(self.tw)[-1]["outcome"], "failed")

    def test_monthly_cap_skips(self):
        self.entry(0, cost=7.0)
        with mock.patch.object(C, "claude_bin", side_effect=AssertionError("must not run")), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(C.scan(self.tw, "full", self.md), 0)      # $7 + $4 > $10
        self.assertIn("monthly cap", out.getvalue())

    def test_init_and_engine(self):
        target = self.dir / "fresh"
        with mock.patch.object(C, "POINTER", self.dir / "cfg" / "where.txt"), redirect_stdout(io.StringIO()):
            C.cmd_init(target)
            self.assertTrue((target / "TECHWATCH.md").exists())
            self.assertEqual(C.folder_from([]), target)
            self.assertFalse(C.engine_stale(target))                   # no engine yet: nothing to refresh
            C.refresh_engine(target)
            self.assertFalse(C.engine_stale(target))
            (target / ".engine" / "scout.md").write_text("old")
            self.assertTrue(C.engine_stale(target))
            out = io.StringIO()
            (target / "proposals" / "2026-12-01-x.md").write_text("# x")
            with redirect_stdout(out):
                C.cmd_waiting()
            self.assertIn("1 proposal waiting", out.getvalue())
            self.assertFalse(C.engine_stale(target))                   # the hook refreshed it

    def test_engine_copy_finds_its_brief(self):
        eng = C.refresh_engine(self.tw)
        r = __import__("subprocess").run([sys.executable, str(eng / "scan.py"), "status", "--dir", str(self.tw)],
                                         capture_output=True, text=True, env={**os.environ, "HOME": str(self.dir)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("waiting for you", r.stdout)

    # Regressions found in testing on claude.ai (2026-11-27): each failed before its fix.

    def test_blank_setting_does_not_take_the_next_line(self):
        md = "## Settings\n- Model:\n- Budget per scan:\n- Monthly cap: $15\n"
        self.assertEqual(C.setting(md, "Model"), "opus")             # the default, not "- Budget per scan:"
        self.assertEqual(C.money(C.setting(md, "Budget per scan"), 3.0), 3.0)   # not $15

    def test_rss1_rdf_feed(self):
        rdf = ('<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
               'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<item rdf:about="https://x/1"><title>RDF one</title><link>https://x/1</link>'
               '<dc:date>2026-11-20</dc:date></item></rdf:RDF>')
        self.assertEqual(S.to_lines({"url": "https://x/feed.rdf"}, rdf), ["2026-11-20 | RDF one | https://x/1"])
        rss2 = ('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel><item><title>Two</title>'
                '<atom:link href="https://x/self" rel="self"/><link>https://x/2</link></item></channel></rss>')
        self.assertEqual(S.to_lines({"url": "https://x/rss"}, rss2), ["| Two | https://x/2"])

    def test_unknown_charset_falls_back(self):
        class R(io.BytesIO):
            class headers:
                get_content_charset = staticmethod(lambda: "klingon")
                get_content_type = staticmethod(lambda: "text/html")
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(S.urllib.request, "urlopen", return_value=R(b"<p>hello world</p>")):
            self.assertEqual(S.fetch("https://x/")[0], "<p>hello world</p>")

    def test_retry_once_on_dropped_connection_or_5xx(self):
        class R(io.BytesIO):
            class headers:
                get_content_charset = staticmethod(lambda: "utf-8")
                get_content_type = staticmethod(lambda: "text/html")
            def __enter__(self): return self
            def __exit__(self, *a): return False
        err502 = S.urllib.error.HTTPError("https://x/", 502, "Bad Gateway", {}, io.BytesIO())
        self.addCleanup(err502.close)
        for first in (ConnectionResetError(104, "reset"), S.urllib.error.URLError("reset"), err502):
            with mock.patch.object(S.urllib.request, "urlopen", side_effect=[first, R(b"<p>ok then</p>")]), \
                    mock.patch.object(S.time, "sleep"):
                self.assertEqual(S.fetch("https://x/")[0], "<p>ok then</p>")
        err404 = S.urllib.error.HTTPError("https://x/", 404, "Not Found", {}, io.BytesIO())
        self.addCleanup(err404.close)
        with mock.patch.object(S.urllib.request, "urlopen", side_effect=[err404]), self.assertRaises(Exception):
            S.fetch("https://x/")                                        # a 404 isn't retried
        with mock.patch.object(S.urllib.request, "urlopen", side_effect=[err502, err502]), \
                mock.patch.object(S.time, "sleep"), self.assertRaises(Exception):
            S.fetch("https://x/")                                        # only once

    def test_missing_claude_is_recorded_and_capped(self):
        with mock.patch.object(C, "claude_bin", return_value=""), mock.patch.object(C.sensor, "run"), \
                redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            for _ in range(3):
                self.assertEqual(C.scan(self.tw, "full", self.md), 1)
        self.assertEqual([r["outcome"] for r in C.runs(self.tw)], ["no-claude"] * 3)
        self.assertEqual(C.due(self.tw, self.md), "")                 # three attempts today: stop until tomorrow
        with redirect_stdout(io.StringIO()) as out, mock.patch.object(C, "scheduled", return_value="on"):
            C.cmd_status(self.tw)
        self.assertIn("warning: the last 3 scan attempts didn't succeed", out.getvalue())

    def test_quick_scans_leave_room_for_a_full_one(self):
        self.entry(1, kind="quick", cost=5.0)                        # cap $10, full budget $4, quick $2
        with mock.patch.object(C, "claude_bin", side_effect=AssertionError("must not run")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(C.scan(self.tw, "quick", self.md), 0)    # $5 + $2 + $4 reserve > $10
            self.assertEqual(C.scan(self.tw, "quick", self.md), 0)
        self.assertEqual(sum(r.get("outcome") == "cap" for r in C.runs(self.tw)), 1)   # one skip line a day
        self.run_scan("full")                                        # $5 + $4 fits: the full scan still runs
        self.assertEqual(C.runs(self.tw)[-1]["outcome"], "ok")

    def test_cron_line_carries_path_and_claude(self):
        written = {}

        def fake_run(cmd, **kw):
            if cmd[:2] == ["crontab", "-"]:
                written["tab"] = kw["input"]
            return subprocess_result()
        subprocess_result = lambda: mock.Mock(returncode=0, stdout="", stderr="")
        which = {"node": "/home/u/.nvm/versions/node/v22/bin/node", "crontab": "/usr/bin/crontab"}
        with mock.patch.object(C.os, "name", "posix"), mock.patch.object(C.shutil, "which", side_effect=which.get), \
                mock.patch.object(C, "claude_bin", return_value="/opt/my node/bin/claude"), \
                mock.patch.dict(C.os.environ, {"PATH": "/mnt/c/Windows:" * 200}), \
                mock.patch.object(C.subprocess, "run", side_effect=fake_run), redirect_stdout(io.StringIO()):
            self.assertEqual(C.cmd_schedule(self.tw, "on"), 0)
        line = [l for l in written["tab"].splitlines() if C.CRON_MARK in l][0]
        self.assertIn('PATH="/opt/my node/bin:/home/u/.nvm/versions/node/v22/bin:/opt/homebrew/bin:/usr/local/bin:'
                      '/usr/bin:/bin"', line)
        self.assertNotIn("/mnt/c/Windows", line)                       # not the shell's whole (long) PATH
        self.assertIn('TECH_WATCH_CLAUDE="/opt/my node/bin/claude"', line)
        with mock.patch.dict(C.os.environ, {"TECH_WATCH_CLAUDE": str(SKILL / "SKILL.md")}):
            self.assertEqual(C.claude_bin(), str(SKILL / "SKILL.md"))   # the pinned path wins when it exists


    def test_twice_a_week_is_two_scans_a_week(self):
        ran, start = [], date.today()
        for i in range(28):
            day = start + timedelta(days=i)
            fake = type("FakeDate", (date,), {"today": classmethod(lambda cls, d=day: d)})
            with mock.patch.object(C, "date", fake):
                kind = C.due(self.tw, self.md)
                if kind:
                    C.record(self.tw, {"kind": kind, "outcome": "ok", "cost_usd": 0})
                    ran.append((i, kind))
        self.assertEqual([k for _, k in ran].count("full"), 4)
        self.assertEqual([k for _, k in ran].count("quick"), 4, ran)

    def test_private_claude_files_stay_unreadable(self):
        md = self.md.replace(f"- Setup to watch: {self.setup_dir}", "- Setup to watch: ~/.claude")
        deny = C.scout_settings(self.tw, md)["permissions"]["deny"]
        home = C.rule_path(Path.home())
        for private in (".claude/settings.json", ".claude/history.jsonl", ".claude/projects/**",
                        ".claude/.credentials.json", ".claude.json"):
            self.assertIn(f"Read({home}/{private})", deny)
        self.assertIn("Read(**/.claude/settings.local.json)", deny)

    def test_cap_stop_is_shown_in_status_and_at_session_start(self):
        self.entry(3, cost=7.0)
        with mock.patch.object(C, "claude_bin", side_effect=AssertionError("must not run")), \
                redirect_stdout(io.StringIO()):
            C.scan(self.tw, "full", self.md)
        with mock.patch.object(C, "scheduled", return_value="on"), redirect_stdout(io.StringIO()) as out:
            C.cmd_status(self.tw)
        self.assertIn("monthly cap stopped scheduled scans", out.getvalue())
        pointer = self.dir / "where.txt"
        pointer.write_text(str(self.tw))
        with mock.patch.object(C, "POINTER", pointer), redirect_stdout(io.StringIO()) as out:
            C.cmd_waiting()
        self.assertIn("monthly cap stopped", out.getvalue())
        C.record(self.tw, {"kind": "full", "outcome": "ok", "cost_usd": 1.0,
                           "ts": "9999-01-01T00:00:00+00:00"})   # later the same day, after raising the cap
        self.assertEqual(C.cap_stop(self.tw), "")

    def test_schedule_sets_runs_and_needs_crontab_before_copying(self):
        (self.tw / "TECHWATCH.md").write_text(self.md.replace("- Runs: scheduled", "- Runs: when I ask"))
        with mock.patch.object(C.os, "name", "posix"), mock.patch.object(C.shutil, "which", return_value=None), \
                mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(C.cmd_schedule(self.tw, "on"), 1)
        self.assertFalse((self.tw / ".engine").exists())               # nothing copied without a crontab
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(C.os, "name", "posix"), \
                mock.patch.object(C.shutil, "which", return_value="/usr/bin/crontab"), \
                mock.patch.object(C.subprocess, "run", return_value=ok), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(C.cmd_schedule(self.tw, "on"), 0)
        md = (self.tw / "TECHWATCH.md").read_text()
        self.assertEqual(C.setting(md, "Runs"), "scheduled")
        self.assertIn("when I ask · scheduled", md)                     # the comment below it is untouched
        self.assertIn("scans may stop before the month ends", out.getvalue())   # $10 cap, twice a week at $4
        with mock.patch.object(C.os, "name", "posix"), \
                mock.patch.object(C.shutil, "which", return_value="/usr/bin/crontab"), \
                mock.patch.object(C.subprocess, "run", return_value=ok), redirect_stdout(io.StringIO()):
            C.cmd_schedule(self.tw, "off")
        self.assertEqual(C.setting((self.tw / "TECHWATCH.md").read_text(), "Runs"), "when I ask")


if __name__ == "__main__":
    unittest.main()
