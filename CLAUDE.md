# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This directory (`automod_agent_v2/`) is a fresh implementation of the AutoMod Agent — an AI-powered community moderation system. The detailed specification lives in `automod-agent-plan-mk2.md`.

**This is a greenfield codebase.** Do not reference or copy code from `../automod_agent_v1/`.

## Running the Implementation

```bash
# Start the server (once implemented)
uvicorn src.automod.main:app --reload --port 7888 --host 0.0.0.0
```

- Admin UI: `http://localhost:7888/admin`
- API docs: `http://localhost:7888/docs`

Requires `ANTHROPIC_API_KEY` in `.env`.

## Implementation Architecture (`src/automod/`)

```
main.py          FastAPI app entrypoint
config.py        pydantic-settings (API keys, thresholds, model names)
api/             Route handlers (rules, moderation, review)
core/            Evaluation engine
  engine.py        Main orchestrator
  tree_evaluator.py  Decision tree walker
  deterministic.py   Regex/keyword matching (no LLM)
  structural.py      Metadata field checks (no LLM)
  subjective.py      LLM-based evaluation (Haiku → Sonnet escalation)
  actions.py         Action resolution + repeat-offender escalation
compiler/        Rule compilation (NLP → decision tree)
  compiler.py      Claude API integration
  prompts.py       Compilation prompts
  renderer.py      Human-readable tree rendering
  validator.py     Compiled rule validation
db/              SQLAlchemy async (SQLite, auto-created as automod.db)
models/          Pydantic schemas + SQLAlchemy ORM
admin/           JS frontend with React + Vite
```

## Core Concepts

**Rule types (from spec):** `actionable` | `procedural` | `meta` | `informational`. Only actionable rules get compiled into decision trees. The other types are included as background context for LLM evaluations.

**Checklist item node types:**
- `deterministic` — regex/pattern matching, executed locally
- `structural` — metadata field checks (account age, post type, etc.), executed locally
- `subjective` — LLM evaluation with a rubric; batched to minimize API calls

**Decision tree combining logic:** `all_must_pass` | `any_must_pass`

**Bidirectional alignment (Phase 3, planned):** Changes to rule text, checklist items, or examples propagate as *suggestions* to the others — never auto-applied. Key design decision: moderator trust over convenience.

## Implementation Phases (from spec)

1. **Core Data Model + Rule Compiler** — community/rule CRUD, checklist compilation, basic UI
2. **Evaluation Engine + Decision Queue** — post evaluation, moderator resolution, decision-to-example feedback loop
3. **Interactive Alignment** — recompile-with-diff, suggest-from-examples, suggest-from-checklist
4. **Platform Integration + Rule Suggestions** — Reddit adapter, background pattern analysis, rule suggestion service

## Key Design Decisions

- All decisions require human verification (no autonomous moderation in v1)
- Every checklist item carries `intent` (why it exists) and `rule_text_anchor` (which phrase in the rule it implements) for transparency and recompile stability
- The "community norms" FLAG (post feels off but violates no explicit rule) is a separate evaluation path from the rule-based system
- Decision queue sorted by agent confidence ascending — lowest confidence gets human attention first
- Moderator overrides automatically create labeled examples for the relevant rules

## Claude API Configuration

Configured via environment variables / `config.py`:
- `COMPILER_MODEL` — rule compilation (default: `claude-sonnet-4-6`)
- `SONNET_MODEL` — borderline case re-evaluation (default: `claude-sonnet-4-6`)
- `HAIKU_MODEL` — initial subjective evaluation (default: `claude-haiku-4-5-20251001`)
- `ESCALATION_CONFIDENCE_THRESHOLD` — when to escalate Haiku → Sonnet (default: 0.75)
