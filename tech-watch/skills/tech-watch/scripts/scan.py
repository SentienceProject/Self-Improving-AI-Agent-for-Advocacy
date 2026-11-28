#!/usr/bin/env python3
"""Tech watch for Claude Code: set up the folder, run scheduled scans, say what's waiting.

    python3 scan.py init [--dir DIR]       make the folder (default ~/tech-watch) with TECHWATCH.md from the template
    python3 scan.py status [--dir DIR]     last scans, this month's spend, proposals waiting for you, schedule
    python3 scan.py run [--dir DIR]        what the schedule calls hourly: a scan if one is due, else nothing
    python3 scan.py run --now [--quick]    a scan now, whatever the schedule says (full unless --quick)
    python3 scan.py schedule on|off        run hourly in the background (cron on Linux/macOS, Task Scheduler on
                                           Windows); `on` copies the scripts into DIR/.engine so plugin updates
                                           don't break the schedule
    python3 scan.py waiting                one line if proposals are waiting (the plugin's session-start hook)

A scheduled scan is one headless `claude -p` run with a hard budget cap. It gets only Read, Glob, Grep, WebSearch,
WebFetch and Write/Edit; it can write only in DIR/reports/ and DIR/proposals/, read only DIR and the paths under
`Setup to watch:` in TECHWATCH.md, and none of your other Claude Code settings, MCP servers or plugins apply to it.
It proposes; it never changes your setup. What you decided lives in TECHWATCH.md's `## Decisions`, which a
scheduled scan can't write, so a proposal (or a web page quoted in one) can't mark itself accepted.
"""

from __future__ import annotations   # Python 3.9 reads the newer type hints

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sensor  # noqa: E402

SKILL = HERE.parent if (HERE.parent / "SKILL.md").exists() else HERE   # skill layout, or DIR/.engine
POINTER = Path.home() / ".config" / "tech-watch" / "where.txt"
ENGINE_FILES = ("scan.py", "sensor.py", "scout.md")
TOOLS = "Read,Glob,Grep,Edit,Write,WebSearch,WebFetch"
CRON_MARK = "# tech-watch"
FREE = 0.05            # a failed run that cost less than this did no real work: try again later
MAX_ATTEMPTS_A_DAY = 3
DEFAULTS = {"Model": "opus", "Budget per scan": "$3", "Monthly cap": "$15", "Scans": "weekly"}
SECRETS = [".ssh/**", ".aws/**", ".claude/.credentials.json", ".config/anthropic/**", ".git-credentials",
           ".config/gh/**", ".gnupg/**", ".netrc", ".config/gcloud/**", ".azure/**", ".kube/**", ".codex/**",
           # Claude Code's own private files: settings (which can hold keys in "env"), prompt history, past
           # conversations and what they touched. A scan reads web pages, and a page could trick it into putting
           # what it read into a URL, so these stay unreadable even when someone lists all of ~/.claude.
           ".claude/settings.json", ".claude/history.jsonl", ".claude/projects/**", ".claude/todos/**",
           ".claude/shell-snapshots/**", ".claude/session-env/**", ".claude/file-history/**", ".claude/debug/**",
           ".claude/ide/**", ".claude/statsig/**", ".claude/paste-cache/**", ".claude/plans/**", ".claude/tasks/**",
           ".claude.json", ".claude.json.backup", ".config/tech-watch/**"]


# ---------------------------------------------------------------- small helpers

