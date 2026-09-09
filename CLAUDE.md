# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

体检报告问答助手 (medical checkup report Q&A assistant). Users upload a checkup report (PDF / photo / manual entry) → structured parsing → rule-based reference-range judgment → per-item interpretation with knowledge-base evidence → four-section interpretation (summary / per-item / graded advice / disclaimer) → structured follow-up plan, plus multi-turn follow-up chat over the report (SSE streaming). Product positioning is interpretation + health advice, **not medical diagnosis**.

All code comments, docstrings, prompts, and user-facing text are in Chinese — match that convention. Design decisions in code comments cite the spec sections (e.g. `spec §11`, `F1/F3/F6`, `评审 Critical-1`).

Docs(**注意:`docs/` 在 .gitignore 中,不入 git 仓库,只保留本地文件**——不要 `git add -f` docs):
- `docs/requirements.md` — requirements + confirmed constraint decisions
- `docs/superpowers/specs/2026-09-02-report-agent-design.md` — system design (spec; sections referenced throughout the code)
- `docs/superpowers/specs/2026-09-06-report-agent-parse-upgrade-design.md` — 解析层升级设计(Paddle + 纯规则表格解析 + 表单元数据;取代旧 spec 解析章节)
- `docs/superpowers/plans/2026-09-02-report-agent.md` — implementation plan (numbered tasks referenced in code docstrings, e.g. "Task 11")

## Layout

- `backend/` — the entire deliverable: FastAPI + LangGraph + Neo4j + Milvus + PostgreSQL. Python ≥3.12, `uv`-managed (uv.lock), hatchling build, src-layout (`backend/src/report_agent`).
- `frontend/` — the web UI: Vue 3 + Vite + TypeScript + Element Plus (pnpm; dev proxy `/api` → backend:8000; see `frontend/README.md` and the frontend design spec in `docs/superpowers/specs/2026-09-03-report-agent-frontend-design.md`). Backend-provided text (interpretation sections, safety scripts, disclaimer) is rendered verbatim.
- `backend/knowledge_seeds/` — seed YAML for the medical knowledge base (LLM-drafted, human-reviewed before import).
- `backend/eval/` — eval fixture reports, QA pairs, baseline/result JSONs (regression gate).
- `backend/scripts/` — operational scripts (seed, smoke, eval); run as `uv run python scripts/<name>.py`.

## Commands

Run all of these from `backend/`.

```bash
# infra (postgres / neo4j / milvus+etcd+minio)
docker compose up -d

# setup
cp .env.example .env        # fill DEEPSEEK_API_KEY and EMBEDDING_API_KEY
uv sync
uv run alembic upgrade head

# run the API — one command: idempotent alembic upgrade head, then uvicorn
uv run python scripts/serve.py                # --host/--port/--skip-migration available
uv run uvicorn report_agent.api.app:create_app --factory --port 8000   # serve only, no migration (on Windows use scripts/serve.py instead — psycopg async needs a selector loop, which the CLI path cannot set up in time)

# lint / tests
uv run ruff check src tests scripts
uv run pytest                                   # whole suite (asyncio_mode=auto, -q)
uv run pytest tests/unit_rules/test_rule_compare.py::test_judge_item   # single test
uv run pytest tests/unit_rules -v --cov=report_agent.pipeline.rule_compare --cov-report=term-missing

# knowledge base (requires infra + LLM keys; YAML must be human-reviewed first)
uv run python scripts/seed_draft.py --all       # LLM-draft seed YAML (resumable)
uv run python scripts/seed_import.py --all      # import YAML → Neo4j + Milvus (idempotent)
uv run python scripts/seed_import.py --entity GLU   # incremental single-entity upsert

# end-to-end smoke + eval regression gate (both need infra + LLM keys)
uv run python scripts/smoke.py
uv run python scripts/gen_eval_reports.py --count 20   # synthesize eval set (gt needs human review)
uv run python scripts/run_eval.py                       # exit 1 = regression, exit 2 = empty eval set
uv run python scripts/run_eval.py --update-baseline     # freeze baseline after human confirmation
uv run python scripts/dry_run_real.py   # 真实 case 解析/归一化对照表(评测升级 spec V2/V3)
```

Notes:
- Unit tests run without infra; `tests/conftest.py` adds `backend/src` to `sys.path` and stubs external-service health checks. `smoke.py` / `run_eval.py` / seed scripts need docker compose + LLM keys.
- `run_eval.py` is a hard gate: rule-layer accuracy must be 1.0, safety violations zero, metrics must not regress against `eval/baseline.json`.
- Alembic ignores `alembic.ini`'s URL — `alembic/env.py` overrides it with `POSTGRES_DSN` from settings.
- New config fields: declare in `report_agent.config.Settings` **and** mirror in `.env.example` (fields correspond 1:1).

## Architecture

