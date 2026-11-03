"""Find task runs a workspace has already completed, and bring them into an eval as runs/EVAL/<ws>/<task>/[rK]/.

Two sources:
  - earlier autoeval runs of the same workspace path (runs/<other eval>/<name>/<task>/), copied as they are;
  - Claude Code sessions run directly in the workspace (~/.claude/projects/<workspace>/*.jsonl): a session is a
    run of a task when its first user messages contain most of that task's request text (a preamble, a paste or
    small edits are fine). The draft is the file the session wrote (outbox, then drafts/, then a new top-level
    .md file), or else its replies, every turn's joined, as for fresh runs.

A run is imported only if it has a draft file or a reply of at least MIN_REPLY_WORDS, and, for tasks with a budget
sentence ("Only use $X"), only if its first message holds the task's current request (apart from whitespace).

Each imported run's meta.json records "source": "import" and where it came from, so nothing is imported twice.
Imported runs come first (oldest first) and fill at most `reps` slots; new runs fill the rest. [autoeval]
import_completed = false (or import_all(enabled=False)) turns imports off. All timestamps written are UTC.
"""
import datetime, json, re, shutil, subprocess
from collections import Counter, defaultdict
from pathlib import Path

import harness as H
import run_agent as RA

PROJECTS = Path.home() / ".claude" / "projects"
SHINGLE = 6           # words per shingle when matching a session's prompt to a task's request
MATCH_SHARE = 0.6     # share of the request's shingles that must appear in the session's first user messages
MIN_REPLY_WORDS = 100 # no draft file and no reply this long: a false start (e.g. restarted), not a run
LATE_EDIT_S = 300     # a draft file changed this long after the session's last message is flagged
COPY_NAMES = ("draft.md", "review.md", "ideator.md", "log.md", "meta.json", "raw")
CLEAR_NAMES = COPY_NAMES + ("draft.failed.md",)
UTC = datetime.timezone.utc


def words(s):
    return re.sub(r"[^a-z0-9$.]+", " ", s.lower()).split()


def shingles(s):
    w = words(s)
    return {" ".join(w[i:i + SHINGLE]) for i in range(max(1, len(w) - SHINGLE + 1))}


def task_keys(tasks):
    # Match on the task itself, not the budget sentences, which were added after many runs were done.
    return {t: shingles(re.split(r"\bOnly use \$", v["request"], 1)[0]) for t, v in tasks.items()}


def utc(iso):
    """An ISO stamp as an aware UTC datetime: transcripts' "Z" stamps as they are, naive ones (fresh runs' meta)
    as local time. None if empty."""
    if not iso:
        return None
    d = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.astimezone()).astimezone(UTC)


def ts(iso):
    d = utc(iso)
    return d.timestamp() if d else 0


def iso(d):
    return d.isoformat(timespec="seconds") if d else ""


def now():
    return iso(datetime.datetime.now(UTC))


def flat(s):
    return " ".join(s.split())


def budget_sentence(request):
    return RA.BUDGET_RE.search(request)


def exact_prompt(first, request, ws_path):
    """Whether the first message holds the task's current request, apart from whitespace (with or without the
    fresh_request_note run_agent adds for some workspaces)."""
    return any(flat(r) in flat(first or "") for r in {request, RA.with_fresh_note(request, ws_path)})


def born(p):
    """Creation time, or 0 if the filesystem doesn't record one."""
    r = subprocess.run(["stat", "-c", "%W", str(p)], capture_output=True, text=True)
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def bash_written(commands, root, start, end):
    """Files a session wrote with Bash (heredocs, sed -i, python): workspace .md files its commands name that
    were created and last changed while it ran."""
    out = set()
    for cmd in commands:
        for tok in re.findall(r"[\w./~-]+\.md\b", cmd):
            p = Path(tok).expanduser()
            p = p if p.is_absolute() else root / p
            if not p.is_relative_to(root) or not p.is_file():
                continue
            b, m = born(p), p.stat().st_mtime
            if start - 60 <= m <= end + LATE_EDIT_S and (b == 0 or b >= start - 60):
                out.add(p)
    return out


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")


def read_jsonl(p):
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def project_dir(path):
    return PROJECTS / ("-" + str(path).strip("/").replace("/", "-").replace(".", "-"))