def rule_path(p) -> str:
    """Permission rules take `//abs/path` for an absolute path (`//c/Users/x` on Windows)."""
    s = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/*(.*)$", s)
    if m:
        s = f"/{m.group(1).lower()}/{m.group(2)}"
    return "//" + s.lstrip("/")


def money(text: str, default: float) -> float:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", text or "")   # "$1,000" is 1000, not 1
    return float(m.group(1).replace(",", "")) if m else default


def folder_from(argv: list[str]) -> Path:
    given = sensor.dir_arg(argv)
    if given:
        return given
    try:
        saved = POINTER.read_text(encoding="utf-8").strip()
        if saved:
            return Path(saved)
    except OSError:
        pass
    if (Path.cwd() / "TECHWATCH.md").exists():
        return Path.cwd()
    return Path.home() / "tech-watch"


def read_md(folder: Path) -> str:
    try:
        return (folder / "TECHWATCH.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def runs(folder: Path) -> list[dict]:
    out = []
    try:
        for line in (folder / "state" / "scans.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def record(folder: Path, entry: dict) -> None:
    (folder / "state").mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "day": date.today().isoformat(),
             **entry}
    with open(folder / "state" / "scans.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def month_spend(folder: Path) -> float:
    month = date.today().isoformat()[:7]
    return round(sum(float(r.get("cost_usd") or 0) for r in runs(folder) if r.get("day", "").startswith(month)), 2)


def waiting(folder: Path) -> list[str]:
    """Proposals whose file name doesn't appear under `## Decisions` in TECHWATCH.md."""
    decided = re.sub(r"(?s)<!--.*?-->", "", sensor.section(read_md(folder), "Decisions"))
    props = sorted((folder / "proposals").glob("*.md")) if (folder / "proposals").is_dir() else []
    # whole names only: deciding on 2026-11-30-memory-v2 mustn't hide 2026-11-30-memory
    return [p.stem for p in props if not re.search(rf"(?<![\w-]){re.escape(p.stem)}(?![\w-])", decided)]


def days_since(day: str) -> int:
    try:
        return (date.today() - date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return 10_000


def due(folder: Path, md: str) -> str:
    """'full', 'quick' or '' (nothing due today)."""
    rs = runs(folder)
    if sum(1 for r in rs if r.get("day") == date.today().isoformat() and r.get("kind") != "skip") \
            >= MAX_ATTEMPTS_A_DAY:
        return ""
    done = [r for r in rs if r.get("outcome") in ("ok", "failed")]   # failed = spent money: counts as done
    last_full = max((r["day"] for r in done if r.get("kind") == "full"), default="")
    last_any = max((r["day"] for r in done), default="")
    if days_since(last_full) >= 7:
        return "full"
    scans = setting(md, "Scans").lower()
    if "daily" in scans and days_since(last_any) >= 1:
        return "quick"
    # twice a week: one quick scan between full ones, at least 3 days after the full one (not one every 3 days,
    # which made it three scans a week)
    quick_since_full = any(r.get("kind") == "quick" and r["day"] > last_full for r in done)
    if "twice" in scans and days_since(last_full) >= 3 and not quick_since_full:
        return "quick"
    return ""


def setting(md: str, key: str) -> str:
    return sensor.setting(md, key, DEFAULTS.get(key, ""))


def set_setting(folder: Path, key: str, value: str) -> bool:
    """Change `- Key: value` under `## Settings` in TECHWATCH.md (adding the line if it's missing)."""
    tw = folder / "TECHWATCH.md"
    try:
        md = tw.read_text(encoding="utf-8")
    except OSError:
        return False
    head = re.search(r"(?im)^##\s+Settings\s*$", md)
    if not head:
        return False
    nxt = re.search(r"(?m)^##?\s", md[head.end():])
    end = head.end() + (nxt.start() if nxt else len(md[head.end():]))
    body = md[head.end():end]
    line = re.compile(rf"(?im)^([ \t]*(?:[-*][ \t]+)?\**{re.escape(key)}\**[ \t]*:)[ \t]*(.*?)[ \t]*$")
    # skip lines inside <!-- comments -->
    spans = [m.span() for m in re.finditer(r"(?s)<!--.*?-->", body)]
    m = next((m for m in line.finditer(body) if not any(a <= m.start() < b for a, b in spans)), None)
    if m:
        body = body[:m.start()] + f"{m.group(1)} {value}" + body[m.end():]
    else:
        body = f"\n- {key}: {value}" + body
    tw.write_text(md[:head.end()] + body + md[end:], encoding="utf-8")
    return True


def most_a_month(md: str) -> float:
    """What a month of scheduled scans could cost if every scan used its whole budget."""
    full = money(setting(md, "Budget per scan"), 3.0)
    quick = round(max(0.5, full / 2), 2)
    scans = setting(md, "Scans").lower()
    quicks = 26 if "daily" in scans else 4.3 if "twice" in scans else 0
    return round(4.3 * full + quicks * quick, 2)


def cap_stop(folder: Path) -> str:
    """The day the monthly cap last stopped a scan this month, if no scan has run since ('' otherwise)."""
    month = date.today().isoformat()[:7]
    rs = [r for r in runs(folder) if r.get("day", "").startswith(month)]
    stops = [r for r in rs if r.get("outcome") == "cap"]
    ran = [r for r in rs if r.get("kind") != "skip" and r.get("outcome") in ("ok", "failed")]
    # by time, not day: a scan that runs later on the day the cap stopped one (after raising the cap) clears it
    if stops and (not ran or stops[-1].get("ts", "") >= ran[-1].get("ts", "")):
        return stops[-1]["day"]
    return ""


def setup_paths(md: str) -> list[Path]:
    raw = setting(md, "Setup to watch")
    out = []
    for part in re.split(r"[,;]\s*", raw):
        part = part.strip().strip("`")
        if part:
            p = Path(part).expanduser()
            if p.exists():
                out.append(p.resolve())
    return out


def claude_bin() -> str:
    # `schedule on` records where `claude` was found in the person's own shell: cron's PATH is only /usr/bin:/bin
    pinned = os.environ.get("TECH_WATCH_CLAUDE", "")
    if pinned and Path(pinned).exists():
        return pinned
    found = shutil.which("claude")
    for p in ([found] if found else []) + [str(Path.home() / ".local/bin/claude"),
                                           str(Path.home() / ".npm-global/bin/claude"), "/usr/local/bin/claude",
                                           "/opt/homebrew/bin/claude",
                                           str(Path.home() / "AppData/Roaming/npm/claude.cmd")]:
        if p and Path(p).exists():
            return p
    return ""


# ---------------------------------------------------------------- the headless scan

def scout_settings(folder: Path, md: str) -> dict:
    home = rule_path(Path.home())
    # No bare "Glob"/"Grep" rules: tested on 2.1.332, those let Grep search anywhere (~/.bashrc, ~/.claude);
    # without them, Glob and Grep follow the Read rules below.
    allow = [f"Read({rule_path(folder)}/**)", "WebSearch", "WebFetch",
             f"Edit({rule_path(folder / 'reports')}/**)", f"Edit({rule_path(folder / 'proposals')}/**)"]
    for p in setup_paths(md):
        allow.append(f"Read({rule_path(p)}/**)" if p.is_dir() else f"Read({rule_path(p)})")
    deny = ["Bash", "PowerShell", "NotebookEdit", "CronCreate", "CronDelete", "RemoteTrigger", "ScheduleWakeup",
            "Read(**/.env)", "Read(**/.env.*)", "Read(**/.claude/settings.local.json)",
            "Read(**/.credentials.json)"] + [f"Read({home}/{s})" for s in SECRETS]
    return {"permissions": {"defaultMode": "dontAsk", "allow": allow, "deny": deny},
            "autoMemoryEnabled": False}


