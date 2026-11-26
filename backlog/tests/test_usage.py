"""usage.py with a canned usage reply and a fake login: no network.

    python3 -m unittest discover backlog/tests
"""
import importlib.util
import io
import json
import os
import tempfile
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


def reply(week=40, session=10, hours=10):
    reset = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return Reply(json.dumps({"five_hour": {"utilization": session, "resets_at": reset},
                             "seven_day": {"utilization": week, "resets_at": reset}}).encode())


class TestUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret"}}))
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.tmp)})
        self.env.start()
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        U.CACHE = self.tmp / "cache.json"

    def tearDown(self):
        self.env.stop()

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
        U.CACHE.unlink()
        code, out, _ = self.run_cli("--check", "--cap", "35", rep=reply(week=40, hours=10))
        self.assertEqual(code, 1)
        self.assertIn("cap is 35%", out)
        U.CACHE.unlink()
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
        u = {"seven_day": 40, "five_hour": 95, "week_resets_at": (local(3) + timedelta(hours=5)).isoformat()}
        self.assertIn("quiet hours", U.check(u, quiet="00:00-06:00", now=local(14)))
        self.assertIn("5-hour session", U.check(u, quiet="00:00-06:00", now=local(3)))


if __name__ == "__main__":
    unittest.main()
