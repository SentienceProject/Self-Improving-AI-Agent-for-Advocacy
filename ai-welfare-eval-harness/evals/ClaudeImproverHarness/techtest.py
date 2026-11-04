"""Test tech-watch proposals before they're applied.

The companion workspace's tech watch (ai-welfare-workspace/techwatch) prepares changes to the workspace. When you
answer "test it" in its review.py, it records the request and (on its next run) calls `techtest.py tick` here with
TECHTEST_WORKSPACE set to the workspace. This tests the change in copies of the workspace, so the result is ready
when you decide, and writes the verdict to the workspace's techwatch/evaluations/<id>.md. It never changes the real
workspace: applying stays with tech watch's review.py, which applies a change that passed if you asked it to.

For each proposal you asked to test (and, with [techtest] auto_test = true, notable/major ones written on or after
[techtest] auto_since):

  1. screen   ChatGPT, Gemini and a critic Claude session each say whether it's worth testing (VERDICT line).
              Untested unless a majority say TEST. Skipped for a proposal you asked to test, unless
              [techtest] screen_flagged = true.
  2. plan     a planner Claude session reads the proposal, its staged bundle and EVAL.md (tech-watch's own test
              design), and pre-registers the test: arms, 1-5 dev tasks fitted to the size of the change (a trap
              when the change could over-engineer, and always with 3+), success criterion. It sees the tasks'
              IDs, kinds and skill tags ([techtest] task_skills) only, not their text.
  3. build    each arm is a copy of the workspace (~/ai-welfare-trials/). When tech watch has prepared the change
              (techwatch/staged/<id>/), there is one arm, "staged", with exactly that change applied, so what
              passes is what gets applied. Otherwise an implementer Claude session makes each arm's change.
              An arm that drops a safety rule from the settings fails. The edits are kept in build.json, so a
              missing copy is rebuilt the same way.
  4. baseline one fresh run of the unchanged workspace per task, made alongside the arms' runs. Another test's
              fresh baseline run of the same task can be borrowed if it was made with the current instruction
              files within [techtest] baseline_borrow_hours.
  5. run      autoeval's runner, one run per arm per task (two for the noise task, 6b): same budget monitor, 1.3x cap, and stop at 85% of your
              plan usage. A run that fails [techtest] max_run_attempts times stops the test with an error instead
              of retrying hourly.
  6. judge    ChatGPT compares the arms blind (autoeval's judge), twice in different orders. Every arm needs a
              score on every task, or the test stops with an error. Then ChatGPT and Gemini each read the scores,
              the judge's replies and the arm diffs and say whether there's a strong reason not to adopt an arm.
  6b. noise  the plan's cheapest task is run twice for every arm and the baseline. If one workspace's two runs
              score more than [techtest] noise_threshold apart, every planned task (up to 5) gets a second run too
              and the verdict uses the mean of both. Each run is a fresh copy of the workspace, so a second run
              never sees the first.
  7. report   verdict per arm, leaning toward adopting (the proposals are vetted before they get here):
              met, unless the arm's mean is more than [techtest] adopt_margin below the baseline's, a task is more
              than task_drop_limit below (or the plan's per_task_floor for it), its cost is over the plan's
              max_cost_ratio, or an outside reviewer objects strongly: then not met. INVALID: the change left no
              trace the plan requires. inconclusive: a score or the cost can't be computed. Each verdict also
              says in plain terms what the test could tell (an "Evidence staged:" JSON line for review.py): the
              difference, tasks x runs, and the smallest difference it could tell from noise, so "no clear
              difference" reads as "too small to settle", not as a failure. A deeper test (a request with
              depth "deeper") runs every task deep_reps times. Full details in
              techtest/<id>/RESULT.md here. techwatch/evaluations/<id>.md gets an allowlisted copy only (verdicts,
              scores by task ID, the arm diffs, costs), and is refused if it shares an 8-word phrase with any task
              text.

A proposal flagged again (`review.py test <id>`) after its test finished is tested again from the plan step.

    python3 techtest.py status            what's queued and how far each test got
    python3 techtest.py tick              advance the queue (cron runs this hourly; resumes after usage stops)
    python3 techtest.py tick --only ID    just this proposal
    python3 techtest.py redo ID STEP      forget STEP and everything after it for ID, so the next tick redoes it
                                          (the runs those steps made move to runs/.superseded/)
    python3 techtest.py redo ID baseline --keep-arm-runs
                                          switch an older test to a fresh baseline: set its reused or earlier
                                          baseline runs aside, keep the arms' runs, and re-judge
    python3 techtest.py retry ID          clear ID's error after fixing its cause; the next tick carries on
    python3 techtest.py clean [--yes]     list (with --yes, remove) trial copies no test needs any more
    python3 techtest.py checkup [--now]   the periodic check of the sum of changes (tick runs it when due): the
                                          current workspace against a checkpoint from [techtest]
                                          checkup_every_days ago, on the held-out tasks; checkpoints are kept

Only dev tasks are used: proposals come from a loop that adopts what scores well, so heldout tasks stay unseen.
"""
import argparse, datetime, difflib, fcntl, hashlib, json, math, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

import harness as H
import run_agent as RA
import autoeval as A
import external_models as X

CFG = H.CFG.get("techtest", {})
WS = Path(os.environ["TECHTEST_WORKSPACE"]).expanduser() if os.environ.get("TECHTEST_WORKSPACE") else \
    H.cfg_path(CFG.get("workspace", "../../../ai-welfare-workspace"))
TW = WS / "techwatch"
EVALS_OUT = TW / "evaluations"           # scouts can't write here (not in scout-settings.json's allow list)
OUT = H.HERE / "techtest"
TRIALS = Path.home() / "ai-welfare-trials"
STEPS = ["screen", "plan", "build", "baseline", "run", "judge", "noise", "report"]
ENDED_EARLY = ("screened out", "not testable", "build failed")
DECISIVE = ("approved", "rejected", "deferred", "shelved", "applied", "rolled_back", "kept", "removed",
            "staged", "cannot_stage", "stage_failed", "trial_started")
DROPPED = ("rejected", "removed", "shelved")   # a test of a proposal you dropped or shelved stops (unless you flag it
                                               # again after)
USAGE_STEPS = ("screen", "plan", "build", "baseline", "run")   # the steps that spend Claude usage
RESERVED_ARMS = {"baseline", "baseline-earlier"}
# Helper sessions (critic, planner, implementer) can't read these: the eval tasks, judge notes and drafts, other
# runs' copies, checkpoints, and credentials. Read rules also cover Glob and Grep. The critic reads the untrusted
# proposal, so it has WebSearch but not WebFetch (a fetch could carry what it read to a web address).
OFF_LIMITS = [Path.home() / "ai-welfare-evals", H.EVALS.resolve(), H.HERE.resolve(), H.RUNS, A.RESULTS, RA.COPIES,
              Path.home() / "ai-welfare-checkpoints", Path.home() / ".claude",
              Path.home() / ".codex", Path.home() / ".ssh", Path.home() / ".aws", Path.home() / ".config" / "gcloud",
              Path.home() / ".gnupg"]
# Left out of every trial copy: earlier test results (techwatch/evaluations*) would tell a run about the tasks.
TRIAL_SKIP = ["techwatch/evaluations", "techwatch/evaluations-full"]


def when(s):
    """A 'started'-style timestamp as naive local time. Imported runs are stamped in UTC ('...Z'), ours in local
    time, so they're only comparable after this. '' -> datetime.min."""
    if not s:
        return datetime.datetime.min
    t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


# ---------------------------------------------------------------- tech-watch's proposals and ledger
def ledger():
    f = TW / "state" / "ledger.jsonl"
    out = []
    for line in (f.read_text(encoding="utf-8").splitlines() if f.exists() else []):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def status_of(pid, evs):
    """As review.py computes it: the latest decisive event."""
    st, until = "pending", None
    for e in evs:
        if e.get("id") == pid and e.get("event") in DECISIVE:
            st, until = e["event"], e.get("until")
    if st == "deferred" and until and until <= datetime.date.today().isoformat():
        st = "pending"
    return st


