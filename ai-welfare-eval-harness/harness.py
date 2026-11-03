#!/usr/bin/env python3
"""Shortcut so `python3 harness.py ...` works from the project root.
It runs the LEGACY rubric judge, evals/legacy-rubric-eval/harness/rubric_harness.py (until 2026-10-26 this was
evals/ClaudeImproverHarness/harness.py). The current evaluation is evals/ClaudeImproverHarness/autoeval.py."""
import runpy, sys
from pathlib import Path

real = Path(__file__).resolve().parent / "evals" / "legacy-rubric-eval" / "harness" / "rubric_harness.py"
sys.argv[0] = str(real)
runpy.run_path(str(real), run_name="__main__")