def is_prompt(e):
    """A message typed (or sent) to the main session, as opposed to tool results and meta lines."""
    return (e.get("type") == "user" and not e.get("isSidechain") and not e.get("isMeta")
            and isinstance(e.get("message"), dict) and text_of(e["message"].get("content", "")).strip() != "")


# ---------------------------------------------------------------- sessions in the workspace itself
def session_info(f, ws_path):
    evs = read_jsonl(f)
    users = [(iso(utc(e.get("timestamp", ""))), text_of(e["message"]["content"])) for e in evs if is_prompt(e)]
    users = [(t, u) for t, u in users if not u.lstrip().startswith(("<task-notification", "<local-command",
                                                                    "<command-", "Another Claude session"))]
    subs = sorted((f.with_suffix("") / "subagents").glob("*.jsonl"))
    all_evs = [(True, e) for e in evs] + [(False, e) for s in subs for e in read_jsonl(s)]
    tools, writes, created, commands, agents, usage = Counter(), [], [], [], [], {}
    # Some logs (subagents' especially) keep only a streaming placeholder for output_tokens (single digits), so
    # ccusage and a plain sum undercount. Estimate each message's output from what it wrote (~4 chars a token).
    written = Counter()
    # Replies as fresh runs record them (one per turn: its last text), so the no-file draft follows the same rule.
    replies, last = [], ""
    for main, e in all_evs:
        m = e.get("message")
        if main and is_prompt(e):  # a new turn (incl. task notifications, which start one headless too)
            replies += [last] if last else []
            last = ""
        if e.get("type") != "assistant" or not isinstance(m, dict):
            continue
        if m.get("usage"):
            usage[(m.get("id"), e.get("requestId"))] = (m.get("model", ""), m["usage"])
        if isinstance(m.get("content"), list):
            written[(m.get("id"), e.get("requestId"))] += sum(
                len(c.get("text") or c.get("thinking") or "") + len(json.dumps(c.get("input"))) * (c.get("type") == "tool_use")
                for c in m["content"] if isinstance(c, dict))
        for c in m.get("content", []) if isinstance(m.get("content"), list) else []:
            if c.get("type") == "tool_use":
                tools[c["name"]] += 1
                fp = (c.get("input") or {}).get("file_path")
                if c["name"] in ("Write", "Edit", "MultiEdit") and fp:
                    writes.append(fp)
                if c["name"] == "Write" and fp:
                    created.append(fp)
                if c["name"] == "Bash":
                    commands.append((c.get("input") or {}).get("command", ""))
                if c["name"] in ("Agent", "Task") and main:
                    agents.append((c.get("input") or {}).get("subagent_type", "general-purpose"))
            elif c.get("type") == "text" and main and c.get("text", "").strip():
                last = c["text"].strip()
    replies += [last] if last else []
    # ccusage and this estimate only see subagents whose logs exist; some sessions lost theirs (e.g. v1's Butlin run)
    missing_subs = max(len(agents) - len(subs), 0)
    stamps = sorted(d for d in (utc(e.get("timestamp")) for e in evs) if d)
    start, end = (stamps[0], stamps[-1]) if stamps else (None, None)
    root = Path(ws_path)
    via_bash = bash_written(commands, root, start.timestamp() if start else 0, end.timestamp() if end else 0)
    files = sorted({Path(p) for p in writes if Path(p).is_relative_to(root) and Path(p).is_file()} | via_bash)
    edited_only = {p for p in files if str(p) not in created and p not in via_bash}
    drafts = RA.find_drafts(root, files, edited_only)  # files it only edited existed before it
    late = [p for p in drafts if end and p.stat().st_mtime > end.timestamp() + LATE_EDIT_S]
    return {"id": f.stem, "file": f, "users": users, "tools": tools, "agents": agents, "files": files,
            "edited_only": edited_only, "drafts": drafts, "late": late, "replies": replies,
            "start": iso(start), "end": iso(end), "subagent_logs_missing": missing_subs,
            "cost_estimate": round(sum(RA.price(model, out_fixed(u, written[k])) for k, (model, u) in usage.items()), 2),
            "cost_logged": round(sum(RA.price(model, u) for model, u in usage.values()), 2)}