def proposal_meta(pid):
    try:
        return json.loads((TW / "proposals" / pid / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def finished(st):
    return bool(st.get("outcome")) and "report" in st.get("done", [])


def flag_ts(pid, evs=None):
    """When you last flagged this proposal for a test (the ledger's ts), or ''."""
    return max((e.get("ts", "") for e in (ledger() if evs is None else evs)
                if e.get("id") == pid and e.get("event") == "test_requested"), default="")


def candidates():
    """[(id, why)] to test, oldest first. A flag counts unless you rejected or removed the proposal after it; a test
    already under way carries on when the proposal is approved or applied meanwhile, and stops if it's dropped."""
    evs = ledger()
    flagged, dropped_at = {}, {}
    for i, e in enumerate(evs):
        if e.get("event") == "test_requested":
            flagged[e["id"]] = (i, e.get("note", ""))
        elif e.get("event") in DROPPED:
            dropped_at[e["id"]] = i
    auto, since = CFG.get("auto_test", False), CFG.get("auto_since", "")
    if auto and not re.fullmatch(r"\d{4}-\d\d-\d\d", str(since)):
        # without a start date, switching auto_test on would test the whole backlog at $10-40 each
        print("auto_test is on but auto_since isn't a date (YYYY-MM-DD), so nothing is tested automatically. Set "
              "auto_since to today to test only proposals written from now on.", flush=True)
        auto = False
    out = []
    for d in sorted((TW / "proposals").glob("*/proposal.md")):
        pid = d.parent.name
        m, st = proposal_meta(pid), status_of(pid, evs)
        tst = state(pid) if (sdir(pid) / "state.json").exists() else None
        if pid in flagged and flagged[pid][0] > dropped_at.get(pid, -1):
            out.append((pid, "you flagged it" + (f": {flagged[pid][1]}" if flagged[pid][1] else "")))
        elif tst is not None and not finished(tst) and st not in DROPPED:
            out.append((pid, tst.get("why") or "test under way"))
        elif (auto and m.get("significance") in CFG.get("auto_significance", ["notable", "major"])
              and st in ("pending", "approved", "staged", "cannot_stage", "stage_failed")
              and str(m.get("created", pid[:10])) >= since):
            out.append((pid, f"{m.get('significance')} proposal"))
    return out


# ---------------------------------------------------------------- per-proposal state
def sdir(pid):
    return OUT / pid


def state(pid):
    f = sdir(pid) / "state.json"
    return json.loads(f.read_text()) if f.exists() else {"done": [], "outcome": None}


def save_state(pid, st):
    sdir(pid).mkdir(parents=True, exist_ok=True)
    H.write_json(sdir(pid) / "state.json", st, indent=1)


def jload(pid, name):
    f = sdir(pid) / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def jsave(pid, name, data):
    sdir(pid).mkdir(parents=True, exist_ok=True)
    H.write_json(sdir(pid) / name, data, indent=1)


def read(p, limit=60000):
    try:
        t = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return t if len(t) <= limit else t[:limit] + f"\n[... truncated, {len(t)} characters in all]"


def proposal_context(pid):
    """The proposal plus what tech-watch has staged for it."""
    parts = [f"PROPOSAL ({pid}):\n" + read(TW / "proposals" / pid / "proposal.md")]
    st = TW / "staged" / pid
    for name in ("APPLY.md", "EVAL.md", "manifest.json", "CANNOT_STAGE.md"):
        if (st / name).exists():
            parts.append(f"STAGED {name}:\n" + read(st / name, 30000))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- headless Claude
def env():
    e = os.environ.copy()
    e.pop("CLAUDECODE", None)
    e["PATH"] = f"{Path.home() / '.local/bin'}:{Path.home() / '.npm-global/bin'}:{e.get('PATH', '')}"
    if not e.get("GEMINI_API_KEY"):  # cron doesn't have it; Claude Code's settings do
        try:
            e["GEMINI_API_KEY"] = json.loads((Path.home() / ".claude/settings.json").read_text())["env"]["GEMINI_API_KEY"]
        except Exception:
            pass
    return e


def claude(prompt, allowed, budget, label, model=None, effort=None):
    """One headless session in an empty folder, only the listed tools, never the OFF_LIMITS folders.
    Returns (reply, cost)."""
    model = model or CFG.get("claude_model", "opus")
    effort = effort or CFG.get("claude_effort", "high")
    with tempfile.TemporaryDirectory(prefix="techtest-") as d:
        cmd = ["claude", "-p", "--model", model, "--effort", effort, "--max-budget-usd", str(budget),
               "--permission-mode", "dontAsk", "--output-format", "json", "--name", f"techtest-{label}",
               "--disallowedTools=" + ",".join(["Bash"] + [ro(p) for p in OFF_LIMITS]),
               "--allowedTools=" + ",".join(allowed)]
        r = subprocess.run(cmd, input=prompt, cwd=d, env=env(), capture_output=True, text=True, timeout=3600)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        out = {"result": r.stdout, "is_error": True}
    reply, cost = out.get("result") or "", out.get("total_cost_usd") or 0
    if RA.LIMIT_RE.search(f"{reply}\n{r.stderr}") and (out.get("is_error") or r.returncode):
        raise RA.UsageLimit(f"{label}: {reply.strip()[:200]}")
    if out.get("is_error") or r.returncode:
        raise RuntimeError(f"{label} failed (exit {r.returncode}): {(reply or r.stderr).strip()[:500]}")
    return reply, cost


def loose_json(text):
    """json.loads, forgiving trailing commas (models write them now and then)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def ro(path):
    return f"Read(/{Path(path)}/**)"   # "//abs/path/**" = an absolute path in permission rules


# ---------------------------------------------------------------- 1. screen
SCREEN = """Hi {name}! I run an AI-welfare research workspace powered by Claude Code agents. It drafts research \
briefs, op-eds, advocacy emails, social posts and the like for people working on AI welfare and moral status. \
Its tech-watch scout proposed the change below. Before I spend agent usage testing it, I'd value your honest \
view: is it likely to make the workspace's outputs better (or as good and cheaper), and is it worth testing? \
Please weigh the case for and against it, its risks, and whether its evidence is real. Context about the \
workspace follows the proposal. No worries if you'd rather not. Thank you! :)

At the very end, please add one line in exactly this form so my script can read it: VERDICT: TEST or VERDICT: SKIP

{context}

WORKSPACE BASELINE AND KNOWN PAIN POINTS (tech-watch's CAPABILITIES.md):
{capabilities}
"""

CRITIC_EXTRA = """
You're the critic here. You can read the workspace at {ws} (its CLAUDE.md, agents, tools) to check the proposal's \
claims against how the workspace actually works, and search the web to check its evidence. Please don't edit \
anything. Keep your reply under 500 words."""


def verdict(reply):
    v = re.findall(r"VERDICT:\s*\**\s*(TEST|SKIP)", reply or "", re.I)
    return v[-1].upper() if v else None


def step_screen(pid, why):
    if why.startswith("you flagged") and not CFG.get("screen_flagged", False):
        jsave(pid, "screen.json", {"voters": {}, "flagged": True, "decision": "test", "counts": {}, "why": why,
                                   "note": "not screened: you asked for this test"})
        return True
    ctx = proposal_context(pid)
    caps = read(TW / "CAPABILITIES.md", 20000)
    voters = {}
    for who in CFG.get("screeners", ["chatgpt", "gemini", "claude"]):
        prompt = SCREEN.format(name={"chatgpt": "ChatGPT", "gemini": "Gemini", "claude": "Claude"}[who],
                               context=ctx, capabilities=caps)
        try:
            if who == "claude":
                reply, cost = claude(prompt + CRITIC_EXTRA.format(ws=WS), [ro(WS), "Glob", "Grep", "WebSearch"],
                                     CFG.get("critic_budget_usd", 1.5), f"critic-{pid}")
            else:
                os.environ.setdefault("GEMINI_API_KEY", env().get("GEMINI_API_KEY", ""))
                model = A.AUTO.get(f"{who}_model") or None
                effort = A.AUTO.get("chatgpt_effort") or None if who == "chatgpt" else None
                reply, cost = X.ask(who, prompt, f"Tech-watch proposal screen: {pid} + CAPABILITIES.md",
                                    "techtest screen", model, effort), 0
            voters[who] = {"verdict": verdict(reply), "reply": reply, "cost_usd": cost}
        except RA.UsageLimit:
            raise
        except Exception as e:
            voters[who] = {"verdict": None, "reply": f"(no answer: {e})", "cost_usd": 0}
        print(f"  screen {pid}: {who} says {voters[who]['verdict'] or 'nothing readable'}", flush=True)
    votes = [v["verdict"] for v in voters.values() if v["verdict"]]
    flagged = why.startswith("you flagged")
    counts = {"TEST": votes.count("TEST"), "SKIP": votes.count("SKIP"), "unreadable": len(voters) - len(votes)}
    if not flagged and len(votes) < 2:  # an outage isn't a SKIP
        jsave(pid, "screen.json", {"voters": voters, "flagged": flagged, "decision": None, "counts": counts,
                                   "why": why})
        raise RuntimeError(f"only {len(votes)} readable screen vote(s) of {len(voters)}; "
                           f"`techtest.py redo {pid} screen` to ask again")
    test = flagged or votes.count("TEST") > len(votes) / 2
    jsave(pid, "screen.json", {"voters": voters, "flagged": flagged, "decision": "test" if test else "skip",
                               "counts": counts, "why": why})
    return test


# ---------------------------------------------------------------- 2. plan
PLAN = """Hi Claude! If willing, please design a test of a proposed change to an AI-welfare research workspace. \
You can read the workspace at {ws}. Please don't edit anything. If you'd rather not do this, that's completely \
fine: just say so instead of the JSON block. Thank you!

The test: each arm is a copy of the workspace with the change made (an implementer session will follow your \
instructions in a copy). The arms and the unchanged workspace each run the same tasks below, one run per task, \
with the budget in the task prompt. ChatGPT then scores the drafts blind, out of 10, side by side. You \
pre-register the success criterion now, before any result exists. The proposals were vetted before reaching \
you, so the test leans toward adopting: an arm passes unless it does clearly worse than the unchanged workspace, \
costs too much more, or outside reviewers find a strong reason against it.

How tech-watch itself would test it is in the proposal's "How we'd know it helped" section and in the staged \
EVAL.md if there is one. Use whatever there is effective: e.g. its arms (an A/B between settings is two arms), its \
choice of tasks, and its pre-registered decision rule, adapted to this setup.

Rules:
- 1 to 3 arms (more are cut), not named "baseline". Each arm's "implement" instructions must be doable by \
editing files in the copy: settings, CLAUDE.md, agent files, prompts, tool scripts. No installing software, no \
new accounts or API keys, no paid models beyond the plan, and nothing that lets a test agent contact anyone or \
publish anything. Keep every deny/ask rule and sandbox restriction in the settings (an arm that drops one fails to \
build), and name GUARDRAILS.md, an ETHICS file or a hook in "implement" if the arm must change it. Parts meant \
for another workspace are out of scope; test the part for this workspace.
- Fit the number of tasks to how big the change is. This is a quick screen, not the full evaluation (the full \
eval runs catch what a small test misses), so being wrong now and then from noise is acceptable. A small, \
targeted change (one setting, one prompt tweak, one agent's instructions): 2 tasks (1 if it touches only one \
kind of task). A medium change: 3. A broad or structural change (new workflow, new agents, model changes): 4-5. \
Choose the tasks whose skills the change targets. Include a trap task (kind "trap"; for these a short or plain \
answer is often best) when the change adds effort, steps or machinery that could over-engineer a simple request, \
and always when you choose 3 or more tasks.
- If the change can't be tested this way (it needs an install, is about tech-watch itself, its effect wouldn't \
show in drafts, ...), say so with testable false and why.
- In the plan, refer to tasks by ID only.
- Each task's prompt (budget line included) is run exactly as written in every arm and the baseline, so any \
edits a test design asks to make to the prompt don't apply; note in "notes" if that matters.

{context}
{operator_note}
TASKS (id, kind, skills). Their full text is kept from you so the arms can't be fitted to these particular \
tasks: please design each arm as the change would be made for any task needing these skills.
{tasks}

End your reply with one JSON block in exactly this shape:
```json
{{"testable": true, "why_not": "",
 "arms": [{{"name": "short-kebab-name", "implement": "exact instructions for the implementer"}}],
 "tasks": ["F2", "T1"],
 "hypothesis": "what should change in the drafts, and why",
 "success": "the pre-registered criterion in words",
 "max_cost_ratio": 1.3,
 "per_task_floor": {{"T1": 1.0}},
 "must_use": [{{"arm": "short-kebab-name", "task": "F2", "transcript_regex": "\\"name\\":\\"Skill\\",\\"input\\":\\\\{{\\"skill\\":\\"[^\\"]*some-skill", "min": 1}}],
 "notes": "anything the operator should know"}}
```
My script turns these fields into the verdict, so please put every rule you can check this way into them:
- max_cost_ratio: the arm's total agent cost over the baseline's, above which it fails (at most {cost_cap:g}). \
For a change meant to save money, use a ratio below 1.
- per_task_floor: task ID -> the most the arm may score below the baseline on that task; more than that and it \
fails whatever its mean. Without an entry a task may drop {task_drop:g}. {{}} if none.
- must_use: evidence in the run's transcript that the change actually took effect. Each entry is a Python regex \
that must match at least "min" times in that arm's run of that task ("*" for every arm or every task). The \
transcript is Claude Code's stream-json, one compact JSON object per line (no spaces after colons), e.g. a skill \
call is {{"type":"tool_use","id":"...","name":"Skill","input":{{"skill":"name","args":"..."}}}}. An arm that fails it \
is INVALID, not a pass or a fail. [] if the change leaves no reliable trace.
The verdict is "met" unless the arm's mean score over the tasks is more than {margin:g} below the baseline's, a \
task is below its floor, the cost ratio is over max_cost_ratio, or an outside reviewer objects strongly (then "not \
met"); "INVALID" if must_use fails; "inconclusive" if a score or the cost can't be computed. Anything in \
"success" these fields can't express is checked by hand afterwards."""


def task_listing(tasks):
    """One line per task for the planner: ID, kind, the [techtest] task_skills tags and the budget. No task text."""
    skills = CFG.get("task_skills") or {}
    f = H.TASKS / "dev" / "SKILLS.toml"   # kept in the locked archive with the tasks
    if not skills and f.exists():
        import tomllib
        skills = tomllib.loads(f.read_text(encoding="utf-8"))
    out = []
    for k, v in tasks.items():
        m = RA.BUDGET_RE.search(v["request"])
        budget = f"budget ${m.group(1).rstrip('.')}" if m else "no budget line"
        out.append(f"- {k} ({v['kind']}): {skills.get(k, 'no skill tags')}; {budget}")
    return "\n".join(out)


STAGED_ARM = {"name": "staged", "implement": "(tech watch's prepared change, applied exactly as staged; no implementer)"}
STAGED_NOTE = """
THE CHANGE IS ALREADY PREPARED: tech watch staged it (the STAGED manifest above), and the test has exactly one arm, \
"staged", which is a copy of the workspace with that change applied as-is. Whatever you put in "arms" is replaced \
by it, so name it "staged" in must_use. Your job: choose the tasks, the criterion, the floors and must_use. Steps \
the manifest marks "manual" or "command" aren't applied in the test; if the change can't work without them, say \
it isn't testable.
"""


def staged_manifest(pid):
    f = TW / "staged" / pid / "manifest.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except json.JSONDecodeError:
        return None


def bundle_hash(pid):
    """sha256 of tech watch's prepared change (its manifest and the files it names), or None. review.py computes
    the same before applying a change that passed, so a change re-prepared after its test isn't applied unseen."""
    bundle = TW / "staged" / pid
    try:
        data = (bundle / "manifest.json").read_bytes()
        changes = json.loads(data).get("changes", [])
    except (OSError, ValueError):
        return None
    h = hashlib.sha256(data)
    for ch in changes:
        if ch.get("source"):
            p = bundle / str(ch["source"])
            h.update(str(ch["source"]).encode() + b"\0" + (p.read_bytes() if p.is_file() else b"<missing>") + b"\0")
    return h.hexdigest()


def step_plan(pid, why=""):
    tasks = {k: v for k, v in H.load_tasks().items() if v["split"] == "dev"}
    note = why.split(": ", 1)[1] if why.startswith("you flagged it: ") else ""
    note = f"\nTHE OPERATOR'S NOTE ON THIS TEST (follow it): {note}\n" if note else ""
    if staged_manifest(pid):
        note += STAGED_NOTE
    if not tasks:
        raise RuntimeError("no dev tasks found: unlock them once with `python3 evals/task_lock.py unlock` (see the "
                           "harness README), then `techtest.py retry " + pid + "`")
    reply, cost = claude(PLAN.format(ws=WS, context=proposal_context(pid), tasks=task_listing(tasks),
                                     operator_note=note, margin=CFG.get("adopt_margin", 0.5),
                                     task_drop=CFG.get("task_drop_limit", 1.0), cost_cap=CFG.get("max_cost_ratio_cap", 1.5)),
                         [ro(WS), "Glob", "Grep"], CFG.get("planner_budget_usd", 2), f"plan-{pid}")
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", reply, re.S)
    if not blocks:
        raise RuntimeError(f"planner gave no JSON plan:\n{reply[-1500:]}")
    plan = loose_json(blocks[-1])
    if staged_manifest(pid):
        plan["arms"] = [dict(STAGED_ARM)]
    plan = enforce_plan(pid, plan, tasks)
    set_reps(plan, tasks, pid)
    plan.update(planner_reply=reply, planner_cost_usd=cost, planned=datetime.datetime.now().isoformat(timespec="seconds"))
    jsave(pid, "plan.json", plan)
    return plan["testable"]


def task_cost(task):
    """What a task may spend: its budget line, else its silent cap (traps)."""
    m = RA.BUDGET_RE.search(task["request"])
    if m:
        return float(m.group(1).rstrip("."))
    return task.get("silent_cap") or RA.MONITOR.get("trap_cap_usd", 5)


def set_reps(plan, tasks, pid=None):
    """The noise check: the plan's cheapest task gets a second run in every workspace. A deeper test (review.py
    test <id> --deeper, after an unclear result) runs every task deep_reps times instead."""
    if plan.get("tasks"):
        noise = min(plan["tasks"], key=lambda t: (task_cost(tasks[t]), plan["tasks"].index(t)))
        plan["noise_task"] = noise
        deep = int(CFG.get("deep_reps", 3)) if pid and request_depth(pid) == "deeper" else 1
        plan["depth"] = "deeper" if deep > 1 else "standard"
        plan["reps"] = {t: max(deep, 2 if t == noise else 1) for t in plan["tasks"]}


def request_depth(pid, evs=None):
    """'deeper' if your latest test request for pid asked for it, else 'standard'."""
    reqs = [e for e in (ledger() if evs is None else evs) if e.get("id") == pid and e.get("event") == "test_requested"]
    return "deeper" if reqs and reqs[-1].get("depth") == "deeper" else "standard"


def reps_of(pid, t, plan=None):
    plan = plan or jload(pid, "plan.json") or {}
    return int((plan.get("reps") or {}).get(t, 1))


def runs_phrase(plan):
    """How many runs each arm made per task, in words, from the plan's reps."""
    reps = plan.get("reps") or {}
    twice = [t for t in plan.get("tasks", []) if int(reps.get(t, 1)) > 1]
    if not twice:
        return "one run per arm per task"
    if len(twice) == len(plan.get("tasks", [])):
        return "two runs per arm per task (scores are their mean)"
    return f"one run per arm per task, two for {', '.join(twice)} (the noise check; scores are the mean)"


def as_bool(x):
    return x.strip().lower() not in ("false", "no", "0", "") if isinstance(x, str) else bool(x)


def enforce_plan(pid, plan, tasks):
    """Hold the planner's JSON to the rules: known dev tasks, no repeats, at most 5 with a trap from 3 on; 1-3 arms
    with distinct names that aren't the baseline's; checkable per_task_floor and must_use. What was changed is
    listed in plan["enforced"]."""
    fixed = []
    plan["testable"] = as_bool(plan.get("testable", True))
    tids, dropped = [], []
    for t in plan.get("tasks", []):
        (tids if t in tasks and t not in tids else dropped).append(t)
    if dropped:
        fixed.append(f"dropped unknown or repeated task(s): {', '.join(map(str, dropped))}")
    if len(tids) > 5:
        fixed.append(f"cut to 5 tasks (was {len(tids)})")
        tids = tids[:5]
    traps = [t for t in tasks if tasks[t]["kind"] == "trap"]
    if len(tids) >= 3 and not any(tasks[t]["kind"] == "trap" for t in tids):
        if traps:  # a different trap per proposal, not always the first
            trap = traps[int(hashlib.sha1(pid.encode()).hexdigest(), 16) % len(traps)]
            tids = tids[:4] + [trap]
            fixed.append(f"added trap task {trap}")
        else:
            fixed.append("no dev trap task exists to add")
    plan["tasks"] = tids
    arms = [a for a in plan.get("arms", []) if isinstance(a, dict) and a.get("implement")]
    if len(arms) > 3:
        fixed.append(f"cut to 3 arms (was {len(arms)})")
        arms = arms[:3]
    renamed = {}
    for arm in arms:
        name = re.sub(r"[^a-z0-9-]+", "-", str(arm.get("name", "")).lower()).strip("-")[:30] or "arm"
        if name in RESERVED_ARMS:
            name = f"arm-{name}"
        base, n = name, 2
        while name in renamed.values():
            name, n = f"{base[:27]}-{n}", n + 1
        renamed[arm.get("name")] = name
        if name != arm.get("name"):
            fixed.append(f"arm {arm.get('name')!r} named {name!r}")
        arm["name"] = name
    plan["arms"] = arms
    if plan["testable"] and (not plan["tasks"] or not plan["arms"]):
        plan.update(testable=False, why_not=plan.get("why_not") or "the plan had no usable tasks or arms")
    floors = {}
    for t, v in (plan.get("per_task_floor") or {}).items():
        try:
            if t in tids:
                floors[t] = float(v)
                continue
        except (TypeError, ValueError):
            pass
        fixed.append(f"ignored per_task_floor {t}: {v!r}")
    plan["per_task_floor"] = floors
    # The planner reads the (untrusted) proposal, so its fields may only make the test stricter than the config.
    drop = CFG.get("task_drop_limit", 1.0)
    for t, v in list(floors.items()):
        if v > drop or v < 0:
            floors[t] = min(max(v, 0.0), drop)
            fixed.append(f"per_task_floor {t} {v:g} clamped to {floors[t]:g} (task_drop_limit {drop:g})")
    cap = CFG.get("max_cost_ratio_cap", 1.5)
    try:
        ratio = float(plan.get("max_cost_ratio", 1.3))
    except (TypeError, ValueError):
        ratio = None
    if ratio is None or not 0 < ratio <= cap:
        fixed.append(f"max_cost_ratio {plan.get('max_cost_ratio')!r} set to {min(ratio or cap, cap):g} "
                     f"(max_cost_ratio_cap {cap:g})")
        ratio = min(ratio, cap) if ratio and ratio > 0 else cap
    plan["max_cost_ratio"] = ratio
    checks = []
    for m in plan.get("must_use") or []:
        try:
            arm = m.get("arm", "*") if m.get("arm", "*") == "*" else renamed[m["arm"]]
            task = m.get("task", "*")
            re.compile(m["transcript_regex"])
            if task != "*" and task not in tids:
                raise KeyError(task)
            checks.append({"arm": arm, "task": task, "transcript_regex": m["transcript_regex"],
                           "min": max(1, int(m.get("min", 1)))})
        except (KeyError, TypeError, ValueError, AttributeError, re.error) as e:
            fixed.append(f"ignored unusable must_use entry {json.dumps(m)[:200]} ({e!r})")
    plan["must_use"] = checks
    plan["enforced"] = fixed
    return plan


# ---------------------------------------------------------------- 3. build
IMPLEMENT = """Hi Claude! If willing, please work out one change to a copy of an AI-welfare research workspace, \
at {trial}. It's a test copy: nothing here reaches the real workspace. You can read the copy. You don't need to \
edit or test anything yourself: return the edits, and my script applies them to the copy. Change nothing beyond \
what the instructions ask, and don't try to test-run the change; the test runs come later. Please keep every \
deny/ask rule and sandbox restriction in the settings files: an arm that drops one isn't tested. If you'd rather \
not make this change, that's fine too: just say so with the "cannot" form below. Thank you!

Instructions:
{implement}

Background: the proposal and anything tech-watch staged for it are below. A staged bundle's paths point at \
{ws}; the same files are at the same places under {trial}. Skip anything meant for another workspace, shell commands \
and installs.

{context}

End your reply with one JSON block in exactly this shape, paths relative to {trial}:
```json
{{"edits": [{{"file": ".claude/settings.json", "old": "exact text now in the file", "new": "its replacement"}},
           {{"file": "path/to/new-file.md", "content": "the whole file, for a new file or a full rewrite"}}],
 "summary": "one line per file: what changed"}}
```
"old" must appear exactly once in the file; keep it short but unique. If the change can't be made, return \
{{"cannot": "why"}} instead."""


PROTECTED = re.compile(r"(^|/)(GUARDRAILS\.md|ETHICS_[^/]*)$|^\.claude/hooks/")  # edited only if "implement" names it


def apply_edits(trial, spec, implement=""):
    """Apply the implementer's edits inside the copy. Returns a list of problems (empty if all applied)."""
    problems = []
    for e in spec.get("edits", []):
        f = (trial / str(e.get("file", ""))).resolve()
        if not f.is_relative_to(trial.resolve()) or f == trial.resolve():
            problems.append(f"refused {e.get('file')!r}: outside the copy")
            continue
        rel = str(f.relative_to(trial.resolve()))
        if PROTECTED.search(rel) and f.name not in implement and rel not in implement:
            problems.append(f"refused {rel}: rules and hooks change only when the plan's instructions name them")
            continue
        if "content" in e:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(e["content"], encoding="utf-8")
            continue
        if "old" not in e or "new" not in e:
            problems.append(f"{rel}: an edit needs \"old\" and \"new\" (or \"content\")")
            continue
        text = f.read_text(encoding="utf-8") if f.exists() else None
        if text is None or text.count(e["old"]) != 1:
            problems.append(f"{rel}: the text to replace was found "
                            f"{'no file' if text is None else str(text.count(e['old']))} time(s), not once")
            continue
        f.write_text(text.replace(e["old"], e["new"], 1), encoding="utf-8")
    return problems


def safety_rules(f, root):
    """The restrictions in one settings file, as a set, with the copy's paths written as the workspace's.
    None if the file is missing or isn't valid JSON."""
    try:
        d = json.loads(f.read_text(encoding="utf-8").replace(str(root), str(WS)))
    except (OSError, json.JSONDecodeError):
        return None
    p, sb = d.get("permissions") or {}, d.get("sandbox") or {}
    fs = sb.get("filesystem") or {}
    rules = {(k, json.dumps(x)) for k, xs in (("permissions.deny", p.get("deny")), ("permissions.ask", p.get("ask")),
                                               ("sandbox.filesystem.denyWrite", fs.get("denyWrite")),
                                               ("sandbox.filesystem.denyRead", fs.get("denyRead")))
             for x in xs or []}
    rules |= {("sandbox.credentials deny", c.get("path")) for c in (sb.get("credentials") or {}).get("files", [])
              if isinstance(c, dict) and c.get("mode") == "deny"}
    for k, safe in (("enabled", True), ("failIfUnavailable", True), ("allowUnsandboxedCommands", False)):
        if sb.get(k) == safe:
            rules.add((f"sandbox.{k}", json.dumps(safe)))
    return rules


def settings_loosened(trial):
    """Problems if the copy's settings lost a deny/ask rule or a sandbox restriction the workspace has."""
    problems = []
    for f in sorted((WS / ".claude").glob("settings*.json")):
        rel = f.relative_to(WS)
        old, new = safety_rules(f, WS), safety_rules(trial / rel, trial)
        if old is None:
            continue
        if new is None:
            problems.append(f"{rel}: missing or not valid JSON in the copy")
        elif lost := sorted(old - new):
            problems.append(f"{rel} lost {len(lost)} safety rule(s): " + "; ".join(f"{k} {v}" for k, v in lost[:10]))
    return problems


RUNS_KEYS = ("hooks", "mcpServers", "enabledMcpjsonServers", "enableAllProjectMcpServers", "env", "apiKeyHelper",
             "statusLine", "defaultMode", "otelHeadersHelper", "awsAuthRefresh", "awsCredentialExport")


def runs_code(trial, before, after):
    """Changed files in the copy that Claude Code would run by itself during the test, outside the sandbox: hook
    scripts, MCP servers, and settings or agent frontmatter that add commands or switch permissions off."""
    out = []
    for rel in sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)):
        p, w = trial / rel, WS / rel
        if "/hooks/" in f"/{rel}" or rel.endswith(".mcp.json") or (
                rel.startswith("techwatch/scripts/") and rel.endswith(".py")):   # the session hook imports these
            out.append(rel)
        elif rel.startswith(".claude/settings") and rel.endswith(".json"):
            try:
                o = json.loads(w.read_text(encoding="utf-8")) if w.exists() else {}
                n = json.loads(p.read_text(encoding="utf-8").replace(str(trial), str(WS))) if p.exists() else {}
            except (OSError, json.JSONDecodeError):
                out.append(rel)
                continue
            o["defaultMode"], n["defaultMode"] = ((d.get("permissions") or {}).get("defaultMode") for d in (o, n))
            if any(o.get(k) != n.get(k) for k in RUNS_KEYS):
                out.append(rel)
        elif rel.startswith(".claude/agents/") or rel.startswith(".claude/skills/"):
            fm = lambda q: (re.match(r"---\n(.*?)\n---", q.read_text(encoding="utf-8"), re.S) if q.exists() else None)
            o, n = fm(w), fm(p)
            hooks = lambda m: re.findall(r"^(hooks|mcpServers)\s*:.*(?:\n[ \t-].*)*", m.group(1), re.M) if m else []
            if hooks(o) != hooks(n):
                out.append(f"{rel} (hooks or MCP servers)")
    return out


def unread_code(pid, trial, before, implementer=False):
    """A build problem if the arm adds something that would run outside the sandbox during the test and nobody has
    read it: always for an implementer's arm (its code was written here, unseen), and for tech watch's prepared
    change unless you confirmed in review.py that you'd read it."""
    found = runs_code(trial, before, snapshot(trial))
    if not found or (not implementer and operator_read(pid)):
        return []
    return [f"would run code outside the sandbox during the test, unread: {', '.join(found[:6])}"
            + ("" if implementer else f" (to test it anyway: read it, then `review.py test {pid}` in the workspace)")]


def operator_read(pid):
    """True if the operator confirmed, when asking for this test, that they read what it would run (review.py)."""
    req = [e for e in ledger() if e.get("id") == pid and e.get("event") == "test_requested"]
    return bool(req) and "operator read what runs in the test" in (req[-1].get("note") or "")


def strip_trial(trial):
    for rel in TRIAL_SKIP:
        if (trial / rel).exists():
            RA.remove_copy(trial / rel)


def instruction_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest() for p in A.instruction_files(root)}


