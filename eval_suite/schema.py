"""
eval_suite/schema.py
====================
JSON-schema definition for golden test-case files, plus a validation helper.

Every file in golden_cases/ must be a JSON array of objects conforming to
CASE_SCHEMA.  Run this module directly to validate a file:

    python eval_suite/schema.py golden_cases/p3_rice_kano.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft7Validator, ValidationError

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

#: Schema for a single golden test-case object.
CASE_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["id", "project", "input", "expected", "notes"],
    "additionalProperties": False,
    "properties": {
        "id": {
            "type": "string",
            "minLength": 1,
            "description": "Unique identifier within the file (e.g. 'p3-001').",
        },
        "project": {
            "type": "string",
            "enum": ["p1", "p3", "p4"],  # extend as new projects are added
            "description": "Which project / agent this case targets.",
        },
        "input": {
            "type": "object",
            "description": "The exact input object fed to the project's core function.",
            "minProperties": 1,
        },
        "expected": {
            "type": "object",
            "required": ["type", "value"],
            "additionalProperties": False,
            "description": "Expected output specification.",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["exact_match", "range", "contains_all", "contains_any", "custom"],
                    "description": (
                        "Comparison strategy:\n"
                        "  exact_match   – output must equal value exactly\n"
                        "  range         – numeric output must be within [value.min, value.max];\n"
                        "                  or compound: {'field': val} exact-matches that field\n"
                        "  contains_all  – output must contain EVERY string in value (list)\n"
                        "  contains_any  – output must contain AT LEAST ONE string in value (list)\n"
                        "  custom        – handled by project-specific scorer logic"
                    ),
                },
                "value": {
                    "description": (
                        "The known-correct output, range bounds "
                        "({min, max}), or list of required substrings."
                    ),
                },
            },
        },
        "tolerance": {
            "type": "number",
            "minimum": 0,
            "description": (
                "Optional numeric tolerance for 'range' or 'exact_match' "
                "checks on floating-point values."
            ),
        },
        "boundary": {
            "type": "boolean",
            "description": (
                "Optional. If true, this case tests a genuinely ambiguous input where "
                "multiple classifications are valid. The scorer flags these cases distinctly "
                "in report output (PASS \u2605 / FAIL \u2605) rather than hiding them inside the "
                "aggregate pass rate."
            ),
        },
        "notes": {
            "type": "string",
            "minLength": 1,
            "description": "Human-readable explanation of why this case matters and what it tests.",
        },
    },
}

#: Compiled validator (reusable, thread-safe after construction).
_VALIDATOR = Draft7Validator(CASE_SCHEMA)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_golden_file(path: str | Path) -> list[dict[str, Any]]:
    """
    Load *path* as a JSON array and validate every entry against :data:`CASE_SCHEMA`.

    Parameters
    ----------
    path : str | Path
        Absolute or relative path to the golden-case JSON file.

    Returns
    -------
    list[dict]
        The validated list of case objects (pass-through for convenience).

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValueError
        If the file is not a JSON array, or if any entry fails schema
        validation.  The error message names the offending case id (or its
        array index when ``id`` is absent) and includes the full validation
        detail.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Golden-case file not found: {path}")

    try:
        cases: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(cases, list):
        raise ValueError(
            f"{path} must contain a JSON array at the top level, "
            f"got {type(cases).__name__}."
        )

    # --- per-case validation ------------------------------------------------
    seen_ids: set[str] = set()
    errors: list[str] = []

    for index, case in enumerate(cases):
        case_id: str = case.get("id", f"<index {index}>") if isinstance(case, dict) else f"<index {index}>"

        # Schema validation
        schema_errors = sorted(_VALIDATOR.iter_errors(case), key=lambda e: e.path)
        if schema_errors:
            detail = "; ".join(
                f"[{'.'.join(str(p) for p in e.absolute_path) or 'root'}] {e.message}"
                for e in schema_errors
            )
            errors.append(f"Case '{case_id}' (index {index}): {detail}")
            continue  # skip duplicate-id check for malformed cases

        # Duplicate-id check
        if case_id in seen_ids:
            errors.append(
                f"Case '{case_id}' (index {index}): duplicate id — "
                "ids must be unique within the file."
            )
        else:
            seen_ids.add(case_id)

    if errors:
        bullet_list = "\n  • ".join(errors)
        raise ValueError(
            f"Schema validation failed for {path} "
            f"({len(errors)} error(s)):\n  • {bullet_list}"
        )

    return cases


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).parent / "golden_cases" / "p3_rice_kano.json"
    )
    try:
        cases = validate_golden_file(target)
        print(f"[OK] {target.name}: all {len(cases)} case(s) passed schema validation.")
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] Validation failed:\n{exc}", file=sys.stderr)
        sys.exit(1)
