# AI PM-OS Eval Suite

This is a golden-case regression harness for three AI PM-OS projects: the PM Skill
Evaluation Harness (P1), the RICE/Kano Prioritisation Engine (P3), and the Failure
Mode Retention Diagnostic (P4). A "golden case" is a hand-verified input/output pair
where the expected value was determined correct by explicit reasoning — not assumed from
the model's own output. The suite runs each project's real scoring function against its
full set of golden cases, compares the results against a saved baseline, and flags any
case that has regressed since the last confirmed-passing run. It is the mechanical
equivalent of asking: "did this AI system just produce the same answer it was right
about before, or did something change?"

---

## Why this exists

P2 (Model Selection Decision Engine) found that the underlying AI model overstates its
confidence by approximately **18 points on average** under high-urgency conditions —
a systematic gap between self-reported correctness and observed accuracy. That finding
was scoped to structured decision-making, but the question it raises generalises
immediately: does the same gap appear when the AI's output is code or a data-processing
pipeline instead of an explicit decision? This suite is the answer applied to P1, P3,
and P4. Rather than asking the model whether its output is correct, each golden case
tests the *actual output* against a hand-verified expected value — the same discipline
of checking AI-claimed correctness against actual correctness, applied to code output
instead of decision confidence.

---

## What this suite actually found

This is not theoretical. Running the suite against live project code surfaced five real
defects, each diagnosed with reproducible probes, fixed in the respective project's own
repository with its own commit, and reconfirmed passing after the fix:

1. **P1 — Token-budget bug causing incomplete API responses.** The P1 API route was
   setting a token budget too low for full rubric scoring. Several golden cases returned
   truncated responses that fell below the score threshold. Found by the suite when P1
   cases failed consistently below a hard overall score floor; fixed in the P1 repo by
   correcting the token limit; reconfirmed passing.

2. **P3 — RICE score multiplier bleeding into `rice_score` from prompt
   under-specification.** The strategic multiplier (applied to `strategic_score`) was
   also being applied to the base `rice_score` due to an ambiguity in the prompt. Found
   when P3 golden cases returned rice scores consistently higher than hand-computed
   expected values; fixed by tightening the prompt; reconfirmed passing.

3. **P3 — Missing Kano category definitions causing model pattern-matching on feature
   names.** The Kano classification prompt listed category labels with no definitions or
   examples. The model was assigning categories based on feature-name surface patterns
   (e.g. "email" → Must-have) rather than Kano methodology. Found when P3 golden cases
   with deliberately ambiguous feature names failed Kano classification consistently;
   fixed by adding explicit definitions and a worked example to the prompt; reconfirmed
   passing.

4. **P4 — `temperature=0.2` masking a real classification defect.** P4's churn
   classifier was running at a non-zero temperature, causing a real classification
   error to surface only intermittently. The suite flagged the case as flaky rather
   than stable-failing. Setting `temperature=0` made the defect reproduce on every run,
   enabling diagnosis; fixed by correcting the underlying classification logic;
   reconfirmed passing.

5. **P4 — Churn pattern tie-breaker gap between `VALUE_DELIVERY_MISS` and
   `COMPETITIVE_SWITCH`.** The churn classification prompt had no rule for inputs
   where both competitor-switching signals and value-delivery-miss signals were present.
   The model's output was undefined at the boundary. Found when P4 golden case p4-007
   failed reproducibly; fixed by adding an explicit tie-breaker rule to the
   classification prompt; reconfirmed passing.

These five fixes were not visible from reading the code, inspecting prompts informally,
or running a single manual test. They were found because the suite holds expected values
constant and detects the gap between what the model should produce and what it actually
produces. That is the point of the tool.

---

## Quick Start

Run everything from the **`d:/ai-pm-os` repo root**, not from inside `eval_suite/`.

```bash
# 1. Install dependencies
pip install -r eval_suite/requirements.txt

# 2. Set your Groq API key
cp eval_suite/.env.example eval_suite/.env
#    Open eval_suite/.env and fill in: GROQ_API_KEY=<your-key>

# 3. Start the P1 Next.js dev server (required before any run)
cd d:/pm-skill-eval-harness && npm run dev
#    Wait for: ✓ Ready at http://localhost:3000
#    Leave this running in a separate terminal.

# 4. Run the full suite (from the ai-pm-os root)
python eval_suite/run_all.py
```

`run_all.py` runs a preflight check automatically before executing any project — it
verifies the API key, model identifiers, golden case files, output directories, and P1
server reachability. If any check fails, it prints a numbered list of exactly what is
missing and exits before making any API calls. No manual preflight step is needed.

