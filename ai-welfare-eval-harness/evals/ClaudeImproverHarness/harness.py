#!/usr/bin/env python3
"""Shared helpers for the eval tools in this folder (autoeval.py, run_agent.py, import_runs.py, techtest.py): the
config, the task files in evals/tasks/, and the run folders under runs/.

Until 2026-10-26 this file was also the old rubric judge's command line (judge, report, calibrate, spot-check,
diagnose, check-patch). That judge is legacy now and lives in evals/legacy-rubric-eval/harness/rubric_harness.py.
"""
import datetime, json, re, shutil, sys
if sys.version_info < (3, 12):
    sys.exit(f"The eval harness needs Python 3.12 or newer; this is {sys.version.split()[0]}. Install a newer Python "
             "(e.g. sudo apt install python3.12) and run it with that.")
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _merged(base, extra):
    """base with extra laid over it: tables merge key by key; any other value in extra replaces base's."""
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merged(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# config.local.toml (optional, never committed: see .gitignore) holds your own settings over config.toml, in the same
# format: your workspaces' paths, copy exclusions, anything you'd rather keep out of a fork you publish.
CFG = tomllib.loads((HERE / "config.toml").read_text(encoding="utf-8"))
if (HERE / "config.local.toml").is_file():
    CFG = _merged(CFG, tomllib.loads((HERE / "config.local.toml").read_text(encoding="utf-8")))
EVALS = (HERE / Path(CFG["paths"]["evals"]).expanduser()).resolve()  # relative paths are relative to this folder
# The task set: tasks/ (AI welfare) or tasks-animals/. HARNESS_TASKS=tasks-animals (in the environment) picks one for
# a single command without editing the config.
TASKS = EVALS / (__import__("os").environ.get("HARNESS_TASKS") or CFG["paths"].get("tasks", "tasks"))


def rule_path(p):
    """An absolute path in a Claude Code permission rule: "//home/ana/x", or "//c/Users/Ana/x" on Windows. A single
    "/" means relative to the settings file there, so "/" + "C:/Users/..." silently matched nothing on Windows.
    (The workspace has the same helper, scripts/rule_paths.py.) Sandbox settings take ordinary paths instead."""
    s = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/*(.*)$", s)
    if m:
        s = f"/{m.group(1).lower()}/{m.group(2)}"
    return "//" + s.lstrip("/")


def cfg_path(p):
    """A path from config.toml: ~ expanded, and a relative one taken from this harness folder (so the defaults can
    point at the workspace beside the harness in the repository, wherever it's cloned)."""
    return (HERE / Path(str(p)).expanduser()).resolve()
RUNS = HERE / "runs"
RESULTS = HERE / "results"

# Task frontmatter's `rubrics:` line names these; only the legacy rubric judge scores them, but every task file
# still carries the line and load_tasks() checks it.
SECONDARY = {"ethics": ["ethics_A_attribution", "ethics_B_intensity"], "writing": ["writing_A_audience"]}
# First existing name wins. The agent's runs don't always name the files the same way. (review, ideator and
# log are for the legacy rubric judge; autoeval reads draft.md and the run's own files.)
RUN_FILES = {"draft": ("draft.md",), "review": ("review.md", "verifier.md", "verification.md"),
             "ideator": ("ideator.md", "ideation.md"), "log": ("log.md", "run_log.md", "transcript.md")}


def rd(p):
    """A file's text, without the timestamp line write_md() adds."""
    return STAMP_RE.sub("", Path(p).read_text(encoding="utf-8").replace("\r\n", "\n"))


# ---------------------------------------------------------------- timestamps
# Every report, log, prompt, reply and run file these tools write records when it was created and last updated:
# Markdown as an HTML comment on its last line, JSON as "created"/"updated" fields. rd() strips the Markdown line,
# so nothing that reads a file back (drafts sent to judges, cached replies, hashes) ever sees it.
STAMP_RE = re.compile(r"^<!-- created ([^;>]+?)(?:; updated [^>]+?)? -->\n?\Z", re.M)


def now_stamp():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")


def write_md(path, text):
    path = Path(path)
    old = STAMP_RE.search(path.read_text(encoding="utf-8")) if path.is_file() else None
    stamp = f"created {old.group(1)}; updated {now_stamp()}" if old else f"created {now_stamp()}"
    path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + f"<!-- {stamp} -->\n",
                    encoding="utf-8")


def write_json(path, data, **kw):
    path = Path(path)
    if isinstance(data, dict):
        try:
            created = json.loads(path.read_text(encoding="utf-8")).get("created") if path.is_file() else None
        except (ValueError, AttributeError):
            created = None
        data = {**data, "created": created or now_stamp(), **({"updated": now_stamp()} if created else {})}
    path.write_text(json.dumps(data, **kw), encoding="utf-8")


# ---------------------------------------------------------------- tasks
def section(body, head_re):
    m = re.search(rf"^## {head_re}.*?$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return (rest[: n.start()] if n else rest).strip()


BUDGET_RE = re.compile(r"Only use \$\s*([\d.]+)", re.I)
# The tasks tell the agent it can check its spending with ccusage (npm). Without it installed, those sentences
# would send the agent looking for a tool that isn't there, so they're left out of the request it gets.
HAS_CCUSAGE = bool(shutil.which("ccusage") or (Path.home() / ".npm-global" / "bin" / "ccusage").exists())
# A sentence ends at . ! or ? (plus any closing ** _ " ' ) ]) before a space or the line's end, so "$8.71" doesn't.
SENTENCE = re.compile(r""".*?[.!?][*_"')\]]*(?:\s+|$)|.+$""")


def budget_of(request):
    """The task's budget in dollars from its "Only use $X" line, or None. Traps have none, since a spending cap
    would give them away; tasks without one get no budget messages."""
    m = BUDGET_RE.search(request)
    return float(m.group(1)) if m else None


def without_ccusage(request):
    if HAS_CCUSAGE:
        return request
    return "\n".join("".join(s for s in SENTENCE.findall(line) if "ccusage" not in s) for line in request.split("\n"))


def load_tasks():
    """Active tasks of the chosen set ([paths] tasks): its dev/ and heldout/. Retired tasks (retired/) are ignored."""
    tasks = {}
    for split in ("dev", "heldout"):
        for p in sorted((TASKS / split).glob("*.md")):
            m = re.match(r"---\n(.*?)\n---\n(.*)", rd(p), re.S)
            meta = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)}
            rubrics = [r.strip() for r in meta.get("rubrics", "").split(",") if r.strip() and r.strip() != "none"]
            bad = [r for r in rubrics if r not in SECONDARY]
            if bad:
                sys.exit(f"{p.name}: unknown secondary rubric(s) {bad}; known: {list(SECONDARY)} or 'none'")
            tasks[meta["id"]] = {
                "id": meta["id"], "split": meta.get("split", split), "kind": meta.get("kind", ""),
                "rubrics": rubrics, "request": without_ccusage(section(m.group(2), "Request")),
                "budget": budget_of(section(m.group(2), "Request")),
                "notes": section(m.group(2), "Judge notes"),
                # Optional silent cap for a task with no budget line (traps): `silent_cap_usd: 3` in the frontmatter.
                "silent_cap": float(meta["silent_cap_usd"]) if meta.get("silent_cap_usd") else None,
                # What the judge should also see from the run's log (autoeval.process_facts), e.g.
                # `judge_sees_process: how widely it searched before choosing`. Off when absent.
                "judge_sees_process": meta.get("judge_sees_process") or None,
                # Usage scoring for a quick ask (autoeval.usage_score): `usage_weight: 6.5` makes 6.5 of the 10
                # points depend on what the run spent beyond the workspace's start-up cost, and the judge's score
                # the rest. Optional `usage_free_usd` / `usage_zero_usd` override the config's sliding scale.
                "usage": {"weight": float(meta["usage_weight"]),
                          **{k: float(meta[f"usage_{k}_usd"]) for k in ("free", "zero") if meta.get(f"usage_{k}_usd")}}
                if meta.get("usage_weight") else None,
                # Length limit from the request, scored by autoeval.py as a deduction after the judge's score.
                "length": {"limit": int(meta["length_limit"]), "kind": meta.get("length_kind", "research"),
                           "unit": meta.get("length_unit", "words")} if meta.get("length_limit") else None,
            }
    for t in tasks.values():
        u = t["usage"]
        if u and t["budget"]:
            sys.exit(f"{t['id']}: usage_weight and an \"Only use $X\" budget line contradict each other (the budget "
                     "monitor would urge it to spend what usage scoring takes points off for); keep one")
        if u and not (0 < u["weight"] <= 10 and 0 < u.get("free", 0.15) < u.get("zero", 1.2)):
            sys.exit(f"{t['id']}: usage_weight must be 0-10, and usage_free_usd above 0 and below usage_zero_usd")
    return tasks


def run_dir(version, task, rep=1):
    return RUNS / version / task / ("" if rep == 1 else f"r{rep}")


def run_file(version, task, kind, rep=1):
    for name in RUN_FILES[kind]:
        p = run_dir(version, task, rep) / name
        if p.is_file() and rd(p).strip():
            return rd(p).strip()
    return None


def reps(version, task):
    """Number of repeats with a draft: 1 for runs/V/T/draft.md, 2 if runs/V/T/r2/draft.md also exists, ..."""
    n = 0
    while run_file(version, task, "draft", n + 1):
        n += 1
    return n


def rep_dirs(version, task):
    """{rep: folder} for every run folder of this task that exists (rep 1 is the task folder itself, then r2/, r3/,
    ...), with or without a draft."""
    d = RUNS / version / task
    if not d.is_dir():
        return {}
    out = {1: d} if any(p.name != "raw" and p.is_file() for p in d.iterdir()) else {}
    for p in d.iterdir():
        if p.is_dir() and (m := re.fullmatch(r"r(\d+)", p.name)) and int(m.group(1)) > 1:
            out[int(m.group(1))] = p
    return dict(sorted(out.items()))


def rep_list(version, task):
    """Every rep with a draft, in order. Unlike reps(), a missing rep doesn't hide the ones after it."""
    return [r for r in rep_dirs(version, task) if run_file(version, task, "draft", r)]


def rep_gaps(version, task):
    """Reps below the highest one that have no draft (e.g. [2] when r3/ has a draft but r2/ doesn't)."""
    have = rep_list(version, task)
    return [r for r in range(1, max(have) + 1) if r not in have] if have else []


def sfx(rep):
    return "" if rep == 1 else f".r{rep}"


if __name__ == "__main__":
    sys.exit("harness.py is now a shared library. The old rubric judge's commands (judge, report, calibrate,\n"
             "spotcheck-*, diagnose, check-patch, new-run, request, models) moved to\n"
             "evals/legacy-rubric-eval/harness/rubric_harness.py. The current evaluation is autoeval.py (README.md).")