def brief_path() -> Path:
    for p in (SKILL / "scout.md", HERE / "scout.md"):
        if p.exists():
            return p
    raise FileNotFoundError("scout.md not found next to scan.py or in the skill folder")


def scan(folder: Path, kind: str, md: str) -> int:
    full_budget = money(setting(md, "Budget per scan"), 3.0)
    budget = full_budget if kind == "full" else round(max(0.5, full_budget / 2), 2)
    cap, spent = money(setting(md, "Monthly cap"), 15.0), month_spend(folder)
    # a quick scan must leave room for a full one, so daily quick scans can't starve the weekly full scan
    reserve = full_budget if kind == "quick" else 0
    if spent + budget + reserve > cap:
        print(f"tech-watch: skipped; this month's scans cost ${spent:.2f} and another could pass the "
              f"${cap:.2f} monthly cap (TECHWATCH.md, Monthly cap:)")
        today = date.today().isoformat()
        if not any(r.get("day") == today and r.get("outcome") == "cap" for r in runs(folder)):
            record(folder, {"kind": "skip", "outcome": "cap", "cost_usd": 0})   # once a day, not every hour
        return 0
    exe = claude_bin()
    if not exe:
        print("tech-watch: can't find the `claude` command; is Claude Code installed? (`scan.py schedule on` "
              "from a terminal where `claude` works records where it is)", file=sys.stderr)
        record(folder, {"kind": kind, "outcome": "no-claude", "cost_usd": 0})   # counts toward the daily attempts
        return 1

    _, report = sensor.run(folder)
    model = setting(md, "Model").split()[0].lower()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    paths = ", ".join(str(p) for p in setup_paths(md)) or "(none listed)"
    prompt = (f"Today: {date.today().isoformat()}\nScan: {kind}\nWhere you are: a scheduled Claude Code run. "
              f"Nobody is here to answer, and you can write only in {folder / 'reports'} and "
              f"{folder / 'proposals'}.\nBudget cap: ${budget} (hard: the run is cut off there, so write your "
              f"scan note early and refine after)\nTech-watch folder: {folder}\n"
              f"Sensor report: {report or '(none: the sensor found no watched pages or could not run)'}\n"
              f"Setup you may read: {paths}\n\n" + brief_path().read_text(encoding="utf-8"))
    (folder / "state").mkdir(parents=True, exist_ok=True)
    (folder / "reports").mkdir(exist_ok=True)
    (folder / "proposals").mkdir(exist_ok=True)
    settings_file = folder / "state" / "scan-settings.json"
    settings_file.write_text(json.dumps(scout_settings(folder, md), indent=1), encoding="utf-8")
    logs = folder / "state" / "logs"
    logs.mkdir(exist_ok=True)
    cmd = [exe, "-p", "--model", model, "--effort", "high" if kind == "full" else "medium",
           "--max-budget-usd", str(budget), "--settings", str(settings_file), "--setting-sources", "",
           "--permission-mode", "dontAsk", "--tools", TOOLS, "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "json"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    out, err = logs / f"{stamp}-{kind}.json", logs / f"{stamp}-{kind}.stderr"
    with open(out, "w", encoding="utf-8") as fo, open(err, "w", encoding="utf-8") as fe:
        rc = subprocess.run(cmd, input=prompt, cwd=folder, env=env, stdout=fo, stderr=fe, text=True).returncode
    try:
        result = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    cost = round(float(result.get("total_cost_usd") or 0), 3)
    ok = rc == 0 and result and not result.get("is_error")
    outcome = "ok" if ok else ("failed" if cost > FREE else "retry")
    record(folder, {"kind": kind, "outcome": outcome, "cost_usd": cost, "model": model, "log": out.name})
    print(f"tech-watch: {kind} scan {outcome} (${cost:.2f}); waiting for you: {len(waiting(folder))}")
    return 0 if ok else 1


# ---------------------------------------------------------------- commands

def cmd_init(folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for sub in ("reports", "proposals", "state"):
        (folder / sub).mkdir(exist_ok=True)
    tw = folder / "TECHWATCH.md"
    if not tw.exists():
        shutil.copyfile(SKILL / "template.md", tw)
        print(f"made {tw} from the template")
    else:
        print(f"{tw} already exists; left as it is")
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(folder), encoding="utf-8")
    return 0


def engine_stale(folder: Path) -> bool:
    eng = folder / ".engine"
    if not eng.is_dir() or SKILL == eng:
        return False
    src = {"scan.py": HERE / "scan.py", "sensor.py": HERE / "sensor.py", "scout.md": brief_path()}
    for name, path in src.items():
        mine = eng / name
        if not mine.exists() or hashlib.sha1(mine.read_bytes()).digest() != hashlib.sha1(path.read_bytes()).digest():
            return True
    return False


def refresh_engine(folder: Path) -> Path:
    eng = folder / ".engine"
    eng.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "scan.py", eng / "scan.py")
    shutil.copyfile(HERE / "sensor.py", eng / "sensor.py")
    shutil.copyfile(brief_path(), eng / "scout.md")
    return eng


