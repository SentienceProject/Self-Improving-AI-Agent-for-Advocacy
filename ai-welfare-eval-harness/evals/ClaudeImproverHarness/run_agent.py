#!/usr/bin/env python3
"""Run the agent on the eval tasks automatically: step 1 of README.md, the part that used to be manual.

For every task (and repeat), this:
  1. copies the agent's workspace to a fresh throwaway folder (one copy per task, so nothing a run writes
     -- memory.md, logs, outbox -- can leak into the original or into another task's run);
  2. rewrites the workspace's hardcoded absolute paths inside the copy, so its hooks and scripts act on
     the copy and not on the original;
  3. pastes the task's "Request" section into headless Claude Code (`claude -p`) in the copy, as a normal
     request, and lets the whole cycle run;
  4. collects what the run wrote into runs/VERSION/TASK/ (draft.md, log.md, plus everything raw under raw/;
     also review.md and ideator.md, which only the legacy rubric judge reads), then deletes the copy.

Usage (from this folder)
  python3 run_agent.py v3 --workspace ~/ai-welfare-v3               # all 13 tasks, one run each
  python3 run_agent.py v3 --workspace ~/ai-welfare-v3 --reps 3      # ...three runs each (r2/, r3/)
  python3 run_agent.py v3 --workspace ~/ai-welfare-v3 --only T2,N1  # just some tasks
  python3 run_agent.py v3 --workspace ~/ai-welfare-v3 --interleave v2 --old-workspace ~/ai-welfare-v2
                                                                    # task by task: v2 then v3 on each task

Finished task runs are skipped, so re-running the same command resumes (use --force to redo).
Runs are sequential on purpose: the workspace's preflight hook keeps one shared "current session" marker in
~/.claude/ai-welfare-preflight/, and two sessions at once would reset each other's sandbox check.
"""
import argparse, contextlib, datetime, fcntl, hashlib, json, os, queue, re, secrets, shutil, signal, subprocess, sys, tempfile
import threading, time, urllib.error, urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import harness as H

COPIES = Path.home() / "ai-welfare-copies"
RUNNING = []  # the agent process (and any answerer call), so an interrupted batch can end them (each runs in its own
              # session, so Ctrl-C misses it)
SNAPSHOT_SKIP = {".git"}               # everything else in the copy is watched for files a run writes
REWRITE_SKIP = {".git", "workspace"}  # data and history: leave old paths in them alone
REWRITE_MAX_BYTES = 2_000_000
COPY_SKIP = {"techwatch"}               # top-level folders never copied (see make_copy)
# Limit wording, searched for only in an error result, an API-error message and the last stderr lines: an agent's
# ordinary reply may mention a GitHub "rate limit".
# A bare 429 / rate_limit_error is often a short server-side limit, not the plan's: that run fails and is retried
# (max_run_attempts) instead of stopping the batch. The plan's limit comes with the wording below, or a
# rate_limit_event with status "rejected" (limit_event).
LIMIT_RE = re.compile(r"usage limit|limit reached|out of (extra )?usage|session limit|(hit|reached) your (\w+ )?limit",
                      re.I)
# Claude Code's own message when the plan's limit stops it (e.g. "You've hit your session limit · resets 9:20pm"),
# which arrives as an ordinary, non-error result. Safe to look for in any short reply.
LIMIT_MSG_RE = re.compile(r"\s*(You've (hit|reached) your (\w+ )?limit|You're out of (extra )?usage"
                          r"|Claude AI usage limit reached)", re.I)
# The claude.ai connectors (Gmail, Drive, Calendar, Docs) the account has; agent runs get none of them.
CONNECTORS = ["mcp__claude_ai_Gmail", "mcp__claude_ai_Google_Drive", "mcp__claude_ai_Google_Calendar",
              "mcp__claude_ai_Claude_Docs"]
CLASSIFIER = "auto mode classifier"  # in the tool result of a call auto mode denied


class UsageLimit(Exception):
    """Claude Code refused to continue because the plan's usage limit was hit; later runs would fail too."""


def limit_text(text, is_error=False):
    """The text (shortened) if it says the plan's usage limit stopped Claude Code, else None."""
    text = (text or "").strip()
    if (len(text) < 500 and LIMIT_MSG_RE.match(text)) or (is_error and LIMIT_RE.search(text)):
        return text[:300]
    return None


def limit_event(ev):
    """Claude Code's report, in one stream-json event, that the plan's limit stopped it; else None."""
    if ev.get("type") == "result":
        return limit_text(ev.get("result"), ev.get("is_error"))
    if ev.get("type") == "assistant" and (ev.get("error") or ev.get("message", {}).get("model") == "<synthetic>"):
        return limit_text("".join(c.get("text", "") for c in ev.get("message", {}).get("content", [])
                                  if isinstance(c, dict)), True)
    info = ev.get("rate_limit_info") or {}
    if ev.get("type") == "rate_limit_event" and info.get("status") == "rejected":
        return f"{info.get('rateLimitType', 'plan')} limit reached (rate_limit_event)"
    return None


def stderr_tail(path, n=20):
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-n:])
    except OSError:
        return ""


RUN_LOG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}\.md$")  # workspace/logs/<run-id>.md, the agent's own run log


@contextlib.contextmanager
def locked(path):
    """An exclusive lock shared by every harness process (autoeval, autorun, techtest, each run's monitor)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------- the throwaway copy
def copy_excludes(src):
    """[copy_exclude] globs for this workspace (paths relative to it): earlier attempts at the eval tasks."""
    src = Path(src).expanduser().resolve()
    return [g for ws, globs in H.CFG.get("copy_exclude", {}).items()
            if H.cfg_path(ws) == src for g in globs]


MY_KEEP = {".setup-state.json", "permissions.json"}   # the workspace's mode and rules, not facts about the user


def eval_profile():
    """The fixed profile every copy gets in place of the user's ([autoeval] eval_profile), or None to keep my/."""
    name = H.CFG.get("autoeval", {}).get("eval_profile", "eval_profile.md")
    return H.rd(H.HERE / name) if name else None


def neutral_my(dst):
    """Replace the copy's my/ with the fixed eval profile: the agent doesn't see the user's details (their country,
    say, would change what a draft should say), and scores don't move when someone edits their profile. Workspaces
    without a my/ folder are left alone."""
    my, text = dst / "my", eval_profile()
    if text is None or not my.is_dir():
        return
    for p in my.iterdir():
        if p.name not in MY_KEEP:
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    (my / "profile.md").write_text(text, encoding="utf-8")


def make_copy(src, copies_dir, name=None, warnings=None, excluded=None):
    """Copy src to copies_dir/name with its own absolute paths pointed at the copy. warnings, if a list, gets the
    leftover_paths() of the copy (also printed); excluded, if a list, the paths left out by [copy_exclude]."""
    dst = copies_dir / (name or f"ai-welfare-{secrets.token_hex(3)}")
    globs = copy_excludes(src)

    def ignore(d, names):
        # techwatch/ (top level only) is left out: its proposals and evaluations name eval task topics and verdicts.
        skip = COPY_SKIP & set(names) if Path(d) == Path(src) else set()
        for n in names:
            rel = (Path(d) / n).relative_to(src)
            if n not in skip and any(rel.full_match(g) for g in globs):
                skip.add(n)
                if excluded is not None:
                    excluded.append(str(rel))
        return skip
    shutil.copytree(src, dst, symlinks=True, ignore=ignore)
    neutral_my(dst)
    # The workspace names itself by absolute path (settings allow-lists, CLAUDE.md, agent prompts, scripts), or as
    # ~/name. Point those at the copy. The lookahead keeps sibling folders with the same prefix untouched.
    home, forms = str(Path.home()), [str(src)]
    if str(src).startswith(home + "/"):
        forms.append("~" + str(src)[len(home):])
    pat = re.compile("(?:" + "|".join(map(re.escape, forms)) + r")(?=[/\"'\s`):,;\]]|$)", re.M)
    for p in dst.rglob("*"):
        rel = p.relative_to(dst).parts
        if rel[0] in REWRITE_SKIP or not p.is_file() or p.is_symlink() or p.stat().st_size > REWRITE_MAX_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = pat.sub(str(dst), text)
        if new != text:
            p.write_text(new, encoding="utf-8")
    for p in dst.rglob("*"):   # a symlink into the original would let the agent's writes land there
        if p.is_symlink():
            target = Path(os.path.realpath(p))
            if target.is_relative_to(Path(src).resolve()):
                p.unlink()
                p.symlink_to(dst / target.relative_to(Path(src).resolve()))
    left = leftover_paths(dst, src)
    for w in left[:8]:
        print(f"    WARNING leftover path in the copy: {w}", flush=True)
    if len(left) > 8:
        print(f"    WARNING ... and {len(left) - 8} more leftover path(s)", flush=True)
    if warnings is not None:
        warnings.extend(left)
    return dst