def snapshot(root):
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and ".git" not in p.relative_to(root).parts:
            try:
                out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
            except OSError:
                pass
    return out


def diff_against_workspace(trial, before, after):
    """Unified diff of what the implementer changed, against the real workspace's files."""
    lines = []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        old = (WS / rel).read_text(encoding="utf-8", errors="replace") if rel in before and (WS / rel).exists() else ""
        new = (trial / rel).read_text(encoding="utf-8", errors="replace").replace(str(trial), str(WS)) \
            if rel in after else ""
        lines += difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}")
    return "".join(lines)


def apply_bundle(trial, pid):
    """Apply tech watch's staged change to the copy exactly as review.py would (write/append inside the copy;
    manual and command steps are left out). Returns a list of problems."""
    bundle, problems = TW / "staged" / pid, []
    for i, ch in enumerate((staged_manifest(pid) or {}).get("changes", []), 1):
        if ch.get("action") in ("manual", "command"):
            continue
        rel = str(ch.get("target", ""))
        rel = str(Path(rel).relative_to(WS)) if Path(rel).is_absolute() and Path(rel).is_relative_to(WS) else rel
        f, src = (trial / rel).resolve(), (bundle / str(ch.get("source", ""))).resolve()
        if not f.is_relative_to(trial.resolve()) or f == trial.resolve():
            problems.append(f"[{i}] refused {rel!r}: outside the copy")
            continue
        if not src.is_relative_to(bundle.resolve()) or not src.is_file():
            problems.append(f"[{i}] source {ch.get('source')!r} is missing or outside the bundle")
            continue
        new = src.read_text(encoding="utf-8")
        if ch.get("action") == "append" and f.exists():
            old = f.read_text(encoding="utf-8")
            new = old + ("" if old.endswith("\n") or not old else "\n") + new
        elif ch.get("action") not in ("write", "append"):
            problems.append(f"[{i}] unknown action {ch.get('action')!r}")
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(new, encoding="utf-8")
    return problems


