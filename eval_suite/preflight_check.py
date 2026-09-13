"""
eval_suite/preflight_check.py
==============================
Validates all external dependencies required to run the eval suite before
any project is evaluated.  Designed to be called as the very first step in
run_all.py so that missing config or dead servers produce a clear, numbered
error list instead of a confusing mid-run traceback.

Checks (in order):
  1. .env exists in eval_suite/ AND GROQ_API_KEY is present and non-empty.
  2. Every model string in config.MODELS is non-empty and plausible.
  3. golden_cases/ contains all three expected JSON files, each with ≥10 cases.
  4. eval_suite/baselines/ and eval_suite/reports/ both exist (created if not).
  5. P1 Next.js dev server is reachable (GET to root, not the POST-only API route).

Usage (standalone):
    python eval_suite/preflight_check.py

Returns exit code 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_EVAL_DIR = Path(__file__).parent.resolve()
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

import config  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENV_FILE        = _EVAL_DIR / ".env"
_GOLDEN_DIR      = _EVAL_DIR / "golden_cases"
_BASELINES_DIR   = _EVAL_DIR / "baselines"
_REPORTS_DIR     = _EVAL_DIR / "reports"

_REQUIRED_GOLDEN_FILES: list[str] = [
    "p1_skill_eval.json",
    "p3_rice_kano.json",
    "p4_retention.json",
]
_MIN_CASES_PER_FILE = 10

# Derive P1 root URL from the API endpoint defined in runner.py
_P1_API_URL  = "http://localhost:3000/api/evaluate"
_p1_parsed   = urlparse(_P1_API_URL)
_P1_ROOT_URL = f"{_p1_parsed.scheme}://{_p1_parsed.netloc}"   # http://localhost:3000
_P1_PROBE_TIMEOUT = 5  # seconds

# Very loose plausibility check — just needs a "/" in the name
def _looks_like_model_id(s: str) -> bool:
    return bool(s) and "/" in s and not s.strip() != s


# ---------------------------------------------------------------------------
# Individual check functions
# Each returns (ok: bool, messages: list[str])
# ---------------------------------------------------------------------------

def _check_env() -> tuple[bool, list[str]]:
    """Check 1: .env exists and GROQ_API_KEY is set."""
    messages: list[str] = []

    if not _ENV_FILE.exists():
        messages.append(
            f"[1] .env file not found at {_ENV_FILE}\n"
            "    Create it and add: GROQ_API_KEY=<your-key>"
        )
        return False, messages

    # Parse .env manually (no dotenv dependency required)
    env_vars: dict[str, str] = {}
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env_vars[k.strip()] = v.strip().strip('"').strip("'")

    if not env_vars.get("GROQ_API_KEY"):
        messages.append(
            "[1] GROQ_API_KEY is missing or empty in .env\n"
            f"    File: {_ENV_FILE}\n"
            "    Add: GROQ_API_KEY=<your-key>"
        )
        return False, messages

    return True, []


def _check_models() -> tuple[bool, list[str]]:
    """Check 2: config.MODELS entries are non-empty and look like model IDs."""
    messages: list[str] = []
    bad: list[str] = []

    for key, model in config.MODELS.items():
        if not model or not _looks_like_model_id(model):
            bad.append(f"'{key}': {model!r}")

    if bad:
        messages.append(
            f"[2] {len(bad)} model identifier(s) in config.MODELS look invalid:\n"
            + "".join(f"    {b}\n" for b in bad)
            + "    Expected format: 'provider/model-name' (e.g. 'openai/gpt-oss-20b')"
        )
        return False, messages

    return True, []


def _check_golden_files() -> tuple[bool, list[str]]:
    """Check 3: golden_cases/ contains all required files with ≥10 cases each."""
    messages: list[str] = []
    all_ok = True

    for filename in _REQUIRED_GOLDEN_FILES:
        path = _GOLDEN_DIR / filename
        if not path.exists():
            messages.append(
                f"[3] Missing golden case file: {path}\n"
                "    All three files must exist: "
                + ", ".join(_REQUIRED_GOLDEN_FILES)
            )
            all_ok = False
            continue

        try:
            cases = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            messages.append(f"[3] {filename} is not valid JSON: {exc}")
            all_ok = False
            continue

        if not isinstance(cases, list) or len(cases) < _MIN_CASES_PER_FILE:
            messages.append(
                f"[3] {filename} has {len(cases) if isinstance(cases, list) else 'non-list'} "
                f"case(s) — expected at least {_MIN_CASES_PER_FILE}"
            )
            all_ok = False

    return all_ok, messages


def _check_output_dirs() -> tuple[bool, list[str]]:
    """Check 4: ensure baselines/ and reports/ exist, creating them if needed."""
    messages: list[str] = []
    created: list[str] = []

    for d in (_BASELINES_DIR, _REPORTS_DIR):
        if not d.exists():
            try:
                d.mkdir(parents=True, exist_ok=True)
                created.append(str(d))
            except OSError as exc:
                messages.append(
                    f"[4] Could not create required directory {d}: {exc}"
                )
                return False, messages

    # Not a failure — creation is expected on first run; just informational
    return True, []


def _check_p1_server() -> tuple[bool, list[str]]:
    """Check 5: P1 Next.js dev server is reachable (GET to root, not the API route)."""
    messages: list[str] = []
    try:
        # Use the root URL so we never hit the POST-only /api/evaluate with a GET
        with urlopen(_P1_ROOT_URL, timeout=_P1_PROBE_TIMEOUT) as resp:
            _ = resp.status  # any HTTP response proves the server is alive
        return True, []
    except URLError as exc:
        reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
        messages.append(
            f"[5] P1 dev server not reachable at {_P1_ROOT_URL}\n"
            f"    Error: {reason}\n"
            "    Fix: cd d:/pm-skill-eval-harness && npm run dev\n"
            "    Wait for '✓ Ready' before re-running the eval suite."
        )
        return False, messages
    except Exception as exc:  # noqa: BLE001
        messages.append(
            f"[5] Unexpected error probing P1 server at {_P1_ROOT_URL}: {exc}"
        )
        return False, messages


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_preflight() -> bool:
    """
    Run all five preflight checks in order.

    Prints a numbered list of every failure, then returns True only if all
    checks pass.  Callers (run_all.py) should call sys.exit(1) if this
    returns False.
    """
    checks = [
        _check_env,
        _check_models,
        _check_golden_files,
        _check_output_dirs,
        _check_p1_server,
    ]

    all_failures: list[str] = []
    for check_fn in checks:
        ok, msgs = check_fn()
        if not ok:
            all_failures.extend(msgs)

    if all_failures:
        print("\n" + "=" * 70, flush=True)
        print("  PREFLIGHT FAILED — fix the following before running the suite:",
              flush=True)
        print("=" * 70, flush=True)
        for msg in all_failures:
            print(f"\n{msg}", flush=True)
        print("\n" + "=" * 70 + "\n", flush=True)
        return False

    print("  Preflight OK — all checks passed.\n", flush=True)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    passed = run_preflight()
    sys.exit(0 if passed else 1)
