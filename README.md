# report-agent 体检报告问答助手

上传体检报告 → 结构化解析 → 异常判定 → 四段式解读(总体结论/逐项解读/分级建议/免责声明)→ 结构化复查计划;
并支持基于本人报告的多轮追问(SSE 流式)。产品定位为报告解读 + 健康建议,**不构成医学诊断**。
提供 Web 界面(上传/手动录入、任务进度、四段式解读与追问聊天,仅桌面端)。

## 目录

- `docs/requirements.md` — 需求文档
- `docs/superpowers/specs/2026-09-02-report-agent-design.md` — 系统设计(spec)
- `docs/superpowers/specs/2026-09-03-report-agent-frontend-design.md` — 前端设计(spec-f)
- `docs/superpowers/plans/2026-09-02-report-agent.md` — 本实现规划
- `backend/` — 后端(FastAPI + LangGraph + Neo4j + Milvus + PostgreSQL)
- `frontend/` — 前端(Vue 3 + Vite + TypeScript + Element Plus,见 `frontend/README.md`)

## 快速开始

### 后端

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
#   (Windows 上请用 serve.py: uvicorn CLI 直启时事件循环已先于应用代码创建,无法切换,
#    psycopg async 追问记忆 checkpointer 会降级)
uv run python scripts/smoke.py                 # 端到端冒烟(见下方验收对照 1/3/4)

# 评测(可选;评测集为空时 run_eval 会 exit 2)
uv run python scripts/gen_eval_reports.py --count 20   # LLM 合成评测集,gt 需人工校对
uv run python scripts/run_eval.py                      # 回归门禁,人工确认后可 --update-baseline 冻结基线
uv run python scripts/dry_run_real.py                  # 真实 case 解析/归一化对照表(gt 标注校标)
```

评测维度(评测升级 spec §4):解析准确率(仅真实 case,eval/real/)、归一化 F1、规则层准确率(硬门禁 1.0)、
证据覆盖率、数值一致性、安全(硬门禁零违规)、拒答正确率、LLM 评分(解读/问答/证据,参考指标+回归监测)。
注:真实 case 归一化 F1 结构性偏低(大量目录外项 code=null,None==None 不计命中),与合成 case 均值合并解读需谨慎。
全链路 trace 落 `backend/eval/traces/<run>/`(不入 git,自动保留最近 5 次):
每 case 阶段 I/O、LLM 调用原文、评分明细、QA 工具调用。

要点:

- `.env.example` 与 `report_agent.config.Settings` 字段一一对应(大小写归一核对);仅
  `APP_NAME`(默认 `report-agent`)未列入,如需改可自行加 `APP_NAME=...`。
- alembic 连接串由 `env.py` 以 `POSTGRES_DSN` 覆写,不读 `alembic.ini` 里的占位行。
- `seed_import.py --entity GLU` 等单实体增量入库(MERGE / delete+insert),不触发全量重建。

### 前端

需 Node ≥ 20 与 pnpm,后端已在 `http://localhost:8000` 启动:

```bash
cd frontend
pnpm install
pnpm run dev      # http://localhost:5173;dev 代理 /api → 后端(后端无 CORS,代理直接绕开)
pnpm test         # Vitest 单测(纯函数为主,无需真实后端)
pnpm run build    # vue-tsc 类型检查 + vite 产物 dist/(可任意静态服务器部署)
```

要点:

- 代理目标可用 `VITE_API_TARGET=http://other:8000 pnpm run dev` 覆盖;生产部署走反代同源挂载 `/api`,无跨域问题。
- 页面:`/` 首页(上传 PDF/照片 或 手动录入 tab、最近报告列表)→ `/report/:id` 报告详情(任务进度带 → 四段式解读 / 复查计划 / 追问 SSE 三 tab);`*` 为 404。
- 后端返回的解读四段、安全话术、免责声明**原样渲染**,前端不改写(视觉与交互约束见前端 spec-f §6/§7)。

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

(前端界面即按此链路实现:上传/录入 → 轮询进度 → 阅读解读与复查计划 → 追问。)

## 验收标准对照(spec §7)

1. PDF/拍照端到端 → `scripts/smoke.py`
2. 规则层单测 100% → `uv run pytest tests/unit_rules -v --cov=report_agent.pipeline.rule_compare --cov-report=term-missing`;安全零违规 → `run_eval.py` 的 `safety_violations`
3. 降级路径演示 → `docker stop report-neo4j` 后重跑 smoke(解读仍产出)
4. 知识库外拒答 → smoke 第 6 步 + 评测 QA 拒答类
5. 增量更新 → `seed_import.py --entity GLU`(单实体 delete+insert / MERGE,不触发全量重建)
6. 前端链路 → 前端 spec-f §10 验收清单(上传/手动录入、awaiting_meta 补录恢复、进度带、四段解读原样渲染、追问 SSE、最近报告);以 `pnpm test` 与 `pnpm run build` 通过为底线
