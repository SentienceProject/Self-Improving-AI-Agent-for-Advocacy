"""usage.py with a canned usage reply and a fake login: no network.

    python3 -m unittest discover backlog/tests
"""
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "backlog" / "scripts" / "usage.py"
spec = importlib.util.spec_from_file_location("usage", SCRIPT)
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(week=40, session=10, hours=10, extra=False):
    reset = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return Reply(json.dumps({"five_hour": {"utilization": session, "resets_at": reset},
                             "seven_day": {"utilization": week, "resets_at": reset},
                             "extra_usage": {"is_enabled": extra}}).encode())


class TestUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret"}}))
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.tmp), "XDG_CACHE_HOME": str(self.tmp),
                                                "LOCALAPPDATA": str(self.tmp)})
        self.env.start()
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        self.refresh = mock.patch.object(U, "refresh_login", return_value=False)   # never starts the real claude
        self.refresh.start()

    def tearDown(self):
        self.env.stop()
        self.refresh.stop()

    def clear_cache(self):
        for f in (self.tmp / "claude-backlog").glob("usage-*.json"):
            f.unlink()

    def run_cli(self, *args, rep=None, err=None):
        out = io.StringIO()
        with mock.patch.object(U.urllib.request, "urlopen", side_effect=err or (lambda req, timeout: rep or reply())) as m, \
                redirect_stdout(out):
            code = U.main(list(args))
        return code, out.getvalue(), m

    def test_reads_and_never_prints_the_token(self):
        code, out, m = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("Weekly usage: 40%", out)
        self.assertNotIn("tok-secret", out)
        req = m.call_args[0][0]
        self.assertEqual(req.full_url, U.URL)
        self.assertEqual(req.get_header("Authorization"), "Bearer tok-secret")

    def test_cache_saves_calls(self):
        self.run_cli()
        code, out, m = self.run_cli("--json")
        self.assertEqual(m.call_count, 0)
        self.assertEqual(json.loads(out)["seven_day"], 40)

    def test_check(self):
        code, out, _ = self.run_cli("--check", rep=reply(week=40, hours=10))
        self.assertEqual((code, out.splitlines()[-1]), (0, "GO"))
        self.clear_cache()
        code, out, _ = self.run_cli("--check", "--cap", "35", rep=reply(week=40, hours=10))
        self.assertEqual(code, 1)
        self.assertIn("cap is 35%", out)
        self.clear_cache()
        code, out, _ = self.run_cli("--check", rep=reply(week=40, hours=50))
        self.assertIn("50 hours away", out)

    def test_cant_read(self):
        (self.tmp / ".credentials.json").unlink()
        with mock.patch.object(U.sys, "platform", "linux"):
            code, out, _ = self.run_cli("--check")
        self.assertEqual(code, 2)
        self.assertIn("CAN'T READ: no Claude Code login", out)
        (self.tmp / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "t"}}))
        e = urllib.error.HTTPError(U.URL, 401, "Unauthorized", {}, None)
        code, out, _ = self.run_cli(err=e)
        self.assertIn("expired", out)

    def test_quiet_hours_and_session(self):
        local = lambda h, m=0: datetime(2026, 11, 6, h, m).astimezone()
        self.assertTrue(U.in_quiet("00:00-06:00", local(3)))
        self.assertFalse(U.in_quiet("00:00-06:00", local(6)))
        self.assertTrue(U.in_quiet("23:30-6", local(23, 45)))
        self.assertFalse(U.in_quiet("23:30-6", local(23)))
        self.assertTrue(U.in_quiet("", local(14)))
        u = {"seven_day": 40, "five_hour": 95, "week_resets_at": local(20).isoformat()}
        self.assertIn("quiet hours", U.check(u, quiet="00:00-06:00", now=local(14)))
        self.assertIn("5-hour session", U.check(u, quiet="00:00-06:00", now=local(3)))


    def test_quiet_hours_in_many_spellings(self):
        for text, want in [("00:00-06:00", "00:00-06:00"), ("0000-0600", "00:00-06:00"), ("12am-6am", "00:00-06:00"),
                           ("10pm-6am", "22:00-06:00"), ("1am to 5am", "01:00-05:00"), ("22h-6h", "22:00-06:00"),
                           ("10.30 p.m. \u2013 6 a.m.", "22:30-06:00"), ("23:30 - 6", "23:30-06:00"), ("", "")]:
            self.assertEqual(U.normal_quiet(text), want, text)
        for bad in ("after dinner", "13pm-6am", "25:00-06:00", "06:00-06:00", "night"):
            with self.assertRaises(ValueError, msg=bad):
                U.parse_quiet(bad)

    def test_unreadable_quiet_hours_never_mean_any_hour(self):
        code, out, _ = self.run_cli("--check", "--quiet", "midnight till dawn", rep=reply(hours=5))
        self.assertEqual(code, 2)
        self.assertIn("can't be read", out)
        self.assertEqual(self.run_cli("--normalize-quiet", "10pm-6am")[1].strip(), "22:00-06:00")

    def test_unknown_reset_means_wait(self):
        u = {"seven_day": 10, "five_hour": 0, "week_resets_at": None}
        self.assertIn("unknown", U.check(u))
        now = datetime(2026, 11, 3, 20).astimezone()           # a Tuesday evening
        self.assertIsNone(U.check(u, now=now, reset_setting="Wednesday 09:00"))
        self.assertIn("hours away", U.check(u, now=now, reset_setting="Saturday 09:00"))
        self.assertIn("unknown", U.check(u, now=now, reset_setting="soon"))

    def test_empty_five_hour_window_is_zero(self):
        reset = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        for five in (None, {"utilization": None, "resets_at": None}, {}):
            body = json.dumps({"five_hour": five, "seven_day": {"utilization": 30, "resets_at": reset}}).encode()
            self.clear_cache()
            code, out, _ = self.run_cli("--check", rep=Reply(body))
            self.assertEqual((code, out.splitlines()[-1]), (0, "GO"), five)
        self.clear_cache()
        code, out, _ = self.run_cli("--check", rep=Reply(b'{"five_hour": {"utilization": 5}, "seven_day": null}'))
        self.assertEqual(code, 2)

    def test_each_login_has_its_own_reading(self):
        self.run_cli(rep=reply(week=20))
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "another-login"
        code, out, m = self.run_cli("--check", rep=reply(week=97))
        self.assertEqual(m.call_count, 1)
        self.assertIn("97%", out)
        self.assertEqual(code, 1)

    def test_odd_times_and_a_broken_cache_dont_crash(self):
        self.assertEqual(U.parse_time("2026-12-01T09:00:00").tzinfo, timezone.utc)      # no zone: UTC
        self.assertIsNotNone(U.parse_time("2026-12-01T09:00:00.01765Z"))               # 5-digit fraction
        self.assertIsNone(U.parse_time("next Tuesday"))
        U.cache_file("tok-secret").write_text('{"at": "x", "value": [1]}')
        code, out, m = self.run_cli("--check")
        self.assertEqual((code, m.call_count), (0, 1))


    def test_use_it_all_and_paid_extra_usage(self):
        now = datetime(2026, 11, 6, 14).astimezone()
        u = {"seven_day": 95, "five_hour": 93, "week_resets_at": (now + timedelta(hours=5)).isoformat()}
        self.assertIn("outside your quiet hours", U.check(u, quiet="00:00-06:00", now=now))
        self.assertIsNone(U.check(u, quiet="00:00-06:00", now=now, use_all=True))   # any hour, both caps 98%
        self.assertIn("98%", U.check({**u, "seven_day": 98}, now=now, use_all=True))
        paid = {**u, "extra_usage": True}                                            # the normal caps stay
        self.assertIn("cap is 90%", U.check(paid, quiet="00:00-06:00", now=now, use_all=True))
        code, out, _ = self.run_cli("--check", "--use-it-all", rep=reply(week=50, hours=5, extra=True))
        self.assertIn("NOTE: paid extra usage is on", out)
        self.assertEqual(code, 0)

    def test_check_json_is_one_object(self):
        code, out, _ = self.run_cli("--check", "--json", "--cap", "30", rep=reply(week=40, hours=5))
        d = json.loads(out)
        self.assertEqual((code, d["decision"]), (1, "WAIT"))
        self.assertIn("cap is 30%", d["why"])
        self.assertEqual(d["usage"]["seven_day"], 40)
        (self.tmp / ".credentials.json").unlink()
        with mock.patch.object(U.sys, "platform", "linux"):
            code, out, _ = self.run_cli("--check", "--json")
        self.assertEqual((code, json.loads(out)["decision"]), (2, "CANT_READ"))

    def test_cache_is_private_and_future_dates_are_ignored(self):
        f = U.cache_file("tok-secret")
        self.assertTrue(str(f).startswith(str(self.tmp / "claude-backlog")))
        f.write_text(json.dumps({"at": time.time() + 3600, "value": {"seven_day": 1, "five_hour": 0,
                                                                     "week_resets_at": None}}))
        code, out, m = self.run_cli("--json", rep=reply(week=97))
        self.assertEqual((m.call_count, json.loads(out)["seven_day"]), (1, 97))


    def test_unclear_quiet_hours_are_refused(self):
        for bad in ("10-6", "11-7"):
            with self.assertRaises(ValueError, msg=bad):
                U.parse_quiet(bad)
        for ok, want in (("22-6", "22:00-06:00"), ("1-5", "01:00-05:00"), ("10:00-06:00", "10:00-06:00")):
            self.assertEqual(U.normal_quiet(ok), want)

    def test_reset_from_the_setting_across_a_clock_change(self):
        with mock.patch.dict(os.environ, {"TZ": "Europe/London"}):
            time.tzset()
            try:
                now = datetime(2026, 10, 23, 12).astimezone()          # clocks go back on Sunday 25 October
                r = U.next_reset("Tuesday 09:00", now)
                self.assertEqual((r.astimezone().weekday(), r.astimezone().hour), (1, 9))
                self.assertEqual(r.utcoffset(), timedelta(0))
            finally:
                os.environ.pop("TZ", None)
        time.tzset()

    def test_a_reset_time_that_has_passed_means_wait(self):
        now = datetime(2026, 11, 6, 3).astimezone()
        u = {"seven_day": 5, "five_hour": 0, "week_resets_at": (now - timedelta(hours=2)).isoformat()}
        self.assertIn("unknown", U.check(u, now=now))

    def test_settings_and_this_week_from_backlog_md(self):
        now = datetime.now(timezone.utc)
        reset = now + timedelta(hours=5)
        md = self.tmp / "BACKLOG.md"
        def write(week):
            md.write_text(f"# Backlog\n\nWeekly reset: (the day and time)\nQuiet hours: 00:00-00:01\nUsage cap: 30%\n"
                          f"This week: {week}\n\n## B1 · Something\nadded 2026-11-20 by me · size small\n")
        write(f"skip (reset {reset.astimezone().date()})")
        code, out, _ = self.run_cli("--check", "--backlog", str(md), rep=reply(week=10, hours=5))
        self.assertIn("skip the backlog this week", out)
        write(f"skip (reset {(reset - timedelta(days=7)).astimezone().date()})")   # last week's: ignored
        self.clear_cache()
        code, out, _ = self.run_cli("--check", "--backlog", str(md), rep=reply(week=10, hours=5))
        self.assertIn("quiet hours", out)
        write(f"use it all (reset {reset.astimezone().date()})")                   # any hour, caps 98%
        self.clear_cache()
        code, out, _ = self.run_cli("--check", "--backlog", str(md), rep=reply(week=50, hours=5))
        self.assertEqual((code, out.splitlines()[-1]), (0, "GO"))
        write("(none)")                                                            # the usage cap comes from the file
        self.clear_cache()
        code, out, _ = self.run_cli("--check", "--backlog", str(md), "--quiet", "", rep=reply(week=40, hours=5))
        self.assertIn("cap is 30%", out)

    def test_cron_lines(self):
        self.assertEqual(U.cron_lines("Tuesday 09:00", "00:00-06:00"),
                         {"whole day": ["0 9-23 * * 1", "0 0-8 * * 2"], "quiet hours": ["0 0-5 * * 2"]})
        self.assertEqual(U.cron_lines("Tuesday 03:00", "00:00-06:00")["quiet hours"], ["0 3-5 * * 1", "0 0-2 * * 2"])
        self.assertEqual(U.cron_lines("Sunday 01:00", "22:00-06:00")["quiet hours"], ["0 1-5,22-23 * * 6", "0 0 * * 0"])
        code, out, _ = self.run_cli("--cron", "--reset", "Tuesday 09:00", "--quiet", "10pm-6am")
        self.assertIn("0 22-23 * * 1", out)

    def test_an_expired_login_is_renewed_first(self):
        creds = self.tmp / ".credentials.json"
        creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "old", "expiresAt": (time.time() - 60) * 1000}}))
        def renew():
            creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "new", "expiresAt": (time.time() + 9000) * 1000}}))
            return True
        U.refresh_login.side_effect = renew
        code, out, m = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(m.call_args[0][0].get_header("Authorization"), "Bearer new")
        code, out, m = self.run_cli("--no-refresh")                  # cached: no new reading needed
        self.assertEqual(m.call_count, 0)

    def test_a_refused_login_is_renewed_once(self):
        calls = []
        def answer(req, timeout):
            calls.append(req.get_header("Authorization"))
            if len(calls) == 1:
                raise urllib.error.HTTPError(U.URL, 401, "Unauthorized", {}, None)
            return reply()
        U.refresh_login.return_value = True
        code, out, _ = self.run_cli(err=answer)
        self.assertEqual((code, len(calls)), (0, 2))


if __name__ == "__main__":
    unittest.main()