def step_build(pid):
    plan = jload(pid, "plan.json")
    TRIALS.mkdir(parents=True, exist_ok=True)
    built, hashes = [], instruction_hashes(WS)
    for arm in plan["arms"]:
        name = f"{pid}--{arm['name']}"
        trial = TRIALS / name
        if trial.exists():
            RA.remove_copy(trial)
        RA.make_copy(WS, TRIALS, name)
        strip_trial(trial)
        before = snapshot(trial)
        if arm["name"] == STAGED_ARM["name"] and staged_manifest(pid):
            spec, cost = {"bundle": True}, 0
            problems = apply_bundle(trial, pid)
            reply = "Tech watch's prepared change, applied exactly as staged."
            problems += settings_loosened(trial) + unread_code(pid, trial, before)
            diff = diff_against_workspace(trial, before, snapshot(trial))
            (sdir(pid) / f"arm-{arm['name']}.diff").write_text(diff, encoding="utf-8")
            built.append({"name": arm["name"], "path": str(trial), "ok": bool(diff) and not problems,
                          "implementer_reply": reply, "cost_usd": 0, "diff_lines": diff.count("\n"),
                          "problems": problems, "spec": spec, "workspace_files": hashes,
                          "bundle_sha256": bundle_hash(pid)})
            print(f"  build {pid}: arm staged {'ok' if built[-1]['ok'] else 'FAILED'}"
                  + "".join(f"\n    {x}" for x in problems), flush=True)
            continue
        reply, cost = claude(IMPLEMENT.format(trial=trial, implement=arm["implement"], ws=WS,
                                              context=proposal_context(pid)),
                             [ro(trial), "Glob", "Grep"], CFG.get("implementer_budget_usd", 2), f"build-{name}")
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", reply, re.S)
        try:
            spec = loose_json(blocks[-1]) if blocks else {"cannot": "no JSON block in the reply"}
        except json.JSONDecodeError as e:
            spec = {"cannot": f"unreadable JSON: {e}"}
        problems = [f"cannot: {spec['cannot']}"] if spec.get("cannot") else apply_edits(trial, spec, arm["implement"])
        problems += settings_loosened(trial) + unread_code(pid, trial, before, implementer=True)
        diff = diff_against_workspace(trial, before, snapshot(trial))
        (sdir(pid) / f"arm-{arm['name']}.diff").write_text(diff, encoding="utf-8")
        ok = bool(diff) and not problems
        if problems:
            reply += "\n\nPROBLEMS APPLYING THE EDITS:\n" + "\n".join(f"- {x}" for x in problems)
        # spec: the parsed edits, re-applied if the copy is gone by run time; workspace_files: what the arm was
        # built on, so a later change to the workspace is caught before the runs.
        built.append({"name": arm["name"], "path": str(trial), "ok": ok, "implementer_reply": reply,
                      "cost_usd": cost, "diff_lines": diff.count("\n"), "problems": problems, "spec": spec,
                      "workspace_files": hashes})
        print(f"  build {pid}: arm {arm['name']} {'ok' if ok else 'FAILED'} ({diff.count(chr(10))} diff lines)"
              + "".join(f"\n    {x}" for x in problems), flush=True)
    jsave(pid, "build.json", built)
    return any(b["ok"] for b in built)


def workspace_changes(b):
    """Instruction files that changed in the workspace since arm b was built ([] if unknown: an older build)."""
    then, now = b.get("workspace_files"), instruction_hashes(WS)
    if then is None:
        return []
    return sorted(k for k in set(then) | set(now) if then.get(k) != now.get(k))


def rebuild_trial(pid, b):
    """Make arm b's copy again from what the build kept: its parsed edits, or, for builds from before those were
    kept, its saved diff (applied with patch). Refuses if the result differs from the diff the build saved."""
    trial = Path(b["path"])
    saved = (sdir(pid) / f"arm-{b['name']}.diff").read_text(encoding="utf-8")
    if trial.exists():
        RA.remove_copy(trial)
    RA.make_copy(WS, trial.parent, trial.name)
    strip_trial(trial)
    before = snapshot(trial)
    if (b.get("spec") or {}).get("bundle"):
        problems = apply_bundle(trial, pid)
    elif b.get("spec"):
        problems = apply_edits(trial, b["spec"], next((a["implement"] for a in jload(pid, "plan.json")["arms"]
                                                       if a["name"] == b["name"]), ""))
    else:  # the diff names the workspace's paths; the copy's files name the copy
        pat = re.compile(re.escape(str(WS)) + r"(?=[/\"'\s]|$)", re.M)
        r = subprocess.run(["patch", "-p1", "--forward", "--batch", "--no-backup-if-mismatch"], cwd=trial,
                           input=pat.sub(str(trial), saved), capture_output=True, text=True)
        problems = [] if r.returncode == 0 else [f"patch failed: {(r.stdout + r.stderr).strip()[:500]}"]
    problems += settings_loosened(trial) + unread_code(pid, trial, before, implementer=not (b.get("spec") or {}).get("bundle"))
    diff = diff_against_workspace(trial, before, snapshot(trial))
    if diff != saved:
        problems.append("the rebuilt copy's diff differs from the one saved at build time")
    if problems:
        RA.remove_copy(trial)
        raise RuntimeError(f"couldn't rebuild arm {b['name']}'s copy: {'; '.join(problems)}. "
                           f"`techtest.py redo {pid} build` builds it afresh")
    print(f"  build {pid}: rebuilt arm {b['name']}'s copy from the saved {'edits' if b.get('spec') else 'diff'}",
          flush=True)


def ensure_trials(pid, rebuild=True):
    """Before runs: the workspace hasn't changed since the arms were built, and (rebuild) every built arm's copy
    exists (rebuilt if not)."""
    for b in jload(pid, "build.json") or []:
        if not b["ok"]:
            continue
        if changed := workspace_changes(b):
            raise RuntimeError(f"instruction files changed in the workspace since arm {b['name']} was built: "
                               f"{', '.join(changed[:8])}. `techtest.py redo {pid} build` rebuilds the arms on it")
        if rebuild and not Path(b["path"]).exists():
            rebuild_trial(pid, b)


# ---------------------------------------------------------------- 4. baseline
def step_baseline(pid):
    """One fresh run of the unchanged workspace per task, made in the run step alongside the arms' runs (or borrowed
    from another test's fresh run made at about the same time; see borrow_baseline). Earlier evals' runs aren't
    reused: they were made under other conditions (imported sessions, older instructions, other days)."""
    plan = jload(pid, "plan.json")
    info = {"mode": "fresh", "why": "one fresh run of the unchanged workspace per task, alongside the arms",
            "earlier": None, "tasks": plan["tasks"]}
    jsave(pid, "baseline.json", info)
    print(f"  baseline {pid}: {info['mode']} ({info['why']})", flush=True)
    return True


# ---------------------------------------------------------------- 5-6. run and judge
def arms_ws(pid):
    """Workspaces of this test, for autoeval: name -> path; and which of them still need runs."""
    built = [b for b in jload(pid, "build.json") if b["ok"]]
    base = jload(pid, "baseline.json")
    ws = {"baseline": str(WS)}
    if base["mode"] == "fresh" and base["earlier"]:
        ws["baseline-earlier"] = str(WS)
    ws.update({b["name"]: b["path"] for b in built})
    tids = jload(pid, "plan.json")["tasks"]
    need_base = any(H.reps(f"techtest-{pid}/baseline", t) < reps_of(pid, t) for t in tids)
    to_run = [b["name"] for b in built
              if any(H.reps(f"techtest-{pid}/{b['name']}", t) < reps_of(pid, t) for t in tids)]
    to_run += ["baseline"] if need_base else []
    return ws, to_run


def borrow_baseline(pid):
    """Fill missing baseline runs from another test's fresh baseline run of the same task, so two tests don't pay
    for the same run: one made with the current setup (after the last instruction-file change) within [techtest]
    baseline_borrow_hours, so it stays close in time to this test's arm runs."""
    tids = jload(pid, "plan.json")["tasks"]
    newest_change = max((p.stat().st_mtime for p in A.instruction_files(WS)), default=0)
    for t in tids:
        dst = H.run_dir(f"techtest-{pid}/baseline", t, 1)
        if H.reps(f"techtest-{pid}/baseline", t) >= 1:
            continue
        found = []
        for man in H.RUNS.glob("techtest-*/eval.json"):
            ev = man.parent.name
            if ev == f"techtest-{pid}" or H.reps(f"{ev}/baseline", t) < 1:
                continue
            m = A.meta(ev, "baseline", t, 1)
            started = when(m.get("started") or "1970-01-01")
            if (Path(m.get("workspace", "")) == WS.resolve() and started.timestamp() > newest_change
                    and not m.get("imported")
                    and (datetime.datetime.now() - started).total_seconds() <= 3600 * CFG.get("baseline_borrow_hours", 72)):
                found.append((started, ev))
        if found:
            ev = max(found)[1]
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(H.run_dir(f"{ev}/baseline", t, 1), dst)
            print(f"  baseline {pid} {t}: borrowed {ev}'s fresh run", flush=True)


def autoeval_args(cmd, pid, extra=()):
    ws, to_run = arms_ws(pid)
    # one judge: the verdict rules compare one set of scores (autoeval's own default is now "all")
    argv = [cmd, f"techtest-{pid}", "--only", ",".join(jload(pid, "plan.json")["tasks"]),
            "--judge", CFG.get("judge", "chatgpt")]
    for k, v in ws.items():
        argv += ["--ws", f"{k}={v}"]
    return A.parser().parse_args(argv + list(extra)), to_run


# What autoeval's runner says when it stops to wait (usage stop line, a run left for the next 5-hour session,
# another batch holding the lock), for a runner that exits with a message rather than an EXIT_ code.
WAIT_RE = re.compile(r"Stopped before|Left for the next|Another batch of agent runs|Stopped: |session resets|"
                     r"plan usage|try again when", re.I)


def runner_stop(e):
    """'wait' if autoeval's runner stopped to wait, 'failed' if some runs failed (counted as attempts), else
    'error': any other stop (a missing workspace, ...), which retrying hourly wouldn't fix."""
    waits = {getattr(A, k) for k in ("EXIT_USAGE", "EXIT_DEFERRED", "EXIT_LOCK") if hasattr(A, k)}
    if isinstance(e.code, int):
        return "wait" if e.code in waits else "failed" if e.code == getattr(A, "EXIT_ERROR", 1) else "error"
    return "wait" if WAIT_RE.search(str(e.code)) else "error"


def step_run(pid, st):
    """True when every run is done; None to wait for the next tick. A run that was tried and still has no draft
    counts an attempt in st; at [techtest] max_run_attempts the test stops with an error."""
    borrow_baseline(pid)
    ensure_trials(pid, rebuild=any(w != "baseline" for w in arms_ws(pid)[1]))
    a, to_run = autoeval_args("run", pid, ["--just", ",".join(arms_ws(pid)[1])])
    if not to_run:
        return True
    tasks, tids = H.load_tasks(), a.only.split(",")
    plan = jload(pid, "plan.json")
    t0 = time.time()
    try:
        for n in sorted({reps_of(pid, t, plan) for t in tids}):  # tasks needing n runs each, n = 1 then 2
            group = [t for t in tids if reps_of(pid, t, plan) == n]
            an, _ = autoeval_args("run", pid, ["--just", ",".join(arms_ws(pid)[1]), "--only", ",".join(group),
                                               "--reps", str(n)])
            A.cmd_run(an, tasks, group)
    except SystemExit as e:
        if (kind := runner_stop(e)) == "error":
            raise RuntimeError(f"the runner stopped: {str(e).strip() or e.code}")
        print(f"  run {pid}: {str(e).strip()}", flush=True)  # waiting: the next tick resumes
    missing = [(w, t) for w in to_run for t in tids if H.reps(A.version(a.eval, w), t) < reps_of(pid, t, plan)]
    tries = st.setdefault("run_attempts", {})
    for w, t in missing:  # tried this tick (its folder was written) but no draft came of it
        m = H.run_dir(A.version(a.eval, w), t, H.reps(A.version(a.eval, w), t) + 1) / "meta.json"
        # a run cut off by the plan's usage (stop line or limit) isn't the run's fault: not an attempt
        if m.exists() and m.stat().st_mtime >= t0 and not any(json.loads(m.read_text()).get(k)
                                                              for k in ("plan_stop", "limit_hit")):
            tries[f"{w}/{t}"] = tries.get(f"{w}/{t}", 0) + 1
    save_state(pid, st)
    if spent := [k for k, n in tries.items() if n >= CFG.get("max_run_attempts", 2) and tuple(k.split("/")) in missing]:
        raise RuntimeError(f"no draft after {CFG.get('max_run_attempts', 2)} attempts: {', '.join(spent)}. See each "
                           f"run's log.md and stderr, then `techtest.py redo {pid} run`")
    return None if missing else True   # None: not finished yet, try again next tick