def cron_path() -> str:
    """A short PATH for the cron line: the folders holding `claude` and `node`, then the usual system ones.
    Not the shell's whole PATH: on WSL that runs to 1000+ characters, and some crons cut long lines."""
    dirs = []
    for exe in (claude_bin(), shutil.which("node") or ""):
        if exe:
            dirs.append(str(Path(exe).parent))
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(d for d in dirs if d.startswith("/")))


def scheduled(folder: Path) -> str:
    if os.name == "nt":
        r = subprocess.run(["schtasks", "/Query", "/TN", "tech-watch"], capture_output=True)
        return "on (Task Scheduler, hourly)" if r.returncode == 0 else "off"
    if not shutil.which("crontab"):
        return "off (no crontab here)"
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    return "on (cron, hourly)" if CRON_MARK in cur else "off"


def cmd_schedule(folder: Path, action: str) -> int:
    if action == "on":
        if not (folder / "TECHWATCH.md").exists():
            print(f"no TECHWATCH.md in {folder}; run `scan.py init` first", file=sys.stderr)
            return 1
        if os.name != "nt" and not shutil.which("crontab"):   # before copying anything
            print("no crontab on this computer; run `scan.py run` from your own scheduler, hourly", file=sys.stderr)
            return 1
        eng = refresh_engine(folder)
        if not claude_bin():
            print("note: can't find the `claude` command from here; scheduled scans will fail until it's on PATH")
    if os.name == "nt":
        if action == "on":
            runner = folder / ".engine" / "run.cmd"
            runner.write_text(f'@echo off\r\n"{sys.executable}" "{eng / "scan.py"}" run --dir "{folder}" '
                              f'>> "{folder / "state" / "schedule.log"}" 2>&1\r\n', encoding="utf-8")
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", "tech-watch", "/TR",
                                f'"{runner}"'], capture_output=True, text=True)
            print("scheduled hourly in Task Scheduler" if r.returncode == 0 else f"schtasks failed: {r.stderr}")
            if r.returncode == 0:
                after_schedule(folder, action)
            return r.returncode
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", "tech-watch"], capture_output=True)
        print("schedule removed")
        after_schedule(folder, action)
        return 0
    if not shutil.which("crontab"):
        print("no crontab on this computer; run `scan.py run` from your own scheduler, hourly", file=sys.stderr)
        return 1
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    lines = [l for l in cur.splitlines() if CRON_MARK not in l]
    if action == "on":
        minute = int(hashlib.sha1(str(folder).encode()).hexdigest(), 16) % 60
        # cron runs with PATH=/usr/bin:/bin, where `claude` (and the `node` an npm install needs) usually isn't:
        # pass on their folders and where `claude` is. `%` means newline to cron, so escape it.
        env = f'PATH="{cron_path()}" '
        if claude_bin():
            env += f'TECH_WATCH_CLAUDE="{claude_bin()}" '
        job = (f'{env}"{sys.executable}" "{folder / ".engine" / "scan.py"}" run --dir "{folder}" '
               f'>> "{folder / "state" / "schedule.log"}" 2>&1').replace("%", r"\%")
        lines.append(f"{minute} * * * * {job} {CRON_MARK}")
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, capture_output=True)
    if r.returncode:
        print(f"crontab failed: {r.stderr}", file=sys.stderr)
        return 1
    print(f"scheduled hourly with cron (minute {minute}); it scans at most as often as `Scans:` says, "
          "and only while this computer is on" if action == "on" else "schedule removed")
    after_schedule(folder, action)
    return 0


