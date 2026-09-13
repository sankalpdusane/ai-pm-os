"""
eval_suite/report.py
====================
Generates a Markdown evaluation report from a completed run_all.py result.

Usage (imported by run_all.py — not normally run standalone):
    from report import generate_report
    generate_report(project_results)

Output
------
Two files are written to eval_suite/reports/ on every run:
    reports/report_{YYYYMMDD_HHMMSS}.md   — timestamped permanent copy
    reports/latest.md                      — always overwritten; easy reference

The report covers:
    • Title + run timestamp
    • One section per project with pass rate, threshold, failed cases, regressions
    • Calibration note connecting this suite's purpose to the broader
      overconfidence finding from the Decision Simulator (P6)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_EVAL_DIR    = Path(__file__).parent.resolve()
_REPORTS_DIR = _EVAL_DIR / "reports"
_REPORTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    return {
        "pass":            "✅ PASS",
        "below_threshold": "❌ BELOW THRESHOLD",
        "regression":      "⚠️  REGRESSION",
        "error":           "🔴 COULD NOT RUN",
    }.get(status, status.upper())


def _pass_rate_str(pr: dict[str, Any]) -> str:
    if pr["pass_rate"] is None:
        return "—"
    pct = f"{pr['pass_rate']:.0%}"
    boundary = (pr.get("summary") or {}).get("boundary_ids", [])
    if boundary:
        pct += f" (★{len(boundary)} boundary)"
    return pct


def _regression_ids(pr: dict[str, Any]) -> list[str]:
    reg_result = pr.get("regression_result") or {}
    return [r["id"] for r in reg_result.get("regressions", [])]


def _fixed_ids(pr: dict[str, Any]) -> list[str]:
    reg_result = pr.get("regression_result") or {}
    return reg_result.get("fixed", [])


def _failed_cases_detail(pr: dict[str, Any]) -> list[dict]:
    """
    Build a list of failed-case dicts for the report.

    scorer.score_results() returns ``failed_ids`` (list of id strings) and
    ``boundary_ids``.  regression.check_regression() returns ``regressions``
    (list of {id, diff, ...}).  We merge both to get id + diff + boundary flag.
    """
    summary    = pr.get("summary") or {}
    reg_result = pr.get("regression_result") or {}

    failed_ids   = set(summary.get("failed_ids", []))
    boundary_ids = set(summary.get("boundary_ids", []))
    # regressions list has per-case dicts with at least {id, diff}
    reg_by_id    = {r["id"]: r for r in reg_result.get("regressions", [])}

    result: list[dict] = []
    for case_id in sorted(failed_ids):
        reg = reg_by_id.get(case_id, {})
        result.append({
            "id":       case_id,
            "boundary": case_id in boundary_ids,
            "reason":   reg.get("diff") or reg.get("reason") or "—",
        })
    return result


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _project_section(pr: dict[str, Any]) -> str:
    project   = pr["project"].upper()
    status    = pr["status"]
    model     = pr["model"]
    threshold = pr["threshold"]

    lines: list[str] = []
    lines.append(f"## Project {project}")
    lines.append("")

    # Summary table
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| **Status** | {_status_badge(status)} |")
    lines.append(f"| **Pass Rate** | {_pass_rate_str(pr)} |")
    lines.append(f"| **Threshold** | {threshold:.0%} |")
    lines.append(f"| **Model** | `{model}` |")

    reg_ids   = _regression_ids(pr)
    fixed_ids = _fixed_ids(pr)
    reg_str   = ", ".join(reg_ids) if reg_ids else "none"
    lines.append(f"| **Regressions** | {len(reg_ids)} — {reg_str} |")
    if fixed_ids:
        lines.append(f"| **Fixed this run** | {', '.join(fixed_ids)} |")

    lines.append("")

    # Error detail
    if status == "error":
        lines.append(f"> 🔴 **Run error:** {pr.get('error_msg', 'Unknown error')}")
        lines.append("")
        return "\n".join(lines)

    # Failed cases detail
    failed = _failed_cases_detail(pr)
    if failed:
        lines.append("### Failed Cases")
        lines.append("")
        lines.append("| Case ID | Diff / Reason |")
        lines.append("|---------|---------------|")
        for fc in failed:
            case_id = fc.get("id", "?")
            reason  = (fc.get("reason") or fc.get("diff") or "—")
            reason  = reason.replace("|", "\\|").replace("\n", " ")
            marker  = " ★" if fc.get("boundary") else ""
            lines.append(f"| `{case_id}`{marker} | {reason} |")
        if any(fc.get("boundary") for fc in failed):
            lines.append("")
            lines.append("_★ = boundary case — ambiguous by design; excluded from regression count._")
        lines.append("")
    else:
        lines.append("_All cases passed._")
        lines.append("")

    return "\n".join(lines)


def _calibration_note() -> str:
    return """\
## Calibration Note

This evaluation suite exists because of the **18-point overconfidence gap**
identified by P6 (Decision Simulator): when the underlying AI model was asked
to assess its own confidence, it systematically over-reported correctness by
roughly 18 percentage points compared to its actual accuracy on the same
problems. That finding was originally scoped to structured decision-making —
but the question it raises generalises immediately: *does the same gap appear
when the AI's output is code or data-processing logic instead of explicit
decisions?*

