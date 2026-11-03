"""Ask an outside model (ChatGPT via Codex, or the Gemini API) one question, and log what was sent. A fresh,
tool-less Claude session ("claude") stands in for an outside judge that isn't set up (see setup_judges.py).

Adapted from an ask_model.py helper. Differences: the prompt goes to Codex on stdin (four
drafts plus ETHICS_SHORT can exceed Linux's 128 KB limit on one command-line argument), Codex runs in an
empty folder with no saved session (so it has no eval files around it to look at), and calls are logged to
this folder's external-queries-log.md.
"""
import datetime, json, os, shutil, subprocess, tempfile, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "external-queries-log.md"
CODEX = Path.home() / ".npm-global" / "bin" / "codex"
if not CODEX.exists():
    CODEX = Path(shutil.which("codex") or CODEX)
GEMINI_MODEL = "gemini-3.8-flash"


class ModelError(Exception):
    pass


def log(model, summary, purpose):
    if not LOG.exists():
        LOG.write_text("# External AI queries log\n\nEvery prompt sent to an outside model by the eval scripts.\n\n"
                       "| Date and time | Model | Prompt (summary) | Purpose |\n|---|---|---|---|\n", encoding="utf-8")
    summary = summary.replace("\n", " ").replace("|", "/")[:300]
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"| {datetime.datetime.now().astimezone():%Y-%m-%d %H:%M %Z} | {model} | {summary} | {purpose} |\n")


def ask_chatgpt(prompt, model=None, timeout=1800, effort=None):
    with tempfile.TemporaryDirectory(prefix="codex-judge-") as d:
        out = Path(d) / "reply.md"
        cmd = [str(CODEX), "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-o", str(out)]
        if model:
            cmd += ["-m", model]
        if effort:
            cmd += ["-c", f"model_reasoning_effort={effort}"]
        r = subprocess.run(cmd + ["-"], input=prompt, cwd=d, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise ModelError(f"codex failed (exit {r.returncode}):\n{r.stderr[-2000:]}")
        reply = out.read_text(encoding="utf-8").strip() if out.exists() else r.stdout.strip()
    if not reply:
        raise ModelError("codex returned an empty reply")
    return reply


def ask_gemini(prompt, model=None, timeout=900, effort=None):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ModelError("GEMINI_API_KEY is not set")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model or GEMINI_MODEL}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            # 429/500/503/504 are usually transient (rate limit, overload); back off and retry, ~10 min in all
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(10 * 2 ** attempt)
                continue
            raise ModelError(f"gemini failed: HTTP {e.code}\n{e.read().decode()[:2000]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < 5:
                time.sleep(10 * 2 ** attempt)
                continue
            raise ModelError(f"gemini failed: {e!r}")
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):  # e.g. a blocked prompt: no candidates
        raise ModelError(f"gemini returned no answer: {json.dumps(data)[:2000]}")
    reply = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not reply:
        raise ModelError("gemini returned an empty reply")
    return reply


def ask_claude(prompt, model=None, timeout=1800, effort=None, budget_usd=3):
    """A fresh headless Claude session in an empty folder: no tools, no MCP servers, no CLAUDE.md, no memory of
    the runs it judges. Uses your Claude plan's usage, like the runs do."""
    cmd = ["claude", "-p", "--model", model or "opus", "--output-format", "json", "--max-budget-usd",
           str(budget_usd), "--permission-mode", "dontAsk", "--tools", "", "--strict-mcp-config"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    with tempfile.TemporaryDirectory(prefix="claude-judge-") as d:
        try:
            r = subprocess.run(cmd, input=prompt, cwd=d, env=env, capture_output=True, text=True, timeout=timeout)
            out = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            raise ModelError(f"claude failed: {e!r}")
    if out.get("is_error") or not (out.get("result") or "").strip():
        raise ModelError(f"claude returned no answer: {json.dumps(out)[:2000]}")
    return out["result"].strip()


def available(which):
    """Whether a judge can be called from here: (True, "") or (False, what to do about it)."""
    if which == "chatgpt":
        if not CODEX.exists():
            return False, "the Codex CLI isn't installed (npm install -g @openai/codex)"
        r = subprocess.run([str(CODEX), "login", "status"], capture_output=True, text=True)
        return (True, "") if r.returncode == 0 else (False, "Codex isn't signed in (run: codex login)")
    if which == "gemini":
        return (True, "") if os.environ.get("GEMINI_API_KEY") else (False, "GEMINI_API_KEY isn't set")
    if which == "claude":
        return (True, "") if shutil.which("claude") else (False, "Claude Code isn't on PATH")
    return True, ""


ASK = {"chatgpt": ask_chatgpt, "gemini": ask_gemini, "claude": ask_claude}
NAMES = {"chatgpt": "ChatGPT", "gemini": "Gemini", "claude": "Claude (stand-in)"}


def ask(which, prompt, summary, purpose, model=None, effort=None):
    log(f"{which} ({model or 'default model'}{', ' + effort + ' effort' if effort else ''})" if which == "chatgpt"
        else f"claude ({model or 'opus'}, stand-in judge)" if which == "claude" else f"{model or GEMINI_MODEL} (API)",
        summary, purpose)
    return ASK[which](prompt, model, effort=effort)