def after_schedule(folder: Path, action: str) -> None:
    """Keep `Runs:` in TECHWATCH.md true to the schedule, and say if the cap could stop scans mid-month."""
    if set_setting(folder, "Runs", "scheduled" if action == "on" else "when I ask"):
        print(f"TECHWATCH.md: Runs: {'scheduled' if action == 'on' else 'when I ask'}")
    md = read_md(folder)
    most, cap = most_a_month(md), money(setting(md, "Monthly cap"), 15.0)
    if action == "on" and most > cap:
        print(f"note: with Scans: {setting(md, 'Scans')}, a month of scans could use up to ${most:.2f} if each "
              f"used its whole budget; the monthly cap is ${cap:.2f}, so scans may stop before the month ends "
              "(status and the session start will say so). Raise `Monthly cap:` to avoid that.")


def cmd_status(folder: Path) -> int:
    md = read_md(folder)
    if not md:
        print(f"tech watch isn't set up in {folder} (no TECHWATCH.md); `scan.py init` makes it")
        return 1
    rs = [r for r in runs(folder) if r.get("kind") != "skip"]
    cap = money(setting(md, "Monthly cap"), 15.0)
    stopped = cap_stop(folder)
    print(f"folder: {folder}")
    print(f"runs: {setting(md, 'Runs') or 'when I ask'} · scans: {setting(md, 'Scans')} · model: "
          f"{setting(md, 'Model')} · schedule: {scheduled(folder)}")
    print(f"this month: ${month_spend(folder):.2f} of ${cap:.2f}")
    if stopped:
        print(f"warning: the monthly cap stopped scheduled scans on {stopped}; they start again next month, or "
              "sooner if you raise `Monthly cap:` in TECHWATCH.md")
    for r in rs[-3:]:
        print(f"  {r.get('day')} {r.get('kind')} scan: {r.get('outcome')} (${float(r.get('cost_usd') or 0):.2f})")
    recent = rs[-3:]
    if recent and all(r.get("outcome") != "ok" for r in recent):
        what = "the last scan attempt" if len(recent) == 1 else f"the last {len(recent)} scan attempts"
        print(f"warning: {what} didn't succeed; see {folder / 'state' / 'schedule.log'} and {folder / 'state' / 'logs'}")
    w = waiting(folder)
    print(f"waiting for you: {len(w)}" + "".join(f"\n  {p}" for p in w))
    if engine_stale(folder):
        print("the scheduled copy of the scripts (.engine) is older than this skill; `scan.py schedule on` updates it")
    return 0