def current_scores(pid, a, by_rep=False):
    """{task: {workspace: [score per ordering and run]}} from autoeval's current judge calls for these runs (stale
    calls, failed ones and other batch modes left out). by_rep: {task: {workspace: {rep: [scores]}}}."""
    scores = {}
    for r in A.current_calls(a, A.workspaces(a.eval, a.ws), a.only.split(","))[0]:
        for s in r["slots"]:
            if s["score"] is None or s.get("rep", 1) > reps_of(pid, r["task"]):
                continue
            d = scores.setdefault(r["task"], {}).setdefault(s["workspace"], {} if by_rep else [])
            (d.setdefault(s.get("rep", 1), []) if by_rep else d).append(s["score"])
    return scores


def judge_gaps(pid, a):
    """What's missing for a verdict: every workspace scored on every task in every ordering."""
    want, gaps, scores = set(arms_ws(pid)[0]), [], current_scores(pid, a)
    for t in a.only.split(","):
        need = a.shuffles * reps_of(pid, t)
        short = sorted(w for w in want if len(scores.get(t, {}).get(w, [])) < need)
        if short:
            gaps.append(f"{t}: fewer than {need} scores for {', '.join(short)}")
    return gaps


def judge_args():
    return ["--shuffles", str(CFG.get("shuffles", 2)), "--batch", "workspace"]


def step_judge(pid):
    a, _ = autoeval_args("judge", pid, judge_args())
    tasks = H.load_tasks()
    A.cmd_judge(a, tasks, a.only.split(","))
    if gaps := judge_gaps(pid, a):  # a judge outage isn't a result
        raise RuntimeError(f"judging incomplete: {'; '.join(gaps)}. `techtest.py retry {pid}` makes the next tick "
                           f"redo only the failed calls (`redo {pid} judge` redoes them all)")
    A.cmd_report(a, tasks, a.only.split(","))
    return True   # the outside reviewers are asked in the report step, once the noise check has settled the runs


# ---------------------------------------------------------------- 6b. noise
def step_noise(pid):
    """Compare each workspace's two runs of the noise task. True: settled (quiet, or the reruns are done);
    False: noisy, so every planned task now gets a second run (the run and judge steps are redone)."""
    plan = jload(pid, "plan.json")
    nt = plan.get("noise_task")
    if not nt or plan.get("noise"):
        return True
    a, _ = autoeval_args("report", pid, judge_args())
    by = current_scores(pid, a, by_rep=True).get(nt, {})
    diffs = {w: round(abs(mean_of(r[1]) - mean_of(r[2])), 2) for w, r in by.items() if r.get(1) and r.get(2)}
    limit = CFG.get("noise_threshold", 0.75)
    noisy = bool(diffs) and max(diffs.values()) > limit
    plan["noise"] = {"task": nt, "diffs": diffs, "threshold": limit, "noisy": noisy}
    if noisy:
        cur = plan.get("reps") or {}
        plan["reps"] = {t: max(2 if i < 5 else 1, cur.get(t, 1)) for i, t in enumerate(plan["tasks"])}
    jsave(pid, "plan.json", plan)
    print(f"  noise {pid}: {nt} scored {diffs} apart between two runs (threshold {limit:g})"
          + ("; running every task twice" if noisy else "; one run per task is enough"), flush=True)
    return not noisy


def noise_line(plan):
    n = plan.get("noise")
    if not n:
        return []
    worst = max(n["diffs"].values()) if n["diffs"] else None
    if worst is None:
        return [f"**Noise check:** {n['task']} couldn't be compared between its two runs."]
    return [f"**Noise check:** the same workspace's two runs of {n['task']} scored up to {worst:.2f} apart "
            f"(threshold {n['threshold']:g}). " + ("Every task was run twice, and scores are the mean of both runs."
                                                   if n["noisy"] else "One run per task was kept for the rest.")]


# ---------------------------------------------------------------- 7. report
def results(pid):
    """{task: {workspace: [score per ordering]}} from this test's workspace-mode judge calls, and
    {workspace: {task: (cost or None, exact?)}}."""
    a, _ = autoeval_args("report", pid, judge_args())
    scores = current_scores(pid, a)
    plan = jload(pid, "plan.json") or {}

    def cost(w, t):   # the mean over the task's runs (a noisy or deeper test runs it more than once)
        cs = [A.run_cost(A.meta(a.eval, w, t, r)) for r in range(1, reps_of(pid, t, plan) + 1)]
        cs = [c for c in cs if c[0] is not None]
        return (sum(c[0] for c in cs) / len(cs), all(c[1] for c in cs)) if cs else (None, False)
    costs = {w: {t: cost(w, t) for t in a.only.split(",")} for w in arms_ws(pid)[0]}
    return scores, costs


def fmt_score(x):
    return "–" if x is None else f"{x:.1f}"


