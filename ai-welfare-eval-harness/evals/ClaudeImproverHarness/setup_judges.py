#!/usr/bin/env python3
"""Set up the outside judges (ChatGPT through the Codex CLI, and Gemini through its API), then test them.

    python3 setup_judges.py            check each judge, offer to fix what's missing, then test the ones that work
    python3 setup_judges.py --check    only report what's set up; change nothing, call nothing

Outside judges matter: Claude judging Claude's drafts may share their blind spots. Free tiers are enough. Any judge
that isn't set up is replaced by a fresh Claude session when you run autoeval.py (standin_judge in config.toml),
so an eval still works without them, just with weaker judging. Every step can be skipped (Enter, or Ctrl+C), and
each test gives up after a minute or two, so this never holds up the rest of setup.

The Gemini key you paste (hidden as you type) is saved in the workspace's git-ignored .env file, readable only by
you. The workspace's scripts/ask_model.py reads it from there, outside Claude Code's sandbox; Claude's own tools can't
read that file, and the key is never put in Claude's environment. This script never prints a key.
"""
import getpass, os, shutil, subprocess, sys
from pathlib import Path

import external_models as X
from style import S

TEST = "Hi! This is a quick connection test. If you're willing, please reply with just: ok. Thank you!"
WINDOWS = os.name == "nt"
LATER = "python3 setup_judges.py   (in this folder: ai-welfare-eval-harness/evals/ClaudeImproverHarness)"


