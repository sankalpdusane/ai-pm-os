"""
eval_suite/regression.py
========================
Snapshot the scorer output as a baseline and, on future runs, diff against
it to distinguish genuine regressions from already-known failures.

Workflow
--------
1. First run  →  ``python regression.py --project p3 --save``
   Runs the full runner → scorer pipeline and saves a baseline JSON.

2. Later run  →  ``python regression.py --project p3``
   Runs the pipeline again and compares case-by-case:
     REGRESSION  — was PASS in baseline, now FAIL  (printed red, with diff)
     FIXED       — was FAIL in baseline, now PASS   (printed green)
     UNCHANGED   — same result as baseline           (not printed by default)

Why --save is explicit (not the default)
-----------------------------------------
Auto-saving on every run would silently promote regressions to
"the new normal".  --save is a deliberate gate.

Baseline format  (baselines/{project}_baseline.json)
------------------------------------------------------
{
  "project":   "p3",
  "model":     "openai/gpt-oss-120b",
  "timestamp": "2026-08-30T13:45:00Z",
  "summary":   { total, passed, failed, pass_rate, failed_ids },
  "cases":     [ { id, passed, actual, diff } ... ]
}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Resolve eval_suite on sys.path regardless of CWD
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
from scorer import score_results, _score_one  # noqa: PLC2701

_BASELINES_DIR = _EVAL_DIR / "baselines"
_console = Console(width=160)


# ---------------------------------------------------------------------------
# Baseline I/O
# ---------------------------------------------------------------------------

def _baseline_path(project_name: str) -> Path:
    return _BASELINES_DIR / f"{project_name}_baseline.json"


def save_baseline(
    project_name: str,
    scored_results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> Path:
    """
    Persist a baseline snapshot to ``baselines/{project_name}_baseline.json``.

    Parameters
    ----------
    project_name : str
        Project key (e.g. ``"p3"``).
    scored_results : list[dict]
        Per-case results decorated with ``passed`` and ``diff`` by the scorer.
    summary : dict
        The summary dict returned by :func:`scorer.score_results`.

    Returns
    -------
    Path
        Absolute path of the written file.
    """
    _BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    path = _baseline_path(project_name)

    # Strip bulky input/notes from the snapshot — we only need id/passed/actual/diff
    slim_cases = [
        {
            "id":     r.get("id"),
            "passed": r.get("passed"),
            "actual": r.get("actual"),
            "diff":   r.get("diff") or "",
        }
        for r in scored_results
    ]

    payload = {
        "project":   project_name,
        "model":     config.MODELS.get(project_name, "unknown"),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary":   summary,
        "cases":     slim_cases,
    }

    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def _load_baseline(project_name: str) -> dict[str, Any] | None:
    """Return the parsed baseline dict, or ``None`` if no file exists."""
    path = _baseline_path(project_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _console.print(f"[yellow]Warning: could not load baseline ({exc})[/yellow]")
        return None


# ---------------------------------------------------------------------------
# Regression check
# ---------------------------------------------------------------------------

def check_regression(
    project_name: str,
    current_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compare *current_results* against the saved baseline for *project_name*.

    Parameters
    ----------
    project_name : str
    current_results : list[dict]
        Scored results — must already have ``passed`` and ``diff`` keys.

    Returns
    -------
    dict with keys:
        new_baseline   bool        — True if no prior baseline existed
        regressions    list[dict]  — cases that went PASS → FAIL
        fixed          list[str]   — case ids that went FAIL → PASS
        unchanged      int         — count of cases with same result as baseline
        baseline_meta  dict        — model / timestamp of the baseline used
    """
    baseline = _load_baseline(project_name)

    if baseline is None:
        return {
            "new_baseline":  True,
            "regressions":   [],
            "fixed":         [],
            "unchanged":     len(current_results),
            "baseline_meta": {},
        }

    # Build lookup: id → baseline case dict
    baseline_by_id: dict[str, dict] = {
        c["id"]: c for c in baseline.get("cases", []) if c.get("id")
    }

    regressions: list[dict] = []
    fixed: list[str] = []
    unchanged = 0

    for result in current_results:
        case_id = result.get("id")
        if not case_id:
            continue

        now_passed = result.get("passed", False)
        base_case  = baseline_by_id.get(case_id)

        if base_case is None:
            # New case not in the baseline — treat as unchanged
            unchanged += 1
            continue

        was_passed = base_case.get("passed", False)

        if was_passed and not now_passed:
            regressions.append({
                "id":   case_id,
                "diff": result.get("diff") or "(no diff recorded)",
            })
        elif not was_passed and now_passed:
            fixed.append(case_id)
        else:
            unchanged += 1

    return {
        "new_baseline":  False,
        "regressions":   regressions,
        "fixed":         fixed,
        "unchanged":     unchanged,
        "baseline_meta": {
            "model":     baseline.get("model", "unknown"),
            "timestamp": baseline.get("timestamp", "unknown"),
        },
    }


# ---------------------------------------------------------------------------
# Rich report
# ---------------------------------------------------------------------------