---

## Architecture

**`schema.py`** is the single source of truth for the golden-case JSON format. It
validates every case file before a run and raises a descriptive error if any field is
missing, mistyped, or structurally invalid. Nothing else in the suite reads golden cases
without first passing them through schema validation.

**`runner.py`** loads validated golden cases for a project, imports the project's real
scoring function (or, for P1, POSTs to the live HTTP API), and calls it once per case.
It returns raw result dicts containing the actual output alongside the expected value.
It performs no pass/fail judgment — that is scorer.py's responsibility.

**`scorer.py`** takes runner.py's raw results and compares each actual output against
its expected value using project-specific tolerance rules (numeric ranges for P1,
string-match with multi-value boundary support for P4). It produces a pass/fail table
printed to the terminal and a structured summary dict consumed by regression.py and
report.py.

**`regression.py`** loads the saved baseline for a project (a JSON snapshot of a
previously confirmed-passing run) and diffs it against the current scorer output. Any
case that was passing in the baseline but is now failing is flagged as a regression.
New failures on cases that were already failing do not count as regressions. Baselines
are saved in `eval_suite/baselines/` and are updated explicitly with `--save`.

**`report.py`** generates Markdown report files — a timestamped copy in
`eval_suite/reports/` and an always-current `eval_suite/reports/latest.md`. It renders
the consolidated pass/fail state, regression counts, model names, and a calibration
note connecting the suite's purpose to the overconfidence research thread. It does not
print a terminal table; that is run_all.py's job.

**`preflight_check.py`** runs five checks before any project executes: (1) `.env`
exists and `GROQ_API_KEY` is present and non-empty; (2) every model string in
`config.MODELS` looks like a valid identifier; (3) all three required golden-case files
exist with at least 10 cases each; (4) `baselines/` and `reports/` directories exist,
creating them silently if not; (5) the P1 Next.js dev server is reachable via a GET to
its root URL. All five checks always run — failures accumulate into a numbered list
rather than stopping at the first error.

**`run_all.py`** is the single entry point for the full suite. It calls `run_preflight()`
first, then runs each project through runner → scorer → regression in sequence, inserts
a configurable inter-project delay (default 70 s) to respect Groq's rate-limit window,
prints a consolidated pass/fail table, calls `report.py` to write `latest.md`, and
exits with code 0 if every project passes or code 1 if anything fails or regresses.

---

## Coverage

| Project | Golden Cases | Pass Threshold | Current Pass Rate | Notes |
|---------|-------------|----------------|-------------------|-------|
| P1 — PM Skill Eval Harness | 10 | 80% | 100% | HTTP adapter — requires dev server |
| P3 — RICE/Kano Priority Engine | 10 | 80% | 100% | p3-010 has a documented model inconsistency (see golden case notes) |
| P4 — Failure Mode Retention Diagnostic | 10 | 70% | 100% | ★2 boundary cases — ambiguous by design, excluded from pass-rate gate |

Pass rates reflect the last confirmed clean run. See
[`eval_suite/reports/latest.md`](eval_suite/reports/latest.md) for the most recent
run's full results.

---

## Design note: CLI-only, no UI

This tool is intentionally CLI-only. There is no deployed web interface and none is
planned. The output is a terminal table during the run and a Markdown report file
after. The current run's full results are always available at
[`eval_suite/reports/latest.md`](eval_suite/reports/latest.md) — open it in any
Markdown viewer or read it plainly as text.

---

## Adding Golden Cases

Each file in `golden_cases/` is a JSON array validated by `schema.py`.
Each object represents one test case:

```jsonc
[
  {
    "id": "p1-001",
    "project": "p1",
    "input": { /* whatever the agent receives */ },
    "expected": { /* what the agent should return */ },
    "tolerance": 0.5,       // optional — numeric tolerance for range checks
    "boundary": false,       // optional — true for intentionally ambiguous cases
    "notes": "Why this case matters and how the expected value was determined"
  }
]
```

Run `python eval_suite/schema.py golden_cases/<file>.json` to validate before
committing a new case file.

---

## P1 adapter detail

P1's evaluation logic lives in a Next.js TypeScript route (`route.ts`), not a Python
module. Rather than reimplementing the prompt in Python (which would drift every time
`route.ts` changes), the P1 runner makes a live HTTP POST to the running dev server:

```
POST http://localhost:3000/api/evaluate
Body: { "question": "...", "answer": "...", "category": "..." }
```

This means P1 results are always evaluated by the same code that runs in production —
no translation layer, no drift.

---

## License

Internal tool — see root `LICENSE` for details.
