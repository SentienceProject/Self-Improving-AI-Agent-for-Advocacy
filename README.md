# Self-Improving AI Agent for Advocacy

A ready-made setup for [Claude Code](https://code.claude.com/docs) that turns it into a careful research, writing and
strategy assistant for people working on the welfare of animals, digital minds, or anyone else whose welfare is
neglected, and that keeps improving itself: it finds new tools and techniques, tests each change on real advocacy
tasks with outside judges, and keeps what works.

It has two parts, and you need both:

| Folder | What it is |
|---|---|
| [`ai-welfare-workspace/`](ai-welfare-workspace/) | **The assistant.** You ask in plain language ("draft an email to my state senator about this bill"); Claude researches with helper agents, writes, has the draft critiqued and fact-checked, and saves it for you. Nothing is sent or posted without your OK. "Tech watch" scans for new tools every day and prepares each one as an exact change to the workspace. |
| [`ai-welfare-eval-harness/`](ai-welfare-eval-harness/) | **What makes the improvement trustworthy.** Before a change is applied, it runs the workspace with and without it on real advocacy tasks (AI welfare or animal advocacy), has ChatGPT and Gemini judge the drafts blind, and keeps a held-out set that no change is ever tuned on. Changes that do worse aren't applied; unclear ones can be tried and re-checked later. |

## Getting started

1. Install [Claude Code](https://code.claude.com/docs/en/setup) and sign in (a Claude Pro or Max plan, or API
   credit). You also need [git](https://git-scm.com/downloads) and Python 3.12 or newer. On Windows,
   [WSL2](https://learn.microsoft.com/windows/wsl/install) is the tested way.
2. In a terminal:

   ```
   git clone https://github.com/SentienceProject/Self-Improving-AI-Agent-for-Advocacy.git
   cd Self-Improving-AI-Agent-for-Advocacy
   cd ai-welfare-workspace
   python3 setup.py
   ```

   Setup asks a few questions (your work, how tech watch runs, phone notifications, bringing in what other AI tools
   know about you), then offers optional extras.
3. Start Claude in the same folder: `claude`. Trust the folder when asked, and ask for something.

Something not working? See [If something goes wrong](ai-welfare-workspace/README.md#if-something-goes-wrong).

The [workspace README](ai-welfare-workspace/README.md) has the full guide: the two safety modes, tech watch, phone
notifications, and what each part costs. The [harness README](ai-welfare-eval-harness/README.md) covers testing and
comparing versions.

## Status

Version 0.1.0. Tested with Claude Code 2.1.317 on Linux (WSL2); macOS and Windows are untested. Reports are welcome:
open an issue.

## License and intended use

[PolyForm Noncommercial 1.0.0](LICENSE): free for personal use, research, charities, education and public bodies;
not for commercial use. Please also read [INTENDED-USE.md](INTENDED-USE.md): this is built to help beings whose
welfare is neglected, and we ask that it not be used against their interests.

## Disclaimer
**Please use caution when using this workspace for social media.** Clearly AI-generated advocacy content can damage the credibility of the animal- and digital-minds-advocacy fields.
