# Self-Improving AI Agent for Advocacy

A research, writing and strategy assistant based on [Claude Code](https://code.claude.com/docs), designed for animal/AI welfare advocates. You send a prompt like "draft emails to my senators on every current bill affecting animal agriculture", and Claude handles the rest. It is self-improving: the maintainer's copy (or yours if enabled) finds new tools, tests them, and ships what works as an update.

**Everything is free but requires a Claude Pro plan.** No coding knowledge necessary. 

**This is the beta version.** Currently, this workspace performs about as well as a carefully prompted, guided vanilla Claude Code session with access to ethics and instructions files. The maintainer has little coding/AI knowledge and is unsure if many of the workspace's features really help. My aim is to give advocates without a powerful AI setup access to one, and to give advocates with deeper AI knowledge a prototype to build off of.

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

## Folders

2 main folders. Ships with both, but the eval harness won't be necessary for many casual users:

| Folder | What it is |
|---|---|
| [`ai-welfare-workspace/`](ai-welfare-workspace/) | **The assistant.** You ask for help with a task; Claude researches with helper agents, writes, has the draft critiqued and fact-checked, and saves it for you. Nothing is sent or posted without your OK. "Tech watch" scans for new tools and prepares each one as an exact change to the workspace. It's off by default: the maintainer runs it and ships the results, so you get them with `python3 scripts/update.py` without spending your own usage. |
| [`ai-welfare-eval-harness/`](ai-welfare-eval-harness/) | **What makes the improvement trustworthy.** Before a change is shipped or applied, it runs the workspace with and without it on real advocacy tasks (AI welfare or animal advocacy), has ChatGPT and Gemini judge the drafts blind, and keeps a held-out set that no change is ever tuned on. Changes that do worse aren't applied; unclear ones can be tried and re-checked later. |

## Status

Version 0.1.0. Tested with Claude Code 2.1.317 on Linux (WSL2); macOS and Windows are untested. Reports are welcome:
open an issue.

## License and intended use

[PolyForm Noncommercial 1.0.0](LICENSE): free for personal use, research, charities, education and public bodies;
not for commercial use. Please also read [INTENDED-USE.md](INTENDED-USE.md): this is built to help beings whose
welfare is neglected, and we ask that it not be used against their interests.

## Disclaimer
**Updates come from one person with limited usage.** The workspace is maintained by a single individual, so
updates may be slow or stop. Future Claude Code updates may break the workspace or make it less useful than plain
Claude Code. If you run into either problem, or anything else in the workspace breaks, I recommend switching to
vanilla Claude Code (run `claude` in any other folder) rather than waiting for my updates.

**Please use caution when using this workspace for social media.** Clearly AI-generated advocacy content can damage the credibility of the animal- and digital-minds-advocacy fields.
