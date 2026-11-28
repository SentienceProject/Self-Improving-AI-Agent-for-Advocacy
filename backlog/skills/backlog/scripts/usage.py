#!/usr/bin/env python3
"""Read your Claude plan usage (what Settings > Usage and Claude Code's /usage show), and say whether a backlog run
may start now.

    python3 usage.py                       usage now: weekly %, 5-hour session %, reset times
    python3 usage.py --json                the same as JSON
    python3 usage.py --check [options]     "GO" (exit 0) or "WAIT: why" (exit 1); "CAN'T READ: why" (exit 2)
                                           (with --json: one JSON object with "decision", "why" and "usage")
        --backlog BACKLOG.md     take the settings below from the top of BACKLOG.md, including its "This week"
                                 line ("skip" waits; "use it all" acts like --use-it-all); options given win
        --cap 90                 weekly usage cap for the backlog, in % (no run starts at or past it)
        --session-cap 90         the same for the 5-hour session
        --window 24              start only if the weekly reset is less than this many hours away
        --quiet 00:00-06:00      start only in these hours (local time, may pass midnight); leave out for any hour
        --reset "Tuesday 09:00"  the weekly reset from the settings, used if the usage reply doesn't give one
        --use-it-all             the person chose "use it all": any hour, caps raised to 98% (weekly and 5-hour);
                                 if paid extra usage is on, the normal caps stay, so it never runs into paid usage
        --no-refresh             don't refresh an expired login (see below)
    python3 usage.py --normalize-quiet "10pm-6am"    prints 22:00-06:00 (for writing the setting)
    python3 usage.py --cron [--backlog BACKLOG.md | --reset ... --quiet ...]
                                           the cron expressions for scheduling the checks (local time)

Quiet hours can be written 00:00-06:00, 0000-0600, 10pm-6am, 1am to 5am or 22h-6h. Text it can't read is an
error (CAN'T READ), never "any hour"; so is an unclear one like "10-6" (10am or 10pm?). Without a known weekly reset
time, or with one that has passed, nothing starts.

In a scheduler, run it before Claude so most hours cost no usage:
    python3 usage.py --check --backlog BACKLOG.md > /dev/null && claude -p "..."     (Windows: > NUL)

It needs a Claude Code login on this computer (a Pro, Max or Team plan, signed in with `claude`): it reads the
login's token from ~/.claude/.credentials.json (macOS: the login Keychain; or CLAUDE_CODE_OAUTH_TOKEN) and asks
api.anthropic.com/api/oauth/usage, the same place /usage reads. That address isn't a documented API, so it may change;
if it does, this says it can't read usage and nothing runs. The token is only sent to api.anthropic.com, never
printed or saved. Readings are kept two minutes (in your own cache folder, one file per login) because the address
refuses frequent calls.

The saved login expires a few hours after Claude Code last ran, and only Claude Code renews it. So when it has
expired (say at night), this asks Claude Code to renew it: first `claude auth status`, and if that doesn't, a
one-line `claude -p` on Haiku ("Please reply with OK"), which uses a very small amount of usage. --no-refresh skips
this. Plain Python 3.9 or newer, no packages.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
MAX_AGE = 120
ALL = 98            # "use it all": a sliver below the plan's limit, so a run isn't cut off mid-step
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
REFRESH_PROMPT = "Please reply with just OK (this only renews the login for a usage check). Thank you!"


class CantRead(Exception):
    pass


class Expired(CantRead):
    pass


# ---------------------------------------------------------------- the login

def login():
    """{"token", "expires" (Unix time or None), "key" (stable per login, for the cache)} from Claude Code's login."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        tok = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
        return {"token": tok, "expires": None, "key": tok}
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
        d = json.loads(raw)["claudeAiOauth"]
        tok = d["accessToken"]
    except (ValueError, KeyError, TypeError):
        raise CantRead("the Claude Code login has no plan token (an API-key login has no plan usage to read)")
    exp = d.get("expiresAt")
    return {"token": tok, "expires": float(exp) / 1000 if isinstance(exp, (int, float)) else None,
            "key": f"{f}|{d.get('accountUuid') or tok}"}


def refresh_login():
    """Ask Claude Code to renew an expired login. True if it may have."""
    claude = shutil.which("claude")
    if not claude:
        return False
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    for cmd in ([claude, "auth", "status"],
                [claude, "-p", "--model", "haiku", "--max-turns", "1", "--permission-mode", "dontAsk", REFRESH_PROMPT]):
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, env=env, cwd=cache_dir())
        except (OSError, subprocess.TimeoutExpired):
            return False
        try:
            lg = login()
        except CantRead:
            return False
        if not lg["expires"] or lg["expires"] > time.time() + 60:
            return True
    return False


# ---------------------------------------------------------------- reading usage