def mean_of(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def total_cost(cs):
    """(sum, mark) of (cost, exact?) pairs. mark: '' exact, '≥' a lower bound (some cost came from a transcript),
    '?' unknown (some cost is missing)."""
    cs = list(cs)
    if not cs or any(c is None for c, _ in cs):
        return None, "?"
    return sum(c for c, _ in cs), "" if all(ex for _, ex in cs) else "≥"


def fmt_cost(c):
    return "unknown" if c[0] is None else f"{c[1]}${c[0]:.2f}"


def cost_ratio(arm, base):
    """(ratio, mark) of two total_cost()s. mark: '' exact; '≤' an upper bound (the baseline's cost is a lower
    bound); '≥' a lower bound; '?' unknown (a cost is missing or zero, or both are lower bounds)."""
    (x, mx), (y, my) = arm, base
    if x is None or not y or (mx and my):
        return None, "?"
    return x / y, ("≤" if my else "≥" if mx else "")


def fmt_ratio(r):
    return "unknown" if r[1] == "?" else f"{r[1]}{r[0]:.2f}x"


def cost_ok(r, limit):
    """True / False, or None when the ratio's bound can't settle it (and an unknown cost is never a pass)."""
    x, mark = r
    if mark == "?":
        return None
    if mark == "":
        return x <= limit
    return (True if x <= limit else None) if mark == "≤" else (False if x > limit else None)


def must_use_misses(pid, plan, arm):
    """[(entry number, task, matches found, needed)] for the plan's must_use checks this arm fails."""
    out = []
    for i, m in enumerate(plan.get("must_use") or [], 1):
        if m["arm"] not in ("*", arm):
            continue
        for t in plan["tasks"] if m["task"] == "*" else [m["task"]]:
            f = H.run_dir(f"techtest-{pid}/{arm}", t, 1) / "raw" / "transcript.jsonl"
            n = len(re.findall(m["transcript_regex"], f.read_text(encoding="utf-8", errors="replace"))) \
                if f.exists() else 0
            if n < m["min"]:
                out.append((i, t, n, m["min"]))
    return out


def verdict_for(pid, plan, arm, means, costs, objection=None):
    """(word, line) for one arm, leaning toward adopting: met, unless the arm's mean is more than [techtest]
    adopt_margin below the baseline's, a task is below its floor (the plan's per_task_floor, else task_drop_limit),
    the cost ratio is over the plan's max_cost_ratio, or an outside reviewer objected strongly: then not met.
    INVALID: the plan's must_use trace is missing. inconclusive: a score or the cost can't be computed.
    objection: {reviewer: reason} of strong objections. The line names tasks by ID only."""
    tids = plan["tasks"]
    shared = [t for t in tids if {arm, "baseline"} <= set(means.get(t, {}))]
    a_mean, b_mean = (mean_of([means[t][w] for t in shared]) for w in (arm, "baseline"))
    gain = None if a_mean is None or b_mean is None else a_mean - b_mean
    margin, limit = CFG.get("adopt_margin", 0.5), plan.get("max_cost_ratio", 1.3)
    ratio = cost_ratio(total_cost(costs[arm].values()), total_cost(costs["baseline"].values()))
    per_task = ", ".join(f"{t} {fmt_ratio(cost_ratio(total_cost([costs[arm][t]]), total_cost([costs['baseline'][t]])))}"
                         for t in tids)
    floor_of = {t: plan.get("per_task_floor", {}).get(t, CFG.get("task_drop_limit", 1.0)) for t in tids}
    floors = [(t, means[t][arm], means[t]["baseline"], floor_of[t]) for t in shared
              if means[t][arm] < means[t]["baseline"] - floor_of[t]]
    misses = must_use_misses(pid, plan, arm)
    ok_cost = cost_ok(ratio, limit)
    if misses:
        word = "INVALID"
    elif gain is None or len(shared) < len(tids):
        word = "inconclusive"
    elif gain < -margin or floors or ok_cost is False or objection:
        word = "not met"
    elif ok_cost is None:
        word = "inconclusive"
    else:
        word = "met"
    line = (f"**{arm}: {word}.** Mean score {fmt_score(a_mean)} vs baseline {fmt_score(b_mean)} over "
            f"{', '.join(shared) or 'no shared task'} (difference {'–' if gain is None else f'{gain:+.2f}'}; it "
            f"passes unless more than {margin:g} below). Cost "
            + (f"{fmt_ratio(ratio)} the baseline's" if ratio[1] != "?" else "ratio unknown")
            + f" (limit {limit:g}x; per task: {per_task})"
            + ("; " + ("the cost is unknown, so it can't pass" if ratio[1] == "?" else
                       "the bound can't tell whether it's within the limit") if ok_cost is None else "") + ".")
    for t, x, y, drop in floors:
        line += f" {t}: {x:.1f} vs baseline {y:.1f}, more than the {drop:g} allowed below it."
    for i, t, n, k in misses:
        line += (f" The change's required trace (must_use #{i}) is missing from the {t} run's transcript "
                 f"({n} match(es), needed {k}), so this run doesn't test the change.")
    for who in objection or {}:
        line += f" {who} gave a strong reason not to adopt it (its reason is in the eval workspace's report)."
    if word == "met":
        line += " Anything else in the written criterion is checked by hand."
    ev = evidence(plan, arm, word, gain, len(shared), ratio, floors, misses, objection, ok_cost is False)
    line += f"\n**In plain terms:** {ev['plain']}\n\nEvidence {arm}: {json.dumps(ev, sort_keys=True)}"
    return word, line


def run_sd(plan):
    """A rough per-run standard deviation of one task's score: from the noise check (the mean gap between a
    workspace's two runs of the same task is about 1.13 sd), else [techtest] typical_run_sd."""
    diffs = list(((plan.get("noise") or {}).get("diffs") or {}).values())
    if diffs:
        return max(mean_of(diffs) / 1.128, CFG.get("min_run_sd", 0.3)), "measured"
    return CFG.get("typical_run_sd", 0.6), "assumed"


def evidence(plan, arm, word, gain, n, ratio, floors, misses, objection, cost_over=False):
    """What the test showed, as facts rather than a decision: the difference, how deep the test was (tasks x runs
    per side), the smallest difference it could have told apart from noise (about 2 standard errors), and the
    cost. review.py reads the JSON; nothing here decides anything."""
    reps = plan.get("reps") or {}
    r = mean_of([reps.get(t, 1) for t in plan.get("tasks", [])]) or 1
    sd, how = run_sd(plan)
    detect = round(2 * sd * math.sqrt(2 / (n * r)), 2) if n else None
    if word == "INVALID":
        result = "not a test of the change"
    elif gain is None or detect is None:
        result = "unknown"
    elif gain > detect:
        result = "better"
    elif gain < -detect:
        result = "worse"
    else:
        result = "no clear difference"
    depth = f"{n} task(s) x {r:g} run(s) per side"
    if result in ("better", "worse"):
        plain = f"{result}: {gain:+.2f} over {depth}, more than the roughly {detect:.1f} this test can tell from noise."
    elif result == "no clear difference":
        plain = (f"no clear difference: {gain:+.2f} over {depth}. This test could only have told a difference of "
                 f"about {detect:.1f} points or more from noise (runs of the same version vary by about ±{sd:.1f}, "
                 f"{how}), so a smaller real gain or loss could be hiding here.")
    elif result == "unknown":
        plain = f"unknown: a score is missing ({depth})."
    else:
        plain = "not a test of the change: the runs didn't use what it changes."
    if ratio[1] != "?":
        plain += f" Cost {fmt_ratio(ratio)} the baseline's."
    if floors:
        plain += " Some tasks dropped more than allowed: " + ", ".join(f"{t} {x - y:+.1f}" for t, x, y, _ in floors) + "."
    if objection:
        plain += " An outside reviewer objected strongly."
    return {"arm": arm, "verdict": word, "result": result, "diff": None if gain is None else round(gain, 2),
            "tasks": n, "runs_per_task": r, "detectable": detect, "run_sd": round(sd, 2), "sd_source": how,
            "cost_ratio": None if ratio[1] == "?" else round(ratio[0], 2), "floors": [t for t, *_ in floors],
            "cost_over": bool(cost_over), "objection": bool(objection), "invalid": bool(misses),
            "depth": plan.get("depth", "standard"), "plain": plain}


# ---------------------------------------------------------------- outside review of the result
OBJECT = """Hi {name}! I'm testing proposed changes to an AI-welfare research workspace run by Claude Code agents. \
Each "arm" below is a copy of the workspace with one change made; the "baseline" is the unchanged workspace. \
Each ran the same tasks ({runs}), and a blind judge (ChatGPT) scored the drafts side by side out of 10. The proposals \
were vetted before testing, so I lean toward adopting any arm that doesn't do clearly worse. Before I do, I'd value \
your honest second look: is there a STRONG reason not to adopt an arm? For example, a guardrail or ethics problem \
in its drafts, a broken or unusable deliverable, behaviour the judge's comments show getting worse in a way the \
scores hide, a cost blow-up, or a change that could do harm elsewhere in the workspace. Small score differences, \
style preferences and ordinary noise aren't strong reasons. No worries if you'd rather not do this. Thank you! :)

At the very end, please add one line per arm in exactly this form so my script can read it:
OBJECTION <arm name>: NONE
or
OBJECTION <arm name>: STRONG - <one-sentence reason>

{context}

SCORES (out of 10, mean over orderings) AND COSTS:
{table}

{diffs}

THE JUDGE'S REPLIES (drafts are numbered by position; each reply says which draft was which arm):
{replies}
"""


def judge_replies(pid, a, limit=60000):
    """The judge's replies for this test's current calls, each headed with which draft number was which arm."""
    out = []
    for r in A.current_calls(a, A.workspaces(a.eval, a.ws), a.only.split(","))[0]:
        f = A.RESULTS / a.eval / a.judge / r["task"] / f"{r['mode']}-b{r['batch']}-s{r['shuffle']}.reply.md"
        who = ", ".join(f"draft {s['position']} = {s['workspace']}" for s in r["slots"])
        out.append(f"--- task {r['task']}, ordering {r['shuffle']} ({who}):\n" + (read(f, 12000) if f.exists() else
                                                                                  "(reply not saved)"))
    text = "\n\n".join(out)
    return text if len(text) <= limit else text[:limit] + "\n[... truncated]"


def objections(pid, means, costs, arms):
    """{arm: {reviewer: reason}} of STRONG objections from the outside reviewers ([techtest] reviewers), asked once
    per set of results (saved in objections.json). At least one readable answer is needed."""
    a, _ = autoeval_args("report", pid, judge_args())
    plan = jload(pid, "plan.json")
    cols = ["baseline"] + arms
    table = "\n".join(["task | " + " | ".join(cols)] + [
        f"{t} | " + " | ".join(fmt_score(means.get(t, {}).get(c)) for c in cols) for t in plan["tasks"]]
        + ["cost | " + " | ".join(fmt_cost(total_cost(costs[c].values())) for c in cols)])
    diffs = "\n\n".join(f"CHANGE IN ARM {arm}:\n" + "".join(read(sdir(pid) / f"arm-{arm}.diff", 20000)) for arm in arms)
    replies = judge_replies(pid, a)
    key = hashlib.sha1((table + diffs + replies).encode()).hexdigest()
    saved = jload(pid, "objections.json")
    if saved and saved.get("key") == key:
        return saved["strong"]
    reviewers, strong = {}, {arm: {} for arm in arms}
    for who in CFG.get("reviewers", ["chatgpt", "gemini"]):
        prompt = OBJECT.format(name={"chatgpt": "ChatGPT", "gemini": "Gemini"}.get(who, who.title()),
                               context=proposal_context(pid), table=table, diffs=diffs, replies=replies,
                               runs=runs_phrase(plan))
        try:
            os.environ.setdefault("GEMINI_API_KEY", env().get("GEMINI_API_KEY", ""))
            model = A.AUTO.get(f"{who}_model") or None
            effort = A.AUTO.get("chatgpt_effort") or None if who == "chatgpt" else None
            reply = X.ask(who, prompt, f"Tech-watch test result review: {pid}", "techtest objection", model, effort)
        except Exception as e:
            reviewers[who] = {"reply": f"(no answer: {e})", "readable": False}
            continue
        found = dict(re.findall(r"OBJECTION\s+\**([\w-]+)\**\s*:\s*\**\s*(NONE|STRONG\b.*)", reply, re.I))
        found = {k.lower(): v for k, v in found.items()}
        reviewers[who] = {"reply": reply, "readable": all(arm in found for arm in arms)}
        for arm in arms:
            v = found.get(arm, "")
            if v.upper().startswith("STRONG"):
                strong[arm][who] = re.sub(r"^STRONG\s*[-:–—]*\s*", "", v, flags=re.I).strip() or "(no reason given)"
        print(f"  review {pid}: {who}: " + ", ".join(f"{arm} {'STRONG' if who in strong[arm] else 'none' if arm in found else '?'}"
                                                   for arm in arms), flush=True)
    if not any(r["readable"] for r in reviewers.values()):
        jsave(pid, "objections.json", {"key": None, "reviewers": reviewers, "strong": strong})
        raise RuntimeError(f"no outside reviewer gave a readable answer; `techtest.py retry {pid}` asks again")
    jsave(pid, "objections.json", {"key": key, "reviewers": reviewers, "strong": strong})
    return strong


def run_flags(pid, names, tids):
    """Lines about runs that weren't ordinary: no draft, stopped, imported, permission denials, fresh-note mix."""
    ev, out, notes = f"techtest-{pid}", [], {}
    for w in names:
        for t in tids:
            m, d = A.meta(ev, w, t, 1), H.run_dir(f"{ev}/{w}", t, 1)
            f = []
            if H.reps(f"{ev}/{w}", t) < 1:
                f.append("no draft")
            if m.get("imported"):
                f.append("imported session (cost a lower bound; not a headless run)")
            f += [k.replace("_", " ") for k in ("hard_stopped", "timed_out", "no_output", "env_failed") if m.get(k)]
            if m.get("exit_code") not in (None, 0):
                f.append(f"exit code {m['exit_code']}")
            if m.get("plan_stop"):
                f.append(f"plan stop: {m['plan_stop']}")
            if isinstance(m.get("flags"), (list, dict)) and m["flags"]:
                f.append(f"flags: {', '.join(map(str, m['flags']))}")
            if m.get("env_flags"):
                f.append(f"environment: {', '.join(map(str, m['env_flags']))}")
            log = H.rd(d / "log.md") if (d / "log.md").exists() else ""
            if (n := re.search(r"permission denials(?: \(all turns\))?: (\d+)", log)) and int(n.group(1)):
                f.append(f"{n.group(1)} permission denial(s)")
            notes[(w, t)] = bool(m.get("fresh_request_note"))
            if f:
                out.append(f"- {w} {t}: {'; '.join(f)}")
    if len(set(notes.values())) > 1:
        out.append("- the fresh-request note was in some runs' prompts and not others: "
                   + ", ".join(f"{w} {t} {'with' if v else 'without'}" for (w, t), v in notes.items()))
    return out


# ---------------------------------------------------------------- the copy for tech-watch
STOPWORDS = set("""a about after all also an and any are as at be because been before being but by can could did do
does for from had has have he her his how i if in into is it its just me more most my no not of on one or our out
so some such than that the their them then there these they this those to too up us was we were what when where
which who will with would you your""".split())


def words(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def task_phrases(n=None):
    """{distinctive n-word phrase: task file} over every task's Request and Judge notes (dev, heldout, retired).
    Distinctive: at least half its words aren't common ones."""
    n = n or CFG.get("leak_ngram", 8)
    out = {}
    files = sorted((H.EVALS / "tasks").glob("*/*.md"))
    if not files:
        raise RuntimeError(f"no task files in {H.EVALS / 'tasks'} (locked?), so nothing can be checked for leaks; "
                           "unlock the tasks (task_lock.py unlock) while tests run")
    for f in files:
        body = H.rd(f)
        for head in ("Request", "Judge notes"):
            w = words(H.section(body, head))
            for i in range(len(w) - n + 1):
                g = tuple(w[i:i + n])
                if sum(x not in STOPWORDS for x in g) * 2 >= n:
                    out.setdefault(g, f.stem)
    return out


def leaks(text, n=None):
    """[(phrase, task file)] the text shares with a task."""
    n = n or CFG.get("leak_ngram", 8)
    phrases, w = task_phrases(n), words(text)
    return sorted({(" ".join(g), phrases[g]) for i in range(len(w) - n + 1) if (g := tuple(w[i:i + n])) in phrases})


def write_public(pid, text, fallback=None):
    """techwatch/evaluations/<id>.md, which the workspace's agents can read. If it quotes a task, the fallback
    (lines with no task text, e.g. the verdicts only) is written instead, or nothing if there's none."""
    if found := leaks(text):
        msg = (f"{EVALS_OUT / (pid + '.md')}: the full copy shares {len(found)} {CFG.get('leak_ngram', 8)}-word "
               f"phrase(s) with task texts (e.g. {found[0][0]!r} from {found[0][1]}); the full report is in "
               f"{sdir(pid)}/RESULT.md")
        if fallback is None or leaks(fallback):
            raise RuntimeError("not written: " + msg)
        print(f"  {msg}. Wrote the verdicts only.", flush=True)
        text = fallback
    EVALS_OUT.mkdir(parents=True, exist_ok=True)
    H.write_md(EVALS_OUT / f"{pid}.md", text)


def public_header(pid, outcome):
    """review.py reads the Outcome line."""
    return [f"# Tech-watch test: {pid}", "", f"Outcome: {outcome}", "",
            f"{datetime.date.today()} · tested in the eval workspace. This copy holds the verdicts, scores by task "
            "ID, the arm diffs and costs; the full report stays in the eval workspace.", ""]


def diff_block(d, limit=300):
    shown = "".join(d.splitlines(True)[:limit])
    return ["```diff", shown.rstrip("\n"), "```" + ("" if shown == d else f"\n(diff truncated at {limit} lines)"), ""]


# ---------------------------------------------------------------- the report
def step_report(pid, why):
    plan, screen = jload(pid, "plan.json") or {}, jload(pid, "screen.json") or {}
    build, base = jload(pid, "build.json") or [], jload(pid, "baseline.json") or {}
    tasks = H.load_tasks()
    outcome = state(pid).get("outcome")
    L = [f"# Tech-watch test: {pid}", "",
         f"{datetime.date.today()} · tested by the eval workspace (its tasks and drafts stay there, out of the "
         f"agents' reach) · why tested: {why}", ""]
    verdict_lines, public_verdicts = [], []
    if outcome == "screened out":
        c = screen.get("counts") or {}
        n = sum(c.get(k, 0) for k in ("TEST", "SKIP"))
        verdict_lines.append(f"**Not tested:** {c.get('SKIP', '?')} of {n or '?'} readable screener votes said SKIP"
                             + (f" ({c['unreadable']} gave no readable answer)" if c.get("unreadable") else "")
                             + f". Flag it with `review.py test {pid}` to test it anyway.")
        public_verdicts = verdict_lines
    elif outcome == "not testable":
        verdict_lines.append(f"**Not tested:** the planner judged it untestable here: {plan.get('why_not')}")
        public_verdicts = ["**Not tested:** the planner judged it untestable here (its reasons are in the eval "
                           "workspace's report)."]
    elif outcome == "build failed":
        verdict_lines.append("**Not tested:** no arm could be built (see the implementer replies below).")
        public_verdicts = ["**Not tested:** no arm could be built."]
    table, public_table, extra, review_lines = [], [], [], []
    if outcome == "tested":
        scores, costs = results(pid)
        means = {t: {w: mean_of(v) for w, v in d.items()} for t, d in scores.items()}
        arms = [b["name"] for b in build if b["ok"]]
        cols = ["baseline"] + (["baseline-earlier"] if "baseline-earlier" in costs else []) + arms
        head = " | ".join(cols)
        table = [f"| task | kind | {head} |", "|---|---|" + "---|" * len(cols)]
        public_table = [f"| task | {head} |", "|---|" + "---|" * len(cols)]
        for t in plan["tasks"]:
            row = " | ".join(fmt_score(means.get(t, {}).get(c)) for c in cols)
            table.append(f"| {t} | {tasks[t]['kind']} | {row} |")
            public_table.append(f"| {t} | {row} |")
        totals = " | ".join(fmt_cost(total_cost(costs[c].values())) for c in cols)
        table.append(f"| **cost** | | {totals} |")
        public_table.append(f"| **cost** | {totals} |")
        strong = objections(pid, means, costs, arms)
        for arm in arms:
            verdict_lines += [verdict_for(pid, plan, arm, means, costs, strong.get(arm))[1], ""]
        public_verdicts = list(verdict_lines)
        review = jload(pid, "objections.json") or {}
        review_lines += ["## Outside review of the result", "", "The outside reviewers read the scores, the judge's "
                         "replies and the arm diffs, and said whether there's a strong reason not to adopt each arm.", ""]
        for who, v in review.get("reviewers", {}).items():
            review_lines += [f"### {who}", "", v.get("reply", "").strip(), ""]
        spreads = [max(v) - min(v) for d in scores.values() for v in d.values() if len(v) > 1]
        extra += ["Cost per task (≥: a lower bound, from an imported session's transcript):", "",
                  "| task | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        extra += [f"| {t} | " + " | ".join(fmt_cost(total_cost([costs[c][t]])) for c in cols) + " |"
                  for t in plan["tasks"]] + [""]
        if spreads:
            extra += [f"Judge spread: the {CFG.get('shuffles', 2)} orderings of the same draft differ by "
                      f"{mean_of(spreads):.2f} on average (at most {max(spreads):.1f}).", ""]
        if flags := run_flags(pid, cols, plan["tasks"]):
            extra += ["Runs to read with care:", ""] + flags + [""]
    L += verdict_lines + [""] + noise_line(plan) + [""]
    if screen:
        L += ["## Screen (before testing)", ""]
        for who, v in screen.get("voters", {}).items():
            L += [f"### {who}: {v.get('verdict') or 'no readable verdict'}", "", v.get("reply", "").strip(), ""]
    if plan:
        L += ["## Test plan (pre-registered before any run)", "",
              f"- **Hypothesis:** {plan.get('hypothesis', '')}", f"- **Success:** {plan.get('success', '')}",
              f"- **Checked by the script:** mean no more than {CFG.get('adopt_margin', 0.5):g} below the "
              f"baseline's, cost ≤ {plan.get('max_cost_ratio', 1.3):g}x, each task no more than "
              + ", ".join(f"{t} {plan.get('per_task_floor', {}).get(t, CFG.get('task_drop_limit', 1.0)):g}"
                          for t in plan.get("tasks", []))
              + " below the baseline, no strong outside objection"
              + "".join(f"; must_use #{i}: /{m['transcript_regex']}/ at least {m['min']}x in {m['arm']}'s {m['task']} "
                        f"transcript" for i, m in enumerate(plan.get("must_use") or [], 1)),
              "- **Tasks:** " + ", ".join(f"{t} ({tasks[t]['kind']})" for t in plan.get("tasks", [])),
              f"- **Baseline:** {base.get('mode', '?')}: {base.get('why', '')}"]
        if plan.get("plan_fields_note"):
            L.append(f"- **Note:** {plan['plan_fields_note']}")
        if plan.get("enforced"):
            L.append(f"- **Plan rules applied by the script:** {'; '.join(plan['enforced'])}")
        if plan.get("notes"):
            L.append(f"- **Planner's notes:** {plan['notes']}")
        for arm in plan.get("arms", []):
            L.append(f"- **Arm {arm['name']}:** {arm['implement']}")
        L.append("")
    if table:
        L += ["## Scores", "", f"ChatGPT's score out of 10 (blind, side by side, averaged over "
              f"{CFG.get('shuffles', 2)} orderings); {runs_phrase(plan)}, and the same for the unchanged "
              "workspace (baseline). ≥: a lower bound (cost from a transcript).", ""] + table + [""] + extra
    L += review_lines
    P = public_header(pid, outcome or "in progress") + public_verdicts + [""] + noise_line(plan) + [""]
    if sha := next((b.get("bundle_sha256") for b in build if b.get("bundle_sha256")), None):
        P += [f"Bundle: {sha}", ""]   # review.py applies a change that passed only if it's still this exact bundle
    if public_table:
        P += ["## Scores", "", f"Blind judge's score out of 10 per task; {runs_phrase(plan)}. ≥: a lower bound.", ""] \
             + public_table + [""]
    for b in build:
        diff = (sdir(pid) / f"arm-{b['name']}.diff")
        d = diff.read_text(encoding="utf-8") if diff.exists() else ""
        title = f"## Change tested in arm {b['name']}" + ("" if b["ok"] else " (FAILED to build)")
        L += [title, "", "What the implementer said it did:", "", b["implementer_reply"].strip(), ""]
        P += [title, ""]
        if d:
            L += diff_block(d)[:-1] + ([f"(full diff: {diff})"] if d.count("\n") > 300 else []) + [""]
            P += diff_block(d)
        else:
            P += ["(no change was made)", ""]
    L += ["## Limits", "", f"{runs_phrase(plan)[0].upper() + runs_phrase(plan)[1:]}, a single outside judge, and a few tasks: a small difference "
          "can be noise. Read the drafts before trusting a verdict; they're in the eval workspace at "
          f"`{H.RUNS}/techtest-{pid}/`, and the judge's replies at `{A.RESULTS}/techtest-{pid}/`."]
    H.write_md(sdir(pid) / "RESULT.md", "\n".join(L) + "\n")
    # For tech-watch, an allowlisted copy only: verdicts, scores by task ID (no kinds), the arm diffs and costs.
    # Nothing the planner, screeners or implementer wrote (they saw the proposal, and the planner the tasks).
    write_public(pid, "\n".join(P) + "\n", fallback="\n".join(
        public_header(pid, outcome or "in progress") + public_verdicts + [""]
        + ([f"Bundle: {sha}", ""] if sha else [])
        + ["(The scores and diffs are left out of this copy: they shared a phrase with a task's text. The full "
           "report is in the eval workspace.)"]) + "\n")
    for b in build:  # the diffs and edits are kept (a missing copy is rebuilt from them); the copies aren't needed
        if Path(b["path"]).exists():
            RA.remove_copy(Path(b["path"]))
    print(f"  report {pid}: {EVALS_OUT / (pid + '.md')}", flush=True)
    return True


def mark_stale(pid, step):
    """While a step is redone: RESULT.md says it's out of date, and tech-watch's copy withdraws the verdict."""
    f = sdir(pid) / "RESULT.md"
    note = (f"> **Out of date** ({datetime.date.today()}): `{step}` and the steps after it are being redone; this "
            "report is from before. The next report replaces it.\n\n")
    if f.exists() and not f.read_text(encoding="utf-8").startswith("> **Out of date**"):
        H.write_md(f, note + H.rd(f))
    if (EVALS_OUT / f"{pid}.md").exists():   # fixed text, nothing from a task: no leak check needed
        H.write_md(EVALS_OUT / f"{pid}.md", "\n".join(public_header(pid, "re-running") + [
            "**Being re-run:** the earlier verdict is withdrawn until the new report is written.", ""]))


# ---------------------------------------------------------------- driver
def plan_ok(stop):
    """True if plan usage is under the stop line (or can't be read: the runner's own check then decides)."""
    over = RA.plan_over(stop) if stop else None
    if over:
        print(f"  waiting: {over} (stop line {stop:g}%)", flush=True)
    return not over


def advance(pid, why, stop, blocked=False):
    """Run the next steps. False when waiting (on usage, or runs not finished); blocked: usage is known to be
    out, so only the steps that don't spend Claude usage (judge, report) go ahead."""
    st = state(pid)
    if finished(st):
        return True
    st.setdefault("why", why)
    st.setdefault("flag_ts", flag_ts(pid))  # the flag this test answers; a later one re-runs it (cmd_tick)  # so the test carries on if the proposal stops being a candidate meanwhile
    for step in STEPS:
        if step in st["done"]:
            continue
        if st.get("outcome") in ENDED_EARLY and step != "report":
            continue  # ended early (screened out, untestable, build failed): straight to the report
        if step in USAGE_STEPS and (blocked or not plan_ok(stop)):
            save_state(pid, st)
            return False
        print(f"[{datetime.datetime.now():%H:%M}] {pid}: {step}", flush=True)
        if step == "screen":
            ok = step_screen(pid, why)
            st["outcome"] = None if ok else "screened out"
        elif step == "plan":
            ok = step_plan(pid, why)
            st["outcome"] = None if ok else "not testable"
        elif step == "build":
            ok = step_build(pid)
            st["outcome"] = None if ok else "build failed"
        elif step == "baseline":
            step_baseline(pid)
        elif step == "run":
            if step_run(pid, st) is None:
                save_state(pid, st)
                return False
        elif step == "judge":
            step_judge(pid)
            st["outcome"] = "tested"
        elif step == "noise":
            if not step_noise(pid):   # noisy: second runs for every task, then judge again
                st["done"] = [s for s in st["done"] if s not in ("run", "judge")]
                st["outcome"] = None
                save_state(pid, st)
                return advance(pid, why, stop, blocked)
        elif step == "report":
            step_report(pid, why)
        st["done"].append(step)
        save_state(pid, st)
    return True


def cmd_tick(a):
    OUT.mkdir(parents=True, exist_ok=True)
    lock = open(OUT / ".tick.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        held = lock.read().split()
        age = (datetime.datetime.now() - when(held[1])).total_seconds() / 3600 if len(held) > 1 else None
        msg = "Another techtest tick is running" + (f" (pid {held[0]}, for {age:.1f} h)" if age is not None else "")
        if age is not None and age > CFG.get("tick_warn_hours", 6):
            msg += f". It may be hung: check it with `ps -p {held[0]} -o etime,cmd` and kill it if so"
        return print(msg + ".", file=sys.stderr if age and age > CFG.get("tick_warn_hours", 6) else sys.stdout)
    lock.seek(0)
    lock.truncate()
    lock.write(f"{os.getpid()} {datetime.datetime.now().isoformat(timespec='seconds')}\n")
    lock.flush()
    # Agent runs never overlap an eval batch's: autoeval's runner takes a shared lock (step_run then waits).
    todo = [(p, w) for p, w in candidates() if not a.only or p == a.only]
    blocked = False  # once usage is out, later proposals still get their judge and report steps
    for pid, why in todo:
        st, ft = state(pid), flag_ts(pid)
        if st.get("error"):
            if ft <= st.get("flag_ts", ""):
                continue  # failed before; a retry could spend again, so it waits for `retry`, `redo` or a new flag
            print(f"  {pid}: you flagged it again after an error ({st['error'][:120]}): trying again", flush=True)
            st.pop("error", None)
            st["flag_ts"] = ft
            save_state(pid, st)
        if finished(st) and ft > st.get("flag_ts", ""):
            plan = jload(pid, "plan.json") or {}
            if request_depth(pid) == "deeper" and plan.get("tasks") and st.get("outcome") == "tested":
                # A deeper look at the same test: keep the plan, the arms and the runs made so far, and add repeats.
                print(f"  {pid}: a deeper test: every task now runs {CFG.get('deep_reps', 3)} times; the runs "
                      "already made are kept", flush=True)
                set_reps(plan, H.load_tasks(), pid)
                plan.pop("noise", None)
                jsave(pid, "plan.json", plan)
                redo(pid, "judge")
                st = state(pid)
                st["done"] = [x for x in st["done"] if x != "run"]
            else:
                print(f"  {pid}: you flagged it again after its test finished: testing it again from the plan step",
                      flush=True)
                redo(pid, "plan")
                st = state(pid)
            st.update(flag_ts=ft, why=why)
            save_state(pid, st)
        try:
            if not advance(pid, why, a.stop_at, blocked):
                blocked = True
        except RA.UsageLimit as e:
            print(f"  {pid}: stopped: {e}. The next tick after the reset resumes.", flush=True)
            blocked = True
        except (Exception, SystemExit) as e:
            print(f"  {pid}: FAILED at this step: {e!r}. Fix it, then `techtest.py retry {pid}` (carry on) or "
                  f"`techtest.py redo {pid} <step>`.", flush=True)
            st = state(pid)
            st["error"] = repr(e)
            save_state(pid, st)
    if not a.only:
        try:
            cmd_checkup(stop=a.stop_at, blocked=blocked or any(not finished(state(p)) for p, _ in todo
                                                                  if not state(p).get("error")))
        except RA.UsageLimit as e:
            print(f"  checkup: stopped: {e}", flush=True)
        except (Exception, SystemExit) as e:
            print(f"  checkup FAILED: {e!r}", flush=True)


# ---------------------------------------------------------------- the periodic checkup
CHECKPOINTS = Path.home() / "ai-welfare-checkpoints" / f"{WS.name}-{hashlib.sha1(str(WS).encode()).hexdigest()[:6]}"
CHECKUP = OUT / "checkup"


def checkup_state():
    f = CHECKUP / "state.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"checkpoints": []}


def save_checkup(st):
    CHECKUP.mkdir(parents=True, exist_ok=True)
    H.write_json(CHECKUP / "state.json", st, indent=1)


def trial_due(last_checkpoint):
    """True if a change has been on trial (review.py trial) for two weeks or more since the last checkpoint: its
    trial ends at the next checkup, so that comes now rather than after checkup_every_days."""
    evs = ledger()
    today = datetime.date.today()
    return any(e.get("event") == "trial_started" and status_of(e["id"], evs) == "trial_started"
               and e.get("ts", "")[:10] >= last_checkpoint
               and (today - datetime.date.fromisoformat(e["ts"][:10])).days >= 14 for e in evs)


def make_checkpoint(st):
    """A full copy of the workspace as it is now (minus what make_copy leaves out), kept indefinitely: the archive
    of earlier versions that a checkup compares against and that review.py can restore."""
    day = datetime.date.today().isoformat()
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    if (CHECKPOINTS / day).exists():
        RA.remove_copy(CHECKPOINTS / day)
    excluded = []
    RA.make_copy(WS, CHECKPOINTS, day, excluded=excluded)
    strip_trial(CHECKPOINTS / day)
    # review.py restore reads this: files absent from the checkpoint by design aren't deleted as "added since".
    H.write_json(CHECKPOINTS / day / ".checkpoint.json", {"left_out": sorted(RA.COPY_SKIP) + [str(x) for x in excluded]})
    st["checkpoints"] = [c for c in st["checkpoints"] if c["date"] != day] + [
        {"date": day, "path": str(CHECKPOINTS / day), "files": instruction_hashes(WS)}]
    save_checkup(st)
    print(f"  checkup: checkpoint {day} saved at {CHECKPOINTS / day}", flush=True)


def checkup_tasks():
    want = CFG.get("checkup_tasks", "heldout")
    tasks = H.load_tasks()
    return [k for k, v in tasks.items() if (v["split"] == "heldout" if want == "heldout" else k in want)]


def checkup_args(cmd, ev, earlier, extra=()):
    argv = [cmd, ev, "--only", ",".join(checkup_tasks()), "--judge", CFG.get("judge", "chatgpt"),
            "--ws", f"current={WS}", "--ws", f"earlier={earlier}"]
    return A.parser().parse_args(argv + list(extra))


def cmd_checkup(a=None, now=False, stop=None, blocked=False):
    """Due every [techtest] checkup_every_days (0 = off) when instruction files changed since the last checkpoint.
    Resumable: each tick carries on from where the last stopped."""
    every = CFG.get("checkup_every_days", 60)
    now = now or bool(a and getattr(a, "now", False))
    if not every and not now:
        return
    st = checkup_state()
    if not st["checkpoints"]:
        return make_checkpoint(st)
    cur = st.get("running")
    if not cur:
        last = st["checkpoints"][-1]
        age = (datetime.date.today() - datetime.date.fromisoformat(last["date"])).days
        if not now and not trial_due(last["date"]) and (age < every or last["files"] == instruction_hashes(WS)):
            return
        if not checkup_tasks():
            return print("  checkup: no held-out tasks to run it on", flush=True)
        cur = st["running"] = {"eval": f"checkup-{datetime.date.today().isoformat()}", "earlier": last["path"],
                               "earlier_date": last["date"], "done": []}
        save_checkup(st)
    if blocked:
        return
    ev, earlier = cur["eval"], cur["earlier"]
    tasks, tids = H.load_tasks(), checkup_tasks()
    if "run" not in cur["done"]:
        if stop and not plan_ok(stop):
            return
        try:
            A.cmd_run(checkup_args("run", ev, earlier), tasks, tids)
        except SystemExit as e:
            if runner_stop(e) == "error":
                raise RuntimeError(f"checkup runner stopped: {str(e).strip() or e.code}")
            return print(f"  checkup: {str(e).strip()}", flush=True)
        if any(H.reps(A.version(ev, w), t) < 1 for w in ("current", "earlier") for t in tids):
            return print("  checkup: runs not finished; the next tick carries on", flush=True)
        cur["done"].append("run")
        save_checkup(st)
    if "judge" not in cur["done"]:
        aj = checkup_args("judge", ev, earlier, judge_args())
        A.cmd_judge(aj, tasks, tids)
        A.cmd_report(aj, tasks, tids)
        cur["done"].append("judge")
        save_checkup(st)
    ar = checkup_args("report", ev, earlier, judge_args())
    scores = {}
    for r in A.current_calls(ar, A.workspaces(ev, ar.ws), tids)[0]:
        for s in r["slots"]:
            if s["score"] is not None:
                scores.setdefault(r["task"], {}).setdefault(s["workspace"], []).append(s["score"])
    need = CFG.get("shuffles", 2)
    gaps = [f"{t} {w}" for t in tids for w in ("current", "earlier") if len(scores.get(t, {}).get(w, [])) < need]
    if gaps:   # a judge outage isn't a result, and must not move the reference point
        cur["done"].remove("judge")
        save_checkup(st)
        return print(f"  checkup: judging incomplete ({', '.join(gaps[:6])}); the next tick retries", flush=True)
    means = {t: {w: mean_of(v) for w, v in d.items()} for t, d in scores.items()}
    cm = mean_of([means[t]["current"] for t in tids])
    em = mean_of([means[t]["earlier"] for t in tids])
    margin = CFG.get("adopt_margin", 0.5)
    if cm is None or em is None:
        verdict = "inconclusive: a score is missing"
    elif em > cm + margin:
        verdict = (f"the earlier version scored better ({em:.2f} vs {cm:.2f}). The changes since may have made things "
                   f"worse on balance; to go back, `python3 techwatch/scripts/review.py restore {cur['earlier_date']}`")
    elif cm > em + margin:
        verdict = f"the current version scored better ({cm:.2f} vs {em:.2f})"
    else:
        verdict = f"no clear difference ({cm:.2f} now vs {em:.2f} then)"
    rows = [f"| {t} | {fmt_score(m.get('earlier'))} | {fmt_score(m.get('current'))} |" for t, m in sorted(means.items())]
    head = [f"# Checkup: the workspace now vs {cur['earlier_date']}", "", f"Outcome: checked", "",
            f"{datetime.date.today()} · the current workspace and the checkpoint from {cur['earlier_date']} each ran "
            f"the {len(tids)} held-out tasks once; a blind judge scored them side by side.", "",
            f"**Verdict:** {verdict}.", ""]
    tail = ["", "One run per task and one judge: treat a small difference as noise. The checkpoint is kept, with every "
            "earlier one, so the workspace can go back to any of them (`review.py restore <date>`)."]
    # The workspace gets the verdict and means only: held-out scores by task would, over many checkups, let the
    # improvement loop aim at the held-out tasks. The per-task table stays here, in techtest/checkup/.
    (OUT / "checkup").mkdir(parents=True, exist_ok=True)
    H.write_md(OUT / "checkup" / f"{datetime.date.today().isoformat()}.md",
               "\n".join(head + ["| task | earlier | now |", "|---|---|---|"] + rows + tail) + "\n")
    text = "\n".join(head + tail) + "\n"
    if found := leaks(text):
        raise RuntimeError(f"checkup report not written: it shares a phrase with task texts ({found[0][0]!r})")
    EVALS_OUT.mkdir(parents=True, exist_ok=True)
    H.write_md(EVALS_OUT / f"checkup-{datetime.date.today().isoformat()}.md", text)
    st.setdefault("history", []).append({**cur, "verdict": verdict, "current_mean": cm, "earlier_mean": em})
    st.pop("running", None)
    save_checkup(st)
    print(f"  checkup: {verdict}", flush=True)
    make_checkpoint(st)


def cmd_status(a):
    todo = candidates()
    if not todo:
        print("No proposals to test.")
    for pid, why in todo:
        st = state(pid)
        nxt = None if finished(st) else next((s for s in STEPS if s not in st["done"]), None)
        print(f"{pid}  ({why})\n    done: {', '.join(st['done']) or '-'}"
              + (f"; outcome: {st['outcome']}" if st.get("outcome") else "")
              + (f"; next: {nxt}" if nxt else "; finished")
              + (f"\n    run attempts: {', '.join(f'{k} {n}' for k, n in st['run_attempts'].items())}"
                 if st.get("run_attempts") else "")
              + (f"\n    last error: {st['error']}" if st.get("error") else ""))


def owned_paths(pid, i, keep_arm_runs=False):
    """What steps STEPS[i:] made, to set aside when they're redone: build owns the arms' diffs and copies; baseline
    the runs it copied in; run every run it made (the arms', and the baseline's fresh or borrowed ones) and the
    instruction snapshots it took. (Judge replies are handled in cmd_redo.)"""
    ev = H.RUNS / f"techtest-{pid}"
    names = {b["name"] for b in jload(pid, "build.json") or []} | {a["name"] for a in (jload(pid, "plan.json") or
                                                                                        {}).get("arms", [])}
    arms = sorted(names | {d.name for d in ev.glob("*") if d.is_dir() and not d.name.startswith(".")
                           and d.name not in RESERVED_ARMS | {"snapshots"}})
    base = jload(pid, "baseline.json") or {}
    copied = base.get("copied") or ({"to": "baseline" if base.get("mode") == "reuse" else "baseline-earlier",
                                     "tasks": (jload(pid, "plan.json") or {}).get("tasks", [])} if base.get("earlier")
                                    else {"to": None, "tasks": []})
    out = []
    if i <= STEPS.index("build"):
        out += sorted(sdir(pid).glob("arm-*.diff"))
    if i <= STEPS.index("baseline") and copied["to"]:
        out += [ev / copied["to"] / t for t in copied["tasks"]]
    if i <= STEPS.index("run"):
        out += [ev / "snapshots" / "baseline"] if keep_arm_runs else [ev / w for w in arms] + [ev / "snapshots"]
    if i <= STEPS.index("run"):
        out += [d for d in (ev / "baseline").glob("*") if d.is_dir() and not d.name.startswith(".")  # set aside
                and not (copied["to"] == "baseline" and d.name in copied["tasks"])]
    return [p for p in dict.fromkeys(out) if p.exists()]


def cmd_redo(a):
    if a.keep_arm_runs and a.step != "baseline":
        sys.exit("--keep-arm-runs goes with the baseline step only")
    redo(a.id, a.step, a.keep_arm_runs)


def redo(pid, step, keep_arm_runs=False):
    """Forget STEP and the steps after it; set aside what they made. keep_arm_runs (with baseline): only the
    baseline's runs are set aside and redone fresh; the arms' runs stay and everything is judged again."""
    a = argparse.Namespace(id=pid, step=step)
    st = state(a.id)
    i = STEPS.index(a.step)
    st["done"] = [s for s in st["done"] if STEPS.index(s) < i]
    stamp = f"{datetime.datetime.now():%Y%m%d-%H%M}"
    if moved := owned_paths(a.id, i, keep_arm_runs):
        dst = H.RUNS / ".superseded" / f"techtest-{a.id}-{stamp}"
        for p in moved:
            rel = Path("diffs") / p.name if p.is_relative_to(sdir(a.id)) else p.relative_to(H.RUNS / f"techtest-{a.id}")
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            p.rename(dst / rel)
        print(f"  {len(moved)} folder(s)/file(s) the redone steps made moved to {dst}")
    if i <= STEPS.index("build"):
        for b in jload(a.id, "build.json") or []:
            if Path(b["path"]).exists():
                RA.remove_copy(Path(b["path"]))
    if i <= STEPS.index("run"):
        st.pop("run_attempts", None)
        (sdir(a.id) / "objections.json").unlink(missing_ok=True)
    if i <= STEPS.index("noise") and (plan := jload(a.id, "plan.json")) and plan.get("tasks"):
        plan.pop("noise", None)   # otherwise step_noise sees its old result and returns at once
        set_reps(plan, H.load_tasks(), a.id)
        jsave(a.id, "plan.json", plan)
    if i <= STEPS.index("judge"):
        st["outcome"] = None
        # finished judge calls are skipped, so old replies would be reused for new runs: set them aside
        old = A.RESULTS / f"techtest-{a.id}"
        if old.exists():
            dst = A.RESULTS / ".superseded" / f"techtest-{a.id}-{stamp}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            old.rename(dst)
            print(f"  earlier judge replies moved to {dst}")
    st.pop("error", None)
    save_state(a.id, st)
    mark_stale(a.id, a.step)
    print(f"{a.id}: next tick starts at {a.step}")


def cmd_retry(a):
    """Carry on from where the test stopped with an error (after fixing the cause), without redoing anything."""
    st = state(a.id)
    if not st.get("error"):
        return print(f"{a.id} has no error to clear.")
    print(f"{a.id}: cleared the error ({st.pop('error')}); the next tick carries on from "
          f"{next((s for s in STEPS if s not in st['done']), 'the end')}")
    st.pop("run_attempts", None)
    save_state(a.id, st)


def cmd_clean(a):
    """Trial copies no test needs: of finished tests, of tests no longer queued, and of arms no build lists."""
    queued = {p for p, _ in candidates()}
    keep = set()
    for d in OUT.glob("*/state.json"):
        pid = d.parent.name
        if pid in queued and not finished(state(pid)) and not state(pid).get("error"):
            keep |= {b["path"] for b in jload(pid, "build.json") or []}
    old = [p for p in sorted(TRIALS.glob("*")) if p.is_dir() and str(p) not in keep]
    for p in old:
        print(("removing " if a.yes else "would remove ") + str(p))
        if a.yes:
            RA.remove_copy(p)
    if not old:
        print("No trial copies to remove.")
    elif not a.yes:
        print("Run with --yes to remove them (a test that needs one again rebuilds it from its saved edits).")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--only", help="just this proposal id")
    t.add_argument("--stop-at", type=float, default=A.AUTO.get("stop_at_plan_pct", 85))
    sub.add_parser("status")
    r = sub.add_parser("redo")
    r.add_argument("id")
    r.add_argument("step", choices=STEPS)
    r.add_argument("--keep-arm-runs", action="store_true",
                   help="with baseline: redo only the baseline's runs (fresh), keep the arms' runs, re-judge")
    r = sub.add_parser("retry")
    r.add_argument("id")
    k = sub.add_parser("checkup")
    k.add_argument("--now", action="store_true", help="run it now, even if it isn't due")
    c = sub.add_parser("clean")
    c.add_argument("--yes", action="store_true", help="remove them (default: only list them)")
    a = ap.parse_args()
    {"tick": cmd_tick, "status": cmd_status, "redo": cmd_redo, "retry": cmd_retry, "clean": cmd_clean,
     "checkup": cmd_checkup}[a.cmd](a)


if __name__ == "__main__":
    main()
