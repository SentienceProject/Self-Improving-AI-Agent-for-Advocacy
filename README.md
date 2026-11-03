# Self-Improving AI Agent for Advocacy

A ready-made setup for [Claude Code](https://code.claude.com/docs) that turns it into a careful research, writing and
strategy assistant for people working on the welfare of animals, digital minds, or anyone else whose welfare is
neglected, plus the tools to check, with outside judges, whether changes to it actually make it better.

It has two parts:

| Folder | What it is | Who needs it |
|---|---|---|
| [`ai-welfare-workspace/`](ai-welfare-workspace/) | The assistant. You ask in plain language ("draft an email to my state senator about this bill"); Claude researches with helper agents, writes, has the draft critiqued and fact-checked, and saves it for you. Nothing is sent or posted without your OK. It also keeps itself up to date ("tech watch"): it looks for new tools and prepares changes, which you approve. | Everyone. Start here. |
| [`ai-welfare-eval-harness/`](ai-welfare-eval-harness/) | Tests whether a change to the assistant helps: runs versions of it on fixed tasks (AI welfare or animal advocacy, locked so agents can't read them), has ChatGPT and Gemini judge the drafts blind, and keeps a held-out set. Tech watch uses it to test a change before applying it. | Optional: for people who want changes tested, or want to compare setups. |

## Getting started

1. Install [Claude Code](https://code.claude.com/docs/en/setup) and sign in (a Claude Pro or Max plan, or API
   credit).
2. Download this repository (Code → Download ZIP, or `git clone`, which lets you get updates later).
3. Open the `ai-welfare-workspace` folder in Claude Code (`cd ai-welfare-workspace`, then `claude`), trust the folder
   when asked, and tell it about your work.

The [workspace README](ai-welfare-workspace/README.md) has the full guide: the two safety modes, tech watch, phone
notifications, and what each part costs. The [harness README](ai-welfare-eval-harness/README.md) covers evaluation.

## Status

Version 0.1.0. Tested with Claude Code 2.1.317 on Linux (WSL2); macOS and Windows are untested. Reports are welcome:
open an issue.

## License and intended use

[PolyForm Noncommercial 1.0.0](LICENSE): free for personal use, research, charities, education and public bodies;
not for commercial use. Please also read [INTENDED-USE.md](INTENDED-USE.md): this is built to help beings whose
welfare is neglected, and we ask that it not be used against their interests.