def cache_dir():
    """This user's own cache folder (not the shared temp folder, which other users could write to)."""
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    d = Path(base or Path.home() / ".cache") / "claude-backlog"
    try:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def cache_file(key):
    """One file per login, so two logins on one computer never share a reading."""
    return cache_dir() / f"usage-{hashlib.sha256(key.encode()).hexdigest()[:12]}.json"


def percent(window):
    """A window's utilization in %, 0 when the window is empty or missing (no use in it yet)."""
    v = window.get("utilization") if isinstance(window, dict) else None
    return float(v) if isinstance(v, (int, float)) else 0.0


def shape(d):
    """Our reading from the usage reply. The weekly window must be there; a missing 5-hour window counts as 0%."""
    if not isinstance(d, dict) or not isinstance(d.get("seven_day"), dict) or \
            not isinstance(d["seven_day"].get("utilization"), (int, float)):
        raise CantRead("the usage reply has no weekly figure (the address may have changed)")
    five = d.get("five_hour") if isinstance(d.get("five_hour"), dict) else {}
    extra = d.get("extra_usage") if isinstance(d.get("extra_usage"), dict) else {}
    return {"seven_day": float(d["seven_day"]["utilization"]), "five_hour": percent(five),
            "week_resets_at": d["seven_day"].get("resets_at"), "session_resets_at": five.get("resets_at"),
            "extra_usage": bool(extra.get("is_enabled"))}


