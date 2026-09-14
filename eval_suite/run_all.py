"""
eval_suite/run_all.py
=====================
One command that runs scorer.py and regression.py across every retrofitted
project in sequence, then prints a single consolidated summary table.

Usage
-----
    python eval_suite/run_all.py               # run all three projects
    python eval_suite/run_all.py --save        # run + update all baselines
    python eval_suite/run_all.py --project p4  # run just one project

Exit code
---------
0  — every project meets its pass threshold AND has zero regressions.
1  — at least one project is below threshold, has regressions, or could
     not be run at all (import error, missing dev server, bad path …).

Rate-limit note
---------------
P3 uses gpt-oss-120b and P4 uses gpt-oss-20b via Groq.  When running all
projects back-to-back the default --delay 70 (seconds) is inserted between
projects to let the Groq sliding-window rate limiter (10 req/60s per model)
reset.  Pass --delay 0 to disable if you know the rate limit has already
cleared, or when running a single project with --project.

CI usage
--------
Add this as a step in your CI pipeline:
    python eval_suite/run_all.py
The non-zero exit code on any failure makes it a hard gate.

Notes
-----
• If a project's import fails (e.g. wrong --p3-path) or its HTTP server is
  unreachable (P1), that project is reported as "COULD NOT RUN" in the
  consolidated table and the remaining projects still execute.
• The per-project pass/fail detail tables (from scorer.py) are printed
  inline — one per project — before the consolidated summary.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Make sibling eval_suite modules importable regardless of CWD
# ---------------------------------------------------------------------------
_EVAL_DIR = Path(__file__).parent.resolve()
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import config
from preflight_check import run_preflight                        # noqa: PLC2701
from runner import _DEFAULT_PATHS, run_project, _GROQ_PROJECTS, _DEFAULT_GROQ_CASE_DELAY, _HTTP_PROJECTS, _DEFAULT_P1_CASE_DELAY  # noqa: PLC2701
from scorer import score_results, _score_one             # noqa: PLC2701
from regression import check_regression, save_baseline   # noqa: PLC2701
from report import generate_report                       # noqa: PLC2701

_console = Console(width=160)

# Projects run by default (in order)
_ALL_PROJECTS: list[str] = ["p1", "p3", "p4"]


# ---------------------------------------------------------------------------
# Per-project runner
# ---------------------------------------------------------------------------

def _run_one(project: str, *, save: bool) -> dict[str, Any]:
    """
    Execute the full scorer → regression pipeline for one project.

    Returns a summary dict with keys:
        project          str
        pass_rate        float | None  (None when the project could not run)
        threshold        float
        summary          dict | None   (scorer summary)
        regression_result dict | None  (check_regression output)
        model            str
        status           'pass' | 'below_threshold' | 'regression' | 'error'
        error_msg        str | None
    """
    threshold = config.get_threshold(project)
    model = config.MODELS.get(project, "unknown")

    _console.print()
    _console.rule(f"[bold cyan]Project {project.upper()}[/bold cyan]")

    # Per-case delay: Groq projects hit the Groq RPM limit; P1 (HTTP) hits
    # P1's own Next.js sliding-window rate limit (10 req/60s).  Both get 7s.
    if project in _GROQ_PROJECTS:
        case_delay = _DEFAULT_GROQ_CASE_DELAY
    elif project in _HTTP_PROJECTS:
        case_delay = _DEFAULT_P1_CASE_DELAY
    else:
        case_delay = 0
    try:
        raw = run_project(project, case_delay=case_delay)
    except (ValueError, ImportError, RuntimeError, Exception) as exc:  # noqa: BLE001
        _console.print(
            f"[bold red]  [ERROR] Could not run '{project}': {exc}[/bold red]"
        )
        return {
            "project":          project,
            "pass_rate":        None,
            "threshold":        threshold,
            "summary":          None,
            "regression_result": None,
            "model":            model,
            "status":           "error",
            "error_msg":        str(exc),
        }

    # ── 2. Score — prints per-case table to console ───────────────────────
    summary = score_results(raw)

    # Rederive scored dicts (score_results doesn't return them individually)
    scored = [_score_one(r) for r in raw]

    # ── 3. Regression: save or check ─────────────────────────────────────
    if save:
        save_baseline(project, scored, summary)
        regression_result: dict[str, Any] = {
            "new_baseline":  True,
            "regressions":   [],
            "fixed":         [],
            "unchanged":     len(scored),
            "baseline_meta": {},
        }
        _console.print(
            f"[dim]  Baseline saved for {project} "
            f"(pass_rate={summary['pass_rate']:.0%}, "
            f"model={model})[/dim]"
        )
    else:
        regression_result = check_regression(project, scored)
        if regression_result.get("new_baseline"):
            save_baseline(project, scored, summary)
            _console.print(f"[dim]  Auto-saved first baseline for {project}.[/dim]")
        else:
            # Print inline regression verdict (concise — no full _print_regression_report)
            regressions = regression_result.get("regressions", [])
            fixed       = regression_result.get("fixed", [])
            meta        = regression_result.get("baseline_meta", {})
            if regressions:
                _console.print(
                    f"[bold red]  ⚠  {len(regressions)} REGRESSION(S): "
                    + ", ".join(r["id"] for r in regressions)
                    + "[/bold red]"
                )
            else:
                _console.print("[dim]  Regression: CLEAN — no previously-passing cases now fail.[/dim]")
            if fixed:
                _console.print(
                    f"[bold green]  ✓  FIXED: {', '.join(fixed)}[/bold green]"
                )
            if meta.get("timestamp"):
                _console.print(
                    f"[dim]  Baseline: model={meta.get('model')}  "
                    f"saved={meta.get('timestamp')}[/dim]"
                )

    # ── 4. Determine overall status ──────────────────────────────────────
    pass_rate   = summary["pass_rate"]
    n_regressions = len(regression_result.get("regressions", []))

    if pass_rate < threshold:
        status = "below_threshold"
    elif n_regressions > 0:
        status = "regression"
    else:
        status = "pass"

    return {
        "project":           project,
        "pass_rate":         pass_rate,
        "threshold":         threshold,
        "summary":           summary,
        "regression_result": regression_result,
        "model":             model,
        "status":            status,
        "error_msg":         None,
    }


# ---------------------------------------------------------------------------
# Consolidated summary table
# ---------------------------------------------------------------------------

def _print_consolidated(
    project_results: list[dict[str, Any]],
    *,
    save: bool,
) -> bool:
    """
    Print the cross-project consolidated summary table.

    Returns True if any project is failing / erroring (for exit code).
    """
    _console.print()
    _console.rule("[bold white]Consolidated Summary — AI PM-OS Eval Suite[/bold white]")

    table = Table(
        title="[bold white]All Projects[/bold white]",
        box=box.SIMPLE_HEAVY,
        show_lines=True,
        header_style="bold magenta",
        expand=False,
    )
    table.add_column("Project",     style="bold",   min_width=10)
    table.add_column("Pass Rate",   justify="right", min_width=10)
    table.add_column("Threshold",   justify="right", min_width=10)
    table.add_column("Status",      justify="center", min_width=18)
    table.add_column("Regressions", justify="center", min_width=13)
    table.add_column("Model Used",  min_width=26)

    any_failing = False

    for pr in project_results:
        project   = pr["project"]
        status    = pr["status"]
        model     = pr["model"]
        threshold = pr["threshold"]

        # ── Error row ──────────────────────────────────────────────────────
        if status == "error":
            any_failing = True
            short_err = (pr["error_msg"] or "")[:60]
            table.add_row(
                project.upper(),
                "—",
                f"{threshold:.0%}",
                Text("COULD NOT RUN", style="bold red"),
                "—",
                model,
                style="red",
            )
            continue

        # ── Normal row ─────────────────────────────────────────────────────
        pass_rate   = pr["pass_rate"]
        regressions = pr["regression_result"].get("regressions", [])
        n_reg       = len(regressions)
        boundary_ids = (pr["summary"] or {}).get("boundary_ids", [])

        # Status cell
        if status == "pass":
            status_cell = Text("PASS", style="bold green")
            row_style   = "green"
        elif status == "below_threshold":
            status_cell = Text("BELOW THRESHOLD", style="bold red")
            row_style   = "red"
            any_failing = True
        else:  # "regression"
            status_cell = Text("REGRESSION", style="bold red")
            row_style   = "red"
            any_failing = True

        # Regression cell
        if n_reg > 0:
            reg_cell  = Text(str(n_reg), style="bold red")
            any_failing = True
        else:
            reg_cell  = Text("none", style="green")

        # Pass rate — append ★ count if there are boundary cases
        pass_rate_str = f"{pass_rate:.0%}"
        if boundary_ids:
            pass_rate_str += f"  (★{len(boundary_ids)})"

        table.add_row(
            project.upper(),
            pass_rate_str,
            f"{threshold:.0%}",
            status_cell,
            reg_cell,
            model,
            style=row_style,
        )

    _console.print()
    _console.print(table)

    # ★ boundary legend
    has_boundary = any(
        (pr.get("summary") or {}).get("boundary_ids")
        for pr in project_results
        if pr["status"] != "error"
    )
    if has_boundary:
        _console.print(
            "[dim]★ = boundary cases — ambiguous by design, "
            "count shown in pass rate cell but not counted as failures[/dim]"
        )

    if save:
        _console.print("[dim](--save: baselines updated for all projects)[/dim]")

    # ── Final verdict panel ────────────────────────────────────────────────
    if any_failing:
        n_fail = sum(
            1 for pr in project_results
            if pr["status"] != "pass"
        )
        verdict_text = (
            f"[bold red]FAILING — {n_fail}/{len(project_results)} project(s) "
            f"below threshold, regressed, or could not run[/bold red]"
        )
    else:
        n = len(project_results)
        verdict_text = (
            f"[bold green]ALL PASSING — {n}/{n} project(s) meet threshold "
            f"with no regressions[/bold green]"
        )

    _console.print(Panel(verdict_text, expand=False))
    return any_failing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run scorer + regression across all projects and print a "
            "consolidated report. Exit 1 if any project fails or regresses."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--save",
        action="store_true",
        help=(
            "Save the current run as the new baseline for every project. "
            "Without this flag, baselines are only auto-saved when no prior "
            "baseline exists for that project."
        ),
    )
    p.add_argument(
        "--project",
        default=None,
        choices=_ALL_PROJECTS,
        metavar="PROJECT",
        help=(
            f"Run only a single project ({', '.join(_ALL_PROJECTS)}). "
            "Omit to run all. Equivalent to calling scorer.py --project <p> directly."
        ),
    )
    p.add_argument(
        "--delay",
        type=int,
        default=70,
        metavar="SECONDS",
        dest="delay",
        help=(
            "Seconds to wait between projects when running all three "
            "back-to-back (default: 70). Prevents Groq 10-req/min rate-limit "
            "hits across consecutive project runs. Pass 0 to disable "
            "(safe when rate-limit window has already cleared)."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    args = _build_parser().parse_args(argv)

    # --- Preflight: validate env, config, files, dirs, P1 server ---
    if not run_preflight():
        sys.exit(1)

    projects_to_run = [args.project] if args.project else _ALL_PROJECTS

    _console.print()
    _console.rule(
        "[bold]AI PM-OS Eval Suite[/bold]  "
        f"[dim]projects: {', '.join(p.upper() for p in projects_to_run)}"
        + ("  mode: SAVE BASELINE" if args.save else "")
        + ([f"  inter-project delay: {args.delay}s" if args.delay > 0 and len(projects_to_run) > 1 else ""][0])
        + "[/dim]"
    )

    project_results: list[dict[str, Any]] = []
    for i, project in enumerate(projects_to_run):
        if i > 0 and args.delay > 0:
            _console.print(
                f"\n[dim]  Waiting {args.delay}s between projects "
                f"(Groq rate-limit window — pass --delay 0 to skip)...[/dim]"
            )
            time.sleep(args.delay)
        pr = _run_one(project, save=args.save)
        project_results.append(pr)

    any_failing = _print_consolidated(project_results, save=args.save)

    # Generate Markdown report (always — both timestamped + latest.md)
    report_path = generate_report(project_results)
    _console.print(
        f"\n[dim]Report → [link={report_path.as_uri()}]{report_path.name}[/link]  "
        f"(latest: {(report_path.parent / 'latest.md').as_uri()})[/dim]"
    )

    sys.exit(1 if any_failing else 0)


if __name__ == "__main__":
    main()