def _print_regression_report(
    project_name: str,
    regression_result: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    """Print a colour-coded regression report to the terminal."""

    if regression_result.get("new_baseline"):
        _console.print(
            Panel(
                f"[bold cyan]No prior baseline found for '{project_name}'.[/bold cyan]\n"
                "This run's results have been saved as the new baseline.\n"
                "[dim]Use --save on future runs to explicitly commit a new baseline.[/dim]",
                title="New Baseline",
                expand=False,
            )
        )
        return

    meta        = regression_result.get("baseline_meta", {})
    regressions = regression_result.get("regressions", [])
    fixed       = regression_result.get("fixed", [])
    unchanged   = regression_result.get("unchanged", 0)

    # ── Baseline provenance ────────────────────────────────────────────────
    _console.print(
        f"\n[dim]Comparing against baseline: "
        f"model=[cyan]{meta.get('model')}[/cyan]  "
        f"saved={meta.get('timestamp')}[/dim]"
    )

    # ── Regression table ───────────────────────────────────────────────────
    if regressions:
        tbl = Table(
            title=f"[bold red]REGRESSIONS — {len(regressions)} case(s) that used to PASS now FAIL[/bold red]",
            box=box.SIMPLE_HEAVY,
            show_lines=True,
            header_style="bold red",
        )
        tbl.add_column("Case ID", style="bold", min_width=10)
        tbl.add_column("What changed (diff)", min_width=60, no_wrap=False)

        for reg in regressions:
            tbl.add_row(
                Text(reg["id"], style="bold red"),
                Text(reg["diff"], style="red"),
                style="red",
            )
        _console.print(tbl)
    else:
        _console.print(
            "\n[bold green]No regressions — all previously-passing cases still pass.[/bold green]"
        )

    # ── Fixed list ─────────────────────────────────────────────────────────
    if fixed:
        _console.print(
            f"\n[bold green]FIXED ({len(fixed)}) — previously failing, now passing:[/bold green] "
            + ", ".join(f"[green]{fid}[/green]" for fid in fixed)
        )

    # ── Summary panel ──────────────────────────────────────────────────────
    r_count = len(regressions)
    f_count = len(fixed)
    color   = "green" if r_count == 0 else "red"
    verdict = "CLEAN — no regressions" if r_count == 0 else f"{r_count} REGRESSION(S) DETECTED"

    _console.print(
        Panel(
            f"[{color}][bold]{verdict}[/bold][/{color}]\n"
            f"[dim]regressions={r_count}  fixed={f_count}  unchanged={unchanged}  "
            f"current_pass_rate={summary.get('pass_rate', 0):.0%}[/dim]",
            title=f"Regression Report — {project_name}",
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run + score golden cases for a project, "
            "then check (or save) a regression baseline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--project", required=True, help="Project key, e.g. p3.")
    p.add_argument(
        "--save",
        action="store_true",
        help=(
            "Save the current run as the new baseline. "
            "Without this flag the run is compared against the existing baseline "
            "and the baseline file is NOT modified."
        ),
    )
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = _build_parser()
    args   = parser.parse_args(argv)

    path_map: dict[str, str | None] = {"p1": None, "p3": args.p3_path, "p4": args.p4_path}
    source_path = path_map.get(args.project)

    _console.rule(f"[bold]Regression Suite — {args.project}[/bold]")
    _console.print(f"[dim]mode={'SAVE BASELINE' if args.save else 'CHECK VS BASELINE'}[/dim]\n")

    # ── 1. Run ──────────────────────────────────────────────────────────────
    try:
        raw_results = run_project(args.project, source_path=source_path)
    except (ValueError, ImportError) as exc:
        _console.print(f"[bold red][runner] ERROR:[/bold red] {exc}")
        sys.exit(1)

    # ── 2. Score (prints the rich table + summary line) ────────────────────
    summary = score_results(raw_results)

    # Re-apply scoring silently to get decorated dicts for baseline storage
    scored = [_score_one(r) for r in raw_results]

    # ── 3. Save or check ────────────────────────────────────────────────────
    if args.save:
        path = save_baseline(args.project, scored, summary)
        _console.print(
            Panel(
                f"[bold green]Baseline saved.[/bold green]\n"
                f"[dim]{path}[/dim]\n"
                f"model=[cyan]{config.MODELS.get(args.project, 'unknown')}[/cyan]  "
                f"cases={len(scored)}  "
                f"pass_rate={summary['pass_rate']:.0%}",
                title="Baseline Saved",
                expand=False,
            )
        )
    else:
        regression_result = check_regression(args.project, scored)

        # Auto-save on first ever run (no existing baseline)
        if regression_result.get("new_baseline"):
            path = save_baseline(args.project, scored, summary)
            _console.print(f"[dim]Auto-saved as first baseline → {path}[/dim]")

        _print_regression_report(args.project, regression_result, summary)

        if regression_result.get("regressions"):
            sys.exit(1)


if __name__ == "__main__":
    main()
