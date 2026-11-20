# Self-Improving AI Agent for Advocacy

## Getting started

1. Install [Claude Code](https://code.claude.com/docs/en/setup) and sign in. You also need [git](https://git-scm.com/downloads) and [Python](https://www.python.org/downloads/release/python-31410/) (3.12 or newer).

2. In a terminal, copy and paste these 4 lines and hit enter after each one:

   ```
   git clone https://github.com/SentienceProject/Self-Improving-AI-Agent-for-Advocacy.git
   cd Self-Improving-AI-Agent-for-Advocacy
   cd ai-welfare-workspace
   python3 setup.py
   ```

   Setup asks a few questions. Reply `y` and hit enter for yes, or `n` and hit enter for no.

3. In the same terminal, type `claude` into the command line and hit enter. Trust the folder when asked, and ask Claude something.

Something not working? See [If something goes wrong](ai-welfare-workspace/README.md#if-something-goes-wrong).

Casual mode (recommended): Windows, Linux, or MacOS (untested). 
Hardened mode: Linux, or Windows with [WSL2](https://learn.microsoft.com/windows/wsl/install) (macOS untested).
No computer of your own, or you work from claude.ai in a browser? See [Using it from claude.ai](ai-welfare-workspace/README.md#using-it-from-claudeai-no-computer-setup): nothing to install, and your work is saved to your own private GitHub copy.

## Features

**Everything is free but requires a Claude Pro plan.** No coding knowledge necessary.

The agent can:
- Research, draft, post, write code, make websites, and navigate browsers.
- Adjust the creativity of a draft.
- Import all of your work from other AI agents.
- Write your personal ethics files that will drive the agent's behavior.
- Transcribe, watch, and analyze videos.
- Check drafts with connected AI models from other companies.
- Install public repos (with your consent).
- Convert and read pdfs.
- Write human-like prose.

Other features:
- Maintainer's copy checks for new technology daily, applies applicable tech to the workspace, and ships improvements as an update.
- Evaluation workspace for making your own improvements.
- Save promising ideas into a backlog to automatically run using remaining plan usage at the end of the week.
- Find and download a corpus of papers for agent drafts to use.
- Citation checker automatically applied to drafts.
- Hardened mode that runs Claude in a bubblewrap sandbox, limiting network access and disallowing code outside the sandbox.

## Folders

Ships with 2 folders, but the eval harness won't be necessary for many casual users:

| Folder | What it is |
|---|---|
| [`ai-welfare-workspace/`](ai-welfare-workspace/) | **The assistant.** |
| [`ai-welfare-eval-harness/`](ai-welfare-eval-harness/) | **Tests changes to the workspace and evaluates them.** |

See the [workspace README](ai-welfare-workspace/README.md) for more information. Refer to the [harness README](ai-welfare-eval-harness/README.md) for testing and comparing versions.

## Disclaimers
**This is the beta version.** Currently, this workspace performs about as well as a carefully prompted, guided vanilla Claude Code session with access to ethics and instructions files. The maintainer has little coding/AI knowledge and is unsure if many of the workspace's features really help. My aim is to give advocates without a powerful AI setup access to one, and to give advocates with deeper AI knowledge a prototype to build off of.

**Updates come from one person with limited usage.** The workspace is maintained by a single individual, so
updates may be slow or stop. Future Claude Code updates may break the workspace or make it less useful than plain
Claude Code. If you run into either problem, or anything else in the workspace breaks, I recommend switching to
vanilla Claude Code (run `claude` in any other folder) rather than waiting for my updates.

**Please use caution when using this workspace for social media.** Clearly AI-generated advocacy content can damage the credibility of the animal- and digital-minds-advocacy fields.

## Status

Version 0.1.0. Tested with Claude Code 2.1.327 on Linux (WSL2) and Windows; macOS is untested. Reports are welcome:
open an issue.

## License and intended use

[PolyForm Noncommercial 1.0.0](LICENSE): free for personal use, research, charities, education and public bodies;
not for commercial use. Please also read [INTENDED-USE.md](INTENDED-USE.md): this is built to help beings whose
welfare is neglected, and we ask that it not be used against their interests.
