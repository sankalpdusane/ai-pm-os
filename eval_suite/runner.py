"""
eval_suite/runner.py
====================
Loads a project's golden cases, dynamically imports the project's real core
function (no Streamlit, no mocks), runs each golden case through it, and
returns raw ``{id, project, input, expected, actual, notes}`` dicts.

Pass/fail logic is intentionally ABSENT here — that is scorer.py's job.

Usage (CLI)
-----------
    # From repo root:
    python eval_suite/runner.py --project p3

    # Override P3 source location:
    python eval_suite/runner.py --project p3 --p3-path ../Project_3_RICE_Kano/app.py

    # Pretty-print results:
    python eval_suite/runner.py --project p3 --pretty
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests

# ---------------------------------------------------------------------------
# Make sibling eval_suite modules importable when called from repo root
# ---------------------------------------------------------------------------
_EVAL_DIR = Path(__file__).parent.resolve()
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from schema import validate_golden_file  # noqa: E402

# ---------------------------------------------------------------------------
# Default source paths for each project
# (relative to the eval_suite/ directory, i.e., ../sibling-repo/...)
# ---------------------------------------------------------------------------
_DEFAULT_PATHS: dict[str, str] = {
    # p1 is HTTP-based (live Next.js API) — no source file to import
    "p3": str(_EVAL_DIR.parent.parent / "Ai feature prioritization engine" / "ai-prioritisation-engine" / "prioritiser.py"),
    "p4": str(_EVAL_DIR.parent.parent / "Ai churn retention action engine" / "ai-churn-engine" / "churn_engine.py"),
}

# ---------------------------------------------------------------------------
# HTTP-based projects — runner skips module import and passes module=None
# ---------------------------------------------------------------------------
_HTTP_PROJECTS: set[str] = {"p1"}

# Default URL for P1's live Next.js evaluate endpoint
_P1_API_URL = "http://localhost:3000/api/evaluate"

# Timeout (seconds) for the pre-flight connectivity probe used by HTTP projects
_HTTP_PREFLIGHT_TIMEOUT = 5

# Default per-case sleep for Groq-backed projects (p3, p4) when running in
# the full suite.  Groq's sliding-window RPM limit (10–30 req/min depending
# on tier) can be hit when 10 cases fire back-to-back after a prior project
# has already consumed part of the same window.  7 s spreads 10 calls over
# ~63 s, keeping the instantaneous rate safely under the limit.
_GROQ_PROJECTS: set[str] = {"p3", "p4"}
_DEFAULT_GROQ_CASE_DELAY: int = 7  # seconds between consecutive cases

# Default per-case sleep for HTTP-backed projects (p1) when running in
# the full suite.  P1's Next.js app enforces a sliding-window rate limit
# of 10 requests per IP per 60 seconds.  7 s between the 10 P1 cases
# spreads them over ~63 s, preventing the window from being exhausted
# mid-run when prior HTTP probes have already consumed part of the budget.
_DEFAULT_P1_CASE_DELAY: int = 7  # seconds between consecutive P1 HTTP calls

# ---------------------------------------------------------------------------
# Project adapter registry
#
# Each adapter is a callable:
#   adapter(module, case_input: dict) -> Any
#
# It bridges the golden-case input format to the real function's signature,
# and returns whatever the real function returns.
# ---------------------------------------------------------------------------

def _p1_adapter(module: Any, case_input: dict) -> Any:
    """
    Adapter for Project 1 — PM Skill Eval Harness (HTTP-based).

    Instead of importing a Python module, this adapter POSTs the golden-case
    input directly to P1's live Next.js API route:
        POST http://localhost:3000/api/evaluate

    This means the eval suite tests P1's *actual* route.ts logic end-to-end,
    not a Python re-implementation that could drift from the real prompt.

    PREREQUISITE: P1 dev server must be running before executing the P1 suite.
    Start it with:  cd d:/pm-skill-eval-harness && npm run dev

    The ``module`` argument is unused (always None for HTTP projects).
    """
    url      = _P1_API_URL
    question = case_input.get("question", "")
    answer   = case_input.get("answer", "")
    category = case_input.get("category", "product_design")

    if not question or not answer:
        raise ValueError("case['input'] must have non-empty 'question' and 'answer'.")

    try:
        resp = requests.post(
            url,
            json={"question": question, "answer": answer, "category": category},
            timeout=60,   # 30s timed out on p1-005; server returned 200 in 30.0s at temp=0
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"P1 dev server not reachable at {url}.\n"
            "Start it first:  cd d:/pm-skill-eval-harness && npm run dev"
        )
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"P1 API returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        )


def _p3_adapter(module: Any, case_input: dict) -> Any:
    """
    Adapter for Project 3 — RICE/Kano prioritisation engine.

    The real function is ``prioritise_features(features: list[dict])``.
    Golden cases store input as::

        {"features": [{"name": ..., "reach": ..., "impact": ...,
                        "confidence": ..., "effort": ...,
                        "description": ..., "strategic_goal": ...}]}

    We unwrap the list and call the real function directly.
    """
    features: list[dict] = case_input.get("features", [])
    if not features:
        raise ValueError("case['input']['features'] must be a non-empty list.")
    return module.prioritise_features(features)



def _p4_adapter(module: Any, case_input: dict) -> Any:
    """
    Adapter for Project 4 — Failure Mode Retention Diagnostic (churn_engine.py).

    Calls ``analyse_churn(cohorts, product_context)`` from the real
    churn_engine module.  Golden cases provide retention curves as::

        {
            "cohorts": [{"d1": 18, "d7": 7, "d14": 4, "d30": 2}],
            "product_context": "..."
        }

    The full response dict (churn_type, root_cause_hypothesis, actions, …)
    is returned as *actual* for the scorer to inspect.
    """
    cohorts: list[dict] = case_input.get("cohorts", [])
    product_context: str = case_input.get("product_context", "")
    if not cohorts:
        raise ValueError("case['input']['cohorts'] must be a non-empty list.")
    return module.analyse_churn(cohorts, product_context)


_ADAPTERS: dict[str, Callable[[Any, dict], Any]] = {
    "p1": _p1_adapter,
    "p3": _p3_adapter,
    "p4": _p4_adapter,
    # Add p5, p6, … adapters here as those golden cases are filled in
}


# ---------------------------------------------------------------------------
# Dynamic import helper
# ---------------------------------------------------------------------------

def _import_module_from_path(module_name: str, file_path: Path) -> Any:
    """
    Dynamically import *file_path* as *module_name* and return the module.

    Adds the file's parent directory to ``sys.path`` so that relative imports
    inside the target module (e.g. ``from prompts import ...``) resolve
    correctly — without this, sibling-module imports in the P3 repo fail.

    Parameters
    ----------
    module_name : str
        Arbitrary name used to register the module in ``sys.modules``.
    file_path : Path
        Absolute path to the ``.py`` file to import.

    Returns
    -------
    module
        The imported module object.

    Raises
    ------
    ImportError
        If the file does not exist or cannot be loaded.
    """
    if not file_path.exists():
        raise ImportError(
            f"Source file not found: {file_path}\n"
            "Tip: pass the correct path with --p3-path (or the equivalent "
            "flag for other projects)."
        )

    # Inject the source file's directory so sibling imports work
    src_dir = str(file_path.parent)

    # Always promote src_dir to position 0.
    # Merely checking "if src_dir not in sys.path" is not enough when a
    # *different* project was imported first — its directory may also be in
    # sys.path and its sibling modules (e.g. "prompts") may be cached in
    # sys.modules.  If we don't evict those caches, Python reuses the previous
    # project's "prompts" instead of this project's one.
    if src_dir in sys.path:
        sys.path.remove(src_dir)
    sys.path.insert(0, src_dir)

    # Stash and evict any sibling modules whose *name* matches a .py file in
    # the new project's directory.  This prevents, e.g., P3's cached "prompts"
    # module from being returned when P4's churn_engine does
    #   from prompts import CHURN_SYSTEM_PROMPT
    # Modules are restored (via setdefault) after the import so previously
    # loaded projects are not broken — their module objects already captured
    # the names they needed at import time and no longer depend on sys.modules.
    sibling_stems: set[str] = {
        p.stem
        for p in file_path.parent.glob("*.py")
        if p.name not in ("__init__.py",) and p.stem != file_path.stem
    }
    stashed: dict[str, Any] = {}
    for name in sibling_stems:
        if name in sys.modules:
            stashed[name] = sys.modules.pop(name)

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        sys.modules.update(stashed)          # restore before raising
        raise ImportError(f"Could not create import spec for {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)      # type: ignore[union-attr]
    except Exception:
        # Restore stashed on failure so already-imported projects still work
        for name, mod in stashed.items():
            sys.modules.setdefault(name, mod)
        raise

    # Restore stashed modules only where the new import did not populate them.
    # (If exec_module just imported a same-named sibling, that wins.)
    for name, mod in stashed.items():
        sys.modules.setdefault(name, mod)

    return module


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_project(
    project_name: str,
    *,
    source_path: str | Path | None = None,
    case_delay: int = 0,
) -> list[dict[str, Any]]:
    """
    Load golden cases for *project_name*, run each through the real scoring
    function, and return a list of raw result dicts.

    Parameters
    ----------
    project_name : str
        Project key, e.g. ``"p3"``.  Must have a registered adapter in
        :data:`_ADAPTERS`.
    source_path : str | Path | None
        Explicit path to the project's source ``.py`` file.  Falls back to
        :data:`_DEFAULT_PATHS` if ``None``.
    case_delay : int
        Seconds to sleep between consecutive cases.  Use this for Groq-backed
        projects (p3, p4) and HTTP-backed projects (p1) when running back-to-back
        in the full suite to avoid hitting per-minute rate limits mid-run.
        Defaults to 0 (no sleep).  ``run_all.py`` sets this automatically.

    Returns
    -------
    list[dict]
        Each dict has keys: ``id``, ``project``, ``input``, ``expected``,
        ``actual``, ``notes``, ``error``.
        ``actual`` is the raw return value of the real function.
        ``error`` is ``None`` on success, or an error string on failure.
        Pass/fail is NOT computed here.

    Raises
    ------
    ValueError
        If *project_name* has no adapter registered.
    ImportError
        If the project source file cannot be imported.
    """
    if project_name not in _ADAPTERS:
        supported = ", ".join(_ADAPTERS.keys()) or "(none yet)"
        raise ValueError(
            f"No adapter registered for project '{project_name}'. "
            f"Supported: {supported}"
        )

    # --- Pre-flight connectivity check for HTTP-based projects ---
    # Must run BEFORE the case loop so that a dead server is caught at the
    # project level, not per-case.  A per-case RuntimeError would be stored
    # as result["error"] and scored as FAIL against the baseline, producing
    # spurious regressions instead of a clean "COULD NOT RUN" in run_all.py.
    #
    # IMPORTANT: probe the server ROOT (e.g. http://localhost:3000), not the
    # API endpoint.  The API route only accepts POST; probing it with GET/HEAD
    # returns HTTP 405 which urllib raises as HTTPError → URLError and the
    # probe would incorrectly report the server as unreachable even when it
    # is fully up.  Any HTTP response to the root URL proves the server is
    # alive — only a connection-refused/timeout means it is actually down.
    if project_name in _HTTP_PROJECTS:
        import urllib.request
        import urllib.error
        # Derive root URL from the API endpoint (strip path)
        from urllib.parse import urlparse as _urlparse
        _api_url = _P1_API_URL if project_name == "p1" else ""
        _parsed   = _urlparse(_api_url)
        probe_url = f"{_parsed.scheme}://{_parsed.netloc}"
        try:
            urllib.request.urlopen(
                urllib.request.Request(probe_url, method="GET"),
                timeout=_HTTP_PREFLIGHT_TIMEOUT,
            )
        except urllib.error.HTTPError:
            # Any HTTP-level response (200, 404, 405…) means the server IS up.
            pass
        except urllib.error.URLError as _probe_exc:
            # Non-HTTP error: connection refused, DNS failure, timeout, etc.
            raise RuntimeError(
                f"P1 dev server not reachable at {_api_url} "
                f"(pre-flight probe to {probe_url} failed: {_probe_exc.reason}).\n"
                "Start it first:  cd d:/pm-skill-eval-harness && npm run dev"
            ) from None
        except OSError:
            raise RuntimeError(
                f"P1 dev server not reachable at {_api_url} (connection refused).\n"
                "Start it first:  cd d:/pm-skill-eval-harness && npm run dev"
            ) from None

    # --- Resolve module (skipped for HTTP-based projects) ---
    module: Any = None
    if project_name not in _HTTP_PROJECTS:
        if source_path is None:
            raw = _DEFAULT_PATHS.get(project_name)
            if raw is None:
                raise ValueError(
                    f"No default source path for project '{project_name}'. "
                    "Pass the source path explicitly (e.g. --p3-path)."
                )
            src = Path(raw)
        else:
            src = Path(source_path)
        module = _import_module_from_path(f"_eval_p{project_name[1:]}_src", src)

    # --- Load golden cases ---
    golden_dir = _EVAL_DIR / "golden_cases"
    pattern = f"{project_name}_*.json"
    case_files = list(golden_dir.glob(pattern))

    if not case_files:
        print(
            f"[runner] Warning: no golden-case files found matching "
            f"{golden_dir / pattern}",
            file=sys.stderr,
        )
        return []

    all_cases: list[dict[str, Any]] = []
    for path in sorted(case_files):
        validated = validate_golden_file(path)  # raises on schema error
        all_cases.extend(validated)

    # --- Run each case ---
    adapter = _ADAPTERS[project_name]
    results: list[dict[str, Any]] = []

    for i, case in enumerate(all_cases):
        # Sleep between cases for Groq-backed projects to avoid RPM exhaustion
        if case_delay > 0 and i > 0:
            time.sleep(case_delay)

        result: dict[str, Any] = {
            "id": case.get("id"),
            "project": project_name,
            "input": case.get("input"),
            "expected": case.get("expected"),
            "actual": None,
            "notes": case.get("notes", ""),
            "tolerance": case.get("tolerance"),   # propagated for scorer
            "boundary": case.get("boundary", False),  # propagated for boundary★ flagging
            "error": None,
        }

        try:
            result["actual"] = adapter(module, case["input"])
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run golden cases for a project against its real scoring function.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--project",
        required=True,
        help="Project key to evaluate (e.g. p3).",
    )
    p.add_argument(
        "--p1-url",
        default=_P1_API_URL,
        dest="p1_url",
        help=(
            f"URL for the P1 evaluate endpoint. Default: {_P1_API_URL}. "
            "Requires P1 dev server running: npm run dev in pm-skill-eval-harness/"
        ),
    )
    p.add_argument(
        "--p3-path",
        default=None,
        dest="p3_path",
        help=(
            "Path to the P3 source file (prioritiser.py). "
            f"Defaults to: {_DEFAULT_PATHS.get('p3', 'not set')}"
        ),
    )
    p.add_argument(
        "--p4-path",
        default=None,
        dest="p4_path",
        help=(
            "Path to the P4 source file (churn_engine.py). "
            f"Defaults to: {_DEFAULT_PATHS.get('p4', 'not set')}"
        ),
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (default: compact one-dict-per-line).",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    # Force UTF-8 output so LLM responses with Unicode (arrows, emoji, …)
    # don't crash on Windows cp1252 consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Inject runtime P1 URL override if provided
    if args.project == "p1" and hasattr(args, "p1_url"):
        global _P1_API_URL  # noqa: PLW0603
        _P1_API_URL = args.p1_url

    # Map CLI path flags to the source_path argument (HTTP projects have no path)
    path_map: dict[str, str | None] = {
        "p1": None,          # HTTP-based — no source file
        "p3": args.p3_path,
        "p4": args.p4_path,
    }
    source_path = path_map.get(args.project)

    print(
        f"[runner] project={args.project}  "
        f"source={source_path or _DEFAULT_PATHS.get(args.project, 'default')}",
        file=sys.stderr,
    )

    try:
        results = run_project(args.project, source_path=source_path)
    except (ValueError, ImportError) as exc:
        print(f"[runner] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[runner] {len(results)} case(s) executed.", file=sys.stderr)
    print()  # blank line before results

    if args.pretty:
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    else:
        for r in results:
            print(json.dumps(r, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
