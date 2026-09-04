# report-agent 体检报告问答助手

上传体检报告 → 结构化解析 → 异常判定 → 四段式解读(总体结论/逐项解读/分级建议/免责声明)→ 结构化复查计划;
并支持基于本人报告的多轮追问(SSE 流式)。产品定位为报告解读 + 健康建议,**不构成医学诊断**。

## 目录

- `docs/requirements.md` — 需求文档
- `docs/superpowers/specs/2026-09-02-report-agent-design.md` — 系统设计(spec)
- `docs/superpowers/plans/2026-09-02-report-agent.md` — 本实现规划
- `backend/` — 后端(FastAPI + LangGraph + Neo4j + Milvus + PostgreSQL)
- `frontend/` — 前端(预留,本期不实现,见 `frontend/README.md`)

## 快速开始

> 冒烟与评测命令依赖真实基础设施(docker compose 服务)与 LLM key,请在具备条件的机器上执行;
> 纯代码验证以 `uv run pytest` 与 `uv run ruff check src tests scripts` 为准。

```bash
cd backend
cp .env.example .env          # 填入 DEEPSEEK_API_KEY / EMBEDDING_API_KEY
docker compose up -d          # postgres + neo4j + milvus(含 milvus 依赖 etcd + minio)
uv sync
uv run alembic upgrade head
uv run python scripts/seed_draft.py --all      # LLM 起草种子(人工校对 YAML 后入库)
uv run python scripts/seed_import.py --all     # 入库(增量: --entity CODE)
uv run python scripts/serve.py                 # 一键启动: 幂等迁移(已是最新自动跳过) + 启动 API(默认 127.0.0.1:8000)
# 或不迁移仅启动: uv run uvicorn report_agent.api.app:create_app --factory --port 8000
uv run python scripts/smoke.py                 # 端到端冒烟(见下方验收对照 1/3/4)

# 评测(可选;评测集为空时 run_eval 会 exit 2)
uv run python scripts/gen_eval_reports.py --count 20   # LLM 合成评测集,gt 需人工校对
uv run python scripts/run_eval.py                      # 回归门禁,人工确认后可 --update-baseline 冻结基线
```

要点:

- `.env.example` 与 `report_agent.config.Settings` 字段一一对应(大小写归一核对);仅
  `APP_NAME`(默认 `report-agent`)未列入,如需改可自行加 `APP_NAME=...`。
- alembic 连接串由 `env.py` 以 `POSTGRES_DSN` 覆写,不读 `alembic.ini` 里的占位行。
- `seed_import.py --entity GLU` 等单实体增量入库(MERGE / delete+insert),不触发全量重建。

## API

端点以代码为准(`backend/src/report_agent/api/` 下 `health / reports / tasks / chat` 四个 router)。
除标注外均为 JSON;追问为 SSE 流式。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | postgres / neo4j / milvus 连通性检查 |
| POST | `/api/reports` | multipart `file` 上传(PDF/拍照),**或**同端点直接 JSON 手动录入(`{meta, items}`,非 form 字段),二者必居其一;返回 `{report_id, task_id}` 并异步启动管线 |
| GET | `/api/tasks/{task_id}` | 轮询任务:`status` ∈ pending / awaiting_meta / running / completed / degraded / failed,含 `stage` / `timings` / `error` |
| PATCH | `/api/reports/{report_id}/meta` | 补录 `sex` / `age`;任务停在 `awaiting_meta` 时自动恢复管线 |
| GET | `/api/reports/{report_id}` | 报告详情与当前任务 |
| GET | `/api/reports/{report_id}/interpretation` | 四段式解读(未生成则 404) |
| GET | `/api/reports/{report_id}/followup-plan` | 结构化复查计划(未生成则 404) |
| POST | `/api/reports/{report_id}/chat/sessions` | 建追问会话,返回 `{session_id}` |
| POST | `/api/chat/sessions/{session_id}/messages` | 流式追问,body `{content}`;SSE 事件:`token` / `tool_call` / `evidence` / `safety` / `error` / `done` |
| GET | `/api/chat/sessions/{session_id}/history` | 会话历史 `{messages}` |

典型链路:

```
POST /api/reports(上传/手动录入)→ GET /api/tasks/{id}(轮询)→
GET /api/reports/{id}/interpretation + /followup-plan →
POST /api/reports/{id}/chat/sessions → POST /api/chat/sessions/{sid}/messages(SSE)
```

## 验收标准对照(spec §7)

1. PDF/拍照端到端 → `scripts/smoke.py`
2. 规则层单测 100% → `uv run pytest tests/unit_rules -v --cov=report_agent.pipeline.rule_compare --cov-report=term-missing`;安全零违规 → `run_eval.py` 的 `safety_violations`
3. 降级路径演示 → `docker stop report-neo4j` 后重跑 smoke(解读仍产出)
4. 知识库外拒答 → smoke 第 6 步 + 评测 QA 拒答类
5. 增量更新 → `seed_import.py --entity GLU`(单实体 delete+insert / MERGE,不触发全量重建)
