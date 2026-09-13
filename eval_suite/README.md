# AI PM-OS Eval Suite

Automated regression + scoring harness for evaluating AI PM-OS project agents
against golden-case expectations.

---

## Structure

```
eval_suite/
├── config.py             # Model registry, paths, thresholds
├── requirements.txt      # Python dependencies (groq, requests, rich, …)
├── .env.example          # Environment variable template
├── preflight_check.py    # Checks external dependencies before a run
├── golden_cases/         # Per-project golden-case JSON arrays
│   ├── p1_skill_eval.json
│   ├── p3_rice_kano.json
│   └── p4_retention.json
├── runner.py             # Calls the model / API for each golden case
├── scorer.py             # Compares output to expected, prints pass/fail table
├── regression.py         # Runs all cases, diffs against a saved baseline
├── report.py             # Renders a rich terminal report
└── run_all.py            # Entry-point: regression → report
```

---

## Quick Start

```bash
# 1. Install deps
pip install -r eval_suite/requirements.txt

# 2. Set your API key
cp eval_suite/.env.example eval_suite/.env
#    → edit .env and fill in GROQ_API_KEY

# 3. (P1 only) Start the Next.js dev server — see note below
cd d:/pm-skill-eval-harness && npm run dev

# 4. Run the full suite
python eval_suite/run_all.py
```

---

## P1 — PM Skill Eval Harness (HTTP adapter)

**P1 is different from all other projects.** Its eval logic lives in a
Next.js TypeScript route (`route.ts`), not a Python module. Rather than
reimplementing the prompt in Python (which would drift every time `route.ts`
changes), the P1 adapter makes a live HTTP POST to the running dev server:

```
POST http://localhost:3000/api/evaluate
Body: { "question": "...", "answer": "...", "category": "..." }
```

### ⚠️ Prerequisite: P1 dev server must be running

Before executing `scorer.py --project p1`, `regression.py --project p1`, or
`run_all.py`, start the Next.js dev server:

```bash
cd d:/pm-skill-eval-harness
npm run dev
# Server ready at http://localhost:3000
```

Use `preflight_check.py` to confirm it is reachable before a run:

```bash
python eval_suite/preflight_check.py p1
# ✓  P1 Next.js dev server: reachable — HTTP 400
```

A failed preflight prints:

```
✗  P1 Next.js dev server not reachable at http://localhost:3000/api/evaluate
   → Start it:  cd d:/pm-skill-eval-harness && npm run dev
```

### Temperature setting

`route.ts` originally used `temperature: 0.2` with no comment or rationale.
This was changed to `temperature: 0` (2026-08-31) to match P3's deterministic
scoring approach. The rubric's explicit 10/7/4/0 anchors mean variance adds
noise without value; determinism makes golden-case ranges tighter and more
meaningful.

---

## Adding Golden Cases

Each file in `golden_cases/` is a JSON array of objects.
Each object represents one test case:

```jsonc
[
  {
    "id": "p1-001",
    "project": "p1",
    "input": { /* whatever the agent receives */ },
    "expected": { /* what the agent should return */ },
    "notes": "Why this case matters"
  }
]
```

---

## Pass Threshold

A project is **green** when `passed / total >= PASS_THRESHOLD` (default **80 %**).
Adjust `PASS_THRESHOLD` in `config.py`.

---

## Models

| Project | Model | Transport |
|---------|-------|-----------|
| p1 | openai/gpt-oss-20b | HTTP → localhost:3000 |
| p2 | openai/gpt-oss-120b | Python module |
| p3 | openai/gpt-oss-120b | Python module |
| p4 | openai/gpt-oss-20b | Python module |
| p5 | openai/gpt-oss-20b | Python module |
| p6 | openai/gpt-oss-20b | Python module |

---

## License

Internal tool — see root `LICENSE` for details.