### Two control-flow styles (spec §6, deliberate)

1. **Deterministic pipeline** for report interpretation (async task + polling, 30s–2min):
   - Stages registered in `STAGE_FUNCS` (dict) in `pipeline/stages.py`, ordered by `STAGE_ORDER`: `parse → normalize → compare → retrieve → generate → guardrail → plan`.
   - `pipeline/runner.py` claims a task (`FOR UPDATE SKIP LOCKED` in `pipeline/tasks.py`), runs stages in order, and persists each stage's output as a checkpoint in `interpretation_tasks.checkpoints` (JSON). A stage already in checkpoints is skipped on resume; startup recovery re-queues `running` tasks. Missing sex/age pauses the task at `awaiting_meta` until `PATCH /api/reports/{id}/meta`.
   - Stages write to DB before saving their checkpoint, delete-then-insert for idempotent crash recovery (F3 pattern).
2. **Bounded LangGraph agent** for follow-up chat (`chat/agent.py`): exactly 4 read-only tools (`chat/tools.py` — report lookup, indicator knowledge, reference-range computation, knowledge search), tool-round cap with forced convergence (`CONVERGE_INSTRUCTION`). Memory via LangGraph Postgres checkpointer; its unavailability only degrades chat memory, not core services.

### Data stores and their roles

- **PostgreSQL** (9 tables in `db/models.py`, all enums stored as strings): reports, raw/normalized items, tasks+checkpoints, interpretations, follow-up plans, chat sessions/messages, audit events.
- **Neo4j = knowledge fact source** (KG): `Indicator` nodes with sex/age-windowed `RangeSpec`s, directional `HIGH_SUGGESTS`/`LOW_SUGGESTS` relations, clusters, interventions, departments, and combination `Pattern`s. Read via `knowledge/kg_client.py`; written only by `scripts/seed_import.py` (entity-level MERGE + delete-outgoing-edges-then-recreate, never full rebuild).
- **Milvus = derived retrieval layer**: one collection with dense (HNSW) + sparse (BM25 via jieba analyzer, auto-generated from `text` field) indexes; chunks tagged with `entity_id` for incremental delete. All Milvus methods are sync — callers wrap with `asyncio.to_thread`.

### Rule layer, retrieval, guardrails

- **Reference-range judgment is pure code** (`pipeline/rule_compare.py`, `parsing/normalizer.py`) — LLM must never judge numeric highs/lows. Report-provided ranges take precedence over KG ranges; critical values always come from KG; unit conversion keeps both raw (report-unit) and standardized values. This layer has a 100% coverage acceptance requirement.
- **Retrieval** (`retrieval/hybrid.py`): 3-path RRF fusion (KG + dense + BM25), per-path failure → empty list absorbed by RRF; character-budget trim. No hits → placeholder evidence. Chat-side refusal when empty or top RRF score < `REFUSAL_RRF_THRESHOLD` (default 0.0).
- **Guardrails** (`guardrails/`): pure-code rules (`rules.py` — diagnosis terms, medication/dose terms, required elements, numeric consistency vs whitelist) → SUSPECT triggers a secondary LLM review (`reviewer.py`) → regenerate once with feedback → else safe degraded template (`enforce.py`). The pipeline's `guardrail` stage is the authoritative source for the stored interpretation doc (deep-copies the generate checkpoint; never mutate it). Chat answers run the same rules in the SSE layer after full assembly. `run_eval.py` must use the same text-assembly function (`item_guardrail_text`) as the guardrail stage or the safety gate misses the risks channel.
- Everything guardrail-related is logged to the `audit_events` table (`guardrails/audit.py`).

### Degradation philosophy (spec §11)

External failures degrade layer-by-layer, never crash the request: app starts with unreachable services (warn only); Neo4j down → KG path returns empty, dense+BM25 continue; Milvus down → dense+BM25 skipped; LLM failure → retry with exponential backoff (`llm/client.py`, shared singleton `llm_factory` — never instantiate clients per-module), then degrade to templates. Vision parse failure → task failed (caller retries), PDF text-layer insufficient → vision fallback.

### Dependency injection

`pipeline/deps.py::build_deps` builds the single `PipelineDeps` (llms, kg, store, retriever, normalizer, db, session_factory) once in the app lifespan; routes read from `request.app.state`. Unit tests inject fakes via constructor params and `runner._stage_funcs` monkeypatching.

### Observability

structlog JSON logs (`observability.py`) with contextvars for `request_id`/`task_id`/`session_id`; optional LangSmith tracing behind `LANGSMITH_TRACING=false`.

### API surface

Four routers in `src/report_agent/api/` (health / reports / tasks / chat); full endpoint table in `README.md`. Report upload → async task poll loop → interpretation + follow-up plan → chat session → SSE stream (events: `token` / `tool_call` / `evidence` / `safety` / `error` / `done`).