def yes(q):
    try:
        return input(f"{S.ask(q)} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def find_npm():
    for p in (shutil.which("npm"), Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npm.cmd"):
        if p and Path(p).exists():
            return str(p)
    return None


def install_node():
    """Node.js is what the Codex CLI runs on. Most people don't have it; say exactly how to get it."""
    print("  The ChatGPT judge uses the Codex CLI, OpenAI's small command-line app, which needs Node.js (a free\n"
          "  program many apps run on). You don't have it yet. To install it:")
    if WINDOWS:
        if shutil.which("winget") and yes("  Install Node.js now with Windows' own installer (winget; it may ask "
                                          "for permission)?"):
            r = subprocess.run(["winget", "install", "--id", "OpenJS.NodeJS.LTS", "-e",
                                "--accept-source-agreements", "--accept-package-agreements"])
            X.node_on_path()   # this window's PATH predates the install
            if r.returncode == 0 and find_npm():
                print("  " + S.ok("Node.js installed."))
                return find_npm()
            print("  " + S.warn("That didn't finish. You can install it by hand instead:"))
        print("  1. Go to " + S.b("https://nodejs.org") + " and download the version marked LTS (the Windows Installer).\n"
              "  2. Run it, clicking Next through the defaults.\n"
              "  3. Close this PowerShell window, open a new one, and run setup again.")
    elif sys.platform == "darwin":
        print("  1. Go to " + S.b("https://nodejs.org") + " and download the version marked LTS (the macOS Installer).\n"
              "  2. Open it and click through the defaults (or, if you use Homebrew: brew install node).\n"
              "  3. Open a new terminal window and run setup again.")
    else:
        print("  1. Install Node.js with your system's package manager, e.g.\n" + S.cmd("sudo apt install nodejs npm") +
              "\n     (or see " + S.b("https://nodejs.org/en/download") + ").\n"
              "  2. Run setup again.")
    return None


def setup_chatgpt(check):
    ok, why = X.available("chatgpt")
    if ok:
        return True
    print(f"ChatGPT: {why}.")
    if check:
        return False
    if not yes("  Set up ChatGPT as a judge now? A free ChatGPT account is enough (Enter skips)"):
        print(f"  skipped (any time later: {LATER})")
        return False
    if not X.CODEX.exists():
        npm = find_npm() or install_node()
        if not npm:
            return False
        print("  Installing the Codex CLI (npm install -g @openai/codex) ...")
        if subprocess.run([npm, "install", "-g", "@openai/codex"]).returncode != 0:
            print("  " + S.warn("The install failed; see npm's message above. If it's a permissions error, see\n"
                  "    https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally"))
            return False
        X.node_on_path()
        X.CODEX = X.find_codex()
        if not X.CODEX.exists():
            print("  " + S.warn("Installed, but this window can't see it yet: open a new terminal and run setup again."))
            return False
    print("  Next, sign in: a browser window opens; log in with your ChatGPT account, then come back here.")
    if yes("  Sign in now?"):
        subprocess.run([str(X.CODEX), "login"])
    return X.available("chatgpt")[0]


def setup_gemini(check):
    ok, why = X.available("gemini")
    if ok:
        where = ("your environment (GEMINI_API_KEY)" if os.environ.get("GEMINI_API_KEY")
                 else f"the workspace's .env ({X.WORKSPACE_ENV})")
        print(f"Gemini: found an API key in {where}.")
        if os.environ.get("GEMINI_API_KEY"):
            print("  Note: a key in your environment reaches every Claude Code session you start from it, so Claude "
                  "could read it.\n  To keep it from Claude, remove it from your environment (and from \"env\" in "
                  "~/.claude/settings.json),\n  then run this again: it saves the key in the workspace's .env instead.")
        return True
    print(f"Gemini: {why}.")
    if check:
        return False
    if not X.WORKSPACE_ENV.parent.is_dir():
        print(f"  (no workspace folder at {X.WORKSPACE_ENV.parent}; set GEMINI_API_KEY in your environment instead)")
        return False
    print("  Optional; setup goes on either way. Get a free API key at " + S.b("https://aistudio.google.com/apikey") +
          " (a Google account is\n  enough), then paste it below. It's saved in the workspace's .env file, which "
          "stays on this computer\n  and which Claude can't read.")
    try:
        key = getpass.getpass(S.ask("  Gemini API key (hidden as you type; Enter skips): ")).strip()
    except EOFError:
        key = ""
    if not key:
        print(f"  skipped (any time later: {LATER})")
        return False
    X.save_gemini_key(key)
    print(f"  saved in {X.WORKSPACE_ENV}")
    return True


def test(which):
    """One short call. Busy servers aren't your setup's fault: the judge is kept (evals retry later)."""
    if which == "gemini":   # checks the key itself, in seconds, without using the small free quota
        print("Checking the Gemini key (a few seconds; Ctrl+C skips) ...", end=" ", flush=True)
        try:
            r = X.check_gemini_key()
        except KeyboardInterrupt:
            print("skipped")
            return False
        if r == "ok":
            print(S.ok("ok") + " (Google accepted the key)")
            return True
        if r.startswith("unreachable"):
            print(S.ok("kept") + f" (couldn't reach Google just now: {r.split(': ', 1)[1]}; evals wait and retry. "
                  f"To check it later: {LATER})")
            return True
        print(S.warn(r.split(": ", 1)[1] + ". Get a new key at https://aistudio.google.com/apikey and set it again."))
        return False
    print(f"Testing {X.NAMES[which]} (up to 2 min; Ctrl+C skips) ...", end=" ", flush=True)
    X.log(which, "connection test", "setup_judges.py")
    try:
        reply = (X.ask_chatgpt(TEST, timeout=120) if which == "chatgpt" else
                 X.ask_gemini(TEST, timeout=25, retries=1))
    except KeyboardInterrupt:
        print("skipped")
        return False
    except subprocess.TimeoutExpired:
        print(S.warn("no reply in time; skipped. Try again later: " + LATER))
        return False
    except X.ModelError as e:
        msg = str(e)
        if any(f"HTTP {c}" in msg for c in (429, 500, 502, 503, 504)):
            print(S.ok("kept") + f" ({X.NAMES[which]} is too busy right now to check the key; evals wait and retry. "
                  f"To check it later: {LATER})")
            return True
        print(S.warn(f"failed: {msg.splitlines()[0][:200]}"))
        return False
    print(S.ok("ok") + f" (replied: {reply[:60]!r})")
    return True


def main():
    check = "--check" in sys.argv
    try:
        ready = {"chatgpt": setup_chatgpt(check), "gemini": setup_gemini(check)}
    except KeyboardInterrupt:
        print("\nskipped the judges' setup (any time later: " + LATER + ")")
        return
    if not check:
        for which in [w for w, ok in ready.items() if ok]:
            ready[which] = test(which)
    print()
    for which, ok in ready.items():
        print(f"  {X.NAMES[which]:8} " + (S.ok("ready") if ok else
                                         "not set up: a fresh Claude session will judge in its place"))
    print("\nPrivacy: judges see the task text, its judge notes and the drafts. On free tiers, prompts may be used "
          "for training:\n  - Gemini's free API tier lets Google use prompts and replies to improve its models, "
          "and people may read them.\n    A paid key (billing enabled) doesn't.\n  - ChatGPT: turn off \"Improve "
          "the model for everyone\" under Settings > Data controls.\nIf that matters for a task, keep it to "
          "judges you're comfortable with (judges in config.toml).")
    if any(ready.values()) and not check:
        print("\nThe workspace can use these logins too: Claude may ask ChatGPT and Gemini for second opinions during "
              "your tasks\n(scripts/ask_model.py), " + S.b("without asking you first") + ", even when it sends files "
              "from workspace/. Every call is logged\nin workspace/logs/outside-models.md. To be asked first, run in "
              "the workspace:\n" + S.cmd("python3 setup.py --ask-outside-models"))


if __name__ == "__main__":
    main()