def cmd_waiting() -> int:
    """For the session-start hook: silent unless set up and something is waiting. Never fails loudly."""
    try:
        if not POINTER.exists():
            return 0
        folder = Path(POINTER.read_text(encoding="utf-8").strip())
        if engine_stale(folder):
            refresh_engine(folder)
        stopped = cap_stop(folder)
        if stopped:
            print(f"Tech watch: the monthly cap stopped scheduled scans on {stopped}; they start again next month, "
                  "or sooner if the person raises `Monthly cap:` in TECHWATCH.md. Mention this in one line.")
        w = waiting(folder)
        if w:
            print(f"Tech watch: {len(w)} proposal{'s' if len(w) != 1 else ''} waiting in {folder / 'proposals'} "
                  f"({', '.join(w[:3])}{', …' if len(w) > 3 else ''}). If it fits, mention this to the person in "
                  "one line; the tech-watch skill goes through them.")
    except Exception:
        pass
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "waiting":
        return cmd_waiting()
    folder = folder_from(rest)
    if command == "init":
        return cmd_init(folder)
    if command == "status":
        return cmd_status(folder)
    if command == "schedule" and rest and rest[0] in ("on", "off"):
        return cmd_schedule(folder, rest[0])
    if command == "run":
        md = read_md(folder)
        if not md:
            print(f"tech-watch: no TECHWATCH.md in {folder}", file=sys.stderr)
            return 1
        kind = ("quick" if "--quick" in rest else "full") if "--now" in rest else due(folder, md)
        return scan(folder, kind, md) if kind else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
