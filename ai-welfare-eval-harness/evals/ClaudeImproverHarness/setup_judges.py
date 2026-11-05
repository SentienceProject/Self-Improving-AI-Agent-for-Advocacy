#!/usr/bin/env python3
"""Set up the outside judges (ChatGPT through the Codex CLI, and Gemini through its API), then test them.

    python3 setup_judges.py            check each judge, offer to fix what's missing, then test the ones that work
    python3 setup_judges.py --check    only report what's set up; change nothing, call nothing

Outside judges matter: Claude judging Claude's drafts may share their blind spots. Free tiers are enough. Any judge
that isn't set up is replaced by a fresh Claude session when you run autoeval.py (standin_judge in config.toml),
so an eval still works without them, just with weaker judging.

This script never reads, stores or prints an API key. It tells you where to put one.
"""
import shutil, subprocess, sys

import external_models as X

TEST = "Hi! This is a quick connection test. If you're willing, please reply with just: ok. Thank you!"


def yes(q):
    try:
        return input(f"{q} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def setup_chatgpt(check):
    ok, why = X.available("chatgpt")
    if ok:
        return True
    print(f"ChatGPT: {why}.")
    if check:
        return False
    if not X.CODEX.exists():
        npm = shutil.which("npm")
        if not npm:
            print("  Install Node.js (which includes npm) first, then run this again: https://nodejs.org")
            return False
        if not yes("  Install the Codex CLI now (npm install -g @openai/codex)?"):
            return False
        if subprocess.run([npm, "install", "-g", "@openai/codex"]).returncode != 0:
            print("  The install failed; see npm's message above. If it's a permissions error, see\n"
                  "  https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally")
            return False
        X.CODEX = X.Path(shutil.which("codex") or X.CODEX)
    if yes("  Sign in to Codex with your ChatGPT account now (opens a browser)?"):
        subprocess.run([str(X.CODEX), "login"])
    return X.available("chatgpt")[0]


def setup_gemini(check):
    ok, why = X.available("gemini")
    if ok:
        return True
    print(f"Gemini: {why}.")
    if not check:
        print("  1. Get a free API key at https://aistudio.google.com/apikey (a Google account is enough).\n"
              "  2. Add this line to your shell's startup file (~/.bashrc on Linux, ~/.zshrc on macOS), with your key:\n"
              "       export GEMINI_API_KEY='your-key'\n"
              "  3. Open a new terminal and run this script again.")
    return False


def test(which):
    print(f"Testing {X.NAMES[which]} ...", end=" ", flush=True)
    try:
        reply = X.ask(which, TEST, "connection test", "setup_judges.py")
    except X.ModelError as e:
        print(f"FAILED: {str(e)[:300]}")
        return False
    print(f"ok (replied: {reply[:60]!r})")
    return True


def main():
    check = "--check" in sys.argv
    ready = {"chatgpt": setup_chatgpt(check), "gemini": setup_gemini(check)}
    if not check:
        for which in [w for w, ok in ready.items() if ok]:
            ready[which] = test(which)
    print()
    for which, ok in ready.items():
        print(f"  {X.NAMES[which]:8} {'ready' if ok else 'not set up: a fresh Claude session will judge in its place'}")
    print("\nPrivacy: judges see the task text, its judge notes and the drafts. On free tiers, prompts may be used "
          "for training:\n  - Gemini's free API tier lets Google use prompts and replies to improve its models, "
          "and people may read them.\n    A paid key (billing enabled) doesn't.\n  - ChatGPT: turn off \"Improve "
          "the model for everyone\" under Settings > Data controls.\nIf that matters for a task, keep it to "
          "judges you're comfortable with (judges in config.toml).")
    if any(ready.values()) and not check:
        print("\nThe workspace can use these logins too: Claude may ask ChatGPT and Gemini for second opinions during "
              "your tasks\n(scripts/ask_model.py), without asking you first, even when it sends files from workspace/. "
              "Every call is logged\nin workspace/logs/outside-models.md. To be asked first, run in the workspace: "
              "python3 setup.py --ask-outside-models")


if __name__ == "__main__":
    main()
