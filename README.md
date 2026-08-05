# AI PM OS — AI Product Management System

**Built by Sankalp Dusane** · [LinkedIn](https://linkedin.com/in/sankalpdusane) · [Notion Portfolio](PASTE_YOUR_NOTION_URL_HERE)

> Six AI tools that together demonstrate the full stack of AI PM skills.
> Each tool proves a different capability that every PM interview tests for.

---

## All six tools

| # | Project | What it proves | Status | Live demo |
|---|---------|---------------|--------|-----------|
| 1 | [PM Skill Eval Harness](https://github.com/sankalpdusane/pm-skill-eval-harness) | Evaluation design — scoring PM answers on a 4-dimension rubric | ✅ Live | [Open tool](https://ai-pm-interview-coach.vercel.app/) |
| 2 | [Model Selection Decision Engine](https://github.com/sankalpdusane/model-selection-decision-engine) | Strategic judgment under constraint — adversarial red-teaming + calibration tracking | ✅ Live | [Open tool](https://model-selection-decision-engine.vercel.app) |
| 3 | [RICE Kano Priority Engine](https://github.com/sankalpdusane/rice-kano-priority-engine) | Feature prioritisation with stakeholder objection simulation and sensitivity analysis | ✅ Live | [Open tool](https://rice-kano-priority-engine-eevg9wtfc9ycphcgjrdn5l.streamlit.app/) |
| 4 | Failure Mode Retention Diagnostic | Churn classification with counter-metric guardrails on every action | 🔨 Building | Soon |
| 5 | PRD Generator with Eval Critic | Three-agent write-critique-revise loop with hard SHIP/NO-SHIP gate | 📋 Planned | Soon |
| 6 | AI Feature Eval Suite | Automated regression testing for AI judgment using golden test cases | 📋 Planned | Soon |

---

## Why these six and not random projects

Each tool fills a gap that prompting a general AI cannot fill.

ChatGPT forgets every session. It has no calibration. It has no adversarial challenge. It has no counter-metric guardrail. It has no outcome logging.

These tools add the infrastructure that turns AI capabilities into accountable, measurable product systems. That is what a PM's job actually is.

The connecting thread across all six: **every tool tracks whether its own AI output is accurate.**

---

## The finding that connects everything

After logging 30+ real decisions in the Decision Simulator against the AI's predicted confidence scores, I found a systematic pattern:

> The AI overstates its confidence by approximately **18 points** on average under high-urgency conditions.

This overconfidence gap is what I want to study formally — and what I am building production mitigations for.

---

## Shared architecture across all six tools

Every tool implements these patterns before any user-facing feature:

- Sliding window rate limiting — 10 requests per IP per 60 seconds
- TTL cache — 30 minutes, keyed on hashed input content
- Input validation before any API call
- Three-attempt retry with delay before returning a user-friendly error
- API keys isolated to server layer — never exposed to the browser

---

## Model selection decisions

| Tool | Model | Why |
|------|-------|-----|
| PM Skill Eval Harness | llama-3.1-8b-instant | Evaluation is structured — 8B sufficient, 14,400 RPD free budget |
| Model Selection Decision Engine | llama-3.3-70b-versatile | 3 distinct strategic options requires stronger reasoning |
| RICE Kano Priority Engine | llama-3.3-70b-versatile | Feature comparison with nuanced tradeoffs needs 70B |
| Failure Mode Retention Diagnostic | llama-3.1-8b-instant | Churn classification is structured — 8B sufficient |
| PRD Generator + Eval Critic | llama-3.1-8b-instant | Generation and critique are structured tasks |
| AI Feature Eval Suite | llama-3.1-8b-instant | Scoring against golden set is formulaic |

---

## Project architecture docs

- [Architecture decisions](docs/architecture.md) — shared 3-zone model and design patterns
- [Eval philosophy](docs/eval-philosophy.md) — how evaluation is designed across the system

---

*~ made by Sankalp Dusane*
