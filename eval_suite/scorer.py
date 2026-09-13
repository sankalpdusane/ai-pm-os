"""
eval_suite/scorer.py
====================
Takes runner.py's raw result dicts, applies pass/fail logic per
``expected["type"]``, attaches ``passed`` and ``diff`` to each result,
prints a rich colour-coded table, and returns a summary dict.

Supported check types
---------------------
exact_match   actual == value  (tolerance-aware for numerics)
range         value["min"] <= actual <= value["max"]
              OR compound-range: keys with ``_min``/``_max`` suffix are
              range-checked; other keys are exact-matched on actual dict.
contains_all  every string in value appears in str(actual) (case-insensitive)
custom        delegates to a named fn in CUSTOM_CHECKS dict

Usage (CLI)
-----------
    python eval_suite/scorer.py --project p3
    python eval_suite/scorer.py --project p3 --p3-path /path/to/prioritiser.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Make sibling eval_suite modules importable from anywhere
# ---------------------------------------------------------------------------
_EVAL_DIR = Path(__file__).parent.resolve()
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
from runner import _DEFAULT_PATHS, run_project  # noqa: PLC2701

# ---------------------------------------------------------------------------
# Custom check registry
# Each entry: check_name -> callable(actual, result_dict) -> (passed: bool, diff: str)
# Extend per project as needed; left empty for now.
# ---------------------------------------------------------------------------
CUSTOM_CHECKS: dict[str, Callable[[Any, dict], tuple[bool, str]]] = {}

_console = Console(width=160)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_item(actual: Any) -> Any:
    """
    If the runner returns a list (e.g. P3 always returns list[dict]),
    extract the first element for single-feature cases.
    """
    if isinstance(actual, list):
        return actual[0] if actual else None
    return actual


def _fmt_expected(exp: dict) -> str:
    """Short human-readable representation of the expected spec."""
    check_type = exp.get("type", "?")
    value = exp.get("value")
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items()]
        return f"[{check_type}] " + ", ".join(parts)
    return f"[{check_type}] {value!r}"


def _fmt_actual(actual: Any, exp: dict) -> str:
    """Short human-readable representation of the actual output."""
    item = _extract_item(actual)
    if not isinstance(item, dict):
        return str(item)[:80]

    # Show only the fields the expected spec cares about
    value = exp.get("value", {})
    if not isinstance(value, dict):
        return str(item)[:80]

    relevant: set[str] = set()
    for k in value:
        if k.endswith("_min") or k.endswith("_max"):
            relevant.add(k[:-4])
        elif k not in ("min", "max"):
            relevant.add(k)

    if relevant:
        parts = [f"{k}: {item.get(k, '?')}" for k in sorted(relevant)]
        return ", ".join(parts)

    # Fallback for simple min/max: show the raw value
    return str(item)[:80]


# ---------------------------------------------------------------------------
# Per-type scoring functions  →  (passed, diff_string)
# ---------------------------------------------------------------------------

def _check_exact_match(
    actual: Any,
    expected_value: Any,
    tolerance: float | None,
) -> tuple[bool, str]:
    item = _extract_item(actual)

    # Tolerance path for numerics
    if isinstance(expected_value, (int, float)) and tolerance is not None:
        try:
            v = float(item)
        except (TypeError, ValueError):
            return False, f"expected numeric ≈{expected_value}±{tolerance}, got {item!r}"
        if abs(v - expected_value) <= tolerance:
            return True, ""
        return False, f"expected {expected_value}±{tolerance}, got {v}"

    if item == expected_value:
        return True, ""
    return False, f"expected {expected_value!r}, got {item!r}"


def _check_range(
    actual: Any,
    expected_value: Any,
    tolerance: float | None,  # unused for range; kept for uniform signature
) -> tuple[bool, str]:
    item = _extract_item(actual)

    if not isinstance(expected_value, dict):
        return False, f"range expected_value must be a dict, got {type(expected_value).__name__}"

    # ── Case 1: simple scalar range {"min": x, "max": y} ──────────────────
    if "min" in expected_value and "max" in expected_value:
        lo, hi = expected_value["min"], expected_value["max"]
        try:
            v = float(item)
        except (TypeError, ValueError):
            return False, f"expected numeric in [{lo}, {hi}], got {item!r}"
        if lo <= v <= hi:
            return True, ""
        return False, f"expected in [{lo}, {hi}], got {v}"

    # ── Case 2: compound range (P3 style) ─────────────────────────────────
    #   Keys ending in _min / _max  → range check on that field of actual
    #   Other keys                  → exact-match check on that field
    if not isinstance(item, dict):
        return False, f"compound-range expects actual to be a dict, got {type(item).__name__}"

    diffs: list[str] = []
    passed = True

    # Build field → (min, max) map
    range_fields: dict[str, list] = {}
    exact_fields: dict[str, Any] = {}

    for k, v in expected_value.items():
        if k.endswith("_min"):
            range_fields.setdefault(k[:-4], [None, None])[0] = v
        elif k.endswith("_max"):
            range_fields.setdefault(k[:-4], [None, None])[1] = v
        else:
            exact_fields[k] = v

    for field, (lo, hi) in range_fields.items():
        actual_val = item.get(field)
        try:
            v = float(actual_val)
        except (TypeError, ValueError):
            diffs.append(f"{field}: expected numeric in [{lo}, {hi}], got {actual_val!r}")
            passed = False
            continue
        below = lo is not None and v < lo
        above = hi is not None and v > hi
        if below or above:
            diffs.append(f"{field}: expected in [{lo}, {hi}], got {v}")
            passed = False

    for field, exp_val in exact_fields.items():
        actual_val = item.get(field)
        if actual_val != exp_val:
            diffs.append(f"{field}: expected {exp_val!r}, got {actual_val!r}")
            passed = False

    return passed, "; ".join(diffs)


def _check_contains_all(
    actual: Any,
    expected_value: Any,
) -> tuple[bool, str]:
    item = _extract_item(actual)
    haystack = str(item).lower()

    if not isinstance(expected_value, list):
        return False, "contains_all expected_value must be a list of strings"

    missing = [s for s in expected_value if str(s).lower() not in haystack]
    if not missing:
        return True, ""
    return False, f"missing substrings: {missing!r}"


def _check_contains_any(
    actual: Any,
    expected_value: Any,
) -> tuple[bool, str]:
    """Pass if AT LEAST ONE string in expected_value appears in str(actual).

    Designed for boundary golden cases where multiple output values are all
    valid (e.g. ACTIVATION_GAP or HABIT_LOOP_FAILURE for a near-threshold
    retention curve).  Uses the lowercased Python repr of the actual dict so
    that field-specific substrings like "'churn_type': 'activation_gap'" match
    precisely without risk of false positives from narrative text.
    """
    item = _extract_item(actual)
    haystack = str(item).lower()

    if not isinstance(expected_value, list):
        return False, "contains_any expected_value must be a list of strings"

    matches = [s for s in expected_value if str(s).lower() in haystack]
    if matches:
        return True, ""
    return False, f"none of {expected_value!r} found in output"


def _check_custom(actual: Any, result: dict) -> tuple[bool, str]:
    check_name: str | None = result.get("expected", {}).get("check_name")
    if not check_name:
        return False, "custom check requires 'check_name' in expected"
    fn = CUSTOM_CHECKS.get(check_name)
    if fn is None:
        return False, (
            f"no custom check registered for '{check_name}'. "
            f"Add it to CUSTOM_CHECKS in scorer.py."
        )
    return fn(actual, result)


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------

def _score_one(result: dict) -> dict:
    """Return a copy of *result* with 'passed' and 'diff' attached."""
    r = dict(result)

    # Runner errors auto-fail
    if r.get("error"):
        r["passed"] = False
        r["diff"] = f"runner error: {r['error']}"
        return r

    expected = r.get("expected") or {}
    check_type = expected.get("type", "exact_match")
    expected_value = expected.get("value")
    actual = r.get("actual")
    tolerance: float | None = r.get("tolerance")

    if check_type == "exact_match":
        passed, diff = _check_exact_match(actual, expected_value, tolerance)

    elif check_type == "range":
        passed, diff = _check_range(actual, expected_value, tolerance)

    elif check_type == "contains_all":
        passed, diff = _check_contains_all(actual, expected_value)

    elif check_type == "contains_any":
        passed, diff = _check_contains_any(actual, expected_value)

    elif check_type == "custom":
        passed, diff = _check_custom(actual, r)

    else:
        passed, diff = False, f"unknown check type '{check_type}'"

    r["passed"] = passed
    r["diff"] = diff
    return r


def score_results(results: list[dict]) -> dict[str, Any]:
    """
    Score all runner results, print a rich table, and return a summary dict.

    Parameters
    ----------
    results : list[dict]
        Raw output of :func:`runner.run_project`.

    Returns
    -------
    dict with keys: total, passed, failed, pass_rate, failed_ids, boundary_ids
    """
    scored = [_score_one(r) for r in results]

    # Derive project key for per-project threshold (fall back to empty string)
    project_key = results[0].get("project", "") if results else ""
    threshold = config.get_threshold(project_key)

    # ── Build rich table ───────────────────────────────────────────────────
    table = Table(
        title="[bold white]Eval Suite — Pass/Fail Results[/bold white]",
        box=box.SIMPLE_HEAVY,
        show_lines=True,
        header_style="bold magenta",
        expand=False,
    )
    table.add_column("ID", style="bold", min_width=8, max_width=10)
    table.add_column("Expected", min_width=28, max_width=38, no_wrap=False, overflow="fold")
    table.add_column("Actual (key fields)", min_width=28, max_width=38, no_wrap=False, overflow="fold")
    table.add_column("Pass?", justify="center", min_width=8, max_width=8)
    table.add_column("Diff / Reason", min_width=40, no_wrap=False, overflow="fold")

    n_passed = 0
    failed_ids: list[str] = []
    boundary_ids: list[str] = []

    for r in scored:
        case_id = str(r.get("id") or "?")
        passed = r.get("passed", False)
        diff = r.get("diff") or ""
        exp = r.get("expected") or {}
        is_boundary = bool(r.get("boundary", False))

        if is_boundary:
            boundary_ids.append(case_id)

        exp_str = _fmt_expected(exp)
        act_str = _fmt_actual(r.get("actual"), exp)

        if passed:
            n_passed += 1
            if is_boundary:
                pass_cell = Text("PASS \u2605", style="bold yellow")   # ★
                row_style = "yellow"
            else:
                pass_cell = Text("PASS", style="bold green")
                row_style = "green"
        else:
            failed_ids.append(case_id)
            if is_boundary:
                pass_cell = Text("FAIL \u2605", style="bold red")
                row_style = "yellow"   # yellow even on fail — boundary, not a regression
            else:
                pass_cell = Text("FAIL", style="bold red")
                row_style = "red"

        table.add_row(
            case_id,
            exp_str,
            act_str,
            pass_cell,
            diff or "—",
            style=row_style,
        )

    _console.print()
    _console.print(table)

    # ★ legend shown only when boundary cases are present
    if boundary_ids:
        _console.print(
            "[dim]\u2605 = BOUNDARY case — ambiguous by design; accepts multiple valid "
            "classifications. These cases are intentional and are excluded from "
            "the regression failure count.[/dim]"
        )

    # ── Summary line ───────────────────────────────────────────────────────
    total = len(scored)
    pass_rate = n_passed / total if total > 0 else 0.0
    meets = pass_rate >= threshold

    status_color = "green" if meets else "red"
    verdict = "meets PASS_THRESHOLD" if meets else "BELOW THRESHOLD"

    boundary_note = (
        f"  |  boundary: {boundary_ids}" if boundary_ids else ""
    )
    summary_text = (
        f"[bold {status_color}]{n_passed}/{total} passed "
        f"({pass_rate:.0%}) — {verdict}[/bold {status_color}]\n"
        f"[dim]PASS_THRESHOLD = {threshold:.0%}  |  "
        f"failed: {failed_ids if failed_ids else 'none'}{boundary_note}[/dim]"
    )
    _console.print(Panel(summary_text, expand=False))

    return {
        "total": total,
        "passed": n_passed,
        "failed": total - n_passed,
        "pass_rate": pass_rate,
        "failed_ids": failed_ids,
        "boundary_ids": boundary_ids,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run + score golden cases for a project and print a rich report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--project", required=True, help="Project key, e.g. p3.")
    p.add_argument(
        "--p1-url",
        default=None,
        dest="p1_url",
        help="Override URL for P1's live evaluate endpoint (default: http://localhost:3000/api/evaluate).",
    )
    p.add_argument(
        "--p3-path",
        default=None,
        dest="p3_path",
        help=(
            "Override path to P3 source file (prioritiser.py). "
            f"Default: {_DEFAULT_PATHS.get('p3', 'not set')}"
        ),
    )
    p.add_argument(
        "--p4-path",
        default=None,
        dest="p4_path",
        help=(
            "Override path to P4 source file (churn_engine.py). "
            f"Default: {_DEFAULT_PATHS.get('p4', 'not set')}"
        ),
    )
    return p


def main(argv: list[str] | None = None) -> None:
    # Force UTF-8 on Windows cp1252 consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = _build_parser()
    args = parser.parse_args(argv)

    path_map: dict[str, str | None] = {"p1": None, "p3": args.p3_path, "p4": args.p4_path}
    source_path = path_map.get(args.project)

    _console.print(
        f"\n[dim][runner] project={args.project}  "
        f"source={source_path or _DEFAULT_PATHS.get(args.project, 'default')}[/dim]"
    )

    try:
        results = run_project(args.project, source_path=source_path)
    except (ValueError, ImportError) as exc:
        _console.print(f"[bold red][runner] ERROR:[/bold red] {exc}")
        sys.exit(1)

    _console.print(f"[dim][runner] {len(results)} case(s) executed.[/dim]")

    summary = score_results(results)

    # Exit code 1 if below threshold (useful for CI)
    if summary["pass_rate"] < config.get_threshold(args.project):
        sys.exit(1)


if __name__ == "__main__":
    main()