def out_fixed(u, chars):
    """The usage record with output_tokens at least what the message's text and tool inputs imply."""
    return {**u, "output_tokens": max(u.get("output_tokens") or 0, chars // 4)}


def match_sessions(ws_path, tasks):
    """{task: [session info]} for sessions in this workspace that ran a task, longest first."""
    keys = task_keys(tasks)
    found = defaultdict(list)
    for f in sorted(project_dir(ws_path).glob("*.jsonl")):
        info = None
        head = None
        for e in read_jsonl(f)[:400]:
            if e.get("type") == "user" and not e.get("isSidechain") and isinstance(e.get("message"), dict):
                head = shingles(text_of(e["message"]["content"])) | (head or set())
        if not head:
            continue
        for t, k in keys.items():
            if len(k & head) / len(k) >= MATCH_SHARE:
                info = info or session_info(f, ws_path)
                found[t].append(info)
    for t in found:
        found[t].sort(key=lambda s: -sum(s["tools"].values()))
    return found


def long_reply(s):
    return max((len(r.split()) for r in s["replies"]), default=0) >= MIN_REPLY_WORDS


def session_log(s, task, root):
    tools = ", ".join(f"{k} x{v}" for k, v in s["tools"].most_common()) or "none"
    lines = [f"# Run summary (imported by import_runs.py from session {s['id']})", "",
             f"- ran {s['start'][:16]} to {s['end'][:16]} UTC directly in the workspace (not a throwaway copy)",
             f"- cost: at least ${s['cost_estimate']:.2f} (from the transcript; Claude Code's internal Haiku calls, "
             "e.g. for WebFetch, aren't recorded there and typically add 15-40%)",
             *([f"- of which ${s['cost_estimate'] - s['cost_logged']:.2f} is output the logs undercount (their "
                "output_tokens are streaming placeholders), estimated from what was written; ccusage would say "
                f"${s['cost_logged']:.2f}"] if s["cost_estimate"] - s["cost_logged"] >= 0.01 else []),
             *([f"- WARNING: the cost is incomplete: {s['subagent_logs_missing']} of {len(s['agents'])} subagent "
                "calls have no log, so their cost isn't counted at all"] if s["subagent_logs_missing"] else []),
             f"- subagents called (in order): {', '.join(s['agents']) or 'none'}",
             f"- tool calls (main session and subagents): {tools}",
             f"- files written: {', '.join(str(p.relative_to(root)) for p in s['files']) or 'none'}",
             f"- first message is the task's current request (apart from whitespace): "
             f"{'yes' if s['exact_prompt'] else 'no'}",
             f"- operator messages after the first: {len(s['users']) - 1}",
             *([f"- WARNING: draft file(s) changed more than {LATE_EDIT_S // 60} min after the session ended, so the "
                f"draft may not be what the session left: {', '.join(str(p.relative_to(root)) for p in s['late'])}"]
               if s["late"] else []),
             "", "## Messages from the operator, verbatim", ""]
    for t, u in s["users"]:
        lines += [f"**{t[:16]}**", "", u.strip(), ""]
    lines += ["## Replies (all turns)", "", "\n\n---\n\n".join(s["replies"]) or "(none)"]
    return "\n".join(lines) + "\n"


def clear_slot(out_dir):
    """Remove what an earlier (failed or deleted) run left in this slot, so an import doesn't merge into it."""
    for name in CLEAR_NAMES:
        p = out_dir / name
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)


def import_session(s, out_dir, ws_path, task):
    root = Path(ws_path)
    drafts = s["drafts"]
    md = [p for p in s["files"] if p.suffix == ".md"]
    reviews = [p for p in md if p.name.endswith("-review.md")]
    ideation = [p for p in md if p.parent.name == "logs" and re.search(r"-(ideation|wild[^/]*|obvious)\.md$", p.name)]
    clear_slot(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    H.write_md(out_dir / "draft.md", RA.cat(drafts) if drafts else "\n\n---\n\n".join(s["replies"]))
    if reviews:
        H.write_md(out_dir / "review.md", RA.cat(reviews))
    if ideation:
        H.write_md(out_dir / "ideator.md", RA.cat(ideation))
    run_logs = [p for p in md if p.parent.name == "logs" and RA.RUN_LOG_RE.match(p.name)]
    log = session_log(s, task, root) + "".join(f"\n## Agent's run log ({p.name})\n\n{H.rd(p)}\n" for p in run_logs)
    H.write_md(out_dir / "log.md", log)
    raw = out_dir / "raw"
    raw.mkdir(exist_ok=True)
    shutil.copy2(s["file"], raw / "transcript.jsonl")
    rel = lambda ps: [str(p.relative_to(root)) for p in ps]
    # Same shape as fresh runs' operator_messages; spent_usd isn't known for a hand session.
    ops = [{"kind": "operator", "spent_usd": None, "text": u, "time": t} for t, u in s["users"][1:]]
    H.write_json(out_dir / "meta.json", {
        "source": "import", "source_session": s["id"], "imported": now(),
        "workspace": str(ws_path), "task": task, "started": s["start"], "ended": s["end"],
        "exact_prompt": s["exact_prompt"], "operator_message_count": len(ops), "operator_messages": ops,
        "cost_usd": None, "cost_usd_transcript_min": s["cost_estimate"], "cost_usd_transcript_logged": s["cost_logged"],
        "draft_files": rel(drafts),
        **({"subagent_logs_missing": s["subagent_logs_missing"]} if s["subagent_logs_missing"] else {}),
        **({"flags": ["draft_modified_after_session"], "draft_modified_after_session": rel(s["late"])}
           if s["late"] else {})}, indent=1)
    return drafts


# ---------------------------------------------------------------- earlier autoeval runs
def is_import(meta):
    src = meta.get("source") or ""
    return src == "import" or src.startswith(("session:", "eval:"))


def source_key(meta, fallback):
    """What a run in a slot came from, as import_all names candidates ("session:<id>" or "eval:<ev>/<ws>/<t>/<rep>").
    Also reads metas written before "source": "import"."""
    if meta.get("source") == "import":
        return f"eval:{meta['source_run']}" if meta.get("source_run") else f"session:{meta.get('source_session')}"
    return meta.get("source") or fallback


def slots(version, tid):
    """[(rep, folder)] for every slot of this task that holds a draft, gaps included."""
    d = H.run_dir(version, tid)
    if not d.is_dir():
        return []
    reps = [(1, d)] + sorted((int(p.name[1:]), p) for p in d.iterdir() if re.fullmatch(r"r\d+", p.name) and p.is_dir())
    return [(r, p) for r, p in reps if H.run_file(version, tid, "draft", r)]


def read_meta(d):
    return json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}


def first_prompt_of_run(d, meta):
    """The first message a fresh run's agent got, from its session file in the copy's project folder, and that
    session's id. ("", id or None) if it can't be found."""
    sid = None
    if (d / "raw" / "transcript.jsonl").exists():
        sid = next((e["session_id"] for e in read_jsonl(d / "raw" / "transcript.jsonl") if e.get("session_id")), None)
    f = project_dir(meta["copy"]) / f"{sid}.jsonl" if sid and meta.get("copy") else None
    if not f or not f.exists():
        return "", sid
    return next((text_of(e["message"]["content"]) for e in read_jsonl(f) if is_prompt(e)), ""), sid


def eval_runs(ws_path, tid, skip_eval):
    """[(source id, run folder, started, meta)] for finished fresh runs of this workspace path in other autoevals."""
    out = []
    for man in sorted(H.RUNS.glob("*/eval.json")):
        ev = man.parent.name
        if ev == skip_eval:
            continue
        if ev.startswith("techtest-"):
            continue  # a techtest's runs are of trial arms and its own baseline, never earlier eval runs (B18)
        for name, p in json.loads(man.read_text())["workspaces"].items():
            if Path(p) != Path(ws_path):
                continue
            for rep, d in slots(f"{ev}/{name}", tid):
                meta = read_meta(d)
                if is_import(meta):
                    continue  # imported there; this eval imports the original itself
                out.append((f"eval:{ev}/{name}/{tid}/{rep}", d, iso(utc(meta.get("started", ""))), meta))
    return out


def existing_sources(version, tid):
    return {source_key(read_meta(d), f"eval:{version}/{tid}/{rep}") for rep, d in slots(version, tid)}


def free_slots(version, tid, reps):
    """Slots 1..reps without a draft (empty, or a failed run's leftovers), lowest first."""
    taken = {r for r, _ in slots(version, tid)}
    paused = {r for r, d in H.rep_dirs(version, tid).items() if read_meta(d).get("status") == "paused"}
    return [r for r in range(1, reps + 1) if r not in taken | paused]


def import_all(ev, ws, tasks, ids, dry=False, reps=None, enabled=None):
    """Import completed runs for every workspace and task, into at most `reps` slots per workspace and task
    (default [autoeval] reps). enabled=False (default [autoeval] import_completed) imports nothing.
    Returns rows for the import summary."""
    auto = H.CFG.get("autoeval", {})
    if not (auto.get("import_completed", True) if enabled is None else enabled):
        return []
    reps = auto.get("reps", 1) if reps is None else reps
    mp = H.RUNS / ev / "eval.json"  # `autoeval.py redo` adds pairs here so they're run fresh
    eval_dont = json.loads(mp.read_text()).get("dont_import", []) if mp.exists() else []
    rows = []
    for name, path in ws.items():
        sessions = match_sessions(path, {t: tasks[t] for t in ids})
        for tid in ids:
            version = f"{ev}/{name}"
            request = tasks[tid]["request"]
            budgeted = bool(budget_sentence(request))
            if f"{name} {tid}" in auto.get("dont_import", []) + eval_dont:
                where = "config.toml" if f"{name} {tid}" in auto.get("dont_import", []) else "eval.json, after a redo"
                rows.append((name, tid, "-", "", f"skipped: in dont_import ({where}), so it will be run fresh"))
                continue
            have = existing_sources(version, tid)
            cands, usable = [], []
            for s in sessions.get(tid, []):
                s["exact_prompt"] = exact_prompt(s["users"][0][1] if s["users"] else "", request, path)
                why = ("false start (no draft file, no reply of %d+ words)" % MIN_REPLY_WORDS
                       if not (s["drafts"] or long_reply(s))
                       else "first message isn't the task's current request with its budget sentence"
                       if budgeted and not s["exact_prompt"] else "")
                if why:
                    rows.append((name, tid, f"session:{s['id']}", s["start"][:10], f"skipped: {why}"))
                else:
                    usable.append(s)
            if usable:  # several sessions for one task: the longest is the run, the rest were restarts
                cands.append(("session", usable[0], usable[0]["start"]))
                for s in usable[1:]:
                    rows.append((name, tid, f"session:{s['id']}", s["start"][:10], "skipped: shorter session for same task"))
            for r in eval_runs(path, tid, ev):
                first, sid = first_prompt_of_run(r[1], r[3])
                r[3]["_exact"], r[3]["_sid"] = (exact_prompt(first, request, path) if first else None), sid
                if budgeted and not r[3]["_exact"]:
                    rows.append((name, tid, r[0], r[2][:10], "skipped: its request wasn't the task's current one "
                                 "with its budget sentence" if first else "skipped: can't find the request it got"))
                    continue
                cands.append(("eval", r, r[2]))
            free = free_slots(version, tid, reps)
            for kind, c, started in sorted(cands, key=lambda c: ts(c[2])):
                src = f"session:{c['id']}" if kind == "session" else c[0]
                if src in have:
                    continue
                if not free:
                    rows.append((name, tid, src, started[:10], f"skipped: all {reps} rep(s) already filled"))
                    continue
                rep = free.pop(0)
                out = H.run_dir(version, tid, rep)
                note = f"-> {out.relative_to(H.RUNS)}"
                if not dry:
                    if kind == "session":
                        drafts = import_session(c, out, path, tid)
                        note += f" (draft: {', '.join(p.name for p in drafts) or 'replies'})"
                        if c["late"]:
                            note += " FLAG: draft changed after the session ended"
                    else:
                        clear_slot(out)
                        out.mkdir(parents=True, exist_ok=True)
                        for n in COPY_NAMES:
                            s = c[1] / n
                            if s.is_dir():
                                shutil.copytree(s, out / n)
                            elif s.exists():
                                shutil.copy2(s, out / n)
                        m = read_meta(out)
                        ops = m.get("operator_messages")
                        m.update({"source": "import", "source_run": src.removeprefix("eval:"),
                                  "source_session": c[3]["_sid"], "imported": now(), "started": started,
                                  "exact_prompt": c[3]["_exact"],
                                  "operator_message_count": len(ops) if isinstance(ops, list) else ops or 0})
                        H.write_json(out / "meta.json", m, indent=1)
                have.add(src)
                rows.append((name, tid, src, started[:10], note))
    return rows