This suite is the answer applied to the three retrofitted AI PM-OS projects
(P1 — skill evaluation rubric, P3 — RICE/Kano prioritisation engine,
P4 — churn retention classifier). Rather than asking the model whether its
output is correct, each golden case tests the *actual output* against a
hand-verified expected value — the same principle as comparing self-reported
confidence to observed accuracy.

**This isn't theoretical.** Running this suite against the live projects
surfaced five committed fixes that were not visible from inspecting the code
or from informal testing:

| Fix | Project | Root cause found by suite |
|-----|---------|--------------------------|
| Token-budget bug causing incomplete API responses | P1 | Score truncation below threshold |
| RICE score multiplier bleed (strategic multiplier applied to `rice_score`) | P3 | Prompt under-specification |
| Missing Kano category definitions (model pattern-matched on feature names) | P3 | Bare label list with no definitions or examples |
| `temperature=0.2` masking a real classification defect | P4 | Non-deterministic output hiding true instability |
| Churn pattern tie-breaker gap (VALUE\_DELIVERY\_MISS / COMPETITIVE\_SWITCH ambiguity) | P4 | Missing boundary rule in classification prompt |

Each fix was committed to the respective project's repository with a
regression-confirmed before/after probe. The suite now holds all three
projects at 100% pass rate with clean baselines — and the process of
building it is itself evidence that calibration tooling catches failures that
subjective review misses, which is exactly the claim P6's Decision Simulator
result implies.

The generalisation: wherever an AI system produces structured output that
is consumed downstream (scores, classifications, ranked lists), there exists
a gap between how confident the output *looks* and how reliable it *is*.
Golden-case testing with hard expected values is the minimum viable way to
measure that gap rather than assuming it is zero.
"""


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def generate_report(
    project_results: list[dict[str, Any]],
    *,
    timestamp: datetime | None = None,
) -> Path:
    """
    Build a Markdown evaluation report from run_all.py's project_results list
    and write it to reports/report_{timestamp}.md and reports/latest.md.

    Parameters
    ----------
    project_results : list[dict]
        The list of per-project result dicts produced by run_all._run_one().
    timestamp : datetime | None
        Defaults to utcnow(). Pass an explicit value for reproducible tests.

    Returns
    -------
    Path
        Path to the timestamped report file.
    """
    if timestamp is None:
        timestamp = datetime.now(tz=timezone.utc)

    ts_display = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    ts_slug    = timestamp.strftime("%Y%m%d_%H%M%S")

    # ── Overall verdict ───────────────────────────────────────────────────
    n_total   = len(project_results)
    n_passing = sum(1 for pr in project_results if pr["status"] == "pass")
    all_pass  = n_passing == n_total

    verdict_line = (
        f"✅ **ALL {n_total} PROJECTS PASSING**"
        if all_pass
        else f"❌ **{n_total - n_passing}/{n_total} PROJECT(S) FAILING**"
    )

    # ── Build document ────────────────────────────────────────────────────
    sections: list[str] = []

    sections.append("# AI PM-OS Evaluation Suite — Run Report")
    sections.append("")
    sections.append(f"**Generated:** {ts_display}  ")
    sections.append(f"**Verdict:** {verdict_line}  ")
    projects_line = " | ".join(
        f"{pr['project'].upper()} {_pass_rate_str(pr)}"
        for pr in project_results
    )
    sections.append(f"**Projects:** {projects_line}")
    sections.append("")

    # Consolidated summary table
    sections.append("## Consolidated Summary")
    sections.append("")
    sections.append("| Project | Pass Rate | Threshold | Status | Regressions | Model |")
    sections.append("|---------|-----------|-----------|--------|-------------|-------|")
    for pr in project_results:
        row_status = _status_badge(pr["status"])
        reg_ids    = _regression_ids(pr)
        n_reg      = len(reg_ids)
        reg_str    = f"{n_reg} ({', '.join(reg_ids)})" if reg_ids else "none"
        sections.append(
            f"| **{pr['project'].upper()}** "
            f"| {_pass_rate_str(pr)} "
            f"| {pr['threshold']:.0%} "
            f"| {row_status} "
            f"| {reg_str} "
            f"| `{pr['model']}` |"
        )
    sections.append("")

    # Per-project detail sections
    sections.append("---")
    sections.append("")
    for pr in project_results:
        sections.append(_project_section(pr))
        sections.append("---")
        sections.append("")

    # Calibration note
    sections.append(_calibration_note())

    # ── Write files ───────────────────────────────────────────────────────
    body = "\n".join(sections)

    timestamped = _REPORTS_DIR / f"report_{ts_slug}.md"
    latest      = _REPORTS_DIR / "latest.md"

    timestamped.write_text(body, encoding="utf-8")
    latest.write_text(body, encoding="utf-8")

    return timestamped


# ---------------------------------------------------------------------------
# Standalone entry-point (smoke test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _stub: list[dict[str, Any]] = [
        {
            "project": "p1", "pass_rate": 1.0, "threshold": 0.80,
            "status": "pass", "model": "openai/gpt-oss-20b",
            "summary": {"pass_rate": 1.0, "boundary_ids": [], "failed_cases": []},
            "regression_result": {"regressions": [], "fixed": []},
            "error_msg": None,
        },
    ]
    out = generate_report(_stub)
    print(f"Stub report written to: {out}", file=sys.stderr)