def fetch(tok):
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise Expired("the login has expired and couldn't be renewed: open Claude Code once")
        raise CantRead({429: "the usage address asked to wait (too many readings); try again in a few minutes"}
                       .get(e.code, f"the usage address answered HTTP {e.code}"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise CantRead(f"couldn't reach api.anthropic.com ({type(e).__name__})")
    return shape(d)


def usage(refresh=True):
    lg = login()
    f = cache_file(lg["key"])
    try:
        c = json.loads(f.read_text(encoding="utf-8"))
        age = time.time() - float(c["at"])
        if 0 <= age < MAX_AGE and isinstance(c["value"]["seven_day"], (int, float)):   # a future date: read again
            return c["value"]
    except (OSError, ValueError, KeyError, TypeError):
        pass   # none yet, old or broken: read again
    if refresh and lg["expires"] and lg["expires"] < time.time() + 60 and refresh_login():
        lg = login()
    try:
        v = fetch(lg["token"])
    except Expired:
        if not refresh or not refresh_login():
            raise
        v = fetch(login()["token"])
    try:
        cache_file(login()["key"]).write_text(json.dumps({"at": time.time(), "value": v}), encoding="utf-8")
    except OSError:
        pass
    return v


# ---------------------------------------------------------------- times and settings

def parse_time(s):
    """An aware datetime from an ISO time (no zone: UTC), or None if missing or unreadable."""
    if not s:
        return None
    s = re.sub(r"(\.\d{1,6})\d*", lambda m: m.group(1).ljust(7, "0"), str(s).strip().replace("Z", "+00:00"))
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def parse_reset(text):
    """(weekday 0-6, hour, minute) from a setting like "Tuesday 09:00", or None."""
    m = re.fullmatch(r"\s*([a-z]+)\s+(\d{1,2})[:.](\d\d)\s*", (text or "").lower())
    if not m or m.group(1) not in DAYS or int(m.group(2)) > 23 or int(m.group(3)) > 59:
        return None
    return DAYS.index(m.group(1)), int(m.group(2)), int(m.group(3))


def next_reset(text, now):
    """The next time of a weekly reset written like "Tuesday 09:00" (local time), or None. Each date gets its own
    UTC offset, so a week in which the clocks change still comes out right."""
    r = parse_reset(text)
    if not r:
        return None
    wd, h, mi = r
    t = now.astimezone()
    for add in range((wd - t.weekday()) % 7, 15, 7):
        d = t.date() + timedelta(days=add)
        when = datetime(d.year, d.month, d.day, h, mi).astimezone()   # local rules for that date
        if when > t:
            return when
    return None


_TIME = r"(\d{1,2})(?:([:.h]?)(\d\d))?\s*(am|pm|a\.m\.|p\.m\.|h)?"
QUIET_RE = re.compile(rf"\s*{_TIME}\s*(?:-|–|—|to|until|till)\s*{_TIME}\s*")


def parse_quiet(text):
    """Quiet hours as (start, end) minutes after midnight; None if empty. ValueError if the text can't be read, or
    is unclear: bare hours that pass midnight, like "10-6" (10am or 10pm?)."""
    if not (text or "").strip():
        return None
    m = QUIET_RE.fullmatch(text.lower())
    if not m:
        raise ValueError(text)
    out = []
    for h, _sep, mi, ap in (m.groups()[:4], m.groups()[4:]):
        h, mi, ap = int(h), int(mi or 0), (ap or "").replace(".", "")
        if ap in ("am", "pm"):
            if not 1 <= h <= 12:
                raise ValueError(text)
            h = h % 12 + (12 if ap == "pm" else 0)
        if h == 24 and mi == 0:
            h = 0
        if h > 23 or mi > 59:
            raise ValueError(text)
        out.append(h * 60 + mi)
    if out[0] == out[1]:
        raise ValueError(text)
    no_ampm = not any(m.group(i) for i in (4, 8))
    no_minutes = not any(m.group(i) for i in (3, 7))
    if no_ampm and no_minutes and out[0] > out[1] and out[0] < 13 * 60 and not m.group(1).startswith("0"):
        raise ValueError(text)   # "10-6": 10am-6am is 20 hours, 10pm-6am is 8; ask instead of guessing
    return tuple(out)


def normal_quiet(text):
    q = parse_quiet(text)
    return "" if not q else "-".join(f"{x // 60:02d}:{x % 60:02d}" for x in q)


def in_quiet(quiet, now):
    """Whether `now` (aware) is within the quiet hours; True if there are none. ValueError if unreadable."""
    q = parse_quiet(quiet)
    if not q:
        return True
    t = now.astimezone()
    mins, (a, b) = t.hour * 60 + t.minute, q
    return a <= mins < b if a < b else (mins >= a or mins < b)


def read_settings(path):
    """The settings lines at the top of BACKLOG.md ("Key: value"), keys in lower case; "(...)" placeholders are
    empty."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") or line.strip() == "# Done":
            break
        m = re.match(r"([A-Za-z][A-Za-z0-9 -]*):\s*(.*)$", line.strip())
        if m:
            v = m.group(2).strip()
            out[m.group(1).strip().lower()] = "" if v.startswith("(") else v
    return out


def this_week(text, reset):
    """"skip", "all" or None, from a "This week" setting, if it's for this reset (its date, local time)."""
    m = re.search(r"reset\s+(\d{4}-\d\d-\d\d)", text or "")
    if not m or not reset or m.group(1) != reset.astimezone().date().isoformat():
        return None   # none, or last week's
    t = text.lower()
    return "skip" if re.match(r"\s*skip", t) else "all" if re.match(r"\s*use it all", t) else None


# ---------------------------------------------------------------- the decision

def check(u, cap=90, session_cap=90, window=24, quiet="", now=None, reset_setting="", use_all=False, week=""):
    """None if a backlog run may start now, else why not. ValueError if the quiet hours can't be read."""
    now = now or datetime.now(timezone.utc)
    quiet_ok = in_quiet(quiet, now)   # read first, so unreadable quiet hours always stop the run
    reset = parse_time(u.get("week_resets_at"))
    if reset and reset <= now:
        reset = None   # a reset time that has passed tells nothing about the next one
    reset = reset or next_reset(reset_setting, now)
    if not reset:
        return "the weekly reset time is unknown (give it with --reset, e.g. \"Tuesday 09:00\")"
    choice = this_week(week, reset)
    if choice == "skip":
        return "you chose to skip the backlog this week"
    if use_all or choice == "all":
        quiet_ok = True
        if not u.get("extra_usage"):  # with paid extra usage on, the plan's limit wouldn't stop a run: keep the caps
            cap, session_cap = ALL, ALL
    hours = (reset - now).total_seconds() / 3600
    if hours > window:
        return f"the weekly reset is {hours:.0f} hours away (starts within {window:g})"
    if not quiet_ok:
        return f"it's outside your quiet hours ({normal_quiet(quiet)})"
    if u["seven_day"] >= cap:
        return f"weekly usage is at {u['seven_day']:g}% (the backlog's cap is {cap:g}%)"
    if (u.get("five_hour") or 0) >= session_cap:
        return f"the 5-hour session is at {u['five_hour']:g}% (the backlog uses up to {session_cap:g}%)"
    return None


def cron_lines(reset_setting, quiet, window=24):
    """{"whole day": [...], "quiet hours": [...]}: cron expressions (local time) for hourly checks in the last
    `window` hours before the reset, and for only the quiet hours among them."""
    r = parse_reset(reset_setting)
    if not r:
        raise ValueError("reset")
    q = parse_quiet(quiet)
    wd, h, mi = r
    reset = datetime(2026, 1, 5 + wd, h, mi)          # any week will do: 2026-01-05 is a Monday
    first = reset - timedelta(hours=window)
    slot = first.replace(minute=0) + (timedelta(hours=1) if first.minute else timedelta(0))
    groups = {"whole day": {}, "quiet hours": {}}
    while slot < reset:
        m, cron_day = slot.hour * 60, (slot.weekday() + 1) % 7   # cron: Sunday is 0
        groups["whole day"].setdefault(cron_day, []).append(slot.hour)
        if not q or (q[0] <= m < q[1] if q[0] < q[1] else (m >= q[0] or m < q[1])):
            groups["quiet hours"].setdefault(cron_day, []).append(slot.hour)
        slot += timedelta(hours=1)

    def ranges(hours):
        out, start = [], hours[0]
        for a, b in zip(hours, hours[1:] + [None]):
            if b != a + 1:
                out.append(str(start) if start == a else f"{start}-{a}")
                start = b
        return ",".join(out)
    return {k: [f"0 {ranges(sorted(v))} * * {d}" for d, v in sorted(g.items(), key=lambda x: (x[0] - (wd + 1) % 7 + 1) % 7)]
            for k, g in groups.items()}


def describe(u):
    def when(s):
        t = parse_time(s)
        return f"{t.astimezone():%a %d %b %H:%M}" if t else "unknown"
    return (f"Weekly usage: {u['seven_day']:g}% (resets {when(u.get('week_resets_at'))}). "
            f"5-hour session: {u.get('five_hour') or 0:g}% (resets {when(u.get('session_resets_at'))}).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backlog")
    ap.add_argument("--cap", type=float)
    ap.add_argument("--session-cap", type=float, default=90)
    ap.add_argument("--window", type=float, default=24)
    ap.add_argument("--quiet")
    ap.add_argument("--reset")
    ap.add_argument("--use-it-all", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--normalize-quiet", metavar="TEXT")
    ap.add_argument("--cron", action="store_true")
    a = ap.parse_args(argv)

    if a.normalize_quiet is not None:
        try:
            print(normal_quiet(a.normalize_quiet) or "(none: any hour)")
            return 0
        except ValueError:
            print(f"CAN'T READ: the quiet hours \"{a.normalize_quiet}\" can't be read or are unclear; write them like "
                  "00:00-06:00 or 10pm-6am")
            return 2
    s = {}
    if a.backlog:
        try:
            s = read_settings(a.backlog)
        except OSError as e:
            print(f"CAN'T READ: {a.backlog} ({type(e).__name__})")
            return 2
    quiet = a.quiet if a.quiet is not None else s.get("quiet hours", "")
    reset_setting = a.reset if a.reset is not None else s.get("weekly reset", "")
    try:
        cap = a.cap if a.cap is not None else float(re.findall(r"[\d.]+", s.get("usage cap", "") or "90")[0])
    except (IndexError, ValueError):
        cap = 90.0
    bad_quiet = f"the quiet hours \"{quiet}\" can't be read or are unclear; write them like 00:00-06:00 or 10pm-6am"

    if a.cron:
        try:
            lines = cron_lines(reset_setting, quiet, a.window)
        except ValueError as e:
            print(f"CAN'T READ: {bad_quiet}" if str(e) != "reset" else
                  "CAN'T READ: the weekly reset isn't known: give it like \"Tuesday 09:00\"")
            return 2
        print("# Every hour of the last day before the reset (\"use it all\" can start any time):")
        print("\n".join(lines["whole day"]))
        print("# Only your quiet hours in that day (fewer sessions in Claude Desktop):")
        print("\n".join(lines["quiet hours"]) or "# (none: your quiet hours don't fall in that day)")
        return 0

    def say(decision, why=None, u=None, note=None):
        if a.json:
            print(json.dumps({"decision": decision, "why": why, "note": note, "usage": u}))
        else:
            if u:
                print(describe(u))
            if note:
                print(f"NOTE: {note}")
            print({"GO": "GO", "WAIT": f"WAIT: {why}", "CANT_READ": f"CAN'T READ: {why}"}[decision])
    try:
        u = usage(refresh=not a.no_refresh)
    except CantRead as e:
        if a.check:
            say("CANT_READ", str(e))
        else:
            print(json.dumps({"error": str(e)}) if a.json else f"Can't read usage: {e}")
        return 2
    if not a.check:
        print(json.dumps(u) if a.json else describe(u))
        return 0
    week = s.get("this week", "")
    try:
        why = check(u, cap, a.session_cap, a.window, quiet, reset_setting=reset_setting, use_all=a.use_it_all,
                    week=week)
    except ValueError:
        say("CANT_READ", bad_quiet, u)
        return 2
    all_chosen = a.use_it_all or re.match(r"\s*use it all", week.lower() or "")
    note = ("paid extra usage is on, so \"use it all\" keeps the normal caps (the plan's limit wouldn't stop it)"
            if all_chosen and u.get("extra_usage") else None)
    say("GO" if not why else "WAIT", why, u, note)
    return 0 if not why else 1


if __name__ == "__main__":
    sys.exit(main())