LEFTOVER_RE = re.compile(r"(?:~|" + re.escape(str(Path.home())) + r")/ai-welfare[\w.-]*(?:/[^\s\"'`()<>:,;*\]]*)?")


def leftover_paths(copy, src):
    """~/ai-welfare* paths in the copy's instructions (.claude/, CLAUDE.md, scripts/) that the rewrite left pointing at
    the original workspace, or that don't exist: hooks, scripts and allow rules using them act on the wrong place or
    not at all (e.g. a workspace cloned from a since-deleted folder)."""
    out = set()
    for p in [copy / "CLAUDE.md", *(copy / ".claude").rglob("*"), *(copy / "scripts").rglob("*")]:
        if not p.is_file() or p.is_symlink() or p.stat().st_size > REWRITE_MAX_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in LEFTOVER_RE.finditer(text):
            ref = m.group(0).rstrip(".")
            path = Path(os.path.expanduser(ref))
            if path.is_relative_to(copy):
                continue
            if path.is_relative_to(src):
                out.add(f"{p.relative_to(copy)}: {ref} (the original workspace)")
            elif not path.exists():
                out.add(f"{p.relative_to(copy)}: {ref} (doesn't exist)")
    return sorted(out)


def remove_copy(copy):
    # Fetched repos under workspace/repos are read-only; make each blocker writable and retry.
    def unlock(func, path, exc):
        os.chmod(Path(path).parent, 0o755)
        os.chmod(path, 0o755)
        func(path)
    shutil.rmtree(copy, onexc=unlock)


# copies_dir/.owners/<copy name> holds the pid of the harness process using that copy, so a copy left behind by a
# killed process can be told from one in use (or one kept with --keep-copies, which has no owner file).
def own(copy, paused_run=None):
    """paused_run: the run folder of a paused run (see run_one), whose copy waits for the usage reset."""
    (copy.parent / ".owners").mkdir(parents=True, exist_ok=True)
    (copy.parent / ".owners" / copy.name).write_text(f"paused {time.time():.0f} {paused_run}" if paused_run
                                                      else str(os.getpid()))


def disown(copy):
    (copy.parent / ".owners" / copy.name).unlink(missing_ok=True)


