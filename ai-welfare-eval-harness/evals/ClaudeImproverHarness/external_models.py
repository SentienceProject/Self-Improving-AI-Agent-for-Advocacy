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
# The Gemini key lives in the workspace's git-ignored .env (setup_judges.py saves it there), where the workspace's
# scripts/ask_model.py finds it too, and Claude's tools can't read it. A GEMINI_API_KEY variable also works.
WORKSPACE_ENV = Path(os.environ.get("AI_WELFARE_ENV_FILE") or HERE.parents[2] / "ai-welfare-workspace" / ".env")


def read_env_file(path=None):
    """KEY=value lines of a .env file ({} if it's missing or unreadable)."""
    try:
        text = Path(path or WORKSPACE_ENV).read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.removeprefix("export ").split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def gemini_key():
    return os.environ.get("GEMINI_API_KEY") or read_env_file().get("GEMINI_API_KEY") or ""


def save_gemini_key(key, path=None):
    """Write GEMINI_API_KEY=key into the .env file (other lines kept), readable only by you."""
    path = Path(path or WORKSPACE_ENV)
    try:
        lines = [l for l in path.read_text(encoding="utf-8-sig").splitlines()
                 if l.strip().removeprefix("export ").split("=", 1)[0].strip() != "GEMINI_API_KEY"]
    except OSError:
        lines = []
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines + [f"GEMINI_API_KEY={key}"]) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def node_on_path():
    """Windows: right after Node.js is installed, this window's PATH doesn't have it yet, and the Codex CLI (a .cmd
    that runs "node") fails with '"node" is not recognized'. Add Node's folders for this process and its children."""
    if os.name != "nt":
        return
    have = os.environ.get("PATH", "").lower().split(os.pathsep)
    for d in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs",
              Path(os.environ.get("APPDATA", "~")) / "npm"):
        if d.is_dir() and str(d).lower() not in have:
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


node_on_path()


def find_codex():
    """The Codex CLI: on PATH, or where npm puts it (also right after an install, before PATH is refreshed)."""
    for p in (shutil.which("codex"), Path.home() / ".npm-global" / "bin" / "codex",
              Path(os.environ.get("APPDATA", "~")) / "npm" / "codex.cmd"):
        if p and Path(p).exists():
            return Path(p)
    return Path.home() / ".npm-global" / "bin" / "codex"


CODEX = find_codex()
GEMINI_MODEL = "gemini-4-hydrogen"
# Thinking level sent with every Gemini call unless one is passed (config.toml gemini_effort), so a change to
# Google's default can't quietly change the judge. "" = the model's own default.
GEMINI_EFFORT = "high"


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


def ask_gemini(prompt, model=None, timeout=900, effort=None, retries=5):
    key = gemini_key()
    if not key:
        raise ModelError("no Gemini key (GEMINI_API_KEY, or the workspace's .env)")
    level = effort or GEMINI_EFFORT
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model or GEMINI_MODEL}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                         **({"generationConfig": {"thinkingConfig": {"thinkingLevel": level}}} if level else {})
                         }).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            # 429/500/503/504 are usually transient (rate limit, overload); back off and retry, ~10 min in all
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(10 * 2 ** attempt)
                continue
            raise ModelError(f"gemini failed: HTTP {e.code}\n{e.read().decode()[:2000]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
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


def check_gemini_key(timeout=20):
    """Checks the key without using any quota: lists the models it may use. Returns "ok", "bad: ..." or
    "unreachable: ..." (busy, slow, or offline: not the key's fault)."""
    key = gemini_key()
    if not key:
        return "bad: no Gemini key is set"
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                                 headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return "ok"
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return f"bad: Google rejected the key (HTTP {e.code})"
        return f"unreachable: HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return f"unreachable: {type(e).__name__}"


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
        return (True, "") if gemini_key() else (False, "no Gemini key yet")
    if which == "claude":
        return (True, "") if shutil.which("claude") else (False, "Claude Code isn't on PATH")
    return True, ""


ASK = {"chatgpt": ask_chatgpt, "gemini": ask_gemini, "claude": ask_claude}
NAMES = {"chatgpt": "ChatGPT", "gemini": "Gemini", "claude": "Claude (stand-in)"}


def ask(which, prompt, summary, purpose, model=None, effort=None):
    log(f"{which} ({model or 'default model'}{', ' + effort + ' effort' if effort else ''})" if which == "chatgpt"
        else f"claude ({model or 'opus'}, stand-in judge)" if which == "claude"
        else f"{model or GEMINI_MODEL} (API{', ' + (effort or GEMINI_EFFORT) + ' thinking' if effort or GEMINI_EFFORT else ''})",
        summary, purpose)
    return ASK[which](prompt, model, effort=effort)
