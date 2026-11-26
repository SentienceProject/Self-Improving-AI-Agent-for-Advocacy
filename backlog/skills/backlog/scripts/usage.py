#!/usr/bin/env python3
"""Read your Claude plan usage (what Settings > Usage and Claude Code's /usage show), and say whether a backlog run
may start now.

    python3 usage.py                       usage now: weekly %, 5-hour session %, reset times
    python3 usage.py --json                the same as JSON
    python3 usage.py --check [options]     "GO" (exit 0) or "WAIT: why" (exit 1); "CAN'T READ: why" (exit 2)
        --cap 90                 weekly usage cap for the backlog, in % (no run starts at or past it)
        --session-cap 90         the same for the 5-hour session
        --window 24              start only if the weekly reset is less than this many hours away
        --quiet 00:00-06:00      start only in these hours (local time, may pass midnight); leave out for any hour

It needs a Claude Code login on this computer (a Pro, Max or Team plan, signed in with `claude`): it reads the
login's token from ~/.claude/.credentials.json (macOS: the login Keychain; or CLAUDE_CODE_OAUTH_TOKEN) and asks
api.anthropic.com/api/oauth/usage, the same place /usage reads. That address isn't a documented API, so it may change;
if it does, this says it can't read usage and nothing runs. The token is only sent to api.anthropic.com, never
printed or saved. Readings are kept two minutes (in your temp folder) because the address refuses frequent calls.
Plain Python 3.9+, no packages, no AI model involved.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
CACHE = Path(tempfile.gettempdir()) / "claude-backlog-usage.json"
MAX_AGE = 120


class CantRead(Exception):
    pass


def token():
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    raw = None
    f = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json"
    if f.exists():
        raw = f.read_text(encoding="utf-8")
    elif sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True)
        raw = r.stdout if r.returncode == 0 else None
    if not raw:
        raise CantRead("no Claude Code login on this computer (sign in once with `claude`; on claude.ai this can't "
                       "work, so use the weekly check-in instead)")
    try:
        return json.loads(raw)["claudeAiOauth"]["accessToken"]
    except (ValueError, KeyError, TypeError):
        raise CantRead("the Claude Code login has no plan token (an API-key login has no plan usage to read)")


def fetch():
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token()}",
                                               "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise CantRead({401: "the login has expired: open Claude Code once so it refreshes",
                        429: "the usage address asked to wait (too many readings); try again in a few minutes"}
                       .get(e.code, f"the usage address answered HTTP {e.code}"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise CantRead(f"couldn't reach api.anthropic.com ({type(e).__name__})")
    try:
        return {"seven_day": float(d["seven_day"]["utilization"]), "five_hour": float(d["five_hour"]["utilization"]),
                "week_resets_at": d["seven_day"].get("resets_at"), "session_resets_at": d["five_hour"].get("resets_at")}
    except (KeyError, TypeError, ValueError):
        raise CantRead("the usage address answered in a form this script doesn't know (it may have changed)")


def usage():
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - c["at"] < MAX_AGE:
            return c["value"]
    except (OSError, ValueError, KeyError):
        pass
    v = fetch()
    try:
        CACHE.write_text(json.dumps({"at": time.time(), "value": v}), encoding="utf-8")
    except OSError:
        pass
    return v


def parse_time(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None


def in_quiet(quiet, now):
    """Whether `now` (aware) is within quiet hours like "00:00-06:00" or "23:30-6"; True if there are none."""
    m = re.match(r"\s*(\d{1,2})(?:[:.](\d\d))?\s*(?:-|–|to)\s*(\d{1,2})(?:[:.](\d\d))?", quiet or "")
    if not m:
        return True
    a = int(m.group(1)) % 24 * 60 + int(m.group(2) or 0)
    b = int(m.group(3)) % 24 * 60 + int(m.group(4) or 0)
    t = now.astimezone()
    mins = t.hour * 60 + t.minute
    return True if a == b else (a <= mins < b if a < b else (mins >= a or mins < b))


def check(u, cap=90, session_cap=90, window=24, quiet="", now=None):
    """None if a backlog run may start now, else why not."""
    now = now or datetime.now(timezone.utc)
    reset = parse_time(u.get("week_resets_at"))
    if reset:
        hours = (reset - now).total_seconds() / 3600
        if hours > window:
            return f"the weekly reset is {hours:.0f} hours away (starts within {window:g})"
    if quiet and not in_quiet(quiet, now):
        return f"it's outside your quiet hours ({quiet})"
    if u["seven_day"] >= cap:
        return f"weekly usage is at {u['seven_day']:g}% (the backlog's cap is {cap:g}%)"
    if u["five_hour"] >= session_cap:
        return f"the 5-hour session is at {u['five_hour']:g}% (the backlog uses up to {session_cap:g}%)"
    return None


def describe(u):
    def when(s):
        t = parse_time(s)
        return f"{t.astimezone():%a %d %b %H:%M}" if t else "unknown"
    return (f"Weekly usage: {u['seven_day']:g}% (resets {when(u.get('week_resets_at'))}). "
            f"5-hour session: {u['five_hour']:g}% (resets {when(u.get('session_resets_at'))}).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--cap", type=float, default=90)
    ap.add_argument("--session-cap", type=float, default=90)
    ap.add_argument("--window", type=float, default=24)
    ap.add_argument("--quiet", default="")
    a = ap.parse_args(argv)
    try:
        u = usage()
    except CantRead as e:
        print(f"CAN'T READ: {e}" if a.check else f"Can't read usage: {e}")
        return 2
    if a.json:
        print(json.dumps(u))
    elif not a.check:
        print(describe(u))
    if a.check:
        why = check(u, a.cap, a.session_cap, a.window, a.quiet)
        print(describe(u))
        print("GO" if not why else f"WAIT: {why}")
        return 0 if not why else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