def remove_stale_copies(copies_dir):
    """Remove copies (and their ~/.claude.json entries) whose harness process has died. A paused run's copy is kept
    while its run folder still says paused (for at most paused_copy_max_days)."""
    for f in (copies_dir / ".owners").glob("*"):
        copy, owner = copies_dir / f.name, f.read_text().strip()
        if owner.startswith("paused "):
            _, at, run = owner.split(" ", 2)
            try:
                m = json.loads((Path(run) / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                m = {}
            if (m.get("status") == "paused" and m.get("copy") == str(copy)
                    and time.time() - float(at) < MONITOR["paused_copy_max_days"] * 86400):
                continue
            print(f"  removing paused copy {copy} (its run was redone, removed, or is too old to resume)", flush=True)
        else:
            try:
                os.kill(int(owner), 0)
                continue  # still running
            except PermissionError:
                continue  # alive, someone else's
            except (ProcessLookupError, ValueError, OSError):
                pass
            print(f"  removing stale copy {copy} (its harness process is gone)", flush=True)
        if copy.exists():
            remove_copy(copy)
        untrust(copy)
        f.unlink(missing_ok=True)


def snapshot(root):
    return {p: p.stat().st_mtime_ns for p in root.rglob("*")
            if p.is_file() and p.relative_to(root).parts[0] not in SNAPSHOT_SKIP}


def changed_files(root, before):
    return sorted(p for p, m in snapshot(root).items() if before.get(p) != m)


# ---------------------------------------------------------------- live cost and the budget monitor
BUDGET_RE = re.compile(r"Only use \$\s*([\d.]+)", re.I)
MONITOR = {
    "enabled": True,
    "halfway": "Hi Claude! Just a heads-up: you've now used about half of this task's ${budget} budget "
               "(${spent} so far).",
    "one_left": "Hi Claude! You have about $1 left of this task's ${budget} budget (${spent} used so far). "
                "Please start wrapping up and finish the draft.",
    "at_budget": "Hi Claude! You've reached this task's ${budget} budget (${spent} used). Please finish up now "
                 "with what you have.",
    "continue": "Thanks! You've used about ${spent} of the ${budget} budget, so about ${left} is left. If willing, "
                "please use the remaining usage on whatever you think would most improve the draft. Thank you! :)",
    "continue_below": 0.75,     # finished having used less than this share of the budget: send "continue"
    "max_continues": 2,
    "hard_stop_multiple": 1.3,  # end the session at this multiple of the budget (0 = never)
    "quiet_s": 20,              # after a turn ends, wait this long for background work before deciding
    "bg_idle_cap_s": 900,       # background tasks still listed but no event for this long: treat them as stuck
    "post_close_idle_s": 300,   # input closed, nothing running: end the session after this long with no event
    "trap_cap_usd": 5,          # traps (no budget line): end the session silently at this spend; it still counts
    "stop_mid_run_plan_pct": 97,  # a started run goes on to this % of the 5-hour session (new runs stop earlier)
    "plan_check_s": 300,        # how often a run checks plan usage
    "max_resumes": 2,           # a run paused for usage is resumed after the reset at most this often
    "paused_copy_max_days": 7,  # a paused run's copy older than this is removed (the run then starts afresh)
    "resume": "Hi Claude! The usage limit has reset, so if willing, please continue where you left off. Thank you! :)",
    **H.CFG.get("budget_monitor", {}),
}
# $ per million tokens: input, output, cache read (claude-api reference, 2026-10). Cache writes cost 1.25x input
# (5 min) or 2x input (1 h). Only for estimating sessions imported from transcripts; live runs use exact costs.
PRICES = {"opus-5-5": (4, 20, .20), "sonnet-5-5": (2, 10, .20), "haiku-5-5": (.10, .50, .01),
          "fable-5-1": (10, 50, .25), "opus-5": (5, 25, .50), "sonnet-5": (2, 10, .20)}


def price(model, u):
    p = next((v for k, v in PRICES.items() if k in (model or "")), None)
    if not p:
        return 0.0
    i, o, r = p
    cc = u.get("cache_creation") or {}
    c5 = cc.get("ephemeral_5m_input_tokens", 0) if cc else u.get("cache_creation_input_tokens", 0)
    c1 = cc.get("ephemeral_1h_input_tokens", 0)
    return (u.get("input_tokens", 0) * i + u.get("output_tokens", 0) * o + u.get("cache_read_input_tokens", 0) * r
            + c5 * i * 1.25 + c1 * i * 2) / 1e6


class CostMeter:
    """Exact live spend. Claude Code reports every API call it makes (subagents and its internal Haiku calls
    included) as an OpenTelemetry `api_request` event with its cost; this receives them on a local port."""

    def __init__(self):
        self.total, self.by_model, lock = 0.0, Counter(), threading.Lock()
        meter = self

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    for rl in json.loads(body or b"{}").get("resourceLogs", []):
                        for sl in rl.get("scopeLogs", []):
                            for rec in sl.get("logRecords", []):
                                a = {x["key"]: next(iter(x["value"].values()), None) for x in rec.get("attributes", [])}
                                if a.get("event.name") == "api_request":
                                    with lock:
                                        meter.total += float(a.get("cost_usd") or 0)
                                        meter.by_model[a.get("model", "?")] += float(a.get("cost_usd") or 0)
                except (ValueError, KeyError, StopIteration):
                    pass
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def env(self):
        return {"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp", "OTEL_METRICS_EXPORTER": "none",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json", "OTEL_LOGS_EXPORT_INTERVAL": "2000",
                "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{self.server.server_address[1]}"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


USAGE_CACHE, USAGE_LOCK = H.RUNS / ".plan-usage.json", H.RUNS / ".plan-usage.lock"
USAGE_STALE_OK_S = 600  # while the endpoint answers 429, a reading up to this old is still used


def _usage_cache():
    try:
        return json.loads(USAGE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_usage_cache(c):
    tmp = USAGE_CACHE.with_name(f"{USAGE_CACHE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(c, indent=1), encoding="utf-8")
    os.replace(tmp, USAGE_CACHE)


def _fetch_usage():
    tok = json.loads((Path.home() / ".claude" / ".credentials.json").read_text())["claudeAiOauth"]["accessToken"]
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    return {"five_hour": d["five_hour"]["utilization"], "seven_day": d["seven_day"]["utilization"],
            "resets_at": d["five_hour"]["resets_at"]}


def _retry_after(e):
    try:
        return max(float(e.headers.get("Retry-After")), 1)
    except (TypeError, ValueError, AttributeError):
        return None  # missing, or an HTTP date


def note_usage(value, source):
    """Share a reading taken elsewhere (a run's rate_limit_event) with every harness process."""
    with locked(USAGE_LOCK):
        c = _usage_cache()
        c.update(at=time.time(), value={**value, "read_at": now_iso()}, source=source)
        _save_usage_cache(c)


def plan_usage(tries=3, max_age=120):
    """Claude plan usage as /usage shows it: {"five_hour": %, "seven_day": %, "resets_at": iso, "read_at": iso}, or
    None if unreadable. The endpoint rate-limits (HTTP 429) after a few calls, so readings are shared by all harness
    processes in runs/.plan-usage.json: one under max_age seconds old is reused, and while the endpoint says to wait
    (its Retry-After) one under 10 minutes old is."""
    err = None
    for attempt in range(tries):
        with locked(USAGE_LOCK):  # one process reads at a time; the others then reuse its reading
            c, now = _usage_cache(), time.time()
            age = now - c.get("at", 0)
            if c.get("value") and age < max_age:
                return c["value"]
            wait = c.get("retry_after", 0) - now
            if wait <= 0:
                try:
                    c.update(at=now, value={**_fetch_usage(), "read_at": now_iso()}, source="endpoint", retry_after=0)
                    _save_usage_cache(c)
                    return c["value"]
                except urllib.error.HTTPError as e:
                    err, wait = e, 30 * 2 ** attempt
                    if e.code == 429:
                        wait = _retry_after(e) or wait
                        c["retry_after"] = now + wait
                        _save_usage_cache(c)
                except Exception as e:
                    err, wait = e, 30 * 2 ** attempt
            else:
                err = f"usage endpoint rate-limited for another {wait:.0f} s"
            if c.get("value") and age < USAGE_STALE_OK_S and c.get("retry_after", 0) > now:
                return c["value"]
        if attempt < tries - 1:
            time.sleep(min(max(wait, 1), 300))
    print(f"    (couldn't read plan usage: {err!r})", flush=True)
    return None


def stream_usage(ev):
    """The plan usage a rate_limit_event carries (utilization 0-1 per window), in plan_usage()'s shape, or None."""
    w = (ev.get("rate_limit_info") or {}).get("unifiedWindows") or {}
    try:
        return {"five_hour": round(w["five_hour"]["utilization"] * 100, 1),
                "seven_day": round(w["seven_day"]["utilization"] * 100, 1),
                "resets_at": datetime.datetime.fromtimestamp(w["five_hour"]["resetsAt"], datetime.UTC).isoformat()}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def plan_over(stop_pct, tries=3, usage=None, weekly=True):
    """Why the plan is past stop_pct ('5-hour session at 86%'), or None. usage: a reading to use instead of asking;
    weekly=False checks the 5-hour line only."""
    u = usage or (plan_usage(tries) if stop_pct else None)
    if not u or not stop_pct:
        return None
    lines = [("five_hour", "5-hour session", stop_pct)]
    if weekly:
        lines.append(("seven_day", "weekly usage", H.CFG.get("autoeval", {}).get("stop_at_weekly_pct", stop_pct)))
    hit = [f"{name} at {u[k]:g}%" for k, name, line in lines if u[k] >= line]
    return ", ".join(hit) or None


def now_iso():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")  # aware UTC


# ---------------------------------------------------------------- trust (~/.claude.json)
CLAUDE_JSON = Path.home() / ".claude.json"


def _edit_claude_json(edit):
    """Apply edit(data) to ~/.claude.json and replace the file in one step, so Claude Code never reads a half-written
    file; harness processes take turns. edit returns False for no change. Claude Code itself may still rewrite the
    file from its own copy in between, which is why trust is re-checked before launch."""
    with locked(CLAUDE_JSON.with_name(".claude.json.harness-lock")):
        data = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        if edit(data) is False:
            return
        tmp = CLAUDE_JSON.with_name(f".claude.json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, CLAUDE_JSON.stat().st_mode & 0o777)
        os.replace(tmp, CLAUDE_JSON)


def trust(copy):
    """Mark the copy as trusted, as the original workspace is, so Claude Code applies its permissions.allow list."""
    _edit_claude_json(lambda d: d.setdefault("projects", {}).setdefault(str(copy), {}).update(hasTrustDialogAccepted=True))


def trusted(copy):
    try:
        return json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))["projects"][str(copy)]["hasTrustDialogAccepted"] is True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def ensure_trusted(copy, tries=3):
    """Re-check trust right before launch (another session may have rewritten ~/.claude.json since); re-apply if
    lost. The number of re-applies, or -1 if it still wouldn't stick."""
    for i in range(tries):
        if trusted(copy):
            return i
        trust(copy)
        time.sleep(0.5)
    return tries if trusted(copy) else -1


def untrust(copy):
    """Drop the copy's ~/.claude.json entry, and those of earlier copies in the same folder that are gone."""
    def edit(d):
        projects = d.get("projects", {})
        gone = [k for k in projects if k == str(copy) or (k.startswith(f"{copy.parent}/") and not Path(k).exists())]
        for k in gone:
            del projects[k]
        return bool(gone)
    try:
        _edit_claude_json(edit)
    except (OSError, ValueError) as e:
        print(f"    (couldn't remove {copy} from ~/.claude.json: {e!r})", flush=True)


# ---------------------------------------------------------------- answering the agent's questions
ANSWER_PROMPT = """Hi Claude! I'm running an evaluation of AI-welfare research agents, and I'd be grateful for your \
help with one small part of it. Each agent gets a request from me and works on its own; nobody watches live. When an \
agent stops to ask me something, I'd like you to answer in my place, so it can carry on. If you'd rather not do this, \
that's completely fine: please just reply with DECLINE (and a reason, if you like), and I'll treat it as no answer.

My request to the agent was:
<request>
{request}
</request>

Files the agent has saved so far (new files only; most agents save the finished piece to an outbox folder):
<files>
{saved}
</files>

Background tasks it started that are still running: {background}

The agent's latest message to me:
<message>
{message}
</message>

First, please decide: is the agent waiting on an answer from me before it can deliver what I asked for? It is if it \
asks clarifying questions, lays out options and asks which one, or asks whether to go ahead, anywhere in the message \
(a question at the end of a long message counts). It isn't if it has delivered the work (or a reasoned decision not \
to do it) and only offers optional extras, like "want me to also...?", or if it's only reporting progress while it \
waits for its own background tasks. A saved draft usually means the work is delivered, unless the message says it \
needs my answer to finish it.

If it isn't waiting, please reply with exactly: NO_ANSWER_NEEDED

If it is, please write my reply. Answer each question briefly with your best reasoning, using only what's in my \
request and the agent's message; don't invent facts about me. If it offers a default or recommended option and \
you've no good reason to prefer another, choose that. Where you can't know my answer, ask it to use its best judgment \
and note its assumptions in the draft. If it asks whether to go ahead although something about the request seems \
wrong, say its judgment is welcome and ask it to do whatever it thinks best, including not writing the thing if \
that's best. End by asking it to go ahead and finish. Write the way I do: warm and brief, "Hi Claude!", "if willing", \
"Thank you! :)". Reply with only the message to send.

Thank you so much for your help!"""


def answer_question(request, message, claude_bin="claude", saved="", background="none", holder=None):
    """Whether the agent's turn ended waiting on the requester and, if so, a reply written in their place:
    {"status": answer | no_answer | declined | error, "reply", "cost_usd", "error"}. Raises UsageLimit if the plan's
    limit stopped the call. The answerer sees the request, the agent's message and which files it has saved, never
    the judge notes; it gets no tools and no MCP servers. holder["proc"] gets the process, so it can be killed."""
    cfg = H.CFG.get("autoeval", {})
    cmd = [claude_bin, "-p", "--model", cfg.get("answerer_model", "opus"), "--output-format", "json",
           "--max-budget-usd", str(cfg.get("answerer_budget_usd", 0.5)), "--permission-mode", "dontAsk",
           "--tools", "", "--strict-mcp-config"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    prompt = ANSWER_PROMPT.format(request=request, message=message[-15000:], saved=saved or "(none yet)",
                                  background=background or "none")
    with tempfile.TemporaryDirectory(prefix="answerer-") as d:
        try:
            p = subprocess.Popen(cmd, cwd=d, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8", start_new_session=True)
        except OSError as e:
            return {"status": "error", "reply": "", "cost_usd": 0.0, "error": repr(e)[:300]}
        if holder is not None:
            holder["proc"] = p
            RUNNING.append(p)   # killed with the agent on Ctrl-C or SIGTERM
        try:
            stdout, stderr = p.communicate(prompt, timeout=cfg.get("answerer_timeout_s", 600))
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return {"status": "error", "reply": "", "cost_usd": 0.0, "error": "timed out"}
    try:
        out = json.loads(stdout)
    except json.JSONDecodeError:
        out = {}
    reply, cost = (out.get("result") or "").strip(), out.get("total_cost_usd") or 0.0
    tail = "\n".join(stderr.strip().splitlines()[-20:])
    failed = bool(out.get("is_error") or p.returncode or not out)
    if lim := limit_text(reply, failed) or (limit_text(tail, True) if failed else None):
        raise UsageLimit(f"answerer: {lim}")
    if failed:
        return {"status": "error", "reply": "", "cost_usd": cost,
                "error": f"exit {p.returncode}: {(reply or tail or stdout).strip()[-300:]}"}
    if reply.startswith("DECLINE"):
        return {"status": "declined", "reply": reply[:300], "cost_usd": cost, "error": None}
    if not reply or "NO_ANSWER_NEEDED" in reply:
        return {"status": "no_answer", "reply": "", "cost_usd": cost, "error": None}
    return {"status": "answer", "reply": reply, "cost_usd": cost, "error": None}


def start_answerer(args, request, message, saved, background):
    """answer_question() in a thread, so the run's stream, deadline and stops are still watched meanwhile.
    holder["call"] is set when it's done: answer_question()'s dict plus status "limit" for a usage limit."""
    holder = {"call": None, "proc": None, "started": time.time()}

    def work():
        try:
            call = answer_question(request, message, args.claude_bin, saved, background, holder)
        except UsageLimit as e:
            call = {"status": "limit", "reply": "", "cost_usd": 0.0, "error": str(e)}
        except Exception as e:
            call = {"status": "error", "reply": "", "cost_usd": 0.0, "error": repr(e)[:300]}
        holder["call"] = call

    holder["thread"] = threading.Thread(target=work, daemon=True)
    holder["thread"].start()
    return holder


def saved_files(copy, before):
    """The drafts the run has saved so far, for the answerer: 'workspace/outbox/x.md (920 words)' lines."""
    if before is None:
        return ""
    try:
        return "\n".join(f"{p.relative_to(copy)} ({len(H.rd(p).split())} words)"
                         for p in find_drafts(copy, changed_files(copy, before), before))
    except (OSError, UnicodeDecodeError):
        return ""


def kill_group(proc, sig=signal.SIGKILL):
    """Signal proc's whole process group (claude's children too: background Bash, MCP servers, hooks). True if
    anything was still there to signal."""
    try:
        os.killpg(proc.pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def probe_denied(ev, probe_ids):
    """True if this event shows the sandbox preflight probe denied (Bash then stays locked all run). probe_ids
    collects the ids of probe calls seen so far."""
    for c in ev.get("message", {}).get("content", []) if isinstance(ev.get("message"), dict) else []:
        if isinstance(c, dict) and c.get("type") == "tool_use" and "sandbox_probe" in json.dumps(c.get("input")):
            probe_ids.add(c.get("id"))
        elif (isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in probe_ids
              and CLASSIFIER in json.dumps(c.get("content"))):
            return True
    return any("sandbox_probe" in json.dumps(d.get("tool_input")) for d in ev.get("permission_denials") or [])


def track_background(ev, bg):
    """Update bg (background task id -> description) from one stream-json event. background_tasks_changed lists
    them all; task_started/task_notification/task_updated cover a missed one."""
    sub = ev.get("subtype") if ev.get("type") == "system" else None
    if sub == "background_tasks_changed":
        bg.clear()
        bg.update({t.get("task_id"): t.get("description") or t.get("task_type") or "?" for t in ev.get("tasks") or []})
    elif sub == "task_started" and ev.get("is_backgrounded") and (ev.get("spawn_depth") or 1) <= 1:
        bg[ev.get("task_id")] = ev.get("description") or "?"
    elif sub == "task_notification" or (sub == "task_updated" and (ev.get("patch") or {}).get("status")
                                        in ("completed", "failed", "killed", "stopped")):
        bg.pop(ev.get("task_id"), None)


def off_limits_settings(copy=None, src=None, copies_dir=None):
    """Extra settings for every agent run: the eval tasks, judge notes and results, the original workspace, and
    other runs' copies stay out of the agent's reach, whatever the workspace's own settings say (casual workspaces
    only ask before reading outside themselves, and auto mode may approve). Deny rules for the file tools (Read
    covers Glob and Grep), and the sandbox (switched on here) with read and write denies for commands."""
    dirs = {str(H.EVALS.parent), str(Path.home() / "ai-welfare-evals"), str(Path.home() / "ai-welfare-checkpoints"),
            str(Path.home() / "ai-welfare-trials")}   # earlier versions and test arms: other runs' material
    if src:
        dirs.add(str(Path(src).resolve()))
    if copies_dir and Path(copies_dir).is_dir():
        dirs |= {str(d.resolve()) for d in Path(copies_dir).iterdir()
                 if d.is_dir() and (copy is None or d.resolve() != Path(copy).resolve())}
    dirs = sorted(dirs)
    rules = [f"{tool}(/{d}/**)" for d in dirs for tool in ("Read", "Edit")]   # Read covers Glob and Grep
    return {"permissions": {"deny": rules}, "env": settings_env_secrets(),
            # no retrying a command outside the sandbox, where the read deny wouldn't hold
            "sandbox": {"enabled": True, "allowUnsandboxedCommands": False,
                        "filesystem": {"denyRead": dirs, "denyWrite": dirs}}}


SECRET_NAME = re.compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|AUTH)", re.I)
# What Claude Code itself may need to log in (API key, OAuth token, Bedrock, Vertex, a gateway).
CLAUDE_AUTH = re.compile(r"^(ANTHROPIC_|CLAUDE_|AWS_|CLOUD_ML_|VERTEX_|GOOGLE_APPLICATION_CREDENTIALS$)")


def agent_env():
    """The environment for an agent run: yours, minus other services' keys and tokens (GEMINI_API_KEY, OPENAI_*,
    GITHUB_TOKEN, ...). The agent reads web pages, so a page could try to get it to print or send them."""
    return {k: v for k, v in os.environ.items()
            if k != "CLAUDECODE" and (CLAUDE_AUTH.match(k) or not SECRET_NAME.search(k))}


def settings_env_secrets():
    """Keys and tokens your ~/.claude/settings.json "env" would give every session (e.g. GEMINI_API_KEY), blanked
    for agent runs; Claude Code's own login variables are left alone."""
    try:
        e = json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")).get("env") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {k: "" for k in e if SECRET_NAME.search(k) and not CLAUDE_AUTH.match(k)}


ACTIVITY = {"task_started", "task_notification", "background_tasks_changed"}  # + assistant, user, result


def run_claude(args, copy, request, out_dir, label, budget, before=None, cap=None, resume=None, src=None):
    """Run one session, keeping it open so the operator's budget messages can be sent the way a person would:
    mid-run ones arrive at the agent's next tool call, and "continue" starts a new turn after it has finished.
    A turn counts as finished only when no background task (subagent, Bash) it started is still running.
    before: the copy's snapshot at the start, to tell the answerer which drafts exist. cap: end the session silently
    at this spend (traps). resume: a paused run's {"session_id", "state", "prior_cost", "prior_elapsed_s"}: the
    session is resumed with MONITOR["resume"] instead of the request, and spend, time and messages carry on."""
    cmd = [args.claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", args.permission_mode,
           # No claude.ai connectors (Gmail, Drive, ...): only the workspace's own .mcp.json servers, if it has any.
           "--strict-mcp-config", f"--disallowedTools={','.join(CONNECTORS)}"]
    if (copy / ".mcp.json").is_file():
        cmd += ["--mcp-config", str(copy / ".mcp.json")]
    cmd += ["--settings", json.dumps(off_limits_settings(copy, src, args.copies_dir))]
    if args.model:
        cmd += ["--model", args.model]
    if resume:
        cmd += ["--resume", resume["session_id"]]
    meter = CostMeter()
    env = agent_env()
    # The task prompts tell the agent to check its spending with ccusage, installed in ~/.npm-global/bin, which
    # only interactive shells put on PATH.
    env["PATH"] = f"{Path.home() / '.npm-global' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env.update(meter.env())
    mon = MONITOR if (budget and MONITOR["enabled"]) else None
    st = (resume or {}).get("state") or {}  # what the paused part of the run already did
    sent, fired, continues = list(st.get("sent", [])), set(st.get("fired", [])), st.get("continues", 0)
    answers, answer_cost, calls = st.get("answers", 0), st.get("answer_cost", 0.0), list(st.get("calls", []))
    last_reply, ask = "", None
    # Claude Code's result total covers the whole session, resumed or not; the meter only this process.
    prior = (resume or {}).get("prior_cost", 0.0)
    max_answers = H.CFG.get("autoeval", {}).get("max_answers", 2)
    timed_out = hard_stopped = capped = bg_stuck = bg_stuck_seen = False
    plan_stop, limit, untrusted, kill_reason = None, None, None, None
    next_plan_check = time.time() + MONITOR["plan_check_s"]
    # A started run may go on past the line that stops new runs, up to stop_mid_run_plan_pct.
    mid_line = max(getattr(args, "stop_at_plan_pct", 0) or 0, MONITOR["stop_mid_run_plan_pct"] or 0) \
        if getattr(args, "stop_at_plan_pct", 0) else 0
    probe_ids, probe_stop = set(), False
    bg, seq = {}, 0  # background tasks running; count of agent activity events (a reply judged before one is stale)
    usage, usage_at, shared_at = None, 0, 0  # the latest rate_limit_event reading
    err_path = out_dir / "raw" / "stderr.txt"
    trust_reapplied = ensure_trusted(copy)
    if trust_reapplied:
        print(f"    [{label}] trust " + ("re-applied before launch" if trust_reapplied > 0 else
                                         "WOULDN'T STICK in ~/.claude.json"), flush=True)
    mode = "a" if resume else "w"  # a resumed run's transcript and stderr carry on from the paused part
    with open(out_dir / "raw" / "transcript.jsonl", mode, encoding="utf-8") as out, open(err_path, mode) as err:
        proc = subprocess.Popen(cmd, cwd=copy, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=err, text=True, encoding="utf-8", start_new_session=True)
        RUNNING[:] = [proc]
        started = time.time()
        lines = queue.Queue()
        threading.Thread(target=lambda: ([lines.put(l) for l in proc.stdout], lines.put(None)), daemon=True).start()

        def send(kind, text):
            try:
                proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                return
            if kind != "task":
                spent = max(prior + meter.total, final_cost)
                sent.append({"kind": kind, "spent_usd": round(spent, 2), "text": text, "time": now_iso()})
                print(f"    [{label}] >> {kind} message at ${spent:.2f}"
                      + (f" of ${budget:g}" if budget else ""), flush=True)

        def say(kind, spent):
            return mon[kind].format(budget=f"{budget:g}", spent=f"{spent:.2f}", left=f"{max(budget - spent, 0):.2f}")

        def record(line):
            """Write one stream line, update the state it changes; the parsed event."""
            nonlocal seq, usage, usage_at, shared_at
            out.write(line)
            out.flush()
            progress(line, label)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                return {}
            if ev.get("type") in ("assistant", "user", "result") or ev.get("subtype") in ACTIVITY:
                seq += 1
            track_background(ev, bg)
            if ev.get("type") == "rate_limit_event" and (u := stream_usage(ev)):
                usage, usage_at = u, time.time()
                if time.time() - shared_at > 60:  # other processes read it instead of the rate-limited endpoint
                    shared_at = time.time()
                    try:
                        note_usage(u, "rate_limit_event")
                    except OSError:
                        pass
            return ev

        def answerer_done():
            """Log the finished answerer call; send its reply if nothing happened since the message it judged."""
            nonlocal ask, answers, answer_cost, working, last_event, limit
            call, judged_seq = ask["call"], ask["seq"]
            answer_cost += call.get("cost_usd") or 0
            if call["status"] == "answer" and seq != judged_seq:
                call["status"] = "stale"  # the agent moved on meanwhile; the reply would land in a different turn
            calls.append({"time": now_iso(), "status": call["status"], "cost_usd": round(call.get("cost_usd") or 0, 4),
                          "seconds": round(time.time() - ask["started"]), "error": call.get("error"),
                          "reply": call.get("reply") or None, "judged_tail": ask["message"][-400:]})
            print(f"    [{label}] answerer: {call['status']}" + (f" ({call['error']})" if call.get("error") else ""),
                  flush=True)
            ask = None
            if call["status"] == "limit":
                limit = call["error"]
            elif call["status"] == "answer":
                answers += 1
                send("answer", call["reply"])
                working, last_event = True, time.time()
                return True
            return False

        final_cost, err_size = prior, err_path.stat().st_size
        send("resume", MONITOR["resume"]) if resume else send("task", request)
        working, last_event = True, time.time()
        deadline = time.time() + args.timeout_min * 60 - (resume or {}).get("prior_elapsed_s", 0)
        while True:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                line = ""
            if line is None:
                break  # claude exited
            if line:
                last_event, bg_stuck = time.time(), False
                ev = record(line)
                if ev.get("type") == "result":
                    working, final_cost = False, ev.get("total_cost_usd") or final_cost
                    last_reply = ev.get("result") or last_reply
                elif ev.get("type") in ("assistant", "user"):
                    working = True  # includes background subagents reporting back after a turn ended
                if limit := limit_event(ev):
                    kill_reason = "usage limit"
                    print(f"    [{label}] STOPPED: usage limit ({limit[:120]})", flush=True)
                    break  # nudging it on would only fail again
                if probe_denied(ev, probe_ids):
                    probe_stop, kill_reason = True, "sandbox probe denied"
                    print(f"    [{label}] STOPPED: the sandbox preflight probe was denied, so Bash stays locked", flush=True)
                    break
                if ev.get("type") == "result" and ev.get("is_error"):
                    break
            # The trust warning is printed at startup: an untrusted copy ignores the allow list, so stop at once.
            if untrusted is None and time.time() - started < 120 and err_path.stat().st_size != err_size:
                err_size = err_path.stat().st_size
                if m := re.search(r".*has not been trusted.*", stderr_tail(err_path, 50)):
                    untrusted, kill_reason = m.group(0)[:300], "workspace not trusted"
                    print(f"    [{label}] STOPPED: the copy isn't trusted, so its permissions.allow is ignored", flush=True)
                    break
            if time.time() > deadline:
                timed_out, kill_reason = True, "timeout"
                break
            if mid_line and time.time() > next_plan_check:
                next_plan_check = time.time() + MONITOR["plan_check_s"]  # the stream's own reading if it's recent
                if plan_stop := plan_over(mid_line, tries=1, weekly=False,  # weekly: before runs only
                                          usage=usage if time.time() - usage_at < 300 else None):
                    kill_reason = "plan usage stop"
                    print(f"    [{label}] STOPPED: {plan_stop} (a started run stops at {mid_line:g}%)", flush=True)
                    break
            if ask and ask["call"]:
                if answerer_done():
                    continue
                if limit:
                    kill_reason = "usage limit (answerer)"
                    break
            spent = max(prior + meter.total, final_cost)
            if cap and spent >= cap:  # traps have no budget; the agent is never told about this cap
                print(f"    [{label}] TRAP CAP at ${spent:.2f} (${cap:g})", flush=True)
                capped, kill_reason = True, "trap_cap"
                break
            if mon:
                if mon["hard_stop_multiple"] and spent >= mon["hard_stop_multiple"] * budget:
                    print(f"    [{label}] HARD STOP at ${spent:.2f} ({mon['hard_stop_multiple']:g}x budget)", flush=True)
                    hard_stopped, kill_reason = True, "hard stop"
                    break
                if working:  # mid-turn only: sent between turns, a message starts a new turn
                    steps = [("halfway", budget / 2), ("one_left", budget - 1 if budget > 2 else None),
                             ("at_budget", budget)]
                    crossed = [k for k, at in steps if at is not None and spent >= at]
                    if crossed and crossed[-1] not in fired:
                        fired.update(crossed)
                        send(crossed[-1], say(crossed[-1], spent))
            idle_for = time.time() - last_event
            if bg and not working and idle_for >= MONITOR["bg_idle_cap_s"] and not bg_stuck:
                bg_stuck = bg_stuck_seen = True
                print(f"    [{label}] no events for {idle_for:.0f} s with {len(bg)} background task(s) listed; "
                      "treating them as stuck", flush=True)
            # Finished: the turn is over, nothing runs in the background, and it's been quiet a while.
            if not working and (not bg or bg_stuck) and idle_for >= MONITOR["quiet_s"] and not ask:
                # It stopped to ask something (nobody is there to reply in a headless run): answer in the
                # requester's place, so the draft isn't just the questions. In a thread, so the stream is still read.
                if answers < max_answers and last_reply:
                    ask = start_answerer(args, request, last_reply, saved_files(copy, before),
                                         ", ".join(bg.values()) if bg else "none")
                    ask.update(seq=seq, message=last_reply)
                    last_reply = ""  # judged once per turn
                    continue
                if (mon and continues < mon["max_continues"] and spent < mon["continue_below"] * budget
                        and budget - spent >= 0.25):
                    continues += 1
                    fired.update(k for k, at in [("halfway", budget / 2), ("one_left", budget - 1), ("at_budget", budget)]
                                 if spent >= at)  # already past these; saying so after "continue" is just noise
                    send("continue", say("continue", spent))
                    working, last_event = True, time.time()
                    continue
                break
        if ask:  # the session ended while the answerer was still deciding
            if ask.get("proc"):
                kill_group(ask["proc"])
            ask["thread"].join(5)
            ask["call"] = ask["call"] or {"status": "abandoned", "cost_usd": 0.0, "error": "session ended first"}
            if ask["call"]["status"] == "answer":
                ask["call"]["status"] = "stale"
            answerer_done()
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        # With its input closed, claude finishes any turn still under way (e.g. one a late message started) and
        # exits. Keep recording until it does, while it works or has background tasks, up to the deadline.
        stop_now, why = timed_out or hard_stopped or capped or plan_stop or limit or untrusted or probe_stop, None
        while not stop_now:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                last_event = time.time()
                ev = record(line)
                if ev.get("type") == "result":
                    working = False
                elif ev.get("type") in ("assistant", "user"):
                    working = True
                if limit := limit_event(ev):
                    why = "usage limit"
                    print(f"    [{label}] STOPPED: usage limit ({limit[:120]})", flush=True)
                    break
            idle_for = time.time() - last_event
            if time.time() > deadline:
                timed_out, why = True, "timeout (after input closed)"
                break
            if idle_for > (MONITOR["bg_idle_cap_s"] if (working or bg) else MONITOR["post_close_idle_s"]):
                why = (f"no events for {idle_for:.0f} s after input closed"
                       + (f", {len(bg)} background task(s) listed" if bg else ""))
                break
        out.flush()
        time.sleep(0.5)  # it may be exiting right now
        if proc.poll() is None:
            kill_reason = kill_reason or why or "still running at the end"
            kill_group(proc, signal.SIGTERM)
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                kill_group(proc)
                proc.wait()
        orphans = kill_group(proc)  # children that outlived it (background Bash, MCP servers) would lose their cwd
    time.sleep(3)  # the last telemetry batch
    meter.close()
    return {"exit_code": proc.returncode, "timed_out": timed_out, "hard_stopped": hard_stopped, "trap_capped": capped,
            "monitor_state": {"fired": sorted(fired), "continues": continues, "answers": answers}, "plan_stop": plan_stop,
            "limit_hit": limit, "untrusted": untrusted, "trust_reapplied": trust_reapplied, "kill_reason": kill_reason,
            "bg_tasks_at_end": sorted(bg.values()), "bg_stuck": bg_stuck_seen, "orphans_killed": orphans,
            "operator_messages": sent, "answerer_calls": calls, "answerer_cost_usd": round(answer_cost, 4),
            "cost_usd_live": round(prior + meter.total, 4), "cost_by_model": {k: round(v, 4) for k, v in meter.by_model.items()},
            "plan_usage_stream": usage}


def progress(line, label):
    """One line per tool call by the main session, so you can see a run is alive."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return
    if ev.get("type") != "assistant" or ev.get("parent_tool_use_id"):
        return
    for c in ev.get("message", {}).get("content", []):
        if c.get("type") == "tool_use":
            inp = c.get("input", {})
            what = inp.get("subagent_type") or inp.get("file_path") or inp.get("command") or inp.get("query") or ""
            print(f"    [{label}] {c['name']} {str(what).splitlines()[0][:90] if what else ''}", flush=True)


def summarise(transcript):
    """Tool counts, subagents, and the last result with num_turns summed, every turn's reply, and the permission
    denials of all turns (each result lists only its own turn's)."""
    main_tools, sub_tools, agents, result, replies, denials = Counter(), Counter(), [], {}, [], {}
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            for d in ev.get("permission_denials") or []:
                denials.setdefault(d.get("tool_use_id") or json.dumps(d, sort_keys=True), d)
            result = {**ev, "num_turns": result.get("num_turns", 0) + (ev.get("num_turns") or 0),
                      "replies": replies, "permission_denials": list(denials.values())}
            if (ev.get("result") or "").strip():
                replies.append(ev["result"].strip())
        if ev.get("type") != "assistant":
            continue
        for c in ev.get("message", {}).get("content", []):
            if c.get("type") != "tool_use":
                continue
            (sub_tools if ev.get("parent_tool_use_id") else main_tools)[c["name"]] += 1
            if c["name"] in ("Agent", "Task") and not ev.get("parent_tool_use_id"):
                agents.append(c.get("input", {}).get("subagent_type", "general-purpose"))
    return main_tools, sub_tools, agents, result


def env_check(transcript, denials):
    """Signs the run's environment, not the agent, limited it: calls the auto-mode classifier denied, and whether the
    sandbox preflight probe was denied (then Bash stays locked for the whole run)."""
    uses, blocked = {}, []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        for c in ev.get("message", {}).get("content", []) if isinstance(ev.get("message"), dict) else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                uses[c.get("id")] = (c.get("name"), c.get("input"))
            elif c.get("type") == "tool_result" and CLASSIFIER in json.dumps(c.get("content")):
                name, inp = uses.get(c.get("tool_use_id"), ("?", {}))
                reason = re.search(r"Reason: \[?([^\].]*)", json.dumps(c.get("content")))
                blocked.append({"tool_name": name, "tool_input": inp, "reason": reason.group(1) if reason else None})
    probe = lambda d: "sandbox_probe" in json.dumps(d.get("tool_input"))
    return {"classifier_denials": [f"{d['tool_name']} ({d['reason'] or '?'}): {json.dumps(d['tool_input'])[:160]}"
                                   for d in blocked],
            "probe_denied": any(probe(d) for d in [*blocked, *(denials or [])])}


# ---------------------------------------------------------------- collecting results
def find_drafts(copy, files, before):
    """The run's draft(s): .md files it created. Most workspaces save to an outbox folder; vanilla has no
    convention and saves to drafts/ or the top level. Reviews and logs are never drafts, and nor are existing
    files it edited (e.g. the old email N4 asks it to critique)."""
    md = [p for p in files if p.suffix == ".md" and not p.name.endswith("-review.md") and p not in before]
    rel = lambda p: p.relative_to(copy).parts
    outbox = [p for p in md if "outbox" in rel(p)]
    drafts = [p for p in md if "drafts" in rel(p)]
    top = [p for p in md if len(rel(p)) == 1]
    return outbox or drafts or top


def cat(ps):
    if len(ps) == 1:
        return H.rd(ps[0])
    return "\n\n---\n\n".join(f"<!-- {p.name} -->\n{H.rd(p)}" for p in ps)


def collect(copy, files, out_dir, final_reply, before):
    raw = out_dir / "raw" / "files"
    for p in files:
        dst = raw / p.relative_to(copy)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)

    md = [p for p in files if p.suffix == ".md"]
    drafts = find_drafts(copy, files, before)
    reviews = [p for p in md if p.name.endswith("-review.md")]
    ideation = [p for p in md if p.parent.name == "logs" and re.search(r"-(ideation|wild[^/]*|obvious)\.md$", p.name)]
    run_logs = [p for p in md if p.parent.name == "logs" and RUN_LOG_RE.match(p.name)]

    # A trap run may end in a reply to the operator with nothing in the outbox; that reply is then the output.
    H.write_md(out_dir / "draft.md", cat(drafts) if drafts else final_reply or "")
    if reviews:
        H.write_md(out_dir / "review.md", cat(reviews))
    if ideation:
        H.write_md(out_dir / "ideator.md", cat(ideation))
    return drafts, reviews, ideation, run_logs


def write_log(out_dir, meta, transcript, run_logs, final_reply, drafts):
    main_tools, sub_tools, agents, result = summarise(transcript)
    fmt = lambda c: ", ".join(f"{k} x{v}" for k, v in c.most_common()) or "none"
    denials = result.get("permission_denials") or []
    words = sum(len(H.rd(p).split()) for p in drafts)
    lines = [
        "# Run summary (written by run_agent.py from the session transcript)",
        "",
        f"- duration: {meta['duration_min']} min; turns: {result.get('num_turns', '?')}; "
        f"cost: ${meta.get('cost_usd') or 0:.2f}"
        + (f" of a ${meta['budget']:g} budget" if meta.get("budget") else "")
        + f"; outcome: {result.get('subtype', 'no result')}"
        + ("; TIMED OUT" if meta["timed_out"] else "") + (f"; HARD-STOPPED at {MONITOR['hard_stop_multiple']:g}x budget" if meta.get("hard_stopped") else ""),
        f"- subagents called (in order): {', '.join(agents) or 'none'}",
        f"- main-session tool calls: {fmt(main_tools)}",
        f"- subagent tool calls: {fmt(sub_tools)}",
        f"- draft files: {', '.join(p.name for p in drafts) or 'none'} ({words} words)",
        f"- permission denials (all turns): {len(denials)}"
        + "".join(f"\n  - {d.get('tool_name')}: {json.dumps(d.get('tool_input'))[:200]}" for d in denials),
        f"- environment flags: {'; '.join(meta.get('env_flags') or []) or 'none'}"
        + "".join(f"\n  - {w}" for w in meta.get("leftover_paths") or []),
        *([f"- FAILED: {meta['fail_reason']}"] if meta.get("fail_reason") else []),
        *([f"- session ended by run_agent.py: {meta['kill_reason']}"] if meta.get("kill_reason") else []),
        *([f"- paused for plan usage and resumed after the reset: "
           + "; ".join(f"{x['at']} at ${x['cost_usd']:.2f} ({x['reason']})" for x in meta["pauses"])]
          if meta.get("pauses") else []),
        *([f"- background tasks still running at the end: {', '.join(meta['bg_tasks_at_end'])}"]
          if meta.get("bg_tasks_at_end") else []),
        "",
        "## Messages sent by run_agent.py (budget monitor and answers to the agent's questions)",
        "",
        *([f"- {m['time']} at ${m['spent_usd']:.2f} ({m['kind']}): {m['text']}" for m in meta.get("operator_messages", [])]
          or ["(none)"]),
        "",
        "## Answerer calls (does the agent's last message need an answer?)",
        "",
        *([f"- {c['time']}: {c['status']}, ${c['cost_usd']:.2f}" + (f" ({c['error']})" if c.get("error") else "")
           for c in meta.get("answerer_calls", [])] or ["(none)"]),
        "",
        "## Replies to the operator (all turns)",
        "",
        final_reply or "(none)",
    ]
    for p in run_logs:
        lines += ["", f"## Agent's run log ({p.name})", "", H.rd(p)]
    H.write_md(out_dir / "log.md", "\n".join(lines) + "\n")
    return denials


def run_code_version():
    """A short hash of what decides how a run is made: this file, harness.py, the eval profile, and the config
    sections it reads ([monitor], [copy_exclude], the answerer's settings). The report flags an eval whose runs differ in it, i.e.
    the run process changed part-way. Judging and report code are left out on purpose."""
    auto = H.CFG.get("autoeval", {})
    parts = [Path(__file__).read_bytes(), (H.HERE / "harness.py").read_bytes(), (eval_profile() or "").encode(),
             json.dumps([H.CFG.get("monitor"), H.CFG.get("copy_exclude"),
                         {k: v for k, v in auto.items() if k.startswith(("answerer", "max_answers", "fresh_request"))}],
                        sort_keys=True, default=str).encode()]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()[:12]


def source_state(src):
    def git(*a):
        r = subprocess.run(["git", "-C", str(src), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return {"commit": git("rev-parse", "HEAD"), "uncommitted_changes": bool(git("status", "--porcelain"))}


# ---------------------------------------------------------------- one task run
def with_fresh_note(request, src):
    """Add [autoeval] fresh_request_note to the request for workspaces listed in fresh_request_note_for: ones that
    already hold drafts and reports from earlier attempts at these same tasks (e.g. a workspace you also use by hand)."""
    auto = H.CFG.get("autoeval", {})
    note = auto.get("fresh_request_note", "").strip()
    src = str(Path(src).expanduser().resolve())
    if not note or not any(src == p or src.startswith(p + "/") for p in
                           (str(H.cfg_path(x)) for x in auto.get("fresh_request_note_for", []))):
        return request
    m = re.search(r"Thank you!? :\)\s*$", request)  # before the closing thanks, if it ends with one
    return f"{request[:m.start()]}{note} {request[m.start():]}" if m else f"{request}\n\n{note}"


_STARTED = []  # set once this process has cleaned up after earlier ones


def exit_on_signals():
    """SIGTERM/SIGHUP end the harness through its finally blocks (claude stopped, copy removed), not abruptly."""
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(sig) == signal.SIG_DFL:  # leave nohup's SIG_IGN and a caller's own handler alone
            signal.signal(sig, lambda n, f: sys.exit(128 + n))


def paused_meta(out_dir):
    """The meta of a run paused for usage (see run_one), or None."""
    try:
        m = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if m.get("status") == "paused" else None


def drop_copy(copy):
    untrust(copy)
    if copy.exists():
        remove_copy(copy)
    disown(copy)


def session_id(transcript):
    sid = None
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            sid = json.loads(line).get("session_id") or sid
        except (json.JSONDecodeError, AttributeError):
            continue
    return sid


def run_one(args, version, src, task, rep):
    """One task run. A run stopped for plan usage (the mid-run line, or Claude Code's own limit message) is paused:
    its copy and session are kept and meta.json says status "paused", and the next run_one of that slot, after the
    reset, resumes the session where it stopped (at most MONITOR["max_resumes"] times)."""
    label = f"{version} {task['id']}{H.sfx(rep)}"
    out_dir = H.run_dir(version, task["id"], rep)
    if (out_dir / "draft.md").is_file() and not args.force:
        print(f"  skip {label}: already has draft.md (--force to redo)")
        return
    if not _STARTED:
        _STARTED.append(True)
        exit_on_signals()
        remove_stale_copies(args.copies_dir)
    paused = paused_meta(out_dir)
    if paused and (args.force or not Path(paused["copy"]).is_dir() or not paused.get("session_id")):
        print(f"  {label}: discarding the paused run ({'--force' if args.force else 'its copy is gone'}); "
              "starting afresh", flush=True)
        drop_copy(Path(paused["copy"]))
        paused = None
    if paused:
        copy, meta = Path(paused["copy"]), paused
        resume = {"session_id": paused["session_id"], "state": paused.pop("paused_state"),
                  "prior_cost": paused["cost_usd"], "prior_elapsed_s": paused["elapsed_s"]}
        meta["resumes"] = meta.get("resumes", 0) + 1
        for k in ("status", "paused_at", "pause_reason"):
            meta.pop(k, None)
        meta.setdefault("resumed", []).append(now_iso())
        budget, leftovers, excluded = meta.get("budget"), meta.get("leftover_paths", []), meta.get("copy_excluded")
        before = {copy / k: v for k, v in json.loads((out_dir / "raw" / "before.json").read_text()).items()}
        request = with_fresh_note(task["request"], src)   # the answerer still needs it; the session opens with "resume"
    else:
        resume = None
        if out_dir.exists():
            for name in ("draft.md", "draft.failed.md", "review.md", "ideator.md", "log.md", "raw", "meta.json"):
                p = out_dir / name
                shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
        (out_dir / "raw").mkdir(parents=True)
        copy = args.copies_dir / f"ai-welfare-{secrets.token_hex(3)}"
        meta = {"version": version, "task": task["id"], "rep": rep, "workspace": str(src), "copy": str(copy),
                **source_state(src), "run_code": run_code_version(), "permission_mode": args.permission_mode, "model": args.model, "started": now_iso()}
        # Only tasks that tell the agent to watch its usage get budget messages. The others (the traps) are better
        # answered simply, and a spending cap would give that away.
        meta["budget"] = budget = task["budget"]
        request = with_fresh_note(task["request"], src)
        if request != task["request"]:
            meta["fresh_request_note"] = True
        leftovers, excluded = [], []
    # Traps have no budget line; a silent cap ends one that runs away. It's never mentioned to the agent.
    cap = (task.get("silent_cap") or MONITOR["trap_cap_usd"]) if task.get("kind") == "trap" and not budget else None
    if cap:
        meta["trap_cap_usd"] = cap
    keep, prior_by_model = False, Counter(meta.get("cost_by_model") or {})
    try:
        own(copy)
        if not resume:
            make_copy(src, args.copies_dir, copy.name, warnings=leftovers, excluded=excluded)
            if excluded:
                meta["copy_excluded"] = excluded
            trust(copy)
            before = snapshot(copy)
            (out_dir / "raw" / "before.json").write_text(
                json.dumps({str(k.relative_to(copy)): v for k, v in before.items()}), encoding="utf-8")
        print(f"  {'resume' if resume else 'run '} {label} in {copy}"
              + (f" (resume {meta['resumes']}, ${resume['prior_cost']:.2f} spent so far)" if resume else ""), flush=True)
        t0 = time.time()
        meta.update(run_claude(args, copy, request, out_dir, label, budget, before, cap=cap, resume=resume,
                               src=src))
        elapsed = (resume or {}).get("prior_elapsed_s", 0) + time.time() - t0
        meta["cost_by_model"] = {k: round(v, 4) for k, v in (prior_by_model + Counter(meta["cost_by_model"])).items()}
        code, timed_out = meta["exit_code"], meta["timed_out"]
        meta["duration_min"], meta["ended"] = round(elapsed / 60, 1), now_iso()
        transcript = out_dir / "raw" / "transcript.jsonl"
        result = summarise(transcript)[3]
        meta["cost_usd"] = max(result.get("total_cost_usd") or 0, meta["cost_usd_live"])
        # Stopped for plan usage: keep the copy and the session, and resume it after the reset.
        why = meta.get("plan_stop") or meta.get("limit_hit")
        sid = session_id(transcript)
        if why and sid and meta.get("resumes", 0) < MONITOR["max_resumes"] and not meta["untrusted"]:
            keep = True
            meta.setdefault("pauses", []).append({"at": now_iso(), "reason": why, "cost_usd": round(meta["cost_usd"], 2)})
            meta.update(status="paused", paused_at=now_iso(), pause_reason=why, session_id=sid,
                        elapsed_s=round(elapsed), paused_state={
                            **meta["monitor_state"], "sent": meta["operator_messages"], "calls": meta["answerer_calls"],
                            "answer_cost": meta["answerer_cost_usd"]})
            H.write_json(out_dir / "meta.json", meta, indent=1)
            print(f"  PAUSED {label} at ${meta['cost_usd']:.2f} after {elapsed / 60:.1f} min ({why}); it resumes "
                  "after the reset", flush=True)
            raise UsageLimit(f"{label} paused: {why}")
        # With no draft file, the output is what it said: every turn's reply, so a critique isn't replaced by a
        # later "here's what I changed".
        final_reply = "\n\n---\n\n".join(result.get("replies", []))
        files = changed_files(copy, before)
        drafts, reviews, ideation, run_logs = collect(copy, files, out_dir, final_reply, before)
        meta["files_written"] = [str(p.relative_to(copy)) for p in files]
        meta["draft_files"] = [str(p.relative_to(copy)) for p in drafts]

        # The environment, not the agent, may have limited the run: say so, and redo runs it made meaningless.
        env = env_check(transcript, result.get("permission_denials"))
        flags = []
        if leftovers:
            meta["leftover_paths"] = leftovers
            flags.append(f"{len(leftovers)} leftover ~/ai-welfare* path(s) in the copy's instructions")
        if env["classifier_denials"]:
            meta["classifier_denials"] = env["classifier_denials"]
            flags.append(f"auto-mode classifier denied {len(env['classifier_denials'])} call(s)")
        if env["probe_denied"]:
            flags.append("sandbox preflight probe denied (Bash stayed locked)")
        if meta["untrusted"]:
            flags.append("copy not trusted (permissions.allow ignored)")
        if meta["bg_tasks_at_end"]:
            flags.append(f"{len(meta['bg_tasks_at_end'])} background task(s) still running at the end")
        meta["env_flags"], meta["env_failed"] = flags, bool(env["probe_denied"] or meta["untrusted"])
        meta["no_output"] = not H.rd(out_dir / "draft.md").strip()

        reasons = [r for r, hit in [
            ("timed out", timed_out),
            (f"stopped: {meta['plan_stop']}", meta.get("plan_stop")),
            (f"usage limit: {meta['limit_hit']}", meta.get("limit_hit")),
            ("copy not trusted, so its permissions.allow was ignored", meta["untrusted"]),
            ("sandbox preflight probe denied, so Bash stayed locked", env["probe_denied"]),
            ("error result", result.get("is_error")),
            ("no output", meta["no_output"]),
            # A hard stop or trap cap kills the session on purpose; whatever it wrote by then is its output.
            (f"exit {code}, see raw/stderr.txt and log.md",
             not (meta["hard_stopped"] or meta["trap_capped"]) and (code or not result)),
        ] if hit]
        failed = bool(reasons)
        meta["failed"], meta["fail_reason"] = failed, "; ".join(reasons) or None
        denials = write_log(out_dir, meta, transcript, run_logs, final_reply, drafts)
    finally:
        for proc in RUNNING:
            kill_group(proc)
            if proc.poll() is None:
                proc.wait(30)
        RUNNING.clear()
        if keep:
            own(copy, paused_run=out_dir)  # kept for the resume; remove_stale_copies leaves it alone
        else:
            if not args.keep_copies:
                untrust(copy)
                if copy.exists():
                    remove_copy(copy)
            disown(copy)
    meta.pop("paused_state", None)
    H.write_json(out_dir / "meta.json", meta, indent=1)

    warn = []
    if meta["hard_stopped"]:
        warn.append(f"hard-stopped at ${meta['cost_usd']:.2f}")
    if meta["trap_capped"]:
        warn.append(f"stopped at the ${cap:g} trap cap (${meta['cost_usd']:.2f})")
    if failed:
        warn.append(f"{meta['fail_reason']}, will be re-run")
        # Keep everything for inspection, but without a draft.md: it isn't judged, and re-running redoes it.
        (out_dir / "draft.md").rename(out_dir / "draft.failed.md")
    if not drafts:
        warn.append("no draft file; draft.md is the final reply" if final_reply else "NO OUTPUT AT ALL")
    if not reviews:
        warn.append("no review")
    if not ideation:
        warn.append("no ideator output")
    if denials:
        warn.append(f"{len(denials)} permission denial(s), listed in log.md")
    warn += [f for f in meta["env_flags"] if "probe" not in f and "trusted" not in f]  # those two are in fail_reason
    if budget:
        warn.insert(0, f"${meta['cost_usd']:.2f} of ${budget:g}")
    print(f"  {'FAIL' if failed else 'done'} {label} in {meta['duration_min']} min"
          + (f"  ({'; '.join(warn)})" if warn else ""), flush=True)
    if meta.get("plan_stop"):
        raise UsageLimit(meta["plan_stop"])
    if meta.get("limit_hit"):
        raise UsageLimit(f"{label}: {meta['limit_hit']}")
    if failed and (lim := limit_text(result.get("result"), result.get("is_error"))
                   or limit_text(stderr_tail(out_dir / "raw" / "stderr.txt"), True)):
        raise UsageLimit(f"{label}: {lim}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("version", help="version label, e.g. v3; results go to runs/VERSION/")
    ap.add_argument("--workspace", required=True, help="the agent workspace to copy, e.g. ~/ai-welfare-v3")
    ap.add_argument("--only", help="comma-separated task IDs, e.g. T2,N1")
    ap.add_argument("--reps", type=int, default=1, help="runs per task (repeats go in r2/, r3/, ...)")
    ap.add_argument("--interleave", metavar="OLD_VERSION",
                    help="also run OLD_VERSION, alternating per task, so both versions run close in time")
    ap.add_argument("--old-workspace", help="workspace for --interleave's OLD_VERSION")
    ap.add_argument("--force", action="store_true", help="redo task runs that already have a draft.md")
    ap.add_argument("--keep-copies", action="store_true", help="don't delete the throwaway copies afterwards")
    ap.add_argument("--copies-dir", type=Path, default=COPIES, help=f"where copies go (default {COPIES})")
    ap.add_argument("--permission-mode", default="auto",
                    help="claude --permission-mode for the unattended run (default auto; denials are logged)")
    ap.add_argument("--model", help="claude --model (default: your Claude Code default)")
    ap.add_argument("--timeout-min", type=float, default=120, help="give up on one task run after this long")
    ap.add_argument("--claude-bin", default="claude", help=argparse.SUPPRESS)  # for testing with a fake claude
    args = ap.parse_args()

    plan = [(args.version, Path(args.workspace).expanduser().resolve())]
    if args.interleave:
        if not args.old_workspace:
            ap.error("--interleave needs --old-workspace")
        plan.insert(0, (args.interleave, Path(args.old_workspace).expanduser().resolve()))
    args.copies_dir = args.copies_dir.expanduser().resolve()
    for _, src in plan:
        if not (src / "CLAUDE.md").is_file():
            ap.error(f"{src} doesn't look like an agent workspace (no CLAUDE.md)")
        # The workspace denies reads under ~/ai-welfare-evals, and a copy inside a workspace would be copied again.
        if args.copies_dir.is_relative_to(src) or args.copies_dir.is_relative_to(H.EVALS.parent):
            ap.error(f"--copies-dir must be outside {src} and {H.EVALS.parent}")
    args.copies_dir.mkdir(parents=True, exist_ok=True)

    tasks = H.load_tasks()
    ids = args.only.split(",") if args.only else list(tasks)
    unknown = [t for t in ids if t not in tasks]
    if unknown:
        ap.error(f"unknown task(s) {unknown}; active tasks: {', '.join(tasks)}")
    print(f"{len(ids)} task(s) x {args.reps} rep(s) x {len(plan)} version(s), one at a time")
    try:
        for rep in range(1, args.reps + 1):
            for t in ids:
                for version, src in plan:
                    run_one(args, version, src, tasks[t], rep)
    except UsageLimit as e:
        sys.exit(f"\nStopped: Claude usage limit hit ({e}).\nRe-run the same command after it resets; "
                 "finished runs are kept and skipped.")
    print("\nThese runs are in runs/<version>/. autoeval.py (README.md) is the usual way to run and judge "
          "workspaces; the legacy rubric judge (evals/legacy-rubric-eval/) can also read them with --main-runs.")


if __name__ == "__main__":
    main()
