"""
eval_suite/config.py
====================
Central configuration for the AI PM-OS evaluation suite.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Root paths
# ---------------------------------------------------------------------------
EVAL_SUITE_DIR = Path(__file__).parent.resolve()
GOLDEN_CASES_DIR = EVAL_SUITE_DIR / "golden_cases"
REPORTS_DIR = EVAL_SUITE_DIR / "reports"

# Ensure output directory exists at import time
REPORTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Model registry
# Maps project key → model string used when calling the Groq/OpenAI endpoint
# ---------------------------------------------------------------------------
MODELS: dict[str, str] = {
    "p1": "openai/gpt-oss-20b",
    "p2": "openai/gpt-oss-120b",
    "p3": "openai/gpt-oss-120b",
    "p4": "openai/gpt-oss-20b",
    "p5": "openai/gpt-oss-20b",
    "p6": "openai/gpt-oss-20b",
}

# ---------------------------------------------------------------------------
# Scoring thresholds — per project
#
# A project is considered "green" when at least this fraction of its golden
# cases pass.  P4 uses 0.70 because it contains 2 boundary cases where the
# rubric's hard thresholds (D1=40%, D7 drop=30pp) sit at the exact ambiguity
# line — forcing 80% would make the bar dishonest.  Boundary cases are also
# flagged distinctly in the scorer output (PASS★ / FAIL★) so they are never
# hidden inside the aggregate pass rate.
# ---------------------------------------------------------------------------
PASS_THRESHOLD: dict[str, float] = {
    "p1": 0.80,
    "p3": 0.80,
    "p4": 0.70,
}

_DEFAULT_THRESHOLD: float = 0.80


def get_threshold(project: str) -> float:
    """Return the pass threshold for *project*, defaulting to _DEFAULT_THRESHOLD."""
    return PASS_THRESHOLD.get(project, _DEFAULT_THRESHOLD)
