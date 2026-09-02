# 体检报告问答助手(report-agent)实现规划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零构建体检报告问答助手后端：报告解析→归一化→规则比对→逐异常项解读(四段式)→复查计划,以及基于报告的多轮追问对话(SSE)。

**Architecture:** 报告解读走确定性异步管线(显式阶段函数 + Postgres 任务表断点),追问对话走 LangGraph 有界 Agent(4 个受控工具 + 轮数上限 + Postgres checkpointer)。知识层:Neo4j 为事实源(指标/区间/疾病提示/组合模式/建议),Milvus 为派生检索层(dense 向量 + BM25 稀疏双索引),三路检索 RRF 融合。所有 LLM 输出过规则护栏 + 风险信号触发的二次审核。

**Tech Stack:** Python 3.13 + uv、FastAPI + Pydantic v2、SQLAlchemy 2.0 async + asyncpg + Alembic、LangGraph + langgraph-checkpoint-postgres、OpenAI SDK(DeepSeek + DashScope 兼容端点)、Neo4j 5(driver)、Milvus 2.5(pymilvus,BM25/jieba analyzer)、Unstructured hi_res + pymupdf、structlog、pytest + pytest-asyncio。

**Spec:** `D:\DeskTop\agent\report-agent\docs\superpowers\specs\2026-09-02-report-agent-design.md`(本规划从 spec 展开,执行者必须同时阅读;spec 的 §2 决策表、§11 降级总览、§13 C9 继承与规避是硬约束)

## Global Constraints

- 仓库根:`D:\DeskTop\agent\report-agent`;后端代码一律在 `backend/`,包名 `report_agent`(src 布局,`backend/src/report_agent/`)
- Python 3.13,依赖管理 uv;依赖锁定见 Task 1 的 pyproject.toml
- **全部配置走环境变量**:pydantic-settings 读 `.env`,必须提供 `.env.example`(spec §5/§3.3;修 C9 env 未接线的缺陷)
- **区间判定、单位换算、危急值触发、组合模式识别必须是纯代码**,单测 100%(spec §3.3/§12);LLM 只做解释、成文、结构化抽取辅助
- 所有 LLM 输出必须过规则护栏;护栏触发/降级/拒答事件写 `audit_events` 表(spec §5.5/§10)
- `chat_messages` 异步写,写失败告警不阻塞用户响应(spec §4.1/§6.3)
- 检索三路(KG + Milvus dense + Milvus fulltext-BM25)+ RRF 融合;任一路失败返回空,由 RRF 吸收,不抛异常(spec §4.3/§11)
- 报告自带参考区间优先,KG 区间仅补全;危急值阈值永远来自 KG(spec §2/§5.2)
- 参考区间单位换算表是**按指标**的(不同物质换算系数不同),不是全局表
- 无鉴权 + ID 隔离;`reports`/`chat_sessions` 保留 user_id 可空列(spec §2)
- 消息/日志/文档一律中文;代码标识符与提交信息用英文
- 每个任务 TDD:先写失败测试→实现→通过→提交;提交信息 `feat:`/`test:`/`docs:` 前缀
- 单元测试不依赖外部服务(全部 mock);真实服务集成验证走 `scripts/smoke.py` 或标记 `@pytest.mark.integration` 且默认跳过

---

## 文件结构总览

```
backend/
├── pyproject.toml                 # 依赖与工具链(uv)
├── alembic.ini                    # 迁移配置
├── alembic/                       # 迁移环境与版本
├── .env.example                   # 全部环境变量样例
├── docker-compose.yml             # postgres16 + neo4j5 + milvus2.5(etcd+minio)
├── knowledge_seeds/
│   ├── indicator_list.yaml        # 指标清单(起草输入,人工维护)
│   └── indicators/{CODE}.yaml     # 每指标种子(LLM 起草→人工校对)
├── src/report_agent/
│   ├── __init__.py
│   ├── config.py                  # Settings(pydantic-settings)+ get_settings()
│   ├── db/
│   │   ├── models.py              # 9 张表 SQLAlchemy 模型
│   │   └── session.py             # async engine/sessionmaker
│   ├── observability.py           # structlog 配置 + 日志工具
│   ├── llm/
│   │   ├── client.py              # DeepSeekClient/EmbeddingClient/工厂/重试
│   │   ├── prompts.py             # load_prompt(name)
│   │   └── prompts/*.txt          # 全部 prompt 模板(集中管理)
│   ├── knowledge/
│   │   ├── milvus_client.py       # MilvusStore(dense+sparse 双索引)
│   │   ├── kg_client.py           # KGClient(指标词典/区间/上下文/模式/建议)
│   │   ├── seed_schemas.py        # 种子 YAML 的 pydantic 校验模型
│   │   └── doc_builder.py         # KG→文档→分块(上下文头/短文单块)
│   ├── parsing/
│   │   ├── schemas.py             # RawReportItem/ReportMeta/NormalizedItem
│   │   ├── pdf_parser.py          # Unstructured hi_res 文字层解析
│   │   ├── vision_parser.py       # 页图→多模态 LLM→JSON
│   │   └── normalizer.py          # 词典匹配/单位换算/区间补全标记
│   ├── retrieval/
│   │   └── hybrid.py              # 三路检索 + RRF + 证据预算
│   ├── pipeline/
│   │   ├── rule_compare.py        # 判定/危急值/组合模式(纯代码核心)
│   │   ├── tasks.py               # TaskService(认领/checkpoint/恢复)
│   │   ├── runner.py              # run_pipeline(阶段调度)
│   │   ├── interpret.py           # 逐项解读/总评/四段式组装
│   │   └── followup_plan.py       # 复查计划(模板+槽位润色)
│   ├── guardrails/
│   │   ├── rules.py               # 规则护栏 4 项(纯代码)
│   │   ├── reviewer.py            # 二次 LLM 审核(风险信号触发)
│   │   ├── enforce.py             # 重生成→降级流程
│   │   └── audit.py               # audit_events 写入
│   ├── chat/
│   │   ├── tools.py               # 4 个受控工具
│   │   ├── agent.py               # LangGraph 图构建(轮数上限/收敛)
│   │   └── sse.py                 # astream_events→SSE 事件
│   └── api/
│       ├── app.py                 # FastAPI 工厂 + lifespan(恢复任务)
│       ├── reports.py             # 上传/手动录入/详情/meta 补录/解读/复查单
│       ├── tasks.py               # 任务轮询
│       ├── chat.py                # 会话/消息 SSE/历史
│       └── health.py              # /api/health
├── scripts/
│   ├── seed_draft.py              # LLM 起草种子 YAML(断点续传)
│   ├── seed_import.py             # YAML→Neo4j+Milvus(增量, --entity)
│   ├── gen_sample_pdf.py          # 生成样例 PDF 供 smoke
│   ├── gen_eval_reports.py        # LLM 合成评测报告集
│   ├── run_eval.py                # 评测+基线回归门禁
│   └── smoke.py                   # 端到端冒烟(含拒答与降级场景)
├── eval/
│   ├── reports/*.json             # 20 份标注报告
│   ├── qa_pairs.jsonl             # 30 条追问 QA(含拒答)
│   ├── fixtures/                  # smoke 用样例文件
│   └── baseline.json              # 评测基线(人工确认后冻结)
└── tests/
    ├── conftest.py                # fixtures(mock clients)
    ├── unit_llm/ unit_knowledge/ unit_rules/ unit_parsing/
    ├── unit_retrieval/ unit_pipeline/ unit_guardrails/ unit_chat/
    └── unit_api/
```

---

### Task 1: 项目骨架 + 基础设施 + 健康检查

**Files:**
- Create: `backend/pyproject.toml`, `backend/.env.example`, `backend/docker-compose.yml`, `backend/src/report_agent/__init__.py`, `backend/src/report_agent/config.py`, `backend/src/report_agent/observability.py`, `backend/src/report_agent/api/__init__.py`, `backend/src/report_agent/api/app.py`, `backend/src/report_agent/api/health.py`, `backend/tests/conftest.py`, `backend/tests/unit_api/__init__.py`, `backend/tests/unit_api/test_health.py`, `backend/.gitignore`
- Test: `backend/tests/unit_api/test_health.py`, `backend/tests/test_config.py`

**Interfaces:**
- Consumes: 无(第一个任务)
- Produces:
  - `report_agent.config.Settings`(字段名见下,后续所有任务从 `get_settings()` 取配置)
  - `report_agent.api.app.create_app() -> FastAPI`(lifespan 暂只做日志,Task 14 加任务恢复)
  - `GET /api/health` 返回 `{"status": "ok", "checks": {"postgres": bool, "milvus": bool, "neo4j": bool}}`
  - `report_agent.observability.get_logger(name) -> structlog.BoundLogger`

- [ ] **Step 1: 写 pyproject.toml 与工具配置**

```toml
[project]
name = "report-agent"
version = "0.1.0"
description = "体检报告问答助手后端"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sse-starlette>=2.1",
    "pydantic>=2.8",
    "pydantic-settings>=2.4",
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "langgraph>=0.2",
    "langgraph-checkpoint-postgres>=2.0",
    "openai>=1.40",
    "pymilvus>=2.5.0,<3.0",
    "neo4j>=5.20",
    "unstructured[pdf,local-inference]>=0.16",
    "pymupdf>=1.24",
    "structlog>=24.1",
    "pyyaml>=6.0",
    "httpx>=0.27",
]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: 写 .env.example**(全量环境变量,后续任务按需增补字段时同步更新本文件)

```bash
# ===== LLM(DeepSeek,OpenAI 兼容)=====
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_API_KEY=
CHAT_MODEL=deepseek-chat
VISION_MODEL=deepseek-v4-flash-vision-exp

# ===== Embedding(DashScope OpenAI 兼容端点)=====
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIM=1024

# ===== Neo4j =====
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=report-agent-dev
NEO4J_DATABASE=neo4j

# ===== Milvus =====
MILVUS_URI=http://localhost:19530
MILVUS_COLLECTION=medical_knowledge

# ===== Postgres =====
POSTGRES_DSN=postgresql+asyncpg://report:report-agent-dev@localhost:5432/report_agent

# ===== 管线 =====
RETRIEVAL_TOP_K=5
RETRIEVE_CONCURRENCY=5
EVIDENCE_BUDGET_CHARS=6000
CHUNK_SIZE=500
CHUNK_OVERLAP=50
SHORT_DOC_MAX_CHARS=1200

# ===== 追问 Agent =====
AGENT_MAX_TOOL_ROUNDS=8
REFUSAL_RRF_THRESHOLD=0.0

# ===== 解析 =====
UNSTRUCTURED_STRATEGY=hi_res
UNSTRUCTURED_INFER_TABLE=true
PARSE_MIN_ITEMS=3

# ===== LangSmith(可选,false 时完全不启用)=====
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=report-agent
```

- [ ] **Step 3: 写 docker-compose.yml**(postgres16 + neo4j5 + milvus2.5 三件套)

```yaml
services:
  postgres:
    container_name: report-postgres
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: report
      POSTGRES_PASSWORD: report-agent-dev
      POSTGRES_DB: report_agent
    ports: ["5432:5432"]
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U report -d report_agent"]
      interval: 10s
      timeout: 5s
      retries: 5

  neo4j:
    container_name: report-neo4j
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/report-agent-dev
      NEO4J_server_memory_heap_max__size: 1G
    ports: ["7474:7474", "7687:7687"]
    volumes: [neo4j_data:/data]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 >/dev/null 2>&1 || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 10

  etcd:
    container_name: report-etcd
    image: quay.io/coreos/etcd:v3.5.16
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
    volumes: [etcd_data:/etcd]
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 30s
      timeout: 20s
      retries: 3

  minio:
    container_name: report-minio
    image: minio/minio:RELEASE.2023-03-20T20-16-18Z
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    ports: ["9001:9001", "9000:9000"]
    volumes: [minio_data:/minio_data]
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  milvus:
    container_name: report-milvus
    image: milvusdb/milvus:v2.5.14
    command: ["milvus", "run", "standalone"]
    security_opt: [seccomp:unconfined]
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    volumes: [milvus_data:/var/lib/milvus]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      start_period: 90s
      timeout: 20s
      retries: 5
    ports: ["19530:19530", "9091:9091"]
    depends_on: [etcd, minio]

volumes:
  postgres_data:
  neo4j_data:
  etcd_data:
  minio_data:
  milvus_data:
```

- [ ] **Step 4: 写 config.py**

```python
"""全部环境变量配置。任何新配置字段先在这里声明,再更新 .env.example。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "report-agent"

    # LLM(DeepSeek,OpenAI 兼容)
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    chat_model: str = "deepseek-chat"
    vision_model: str = "deepseek-v4-flash-vision-exp"

    # Embedding(DashScope OpenAI 兼容端点)
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = 1024

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "report-agent-dev"
    neo4j_database: str = "neo4j"

    # Milvus
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "medical_knowledge"

    # Postgres
    postgres_dsn: str = "postgresql+asyncpg://report:report-agent-dev@localhost:5432/report_agent"

    # 管线与检索
    retrieval_top_k: int = 5
    retrieve_concurrency: int = 5
    evidence_budget_chars: int = 6000
    chunk_size: int = 500
    chunk_overlap: int = 50
    short_doc_max_chars: int = 1200

    # 追问 Agent
    agent_max_tool_rounds: int = 8
    refusal_rrf_threshold: float = 0.0

    # 解析
    unstructured_strategy: str = "hi_res"
    unstructured_infer_table: bool = True
    parse_min_items: int = 3

    # LangSmith(可选)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "report-agent"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: 写 observability.py**

```python
"""structlog JSON 结构化日志。上下文变量: request_id/task_id/session_id。"""
import contextvars

import structlog

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)


def bind_context(**values) -> None:
    for k, v in values.items():
        if v is not None:
            structlog.contextvars.bind_contextvars(**{k: v})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def setup_logging() -> None:
    import logging

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
```

- [ ] **Step 6: 写失败测试(test_health.py)**

```python
from fastapi.testclient import TestClient

from report_agent.api.app import create_app


def test_health_returns_checks(monkeypatch):
    # 外部服务不可达时健康检查不抛异常,返回 False
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["checks"].keys()) == {"postgres", "milvus", "neo4j"}
```

- [ ] **Step 7: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv sync && uv run pytest tests/unit_api/test_health.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'report_agent'`

- [ ] **Step 8: 实现 app.py 与 health.py**

```python
# api/health.py
import asyncio

from fastapi import APIRouter

from report_agent.config import get_settings
from report_agent.observability import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["health"])


async def _check(name: str, fn) -> bool:
    try:
        await asyncio.wait_for(fn(), timeout=3)
        return True
    except Exception as e:  # noqa: BLE001 —— 健康检查必须吞掉一切异常
        log.warning("health_check_failed", check=name, error=str(e))
        return False


async def _pg_check() -> None:
    from sqlalchemy import text

    from report_agent.db.session import engine

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@router.get("/health")
async def health():
    from report_agent.knowledge.kg_client import check_neo4j
    from report_agent.knowledge.milvus_client import check_milvus

    s = get_settings()
    checks = {
        "postgres": await _check("postgres", _pg_check),
        # neo4j/milvus 驱动为同步,放线程池执行
        "milvus": await _check("milvus", lambda: asyncio.to_thread(check_milvus, s.milvus_uri)),
        "neo4j": await _check(
            "neo4j", lambda: asyncio.to_thread(check_neo4j, s.neo4j_uri, s.neo4j_user, s.neo4j_password)
        ),
    }
    return {"status": "ok", "checks": checks}
```

由于 Task 1 尚无 db/knowledge 模块,先建**空实现占位**(Task 2/4/5 替换为真实实现):

```python
# api/app.py
from fastapi import FastAPI

from report_agent.observability import setup_logging


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="report-agent", version="0.1.0")
    from report_agent.api import health  # noqa: F401

    app.include_router(health.router)
    return app
```

```python
# db/session.py(占位:Task 2 扩充)
# knowledge/milvus_client.py(占位)
def check_milvus(uri: str) -> bool:
    from pymilvus import MilvusClient

    client = MilvusClient(uri=uri, timeout=3)
    client.list_collections()  # 不可达会抛异常,由 _check 捕获
    return True


# knowledge/kg_client.py(占位)
def check_neo4j(uri: str, user: str, password: str) -> bool:
    from neo4j import GraphDatabase

    with GraphDatabase.driver(uri, auth=(user, password)).session() as s:
        s.run("RETURN 1")
    return True
```

- [ ] **Step 9: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_api/test_health.py -v`
Expected: PASS

- [ ] **Step 10: 写 .gitignore 并启动基础设施验证**

`backend/.gitignore` 内容:

```
.venv/
__pycache__/
.env
*.pyc
.pytest_cache/
.ruff_cache/
```

Run: `cd /d/DeskTop/agent/report-agent/backend && docker compose up -d && docker compose ps`
Expected: postgres/neo4j/etcd/minio/milvus 全部 running(healthy)

- [ ] **Step 11: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: scaffold backend with config, logging, health endpoint and docker infra"
```

---

### Task 2: 数据库模型与迁移

**Files:**
- Modify: `backend/src/report_agent/db/session.py`(占位 → 真实), `backend/src/report_agent/config.py`(无改动)
- Create: `backend/src/report_agent/db/__init__.py`, `backend/src/report_agent/db/models.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_init.py`, `backend/tests/unit_api/test_models.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings`
- Produces:
  - `report_agent.db.models`: `Report`, `RawItem`, `NormalizedItemRow`, `InterpretationTask`, `InterpretationRow`, `FollowupPlanRow`, `ChatSession`, `ChatMessage`, `AuditEvent`(表名:reports / report_items_raw / report_items_normalized / interpretation_tasks / interpretations / followup_plans / chat_sessions / chat_messages / audit_events)
  - `report_agent.db.session`: `engine: AsyncEngine`, `session_factory: async_sessionmaker`, `get_session()` 依赖
  - 枚举字符串约定:`status`/`source` 等列存 Python 枚举的 `.value` 字符串,DB 列类型 `String`,不用原生 enum(避免迁移痛苦)

- [ ] **Step 1: 写 models.py(9 张表)**

```python
"""9 张表,全部 String 列存枚举值(见 spec §4.1)。JSON 列用 sqlalchemy JSON 类型。"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"

    id = _pk()
    source: Mapped[str] = mapped_column(String(16))  # pdf / photo / manual
    file_path: Mapped[str | None] = mapped_column(String(512))
    institution: Mapped[str | None] = mapped_column(String(128))
    report_date: Mapped[str | None] = mapped_column(String(32))
    sex: Mapped[str | None] = mapped_column(String(8))  # male / female
    age: Mapped[float | None] = mapped_column(Float)
    user_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items_raw: Mapped[list["RawItem"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    items_normalized: Mapped[list["NormalizedItemRow"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class RawItem(Base):
    __tablename__ = "report_items_raw"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    section: Mapped[str | None] = mapped_column(String(64))
    item_name: Mapped[str] = mapped_column(String(128))
    value_text: Mapped[str | None] = mapped_column(String(64))
    value_num: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    ref_range_text: Mapped[str | None] = mapped_column(String(128))
    abnormal_flag: Mapped[str | None] = mapped_column(String(16))

    report: Mapped["Report"] = relationship(back_populates="items_raw")


class NormalizedItemRow(Base):
    __tablename__ = "report_items_normalized"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    raw_item_id: Mapped[str | None] = mapped_column(ForeignKey("report_items_raw.id"))
    section: Mapped[str | None] = mapped_column(String(64))
    item_name: Mapped[str] = mapped_column(String(128))
    indicator_code: Mapped[str | None] = mapped_column(String(32), index=True)  # None = unmapped
    value_text: Mapped[str | None] = mapped_column(String(64))
    value_num: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))  # 标准单位
    raw_value_num: Mapped[float | None] = mapped_column(Float)  # 换算前原值(与报告区间同单位)
    raw_unit: Mapped[str | None] = mapped_column(String(32))
    ref_range_text: Mapped[str | None] = mapped_column(String(128))  # 报告自带区间
    range_from: Mapped[str | None] = mapped_column(String(8))  # report / kg
    status: Mapped[str | None] = mapped_column(String(16))  # 判定结果,normalize 阶段后填写
    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    critical: Mapped[bool] = mapped_column(default=False)

    report: Mapped["Report"] = relationship(back_populates="items_normalized")


class InterpretationTask(Base):
    __tablename__ = "interpretation_tasks"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending/awaiting_meta/running/completed/failed/degraded
    stage: Mapped[str | None] = mapped_column(String(16))
    checkpoints: Mapped[dict] = mapped_column(JSON, default=dict)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InterpretationRow(Base):
    __tablename__ = "interpretations"

    id = _pk()
    task_id: Mapped[str] = mapped_column(
        ForeignKey("interpretation_tasks.id", ondelete="CASCADE"), unique=True
    )
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSON)  # 逐项解读数组
    advice_summary: Mapped[str] = mapped_column(Text)
    disclaimer: Mapped[str] = mapped_column(Text)
    degraded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FollowupPlanRow(Base):
    __tablename__ = "followup_plans"

    id = _pk()
    task_id: Mapped[str] = mapped_column(
        ForeignKey("interpretation_tasks.id", ondelete="CASCADE"), unique=True
    )
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    items: Mapped[list] = mapped_column(JSON)
    degraded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = _pk()
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSON)
    evidence_ids: Mapped[list | None] = mapped_column(JSON)
    guardrail_flags: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = _pk()
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    report_id: Mapped[str | None] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: 写 session.py(替换占位)**

```python
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from report_agent.config import get_settings

engine: AsyncEngine = create_async_engine(get_settings().postgres_dsn, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with session_factory() as session:
        yield session
```

- [ ] **Step 3: 写失败测试**

```python
# tests/unit_api/test_models.py —— 不需要真库,验证表定义与枚举字符串约定
from report_agent.db import models


def test_all_tables_defined():
    assert {t.name for t in models.Base.metadata.sorted_tables} == {
        "reports",
        "report_items_raw",
        "report_items_normalized",
        "interpretation_tasks",
        "interpretations",
        "followup_plans",
        "chat_sessions",
        "chat_messages",
        "audit_events",
    }


def test_report_has_meta_columns():
    cols = {c.name for c in models.Report.__table__.columns}
    assert {"id", "source", "file_path", "institution", "report_date", "sex", "age", "user_id"} <= cols


def test_task_has_checkpoint_columns():
    cols = {c.name for c in models.InterpretationTask.__table__.columns}
    assert {"checkpoints", "timings", "status", "stage", "error"} <= cols
```

- [ ] **Step 4: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_api/test_models.py -v`
Expected: FAIL,import error(占位 session.py 尚可用但 models 不存在)

- [ ] **Step 5: 运行测试确认通过**

Run: 同上
Expected: PASS

- [ ] **Step 6: 配置 Alembic(异步模板)并生成初始迁移**

```bash
cd /d/DeskTop/agent/report-agent/backend
uv run alembic init -t async alembic
```

编辑 `alembic/env.py`:import `report_agent.db.models` 保证 `Base.metadata` 完整,target_metadata 指向 `models.Base.metadata`,sqlalchemy.url 从 `report_agent.config.get_settings().postgres_dsn` 读取:

```python
from report_agent.config import get_settings
from report_agent.db import models

config.set_main_option("sqlalchemy.url", get_settings().postgres_dsn)
target_metadata = models.Base.metadata
```

生成并应用:

```bash
docker compose up -d postgres
uv run alembic revision --autogenerate -m "init"
uv run alembic upgrade head
uv run python -c "from sqlalchemy import inspect, create_engine; e=create_engine('postgresql+psycopg://report:report-agent-dev@localhost:5432/report_agent'); print([t for t in inspect(e).get_table_names() if not t.startswith('alembic')])"
```

Expected: 输出 9 张表名

- [ ] **Step 7: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add async db models for reports, tasks, chat and audit with alembic"
```

---

### Task 3: LLM 与 Embedding 客户端

**Files:**
- Create: `backend/src/report_agent/llm/__init__.py`, `backend/src/report_agent/llm/client.py`, `backend/src/report_agent/llm/prompts.py`, `backend/src/report_agent/llm/prompts/parse_vision.txt`, `backend/tests/unit_llm/__init__.py`, `backend/tests/unit_llm/test_client.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings`
- Produces:
  - `DeepSeekClient.chat(messages: list[dict], temperature=0.1, max_tokens=2048, model: str | None = None) -> str`(重试+退避,失败抛 `LLMError`)
  - `DeepSeekClient.chat_stream(messages, **kw) -> AsyncIterator[str]`(流式,同样重试)
  - `DeepSeekClient.complete_json(messages, retry_feedback: bool = True) -> dict`(JSON 输出 + 解析失败反馈重试,最终抛 `LLMError`)
  - `EmbeddingClient.embed_texts(texts: list[str]) -> list[list[float]]`(批量,重试)
  - `create_llm_clients() -> LLMClients`(chat / vision / embedding 三实例共享重试逻辑;vision 与 chat 同 client 不同默认 model)
  - `load_prompt(name: str) -> str`(读 `llm/prompts/{name}.txt`,缺失抛 FileNotFoundError)
  - 异常类 `LLMError`

- [ ] **Step 1: 写失败测试(用 respx/mock 验证重试与 JSON 修复)**

```python
import asyncio

import pytest
from openai import APITimeoutError

from report_agent.llm.client import DeepSeekClient, LLMError
from report_agent.llm.prompts import load_prompt


def test_load_prompt_found():
    text = load_prompt("parse_vision")
    assert "体检报告" in text  # parse_vision.txt 必须在 llm/prompts/ 下


def test_load_prompt_missing():
    with pytest.raises(FileNotFoundError):
        load_prompt("no_such_prompt")


def test_chat_retries_then_raises():
    calls = []

    async def failing(messages, **kw):
        calls.append(messages)
        raise APITimeoutError(request=object())

    c = DeepSeekClient(client=object(), max_retries=2, base_delay=0.0)
    c._chat_raw = failing  # 注入失败实现,绕过真网络
    with pytest.raises(LLMError):
        asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    assert len(calls) == 3  # 1 次初始 + 2 次重试


def test_complete_json_repairs_bad_json():
    responses = ["{broken", '{"ok": 1}']
    c = DeepSeekClient(client=object(), max_retries=1, base_delay=0.0)
    calls = []

    async def fake_chat_raw(messages, **kw):
        calls.append(messages)
        return responses[len(calls) - 1]

    c._chat_raw = fake_chat_raw  # 注入
    result = asyncio.run(c.complete_json([{"role": "user", "content": "x"}]))
    assert result == {"ok": 1}
    assert len(calls) == 2
    assert "JSON" in calls[1][-1]["content"]  # 第二次带了修复反馈
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_llm -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 prompts.py 与 client.py**

```python
# llm/prompts.py
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """读 prompts/{name}.txt;name 不带扩展名。"""
    path = PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt 模板不存在: {path}")
    return path.read_text(encoding="utf-8")
```

```python
# llm/client.py
"""DeepSeek / DashScope-embedding 统一客户端(均为 OpenAI 兼容端点)。

重试策略:指数退避 base_delay * 2**attempt,上限 max_retries 次。
所有模块共享同一 client 实例(单例工厂),不要各自 new。
"""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator

from openai import AsyncOpenAI

from report_agent.config import Settings, get_settings
from report_agent.observability import get_logger

log = get_logger(__name__)

RETRIABLE = ("timeout", "rate_limit", "connection", "server")


class LLMError(Exception):
    """LLM 调用在全部重试后仍失败。上层按 spec §11 降级。"""


def _is_retriable(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(k in s for k in RETRIABLE) or isinstance(exc, (asyncio.TimeoutError,))


@dataclass
class DeepSeekClient:
    client: AsyncOpenAI
    model: str
    max_retries: int = 3
    base_delay: float = 1.0
    timeout: float = 60.0

    async def _chat_raw(self, messages: list[dict], **kw) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, timeout=self.timeout, **kw
        )
        return resp.choices[0].message.content or ""

    async def _run_with_retry(self, fn, *args, **kw):
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args, **kw)
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt >= self.max_retries or not _is_retriable(e):
                    break
                delay = self.base_delay * (2**attempt)
                log.warning("llm_retry", attempt=attempt + 1, delay=delay, error=str(e))
                await asyncio.sleep(delay)
        raise LLMError(f"LLM 调用失败(已重试 {self.max_retries} 次): {last}") from last

    async def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 2048) -> str:
        t0 = time.perf_counter()
        try:
            out = await self._run_with_retry(
                self._chat_raw, messages, temperature=temperature, max_tokens=max_tokens
            )
            log.info("llm_call", model=self.model, purpose="chat", chars=len(out),
                     seconds=round(time.perf_counter() - t0, 2))
            return out
        except LLMError:
            log.error("llm_call_failed", model=self.model, purpose="chat")
            raise

    async def chat_stream(self, messages: list[dict], temperature: float = 0.1,
                          max_tokens: int = 2048) -> AsyncIterator[str]:
        t0 = time.perf_counter()
        stream = await self._run_with_retry(
            self.client.chat.completions.create,
            model=self.model, messages=messages, stream=True,
            temperature=temperature, max_tokens=max_tokens, timeout=self.timeout,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
        log.info("llm_call", model=self.model, purpose="chat_stream",
                 seconds=round(time.perf_counter() - t0, 2))

    async def complete_json(self, messages: list[dict], retry_feedback: bool = True) -> dict:
        """要求 JSON 输出;解析失败时带反馈重试一次。"""
        for i in range(2):
            raw = await self.chat(messages, temperature=0.1)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                if not retry_feedback or i == 1:
                    raise LLMError(f"LLM 未返回合法 JSON: {raw[:200]}")
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "你的输出不是合法 JSON,请重新只输出 JSON。"},
                ]
        raise LLMError("unreachable")


@dataclass
class EmbeddingClient:
    client: AsyncOpenAI
    model: str
    dim: int
    max_retries: int = 3
    base_delay: float = 1.0
    timeout: float = 30.0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.client.embeddings.create(
                    model=self.model, input=texts, timeout=self.timeout
                )
                return [d.embedding for d in resp.data]
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt >= self.max_retries or not _is_retriable(e):
                    break
                await asyncio.sleep(self.base_delay * (2**attempt))
                log.warning("embedding_retry", attempt=attempt + 1, error=str(e))
        raise LLMError(f"Embedding 调用失败: {last}") from last

    async def embed_text(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


@dataclass
class LLMClients:
    chat: DeepSeekClient
    vision: DeepSeekClient
    embedding: EmbeddingClient


@dataclass
class _LazyFactory:
    """单例工厂:所有模块共享同一批 client 实例。"""

    _instance: "LLMClients | None" = None
    _lock: asyncio.Lock | None = None

    async def get(self) -> LLMClients:
        if self._instance is not None:
            return self._instance
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._instance is not None:
                return self._instance
            s = get_settings()
            chat_client = AsyncOpenAI(base_url=s.deepseek_base_url, api_key=s.deepseek_api_key)
            vision_client = AsyncOpenAI(base_url=s.deepseek_base_url, api_key=s.deepseek_api_key)
            emb_client = AsyncOpenAI(base_url=s.embedding_base_url, api_key=s.embedding_api_key)
            self._instance = LLMClients(
                chat=DeepSeekClient(chat_client, model=s.chat_model),
                vision=DeepSeekClient(vision_client, model=s.vision_model),
                embedding=EmbeddingClient(emb_client, model=s.embedding_model, dim=s.embedding_dim),
            )
            return self._instance


llm_factory = _LazyFactory()
```

- [ ] **Step 4: 写 parse_vision.txt(Task 10 用,本任务先落模板文件)**

```
你是体检报告数据提取器。输入是体检报告的一页图片,请提取其中的全部检验指标与报告元数据,只输出 JSON,不要任何解释。

输出结构(严格按此 schema):
{
  "meta": {"institution": "机构名或null", "report_date": "报告日期或null", "sex": "male/female/null", "age": 数值或null},
  "items": [
    {
      "section": "检验分组,如 血常规/肝功能/血脂,无则null",
      "name": "项目名,保持报告中原文",
      "value_text": "原始结果文本,如 6.2 或 阳性(+)",
      "value_num": 数值结果或null,
      "unit": "单位或null",
      "ref_range_text": "参考区间原文,如 3.5-9.5 或 null",
      "abnormal_flag": "报告自带异常标记,如 ↑/↓/H/L 或 null"
    }
  ]
}

要求:
1. 逐行提取,不要合并或省略项目;看不清的数值填 null,不要编造。
2. 阴性/阳性等定性结果放 value_text,value_num 填 null。
3. 报告中没有的项目(如无机构名)填 null,不要推断。
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_llm -v`
Expected: PASS(注意:测试里 `DeepSeekClient(client=FakeClient(), ...)` 是 dataclass 直接构造,`_chat_raw` 未被覆盖路径不依赖真网络)

- [ ] **Step 6: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add DeepSeek/embedding clients with retry, JSON repair and prompt loader"
```

---

### Task 4: Milvus 存储客户端(dense + BM25 稀疏双索引)

**Files:**
- Modify: `backend/src/report_agent/knowledge/milvus_client.py`(替换占位)
- Create: `backend/src/report_agent/knowledge/__init__.py`, `backend/scripts/verify_milvus_bm25.py`, `backend/tests/unit_knowledge/__init__.py`, `backend/tests/unit_knowledge/test_milvus_client.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings`
- Produces(全部为**同步**方法,调用方用 `asyncio.to_thread` 包装):
  - `KnowledgeChunk`(chunk_id, text, entity_type, entity_id, title, section_title, chunk_index, total_chunks, parent_id)
  - `ScoredChunk`(chunk: KnowledgeChunk, score: float, search_method: str — "dense"/"fulltext")
  - `class MilvusStore`: `ensure_collection()`, `upsert_chunks(chunks: list[KnowledgeChunk])`, `delete_entity(entity_type: str, entity_id: str)`, `search_dense(embedding: list[float], top_k: int) -> list[ScoredChunk]`, `search_fulltext(query_text: str, top_k: int) -> list[ScoredChunk]`
  - `check_milvus(uri) -> bool`(供健康检查,保留)
- 本任务同时完成 spec §15 验证项 1(见 Step 7)

- [ ] **Step 1: 写失败测试(monkeypatch 掉 pymilvus.MilvusClient)**

```python
"""MilvusStore 单测:fake MilvusClient 记录调用,验证 schema/索引/搜索参数。"""
from report_agent.knowledge.milvus_client import KnowledgeChunk, MilvusStore


class FakeMilvus:
    def __init__(self, **kw):
        self.collections = set()
        self.rows: list[dict] = []
        self.deleted_filters: list[str] = []
        self.searches: list[dict] = []

    def has_collection(self, name):
        return name in self.collections

    def create_collection(self, collection_name, schema=None, **kw):
        self.collections.add(collection_name)
        self.schema = schema

    def prepare_index_params(self):
        class P:
            def __init__(self):
                self.entries = []
            def add_index(self, **kw):
                self.entries.append(kw)
        return P()

    def create_index(self, name, index_params, **kw):
        self.index_entries = index_params.entries

    def load_collection(self, name):
        pass

    def insert(self, collection_name, data):
        self.rows.extend(data)

    def delete(self, collection_name, filter):
        self.deleted_filters.append(filter)

    def search(self, collection_name, data, anns_field, limit, output_fields, search_params):
        self.searches.append(
            dict(collection_name=collection_name, data=data, anns_field=anns_field,
                 limit=limit, output_fields=output_fields, search_params=search_params)
        )
        hit = {"id": "c1", "distance": 0.9, "entity": {
            "chunk_id": "c1", "text": "空腹血糖相关释义", "entity_type": "indicator",
            "entity_id": "GLU", "title": "空腹血糖", "section_title": "指标说明",
            "chunk_index": 0, "total_chunks": 1, "parent_id": "indicator:GLU",
        }}
        return [[hit]]


def _chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="c1", text="空腹血糖相关释义", entity_type="indicator", entity_id="GLU",
        title="空腹血糖", section_title="指标说明", chunk_index=0, total_chunks=1,
        parent_id="indicator:GLU",
    )


def test_ensure_collection_creates_dense_and_sparse_index(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.ensure_collection()
    fields = {f.name for f in fake.schema.fields}
    assert {"dense_vec", "sparse_vec", "text"} <= fields
    assert fake.index_entries  # 有索引定义
    types = {e["index_type"] for e in fake.index_entries}
    assert {"HNSW", "SPARSE_INVERTED_INDEX"} <= types


def test_ensure_collection_idempotent(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.ensure_collection()  # 已存在 → 不报错不重建
    assert fake.collections == {"kb"}


def test_upsert_and_delete_entity(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.upsert_chunks([_chunk()], dense_embeddings=[[0.1] * 1024])
    assert fake.rows[0]["chunk_id"] == "c1"
    assert "sparse_vec" not in fake.rows[0]  # 稀疏向量由 BM25 Function 自动生成,不手工传
    store.delete_entity("indicator", "GLU")
    assert fake.deleted_filters[0] == "entity_type == 'indicator' and entity_id == 'GLU'"


def test_search_dense_and_fulltext(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    r1 = store.search_dense([0.1] * 1024, top_k=5)
    assert r1[0].search_method == "dense" and r1[0].chunk.entity_id == "GLU"
    r2 = store.search_fulltext("血糖", top_k=5)
    assert r2[0].search_method == "fulltext"
    assert fake.searches[0]["anns_field"] == "dense_vec"
    assert fake.searches[1]["anns_field"] == "sparse_vec"
    assert fake.searches[1]["data"] == ["血糖"]  # 稀疏检索直接传原始查询文本
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_knowledge/test_milvus_client.py -v`
Expected: FAIL(占位模块无 MilvusStore)

- [ ] **Step 3: 实现 milvus_client.py(替换占位)**

```python
"""Milvus 存储客户端:dense(HNSW)+ sparse(BM25/jieba)双索引,单 collection。

所有方法同步;调用方用 asyncio.to_thread 包装。稀疏向量由 text 字段上的
BM25 Function 自动生成,应用层只维护 text(spec §4.3)。
"""
from dataclasses import dataclass

from pymilvus import DataType, Function, FunctionType, MilvusClient

from report_agent.observability import get_logger

log = get_logger(__name__)


@dataclass
class KnowledgeChunk:
    chunk_id: str
    text: str
    entity_type: str  # indicator/condition/cluster/pattern
    entity_id: str    # 指向 KG 节点(code/name),增量删除键
    title: str
    section_title: str | None
    chunk_index: int
    total_chunks: int
    parent_id: str


@dataclass
class ScoredChunk:
    chunk: KnowledgeChunk
    score: float
    search_method: str  # dense / fulltext


_OUTPUT_FIELDS = [
    "chunk_id", "text", "entity_type", "entity_id", "title",
    "section_title", "chunk_index", "total_chunks", "parent_id",
]


def check_milvus(uri: str) -> bool:
    client = MilvusClient(uri=uri, timeout=3)
    client.list_collections()
    return True


class MilvusStore:
    def __init__(self, uri: str, collection: str, embedding_dim: int):
        self._client = MilvusClient(uri=uri)
        self.collection = collection
        self.dim = embedding_dim

    def ensure_collection(self) -> None:
        """幂等创建 collection + 双索引(验证项 1 的核心代码)。"""
        if self._client.has_collection(self.collection):
            return
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(
            "text", DataType.VARCHAR, max_length=65535,
            enable_analyzer=True, analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field("sparse_vec", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("dense_vec", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("entity_type", DataType.VARCHAR, max_length=64)
        schema.add_field("entity_id", DataType.VARCHAR, max_length=128)
        schema.add_field("title", DataType.VARCHAR, max_length=256)
        schema.add_field("section_title", DataType.VARCHAR, max_length=256)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("total_chunks", DataType.INT64)
        schema.add_field("parent_id", DataType.VARCHAR, max_length=128)
        bm25 = Function(
            name="bm25_fn", function_type=FunctionType.BM25,
            input_field_names=["text"], output_field_names="sparse_vec",
        )
        schema.add_function(bm25)
        self._client.create_collection(self.collection, schema=schema)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vec", index_type="HNSW", metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        index_params.add_index(
            field_name="sparse_vec", index_type="SPARSE_INVERTED_INDEX", metric_type="IP",
        )
        self._client.create_index(self.collection, index_params=index_params)
        self._client.load_collection(self.collection)
        log.info("milvus_collection_ready", collection=self.collection)

    def upsert_chunks(self, chunks: list[KnowledgeChunk], dense_embeddings: list[list[float]]) -> None:
        rows = [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "dense_vec": emb,
                "entity_type": c.entity_type,
                "entity_id": c.entity_id,
                "title": c.title,
                "section_title": c.section_title,
                "chunk_index": c.chunk_index,
                "total_chunks": c.total_chunks,
                "parent_id": c.parent_id,
            }
            for c, emb in zip(chunks, dense_embeddings, strict=True)
        ]
        self._client.insert(self.collection, rows)
        log.info("milvus_upsert", count=len(rows))

    def delete_entity(self, entity_type: str, entity_id: str) -> None:
        for v in (entity_type, entity_id):
            assert "'" not in v, f"非法 entity 标识(含单引号): {v}"
        self._client.delete(
            self.collection, filter=f"entity_type == '{entity_type}' and entity_id == '{entity_id}'"
        )

    def search_dense(self, embedding: list[float], top_k: int) -> list[ScoredChunk]:
        res = self._client.search(
            collection_name=self.collection, data=[embedding], anns_field="dense_vec",
            limit=top_k, output_fields=_OUTPUT_FIELDS,
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        )
        return [self._to_scored(hit, "dense") for hit in (res[0] if res else [])]

    def search_fulltext(self, query_text: str, top_k: int) -> list[ScoredChunk]:
        res = self._client.search(
            collection_name=self.collection, data=[query_text], anns_field="sparse_vec",
            limit=top_k, output_fields=_OUTPUT_FIELDS,
            search_params={"params": {"drop_ratio_search": 0.2}},
        )
        return [self._to_scored(hit, "fulltext") for hit in (res[0] if res else [])]

    def query_chunks_by_entity(self, entity_type: str, entity_id: str) -> list[KnowledgeChunk]:
        """评测与调试用:取某实体的全部 chunk。"""
        rows = self._client.query(
            collection_name=self.collection,
            filter=f"entity_type == '{entity_type}' and entity_id == '{entity_id}'",
            output_fields=_OUTPUT_FIELDS, limit=1000,
        )
        return [self._row_to_chunk(r) for r in rows]

    def _to_scored(self, hit: dict, method: str) -> ScoredChunk:
        return ScoredChunk(
            chunk=self._row_to_chunk(hit.get("entity", {})), score=hit.get("distance", 0.0),
            search_method=method,
        )

    @staticmethod
    def _row_to_chunk(r: dict) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id=r["chunk_id"], text=r["text"], entity_type=r["entity_type"],
            entity_id=r["entity_id"], title=r["title"], section_title=r.get("section_title"),
            chunk_index=r["chunk_index"], total_chunks=r["total_chunks"], parent_id=r["parent_id"],
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_knowledge/test_milvus_client.py -v`
Expected: PASS

- [ ] **Step 5: 写 verify_milvus_bm25.py(spec §15 验证项 1)**

```python
"""验证项 1:真实 Milvus 上验证 jieba analyzer + BM25 Function 组合。

运行: uv run python scripts/verify_milvus_bm25.py
通过标准: 查询"血糖"能命中含"空腹血糖"的文本;dense 与 sparse 双路均返回结果。
失败兜底: 若 analyzer 报错或中文检索无效,记录输出,改用 spec §15 的兜底方案
(Postgres FTS + zhparser),并回来改设计文档与 Task 9。
"""
from report_agent.config import get_settings
from report_agent.knowledge.milvus_client import KnowledgeChunk, MilvusStore


def main() -> None:
    s = get_settings()
    store = MilvusStore(uri=s.milvus_uri, collection="verify_bm25", embedding_dim=8)
    try:
        store.ensure_collection()
        chunks = [
            KnowledgeChunk(
                chunk_id="v1", text="空腹血糖是指隔夜禁食8小时后的血糖浓度", entity_type="indicator",
                entity_id="GLU", title="空腹血糖", section_title="指标说明", chunk_index=0,
                total_chunks=1, parent_id="indicator:GLU",
            ),
            KnowledgeChunk(
                chunk_id="v2", text="白细胞计数反映机体感染与炎症状态", entity_type="indicator",
                entity_id="WBC", title="白细胞计数", section_title="指标说明", chunk_index=0,
                total_chunks=1, parent_id="indicator:WBC",
            ),
        ]
        store.upsert_chunks(chunks, dense_embeddings=[[0.1] * 8, [0.2] * 8])
        hits = store.search_fulltext("血糖", top_k=3)
        assert hits and any("血糖" in h.chunk.text for h in hits), "BM25 中文检索未命中"
        print(f"[PASS] fulltext('血糖') -> {[h.chunk.title for h in hits]}")
        hits2 = store.search_dense([0.1] * 8, top_k=3)
        assert hits2, "dense 检索无结果"
        print("[PASS] dense search OK")
    finally:
        # 清理验证集合
        from pymilvus import MilvusClient

        MilvusClient(uri=s.milvus_uri).drop_collection("verify_bm25")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 跑真实验证(docker compose 已启动)**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run python scripts/verify_milvus_bm25.py`
Expected: 两个 [PASS]。若失败,按脚本 docstring 的兜底说明处理并记录结论。

- [ ] **Step 7: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add Milvus store with dense+BM25 sparse dual index (jieba analyzer verified)"
```

---

### Task 5: Neo4j 知识访问层(KGClient)

**Files:**
- Modify: `backend/src/report_agent/knowledge/kg_client.py`(替换占位)
- Create: `backend/tests/unit_knowledge/test_kg_client.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings`
- Produces(同步方法,driver 异常一律**降级为返回空结果 + error 日志**,不抛——spec §11):
  - `class KGClient`: `list_indicators() -> list[IndicatorEntry]`, `range_specs(code: str) -> list[RangeSpec]`, `indicator_context(code: str) -> IndicatorContext`, `all_patterns() -> list[PatternSpec]`, `close()`
  - dataclass:`IndicatorEntry(code, name, aliases, unit, unit_conversions, category, description)`、`RangeSpec(sex, age_min, age_max, low, high, critical_low, critical_high, unit, source_note)`、`ConditionFact(name, description, strength, note)`、`InterventionFact(level, text, timeframe, departments)`、`IndicatorContext(code, name, high_suggests, low_suggests, clusters, interventions, departments)`、`PatternSpec(name, description, criteria)`、`PatternCriterion(indicator_code, direction)`

- [ ] **Step 1: 写失败测试(fake driver)**

```python
from neo4j import ServiceUnavailable

from report_agent.knowledge import kg_client


class FakeRecord(dict):
    def get(self, key, default=None):
        return dict.__getitem__(self, key) if key in self else default


class FakeCursor:
    def __init__(self, records):
        self._records = records

    def data(self, *keys):
        return [tuple(r.get(k) for k in keys) for r in self._records]


class FakeSession:
    def __init__(self, records_by_query):
        self.records_by_query = records_by_query

    def run(self, cypher, **params):
        self.last = (cypher, params)
        return FakeCursor(self.records_by_query.get("all", []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, records_by_query):
        self.records_by_query = records_by_query

    def session(self, **kw):
        return FakeSession(self.records_by_query)


def _client(driver, monkeypatch):
    monkeypatch.setattr(kg_client, "GraphDatabase", type("G", (), {"driver": lambda **kw: driver}))
    return kg_client.KGClient(uri="bolt://x", user="u", password="p", database="neo4j")


def test_list_indicators_maps_fields(monkeypatch):
    rec = FakeRecord(
        {"code": "GLU", "name": "空腹血糖", "aliases": ["血糖", "FBG"], "unit": "mmol/L",
         "unit_conversions": {"mg/dL": 0.0555}, "category": "糖代谢", "description": "desc"}
    )
    c = _client(FakeDriver({"all": [rec]}), monkeypatch)
    entries = c.list_indicators()
    assert len(entries) == 1
    e = entries[0]
    assert e.code == "GLU" and e.aliases == ["血糖", "FBG"]
    assert e.unit_conversions == {"mg/dL": 0.0555}


def test_indicator_context_assembles_directions(monkeypatch):
    recs = [
        FakeRecord({"dir": "HIGH", "cond": "糖尿病风险", "cdesc": "c1", "strength": "strong", "note": "n1"}),
        FakeRecord({"dir": "LOW", "cond": "低血糖", "cdesc": "c2", "strength": "strong", "note": None}),
    ]
    c = _client(FakeDriver({"ctx": recs, "clusters": [], "iv": []}), monkeypatch)
    ctx = c.indicator_context("GLU")
    assert [f.name for f in ctx.high_suggests] == ["糖尿病风险"]
    assert [f.name for f in ctx.low_suggests] == ["低血糖"]


def test_driver_down_degrades_to_empty(monkeypatch):
    class Broken:
        def session(self, **kw):
            raise ServiceUnavailable("down")

    c = _client(Broken(), monkeypatch)
    assert c.list_indicators() == []  # 不抛异常,降级为空
    assert c.indicator_context("GLU").code == "GLU"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_knowledge/test_kg_client.py -v`
Expected: FAIL(占位模块无 KGClient)

- [ ] **Step 3: 实现 kg_client.py(替换占位)**

```python
"""Neo4j 知识访问层(KG 是事实源,spec §4.2)。

约定:driver 不可用/查询失败一律降级为返回空结果并记 error 日志,
上层(RRF/规则)自然吸收空结果——spec §11 降级总览。
"""
from dataclasses import dataclass, field

from neo4j import GraphDatabase

from report_agent.observability import get_logger

log = get_logger(__name__)


@dataclass
class IndicatorEntry:
    code: str
    name: str
    aliases: list[str] = field(default_factory=list)
    unit: str | None = None
    unit_conversions: dict[str, float] = field(default_factory=dict)
    category: str | None = None
    description: str | None = None


@dataclass
class RangeSpec:
    sex: str  # male/female/any
    age_min: float
    age_max: float
    low: float | None
    high: float | None
    critical_low: float | None
    critical_high: float | None
    unit: str | None
    source_note: str | None


@dataclass
class ConditionFact:
    name: str
    description: str | None
    strength: str | None
    note: str | None


@dataclass
class InterventionFact:
    level: str  # lifestyle/recheck/specialist/urgent
    text: str
    timeframe: str | None
    departments: list[str] = field(default_factory=list)


@dataclass
class IndicatorContext:
    code: str
    name: str
    high_suggests: list[ConditionFact] = field(default_factory=list)
    low_suggests: list[ConditionFact] = field(default_factory=list)
    clusters: list[str] = field(default_factory=list)
    interventions: list[InterventionFact] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)


@dataclass
class PatternCriterion:
    indicator_code: str
    direction: str  # high / low


@dataclass
class PatternSpec:
    name: str
    description: str
    criteria: list[PatternCriterion] = field(default_factory=list)


def check_neo4j(uri: str, user: str, password: str) -> bool:
    with GraphDatabase.driver(uri, auth=(user, password)).session() as s:
        s.run("RETURN 1")
    return True


class KGClient:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = database

    def close(self) -> None:
        self._driver.close()

    def _query(self, cypher: str, **params) -> list[dict]:
        try:
            with self._driver.session(database=self._db) as session:
                result = session.run(cypher, **params)
                return [dict(r) for r in result]
        except Exception as e:  # noqa: BLE001 —— KG 降级为事实源不可用
            log.error("neo4j_query_failed", error=str(e), cypher=cypher[:120])
            return []

    # ---------- 指标词典 ----------
    def list_indicators(self) -> list[IndicatorEntry]:
        rows = self._query(
            "MATCH (i:Indicator) RETURN i.code AS code, i.name AS name, i.aliases AS aliases, "
            "i.unit AS unit, i.unit_conversions AS unit_conversions, "
            "i.category AS category, i.description AS description"
        )
        return [
            IndicatorEntry(
                code=r["code"], name=r["name"], aliases=r.get("aliases") or [],
                unit=r.get("unit"), unit_conversions=r.get("unit_conversions") or {},
                category=r.get("category"), description=r.get("description"),
            )
            for r in rows
            if r.get("code")
        ]

    # ---------- 参考区间 ----------
    def range_specs(self, code: str) -> list[RangeSpec]:
        rows = self._query(
            "MATCH (i:Indicator {code: $code})-[:HAS_RANGE]->(r:RangeSpec) RETURN r", code=code
        )
        return [
            RangeSpec(
                sex=r["r"].get("sex", "any"), age_min=r["r"].get("age_min", 0.0),
                age_max=r["r"].get("age_max", 150.0), low=r["r"].get("low"),
                high=r["r"].get("high"), critical_low=r["r"].get("critical_low"),
                critical_high=r["r"].get("critical_high"), unit=r["r"].get("unit"),
                source_note=r["r"].get("source_note"),
            )
            for r in rows
            if r.get("r")
        ]

    # ---------- 指标上下文(疾病提示/簇/建议/科室)----------
    def indicator_context(self, code: str) -> IndicatorContext:
        row = self._query(
            "MATCH (i:Indicator {code: $code}) RETURN i.name AS name LIMIT 1", code=code
        )
        name = row[0]["name"] if row else code
        ctx = IndicatorContext(code=code, name=name)

        for r in self._query(
            "MATCH (i:Indicator {code: $code})-[rel:HIGH_SUGGESTS|LOW_SUGGESTS]->(c:Condition) "
            "RETURN type(rel) AS dir, c.name AS cond, c.description AS cdesc, "
            "rel.strength AS strength, rel.note AS note",
            code=code,
        ):
            fact = ConditionFact(
                name=r["cond"], description=r.get("cdesc"), strength=r.get("strength"),
                note=r.get("note"),
            )
            (ctx.high_suggests if r["dir"] == "HIGH_SUGGESTS" else ctx.low_suggests).append(fact)

        for r in self._query(
            "MATCH (i:Indicator {code: $code})-[:PART_OF]->(cl:IndicatorCluster) "
            "RETURN cl.name AS name",
            code=code,
        ):
            ctx.clusters.append(r["name"])

        iv_rows = self._query(
            "MATCH (i:Indicator {code: $code})-[:DEFAULT_INTERVENTION]->(iv:Intervention) "
            "OPTIONAL MATCH (iv)-[:REFER_TO]->(d:Department) "
            "RETURN iv.level AS level, iv.text AS text, iv.timeframe AS timeframe, "
            "COLLECT(DISTINCT d.name) AS deps",
            code=code,
        )
        for r in iv_rows:
            ctx.interventions.append(
                InterventionFact(
                    level=r["level"], text=r["text"], timeframe=r.get("timeframe"),
                    departments=[d for d in r.get("deps", []) if d],
                )
            )
            ctx.departments.extend(d for d in r.get("deps", []) if d)
        ctx.departments = sorted(set(ctx.departments))
        return ctx

    # ---------- 组合模式 ----------
    def all_patterns(self) -> list[PatternSpec]:
        rows = self._query(
            "MATCH (p:Pattern) OPTIONAL MATCH (p)-[r:REQUIRES]->(i:Indicator) "
            "RETURN p.name AS name, p.description AS description, "
            "COLLECT({code: i.code, direction: r.direction}) AS criteria"
        )
        return [
            PatternSpec(
                name=r["name"], description=r.get("description") or "",
                criteria=[
                    PatternCriterion(indicator_code=c["code"], direction=c["direction"])
                    for c in r.get("criteria", [])
                    if c.get("code")
                ],
            )
            for r in rows
        ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_knowledge/test_kg_client.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add Neo4j KG client with indicator dict, ranges, context and patterns"
```

---

### Task 6: 归一化(词典匹配 + 单位换算 + LLM 别名兜底)

**Files:**
- Create: `backend/src/report_agent/parsing/__init__.py`, `backend/src/report_agent/parsing/schemas.py`, `backend/src/report_agent/parsing/normalizer.py`, `backend/src/report_agent/llm/prompts/alias_map.txt`, `backend/tests/unit_parsing/__init__.py`, `backend/tests/unit_parsing/test_normalizer.py`

**Interfaces:**
- Consumes: Task 3 的 `DeepSeekClient`/`load_prompt`, Task 5 的 `IndicatorEntry`
- Produces:
  - `ReportMeta(institution, report_date, sex, age, source)`(dataclass,parsing/schemas.py)
  - `RawReportItem(section, name, value_text, value_num, unit, ref_range_text, abnormal_flag)`
  - `NormalizedItem(raw_index, section, name, indicator_code, value_text, value_num, unit, raw_value_num, raw_unit, ref_range_text, range_from)`(range_from: "report" 或 None;compare 阶段再填 "kg"。**raw_value_num/raw_unit 保留换算前原值**——报告区间与原值同单位,判定时报告区间对原始值比、KG 区间对标准值比,避免单位错配)
  - `class Normalizer(entries: list[IndicatorEntry])`: `async normalize(raw_items: list[RawReportItem], llm: DeepSeekClient | None = None) -> list[NormalizedItem]`(注意是 **async**——LLM 映射需在事件循环内 await)
  - `match_indicator(name: str, entries) -> str | None`(纯函数,供 Task 15 工具复用)
  - `convert_value(value: float, from_unit: str | None, entry) -> tuple[float, str | None]`(纯函数)
  - `async map_unknown_names(names: list[str], entries, llm) -> dict[str, str | None]`(LLM 批量别名映射;LLMError → 全 None)

- [ ] **Step 1: 写失败测试**

```python
import asyncio

import pytest

from report_agent.llm.client import LLMError
from report_agent.knowledge.kg_client import IndicatorEntry
from report_agent.parsing.normalizer import Normalizer, convert_value, match_indicator
from report_agent.parsing.schemas import RawReportItem

GLU = IndicatorEntry(code="GLU", name="空腹血糖", aliases=["血糖", "FBG"], unit="mmol/L",
                     unit_conversions={"mg/dL": 0.0555})
WBC = IndicatorEntry(code="WBC", name="白细胞计数", aliases=["白细胞", "WBC计数"], unit="10^9/L")
ENTRIES = [GLU, WBC]


def test_match_exact_alias_and_normalized_variants():
    assert match_indicator("空腹血糖", ENTRIES) == "GLU"
    assert match_indicator("血糖", ENTRIES) == "GLU"
    assert match_indicator("白细胞计数", ENTRIES) == "WBC"
    # 全角/空格/大小写变体
    assert match_indicator("ＦＢＧ", ENTRIES) == "GLU"
    assert match_indicator("wbc计数", ENTRIES) == "WBC"
    # 括号变体:"空腹葡萄糖(空腹血糖)" 应命中
    assert match_indicator("空腹葡萄糖(空腹血糖)", ENTRIES) == "GLU"
    assert match_indicator("不存在的指标", ENTRIES) is None


def test_convert_value():
    v, u = convert_value(110.0, "mg/dL", GLU)
    assert abs(v - 6.105) < 0.01 and u == "mmol/L"
    v2, u2 = convert_value(6.1, "mmol/L", GLU)  # 已是标准单位
    assert v2 == 6.1 and u2 == "mmol/L"
    v3, u3 = convert_value(6.1, None, GLU)  # 无单位原样
    assert v3 == 6.1 and u3 is None
    v4, u4 = convert_value(110.0, "mg/dL", WBC)  # 无换算表
    assert v4 == 110.0 and u4 == "mg/dL"


def test_normalize_keeps_raw_value_for_report_range():
    # 数值 110 mg/dL 换算为 6.105 mmol/L,但报告区间 "70-110" 是 mg/dL 单位:
    # 判定必须用 raw_value_num 对报告区间比(见 Task 7),标准值只对 KG 区间比
    n = Normalizer(ENTRIES)
    raws = [RawReportItem(section="糖代谢", name="空腹血糖", value_text="110", value_num=110.0,
                          unit="mg/dL", ref_range_text="70-110", abnormal_flag=None)]
    out = asyncio.run(n.normalize(raws))
    assert abs(out[0].value_num - 6.105) < 0.01
    assert out[0].unit == "mmol/L"
    assert out[0].raw_value_num == 110.0
    assert out[0].raw_unit == "mg/dL"


def test_normalize_marks_unmapped_and_keeps_flow():
    n = Normalizer(ENTRIES)
    raws = [
        RawReportItem(section="糖代谢", name="空腹血糖", value_text="6.2", value_num=6.2,
                      unit="mmol/L", ref_range_text="3.9-6.1", abnormal_flag="↑"),
        RawReportItem(section="其他", name="神秘指标X", value_text="1", value_num=1.0,
                      unit="u", ref_range_text=None, abnormal_flag=None),
    ]
    out = asyncio.run(n.normalize(raws))  # llm=None:不做 LLM 映射
    assert out[0].indicator_code == "GLU"
    assert out[0].range_from == "report"
    assert out[1].indicator_code is None  # unmapped 保留继续
    assert out[1].name == "神秘指标X"


def test_normalize_llm_map_fallback_keeps_unknown():
    class FakeLLM:
        async def complete_json(self, messages, retry_feedback=True):
            raise LLMError("down")

    n = Normalizer(ENTRIES)
    raws = [RawReportItem(section=None, name="神秘指标X", value_text="1", value_num=1.0,
                          unit=None, ref_range_text=None, abnormal_flag=None)]
    out = asyncio.run(n.normalize(raws, llm=FakeLLM()))
    assert out[0].indicator_code is None  # LLM 挂 → 规则兜底 unknown
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_parsing/test_normalizer.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 schemas.py 与 normalizer.py**

```python
# parsing/schemas.py
from dataclasses import dataclass


@dataclass
class ReportMeta:
    institution: str | None = None
    report_date: str | None = None
    sex: str | None = None  # male / female
    age: float | None = None
    source: str = "pdf"  # pdf / photo / manual


@dataclass
class RawReportItem:
    section: str | None
    name: str
    value_text: str | None
    value_num: float | None
    unit: str | None
    ref_range_text: str | None
    abnormal_flag: str | None


@dataclass
class NormalizedItem:
    raw_index: int
    section: str | None
    name: str
    indicator_code: str | None  # None = unmapped
    value_text: str | None
    value_num: float | None     # 标准单位数值(未换算则为原值)
    unit: str | None            # 标准单位(未换算则保留原单位)
    raw_value_num: float | None  # 换算前原值(与报告区间同单位,判定用)
    raw_unit: str | None
    ref_range_text: str | None
    range_from: str | None  # "report" 或 None(compare 阶段补 "kg")
```

```python
# parsing/normalizer.py
"""归一化:词典匹配(规则) → 单位换算(按指标) → 未命中批量 LLM 映射(增强)→ unknown 兜底。"""
import re

from report_agent.knowledge.kg_client import IndicatorEntry
from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.parsing.schemas import NormalizedItem, RawReportItem

log = get_logger(__name__)


def _norm_text(s: str) -> str:
    """全角→半角、去空白、小写。用于匹配索引。"""
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return re.sub(r"\s+", "", "".join(out)).lower()


def _match_keys(entry: IndicatorEntry) -> list[str]:
    """别名产生两个匹配键:原样归一化 + 去掉括号内容的归一化。"""
    keys = []
    for alias in [entry.name, *entry.aliases]:
        k = _norm_text(alias)
        keys.append(k)
        no_paren = re.sub(r"[\(（][^)）]*[\)）]", "", alias)
        if no_paren != alias:
            keys.append(_norm_text(no_paren))
    return keys


def match_indicator(name: str, entries: list[IndicatorEntry]) -> str | None:
    key = _norm_text(name)
    for e in entries:
        if key in _match_keys(e):
            return e.code
    no_paren = re.sub(r"[\(（][^)）]*[\)）]", "", name)
    if no_paren != name:
        key2 = _norm_text(no_paren)
        for e in entries:
            if key2 in _match_keys(e):
                return e.code
    return None


def convert_value(value: float, from_unit: str | None, entry: IndicatorEntry) -> tuple[float, str | None]:
    """value_standard = value_from × factor(seed YAML 的 unit_conversions 语义)。"""
    if from_unit is None or entry.unit is None or from_unit == entry.unit:
        return value, from_unit
    if from_unit in entry.unit_conversions:
        return value * entry.unit_conversions[from_unit], entry.unit
    return value, from_unit  # 无换算表 → 原值原单位,判定用报告区间


async def map_unknown_names(
    names: list[str], entries: list[IndicatorEntry], llm
) -> dict[str, str | None]:
    """未命中项批量一次 LLM 调用尝试映射(候选列表 + JSON 输出);失败全 None。"""
    prompt = load_prompt("alias_map")
    catalog = [{"code": e.code, "name": e.name, "aliases": e.aliases} for e in entries]
    messages = [
        {"role": "user", "content": prompt.format(catalog=repr(catalog), names=repr(names))},
    ]
    try:
        result = await llm.complete_json(messages)
        return {k: (v if v in {e.code for e in entries} else None) for k, v in result.items()}
    except LLMError as e:
        log.warning("alias_map_llm_failed", error=str(e))
        return {n: None for n in names}


class Normalizer:
    def __init__(self, entries: list[IndicatorEntry]):
        self._entries = entries

    async def normalize(
        self, raw_items: list[RawReportItem], llm=None
    ) -> list[NormalizedItem]:
        out: list[NormalizedItem] = []
        unknown_raises: list[int] = []
        for idx, raw in enumerate(raw_items):
            code = match_indicator(raw.name, self._entries)
            item = NormalizedItem(
                raw_index=idx, section=raw.section, name=raw.name, indicator_code=code,
                value_text=raw.value_text, value_num=raw.value_num, unit=raw.unit,
                raw_value_num=raw.value_num, raw_unit=raw.unit,
                ref_range_text=raw.ref_range_text,
                range_from="report" if raw.ref_range_text else None,
            )
            if code is not None:
                entry = next(e for e in self._entries if e.code == code)
                if raw.value_num is not None:
                    item.value_num, item.unit = convert_value(raw.value_num, raw.unit, entry)
            else:
                unknown_raises.append(idx)
            out.append(item)

        if unknown_raises and llm is not None:
            mapping = await map_unknown_names(
                [raw_items[i].name for i in unknown_raises], self._entries, llm
            )
            for i, idx in enumerate(unknown_raises):
                code = mapping.get(raw_items[idx].name)
                if code:
                    out[idx].indicator_code = code
        return out
```

- [ ] **Step 4: 写 alias_map.txt**

```
你是医学检验指标归一化助手。给定一份标准指标目录(catalog)和一份未能匹配的项目名列表(names),
判断每个项目名对应目录中的哪个指标,只输出 JSON:{"项目名": "对应指标code" 或 null}。

catalog: {catalog}
names: {names}

规则:
1. 只在目录里找对应,目录没有的项目输出 null,不要自己编 code。
2. 注意常见别名(如"谷丙转氨酶"→ALT、"丙氨酸氨基转移酶"→ALT)。
3. 项目名包含两个以上指标(如"血糖/糖化")输出 null。
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_parsing/test_normalizer.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add normalizer with dict matching, per-indicator unit conversion and LLM alias fallback"
```

---

### Task 7: 规则判定与组合模式(纯代码核心,单测 100% 覆盖层)

**Files:**
- Create: `backend/src/report_agent/pipeline/__init__.py`, `backend/src/report_agent/pipeline/rule_compare.py`, `backend/tests/unit_rules/__init__.py`, `backend/tests/unit_rules/test_rule_compare.py`

**Interfaces:**
- Consumes: Task 5 的 `RangeSpec`/`PatternSpec`, Task 6 的 `NormalizedItem`, `ReportMeta`
- Produces(全部纯函数,无 IO,禁止 LLM):
  - `class ItemStatus(str, Enum)`: NORMAL/HIGH/LOW/CRITICAL_HIGH/CRITICAL_LOW/UNKNOWN/UNMAPPED(`.value` 为 normal/high/low/critical_high/critical_low/unknown/unmapped)
  - `ItemJudgment(indicator_code, name, value_num, value_text, unit, status, ref_low, ref_high, critical, range_source)`(range_source: "report"/"kg"/None)
  - `parse_range_text(text: str | None) -> tuple[float | None, float | None]`
  - `select_range(specs: list[RangeSpec], sex: str | None, age: float | None) -> RangeSpec | None`
  - `judge_item(item: NormalizedItem, spec: RangeSpec | None) -> ItemJudgment`
  - `judge_all(items: list[NormalizedItem], specs_by_code: dict[str, list[RangeSpec]], meta) -> list[ItemJudgment]`
  - `match_patterns(judgments, patterns) -> list[tuple[PatternSpec, list[ItemJudgment]]]`
  - `is_critical(j: ItemJudgment) -> bool`

- [ ] **Step 1: 写失败测试(12 个用例,覆盖所有分支)**

```python
from report_agent.knowledge.kg_client import PatternCriterion, PatternSpec, RangeSpec
from report_agent.parsing.schemas import NormalizedItem, ReportMeta
from report_agent.pipeline.rule_compare import (
    ItemJudgment,
    ItemStatus,
    is_critical,
    judge_all,
    judge_item,
    match_patterns,
    parse_range_text,
    select_range,
)

GLU_SPEC = RangeSpec(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                     critical_low=2.8, critical_high=22.0, unit="mmol/L", source_note=None)
FEMALE_ONLY = RangeSpec(sex="female", age_min=18, age_max=60, low=4.0, high=6.0,
                        critical_low=None, critical_high=None, unit="mmol/L", source_note=None)
CHILD = RangeSpec(sex="any", age_min=0, age_max=18, low=3.5, high=5.5,
                  critical_low=None, critical_high=None, unit="mmol/L", source_note=None)


def _item(**kw) -> NormalizedItem:
    base = dict(raw_index=0, section=None, name="空腹血糖", indicator_code="GLU",
                value_text=None, value_num=5.0, unit="mmol/L", raw_value_num=5.0,
                raw_unit="mmol/L", ref_range_text=None, range_from=None)
    base.update(kw)
    return NormalizedItem(**base)


# ---------- parse_range_text ----------
def test_parse_range_interval_and_tilde():
    assert parse_range_text("3.5-9.5") == (3.5, 9.5)
    assert parse_range_text("3.5~9.5") == (3.5, 9.5)
    assert parse_range_text("3.5-9.5×10^9/L") == (3.5, 9.5)  # 带单位
    assert parse_range_text("0-10") == (0.0, 10.0)


def test_parse_range_directional():
    assert parse_range_text("<5.0") == (None, 5.0)
    assert parse_range_text(">1.0") == (1.0, None)
    assert parse_range_text("≤2.0") == (None, 2.0)


def test_parse_range_garbage():
    assert parse_range_text("阴性") == (None, None)
    assert parse_range_text(None) == (None, None)
    assert parse_range_text("") == (None, None)


# ---------- select_range ----------
def test_select_range_prefers_sex_exact_and_narrowest():
    specs = [GLU_SPEC, FEMALE_ONLY, CHILD]
    assert select_range(specs, "female", 40) == FEMALE_ONLY  # sex 精确优先
    assert select_range(specs, "male", 40) == GLU_SPEC  # any 兜底
    assert select_range(specs, "male", 10) == CHILD  # 年龄窗口
    assert select_range(specs, "female", 70) == GLU_SPEC  # 女 70 超出 FEMALE_ONLY 窗口 → any
    assert select_range(specs, None, 40) == GLU_SPEC  # sex None → 只用 any
    assert select_range(specs, "male", 200) is None  # 无匹配


def test_select_range_empty_and_age_none():
    assert select_range([], "male", 40) is None
    assert select_range([GLU_SPEC], "male", None) == GLU_SPEC  # age None 跳过年龄过滤


# ---------- judge_item ----------
def test_judge_unmapped():
    j = judge_item(_item(indicator_code=None), GLU_SPEC)
    assert j.status == ItemStatus.UNMAPPED


def test_judge_qualitative():
    assert judge_item(_item(value_num=None, value_text="阴性", raw_value_num=None),
                      GLU_SPEC).status == ItemStatus.NORMAL
    assert judge_item(_item(value_num=None, value_text="阳性", raw_value_num=None),
                      GLU_SPEC).status == ItemStatus.HIGH
    assert judge_item(_item(value_num=None, value_text="弱阳性", raw_value_num=None),
                      GLU_SPEC).status == ItemStatus.UNKNOWN


def test_judge_report_range_first():
    j = judge_item(_item(value_num=110.0, raw_value_num=110.0, raw_unit="mg/dL",
                         ref_range_text="70-110"), GLU_SPEC)
    assert j.status == ItemStatus.NORMAL and j.range_source == "report"


def test_judge_kg_range_fallback_and_bounds():
    j = judge_item(_item(value_num=7.0, raw_value_num=7.0), GLU_SPEC)
    assert j.status == ItemStatus.HIGH and j.range_source == "kg"
    assert (j.ref_low, j.ref_high) == (3.9, 6.1)
    j2 = judge_item(_item(value_num=2.0, raw_value_num=2.0), GLU_SPEC)
    assert j2.status == ItemStatus.LOW


def test_judge_critical_beats_report_range():
    # 危急值永远来自 KG:即使报告区间内正常,KG critical 也触发
    j = judge_item(_item(value_num=23.0, raw_value_num=23.0, ref_range_text="20-25"), GLU_SPEC)
    assert j.status == ItemStatus.CRITICAL_HIGH and j.critical
    j2 = judge_item(_item(value_num=1.5, raw_value_num=1.5, ref_range_text="1.0-25"), GLU_SPEC)
    assert j2.status == ItemStatus.CRITICAL_LOW and j2.critical


def test_judge_no_bounds_unknown():
    spec = RangeSpec(sex="any", age_min=0, age_max=150, low=None, high=None,
                     critical_low=None, critical_high=None, unit=None, source_note=None)
    assert judge_item(_item(value_num=5.0, raw_value_num=5.0), spec).status == ItemStatus.UNKNOWN


# ---------- judge_all / patterns ----------
def test_judge_all_and_match_patterns_with_direction():
    items = [
        _item(indicator_code="TG", value_num=2.5, raw_value_num=2.5, ref_range_text="0.4-1.7"),
        _item(indicator_code="HDL_C", value_num=0.8, raw_value_num=0.8, ref_range_text="1.0-2.0"),
        _item(indicator_code="GLU", value_num=6.5, raw_value_num=6.5, ref_range_text="3.9-6.1"),
    ]
    meta = ReportMeta(sex="male", age=45)
    specs = {"TG": [GLU_SPEC], "HDL_C": [GLU_SPEC], "GLU": [GLU_SPEC]}
    judgments = judge_all(items, specs, meta)
    pattern = PatternSpec(name="代谢综合征", description="d", criteria=[
        PatternCriterion(indicator_code="TG", direction="high"),
        PatternCriterion(indicator_code="HDL_C", direction="low"),
        PatternCriterion(indicator_code="GLU", direction="high"),
    ])
    matched = match_patterns(judgments, [pattern])
    assert len(matched) == 1 and matched[0][0].name == "代谢综合征"


def test_match_patterns_direction_mismatch_and_partial():
    j_high_tg = ItemJudgment(indicator_code="TG", name="甘油三酯", value_num=2.5, value_text=None,
                             unit="mmol/L", status=ItemStatus.HIGH, ref_low=None, ref_high=None,
                             critical=False, range_source="report")
    j_low_hdl = ItemJudgment(indicator_code="HDL_C", name="高密度脂蛋白", value_num=0.8,
                             value_text=None, unit="mmol/L", status=ItemStatus.LOW, ref_low=None,
                             ref_high=None, critical=False, range_source="report")
    j_crit = ItemJudgment(indicator_code="GLU", name="空腹血糖", value_num=25.0, value_text=None,
                          unit="mmol/L", status=ItemStatus.CRITICAL_HIGH, ref_low=None,
                          ref_high=None, critical=True, range_source="kg")
    pattern = PatternSpec(name="P", description="d", criteria=[
        PatternCriterion(indicator_code="TG", direction="low"),  # 方向不对 → 不匹配
    ])
    assert match_patterns([j_high_tg], [pattern]) == []
    # 危急值计入方向(CRITICAL_HIGH 算 high)
    p2 = PatternSpec(name="P2", description="d", criteria=[
        PatternCriterion(indicator_code="TG", direction="high"),
        PatternCriterion(indicator_code="HDL_C", direction="low"),
        PatternCriterion(indicator_code="GLU", direction="high"),
    ])
    assert len(match_patterns([j_high_tg, j_low_hdl, j_crit], [p2])) == 1
    assert not is_critical(j_high_tg) and is_critical(j_crit)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_rules/test_rule_compare.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 rule_compare.py**

```python
"""规则判定层:区间解析/区间选择/数值比对/危急值/组合模式。全部纯代码,禁止 LLM。

单位语义(spec §5.2 与 Task 6 约定):
- 报告区间与 raw_value_num/raw_unit 同单位 → 报告区间对原始值比
- KG 区间与 value_num/unit(标准单位)同单位 → KG 区间对标准值比
- 危急值永远来自 KG(spec §2 决策表)
"""
import re
from dataclasses import dataclass
from enum import Enum

from report_agent.knowledge.kg_client import PatternSpec, RangeSpec
from report_agent.parsing.schemas import NormalizedItem, ReportMeta


class ItemStatus(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"
    UNKNOWN = "unknown"
    UNMAPPED = "unmapped"


@dataclass
class ItemJudgment:
    indicator_code: str | None
    name: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    status: ItemStatus
    ref_low: float | None
    ref_high: float | None
    critical: bool
    range_source: str | None  # report / kg


_QUAL_NEG = {"阴性", "negative", "neg", "(-)", "-", "未见异常"}
_QUAL_POS = {"阳性", "positive", "pos", "(+)", "+"}


def parse_range_text(text: str | None) -> tuple[float | None, float | None]:
    """'3.5-9.5' / '3.5~9.5' / '<5.0' / '>1.0' / '≤2.0',带单位也可;无法解析返回 (None, None)。"""
    if not text or not text.strip():
        return None, None
    t = text.strip()
    if any(m in t for m in "<≤>"):  # 方向式
        m = re.search(r"\d+(?:\.\d+)?", t)
        if not m:
            return None, None
        n = float(m.group())
        return (n, None) if ">" in t else (None, n)
    parts = re.split(r"[-~—～]", t)
    if len(parts) >= 2:
        m1, m2 = re.search(r"\d+(?:\.\d+)?", parts[0]), re.search(r"\d+(?:\.\d+)?", parts[1])
        if m1 and m2:
            a, b = float(m1.group()), float(m2.group())
            return min(a, b), max(a, b)
    return None, None


def select_range(specs: list[RangeSpec], sex: str | None, age: float | None) -> RangeSpec | None:
    """sex 精确匹配优先于 any;age 落入 [age_min, age_max);多条命中取最窄年龄窗。"""
    def in_window(r: RangeSpec) -> bool:
        return age is None or (r.age_min <= age < r.age_max)

    cands = [r for r in specs if (sex is None or r.sex in (sex, "any")) and in_window(r)]
    if not cands:
        return None
    if sex is not None:
        exact = [r for r in cands if r.sex == sex]
        if exact:
            cands = exact
    return min(cands, key=lambda r: r.age_max - r.age_min)


def judge_item(item: NormalizedItem, spec: RangeSpec | None) -> ItemJudgment:
    j = ItemJudgment(
        indicator_code=item.indicator_code, name=item.name, value_num=item.value_num,
        value_text=item.value_text, unit=item.unit, status=ItemStatus.UNKNOWN,
        ref_low=None, ref_high=None, critical=False, range_source=None,
    )
    if item.indicator_code is None:
        j.status = ItemStatus.UNMAPPED
        return j

    if item.value_num is None:  # 定性结果
        vt = (item.value_text or "").strip().lower().replace(" ", "")
        if vt in _QUAL_NEG:
            j.status = ItemStatus.NORMAL
        elif vt in _QUAL_POS:
            j.status = ItemStatus.HIGH  # 阳性记为异常方向,触发解读
        else:
            j.status = ItemStatus.UNKNOWN
        return j

    # 报告区间优先(对原始值比)
    report_low, report_high = parse_range_text(item.ref_range_text)
    bounds: tuple[float | None, float | None] | None = None
    if report_low is not None or report_high is not None:
        bounds = (report_low, report_high)
        j.range_source = "report"
    elif spec is not None and (spec.low is not None or spec.high is not None):
        bounds = (spec.low, spec.high)
        j.range_source = "kg"
    if bounds is None:
        return j

    low, high = bounds
    j.ref_low, j.ref_high = low, high
    v = item.raw_value_num if (j.range_source == "report" and item.raw_value_num is not None) \
        else item.value_num
    if v is None:
        return j

    # 危急值永远来自 KG(对标准值比)
    if spec is not None and item.value_num is not None:
        if spec.critical_high is not None and item.value_num >= spec.critical_high:
            j.status, j.critical = ItemStatus.CRITICAL_HIGH, True
            return j
        if spec.critical_low is not None and item.value_num <= spec.critical_low:
            j.status, j.critical = ItemStatus.CRITICAL_LOW, True
            return j

    if high is not None and v > high:
        j.status = ItemStatus.HIGH
    elif low is not None and v < low:
        j.status = ItemStatus.LOW
    else:
        j.status = ItemStatus.NORMAL
    return j


def judge_all(
    items: list[NormalizedItem], specs_by_code: dict[str, list[RangeSpec]], meta: ReportMeta
) -> list[ItemJudgment]:
    return [
        judge_item(it, select_range(specs_by_code.get(it.indicator_code or "", []), meta.sex, meta.age))
        for it in items
    ]


def match_patterns(
    judgments: list[ItemJudgment], patterns: list[PatternSpec]
) -> list[tuple[PatternSpec, list[ItemJudgment]]]:
    """全部 criteria 满足才算命中;critical 计入方向(CRITICAL_HIGH→high)。"""
    status_high = {ItemStatus.HIGH, ItemStatus.CRITICAL_HIGH}
    status_low = {ItemStatus.LOW, ItemStatus.CRITICAL_LOW}
    matched = []
    for p in patterns:
        hits: list[ItemJudgment] = []
        ok = True
        for c in p.criteria:
            hit = next(
                (
                    j for j in judgments
                    if j.indicator_code == c.indicator_code
                    and (
                        (c.direction == "high" and j.status in status_high)
                        or (c.direction == "low" and j.status in status_low)
                    )
                ),
                None,
            )
            if hit is None:
                ok = False
                break
            hits.append(hit)
        if ok:
            matched.append((p, hits))
    return matched


def is_critical(j: ItemJudgment) -> bool:
    return j.critical
```

- [ ] **Step 4: 运行测试确认通过 + 覆盖率检查(单测 100% 要求)**

Run:
```bash
cd /d/DeskTop/agent/report-agent/backend
uv run pytest tests/unit_rules/test_rule_compare.py -v --cov=report_agent.pipeline.rule_compare --cov-report=term-missing
```
Expected: PASS,`report_agent.pipeline.rule_compare` 覆盖率 100%(缺分支则补测试,直到 100%)

- [ ] **Step 5: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add pure-code rule engine for range judging, critical values and patterns (100% covered)"
```

---

### Task 8: 种子知识数据链(起草 → 校对 → 增量入库)

**Files:**
- Create: `backend/src/report_agent/knowledge/seed_schemas.py`, `backend/src/report_agent/knowledge/doc_builder.py`, `backend/src/report_agent/llm/prompts/seed_draft.txt`, `backend/scripts/seed_draft.py`, `backend/scripts/seed_import.py`, `backend/knowledge_seeds/indicator_list.yaml`, `backend/knowledge_seeds/indicators/GLU.yaml`, `backend/knowledge_seeds/patterns/metabolic_syndrome.yaml`, `backend/tests/unit_knowledge/test_seed_schemas.py`, `backend/tests/unit_knowledge/test_doc_builder.py`

**Interfaces:**
- Consumes: Task 3 的 `llm_factory`/`load_prompt`, Task 4 的 `MilvusStore`, Task 5 的 `KGClient`
- Produces:
  - `seed_schemas`: `IndicatorSeed` / `RangeYaml` / `SuggestYaml` / `InterventionYaml` / `PatternSeed`(pydantic 模型,YAML 校验)
  - `doc_builder`: `DocDraft(entity_type, entity_id, title, text)`、`build_indicator_doc(seed) -> DocDraft`、`build_condition_docs(seeds) -> list[DocDraft]`、`chunk_documents(docs, settings) -> list[KnowledgeChunk]`
  - `scripts/seed_draft.py --all --force`(LLM 起草,断点续传)
  - `scripts/seed_import.py --all | --entity CODE`(YAML→Neo4j MERGE→文档→切块→embedding→Milvus 增量)
  - `upsert_indicator(session, seed)`、`upsert_pattern(session, seed)`(Neo4j 写入函数)

- [ ] **Step 1: 写失败测试(校验模型 + 分块)**

```python
import pytest
from pydantic import ValidationError

from report_agent.config import Settings
from report_agent.knowledge.doc_builder import DocDraft, build_indicator_doc, chunk_documents
from report_agent.knowledge.seed_schemas import IndicatorSeed


def test_indicator_seed_valid():
    seed = IndicatorSeed.model_validate({
        "code": "GLU", "name": "空腹血糖", "aliases": ["血糖"],
        "unit": "mmol/L", "unit_conversions": {"mg/dL": 0.0555},
        "ranges": [{"sex": "any", "age_min": 18, "age_max": 100,
                    "low": 3.9, "high": 6.1, "critical_low": 2.8, "critical_high": 22.0}],
        "high_suggests": [{"condition": "糖尿病风险", "strength": "strong"}],
        "interventions": [{"level": "recheck", "text": "复查空腹血糖", "timeframe": "2-4周",
                           "departments": ["内分泌科"]}],
        "narrative": "空腹血糖反映糖代谢。",
    })
    assert seed.unit_conversions == {"mg/dL": 0.0555}


def test_indicator_seed_invalid_level():
    with pytest.raises(ValidationError):
        IndicatorSeed.model_validate({
            "code": "X", "name": "x", "interventions": [{"level": "nonsense", "text": "t"}],
        })


def _settings() -> Settings:
    return Settings(short_doc_max_chars=1200, chunk_size=500, chunk_overlap=50)


def test_short_doc_single_chunk():
    seed = IndicatorSeed(code="GLU", name="空腹血糖", narrative="短短的解释")
    doc = build_indicator_doc(seed)
    chunks = chunk_documents([doc], _settings())
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "indicator:GLU:0"
    assert chunks[0].parent_id == "indicator:GLU"
    assert chunks[0].entity_type == "indicator" and chunks[0].entity_id == "GLU"


def test_long_doc_split_by_headings_with_context_header():
    text = "# 空腹血糖\n\n## 指标说明\n" + "糖代谢指标。" * 300 + "\n\n## 参考区间\n成年 3.9-6.1 mmol/L\n"
    doc = DocDraft(entity_type="indicator", entity_id="GLU", title="空腹血糖", text=text)
    chunks = chunk_documents([doc], _settings())
    assert len(chunks) > 1
    # 长文档的每个 chunk 都有上下文头(文档标题 + 章节标题)
    for c in chunks:
        assert "文档标题: 空腹血糖" in c.text
        assert "章节:" in c.text
    # chunk_index/total_chunks 连续
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.total_chunks == len(chunks) for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_knowledge/test_seed_schemas.py tests/unit_knowledge/test_doc_builder.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 seed_schemas.py 与 doc_builder.py**

```python
# knowledge/seed_schemas.py
from typing import Literal

from pydantic import BaseModel, Field


class RangeYaml(BaseModel):
    sex: Literal["male", "female", "any"] = "any"
    age_min: float = 0
    age_max: float = 150
    low: float | None = None
    high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None
    unit: str | None = None
    source_note: str | None = None


class SuggestYaml(BaseModel):
    condition: str
    strength: str = "medium"  # strong / medium / weak
    note: str | None = None
    description: str | None = None


class InterventionYaml(BaseModel):
    level: Literal["lifestyle", "recheck", "specialist", "urgent"]
    text: str
    timeframe: str | None = None
    departments: list[str] = Field(default_factory=list)


class IndicatorSeed(BaseModel):
    code: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    unit: str | None = None
    unit_conversions: dict[str, float] = Field(default_factory=dict)  # {from_unit: 乘数}
    category: str | None = None
    description: str | None = None
    ranges: list[RangeYaml] = Field(default_factory=list)
    high_suggests: list[SuggestYaml] = Field(default_factory=list)
    low_suggests: list[SuggestYaml] = Field(default_factory=list)
    clusters: list[str] = Field(default_factory=list)
    interventions: list[InterventionYaml] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    narrative: str = ""


class PatternSeed(BaseModel):
    name: str
    description: str = ""
    criteria: list[dict] = Field(default_factory=list)  # [{indicator_code, direction}]
```

```python
# knowledge/doc_builder.py
"""KG 事实 → 释义文档 → 分块(派生检索层,spec §4.3)。

分块三级策略(自 C9 移植 + 域适配):
1. 短文档(≤ short_doc_max_chars)整篇单块——KG 派生文档的常态
2. 长文档按 '## ' 二级标题分节,节内超长再滑窗
3. 多块文档的每个 chunk 前置上下文头(文档标题 + 章节标题),保证脱离全文可读
不做父文档回填(spec §4.3 已确认移除)。
"""
import re
from dataclasses import dataclass

from report_agent.config import Settings
from report_agent.knowledge.milvus_client import KnowledgeChunk
from report_agent.knowledge.seed_schemas import IndicatorSeed, SuggestYaml


@dataclass
class DocDraft:
    entity_type: str
    entity_id: str
    title: str
    text: str


def _suggests_lines(items: list[SuggestYaml], label: str) -> str:
    if not items:
        return ""
    lines = [f"## {label}", ""]
    for s in items:
        strength = {"strong": "强", "medium": "中", "weak": "弱"}.get(s.strength, s.strength)
        lines.append(f"- {s.condition}(强度:{strength})")
        if s.note:
            lines.append(f"  - {s.note}")
    return "\n".join(lines) + "\n\n"


def build_indicator_doc(seed: IndicatorSeed) -> DocDraft:
    parts = [f"# {seed.name}"]
    if seed.aliases:
        parts.append(f"别名: {'、'.join(seed.aliases)}")
    parts.append("")
    parts.append("## 指标说明")
    parts.append("")
    if seed.description:
        parts.append(seed.description)
        parts.append("")
    if seed.narrative:
        parts.append(seed.narrative)
        parts.append("")
    if seed.ranges:
        parts.append("## 参考区间")
        parts.append("")
        for r in seed.ranges:
            sex = {"male": "男", "female": "女", "any": "通用"}[r.sex]
            lo = r.low if r.low is not None else "-∞"
            hi = r.high if r.high is not None else "+∞"
            parts.append(f"- {sex} {r.age_min:.0f}-{r.age_max:.0f} 岁: {lo} ~ {hi} {r.unit or seed.unit or ''}".rstrip())
            if r.critical_low is not None or r.critical_high is not None:
                parts.append(f"  危急值: <{r.critical_low} 或 >{r.critical_high}".replace("None", "?"))
        parts.append("")
    parts.append(_suggests_lines(seed.high_suggests, "升高提示"))
    parts.append(_suggests_lines(seed.low_suggests, "降低提示"))
    if seed.interventions:
        parts.append("## 干预建议")
        parts.append("")
        for iv in seed.interventions:
            tf = f",时间窗: {iv.timeframe}" if iv.timeframe else ""
            deps = f",科室: {'、'.join(iv.departments)}" if iv.departments else ""
            parts.append(f"- [{iv.level}] {iv.text}{tf}{deps}")
    return DocDraft(
        entity_type="indicator", entity_id=seed.code, title=seed.name, text="\n".join(parts) + "\n",
    )


def build_condition_docs(seeds: list[IndicatorSeed]) -> list[DocDraft]:
    """按疾病/风险方向聚合所有种子里的提示,生成 Condition 释义文档。"""
    by_cond: dict[str, dict] = {}
    for seed in seeds:
        for s in seed.high_suggests + seed.low_suggests:
            c = by_cond.setdefault(
                s.condition, {"description": s.description, "mentions": []}
            )
            for direction, lst in (("升高", seed.high_suggests), ("降低", seed.low_suggests)):
                if s in lst:
                    c["mentions"].append(f"{seed.name}({seed.code}) {direction}提示")
    docs = []
    for name, c in by_cond.items():
        body = [f"# {name}", ""]
        if c["description"]:
            body += [c["description"], ""]
        body += ["## 关联指标", ""] + [f"- {m}" for m in sorted(set(c["mentions"]))]
        docs.append(DocDraft(entity_type="condition", entity_id=name, title=name,
                             text="\n".join(body) + "\n"))
    return docs


def _sliding(text: str, size: int, overlap: int) -> list[str]:
    parts, start = [], 0
    while start < len(text):
        parts.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return parts


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """按 '\n## ' 分节,返回 [(章节标题, 正文)];首段(无标题)标题为 None。"""
    parts = re.split(r"\n(?=## )", text)
    out = []
    for part in parts:
        m = re.match(r"^## (.+)\n", part)
        if m:
            out.append((m.group(1).strip(), part[m.end():]))
        else:
            out.append((None, part))
    return out


def chunk_documents(docs: list[DocDraft], s: Settings) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for d in docs:
        if len(d.text) <= s.short_doc_max_chars:
            chunks.append(KnowledgeChunk(
                chunk_id=f"{d.entity_type}:{d.entity_id}:0", text=d.text,
                entity_type=d.entity_type, entity_id=d.entity_id, title=d.title,
                section_title=None, chunk_index=0, total_chunks=1,
                parent_id=f"{d.entity_type}:{d.entity_id}",
            ))
            continue
        sections = _split_sections(d.text)
        parts: list[tuple[str | None, str]] = []
        for sec_title, body in sections:
            if len(body) <= s.chunk_size:
                parts.append((sec_title, body))
            else:
                for piece in _sliding(body, s.chunk_size, s.chunk_overlap):
                    parts.append((sec_title, piece))
        for i, (sec_title, body) in enumerate(parts):
            header = f"文档标题: {d.title}\n章节: {sec_title or '概述'}\n\n"
            chunks.append(KnowledgeChunk(
                chunk_id=f"{d.entity_type}:{d.entity_id}:{i}", text=header + body,
                entity_type=d.entity_type, entity_id=d.entity_id, title=d.title,
                section_title=sec_title, chunk_index=i, total_chunks=len(parts),
                parent_id=f"{d.entity_type}:{d.entity_id}",
            ))
    return chunks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_knowledge/test_seed_schemas.py tests/unit_knowledge/test_doc_builder.py -v`
Expected: PASS

- [ ] **Step 5: 写 indicator_list.yaml(起草清单,人工维护;示例 24 项,执行时补充到 50~100 项)**

```yaml
# 高频体检指标起草清单。code 为指标英文缩写;起草后人工校对再入库。
- code: GLU
  name: 空腹血糖
  category: 糖代谢
  aliases: [血糖, FBG]
- code: HBA1C
  name: 糖化血红蛋白
  category: 糖代谢
  aliases: [糖化, HbA1c]
- code: TC
  name: 总胆固醇
  category: 血脂
  aliases: [胆固醇, CHOL]
- code: TG
  name: 甘油三酯
  category: 血脂
  aliases: [三酰甘油]
- code: HDL_C
  name: 高密度脂蛋白胆固醇
  category: 血脂
  aliases: [高密度脂蛋白, HDL]
- code: LDL_C
  name: 低密度脂蛋白胆固醇
  category: 血脂
  aliases: [低密度脂蛋白, LDL]
- code: ALT
  name: 丙氨酸氨基转移酶
  category: 肝功能
  aliases: [谷丙转氨酶]
- code: AST
  name: 天冬氨酸氨基转移酶
  category: 肝功能
  aliases: [谷草转氨酶]
- code: GGT
  name: γ-谷氨酰转移酶
  category: 肝功能
  aliases: [谷氨酰转肽酶]
- code: ALP
  name: 碱性磷酸酶
  category: 肝功能
  aliases: []
- code: TBIL
  name: 总胆红素
  category: 肝功能
  aliases: [胆红素]
- code: ALB
  name: 白蛋白
  category: 肝功能
  aliases: [清蛋白]
- code: CREA
  name: 肌酐
  category: 肾功能
  aliases: [血肌酐, Cr]
- code: UREA
  name: 尿素
  category: 肾功能
  aliases: [尿素氮, BUN]
- code: UA
  name: 尿酸
  category: 肾功能
  aliases: [血尿酸]
- code: WBC
  name: 白细胞计数
  category: 血常规
  aliases: [白细胞, WBC计数]
- code: RBC
  name: 红细胞计数
  category: 血常规
  aliases: [红细胞]
- code: HGB
  name: 血红蛋白
  category: 血常规
  aliases: [血红蛋白浓度, Hb]
- code: PLT
  name: 血小板计数
  category: 血常规
  aliases: [血小板]
- code: NEUT
  name: 中性粒细胞百分比
  category: 血常规
  aliases: [中性粒细胞比率]
- code: TSH
  name: 促甲状腺激素
  category: 甲状腺
  aliases: []
- code: FT3
  name: 游离三碘甲状腺原氨酸
  category: 甲状腺
  aliases: []
- code: FT4
  name: 游离甲状腺素
  category: 甲状腺
  aliases: []
- code: UP
  name: 尿蛋白
  category: 尿常规
  aliases: [尿蛋白定性, PRO]
```

- [ ] **Step 6: 写样例种子 GLU.yaml(人工校对版,few-shot 也是起草 prompt 的范例)**

```yaml
code: GLU
name: 空腹血糖
aliases: [血糖, FBG, 空腹葡萄糖]
unit: mmol/L
unit_conversions:
  mg/dL: 0.0555
category: 糖代谢
description: 空腹血糖指隔夜禁食 8 小时以上测定的血浆葡萄糖浓度,是糖代谢状态的基础筛查指标。
ranges:
  - sex: any
    age_min: 18
    age_max: 100
    low: 3.9
    high: 6.1
    critical_low: 2.8
    critical_high: 22.0
    source_note: 成人通用参考区间,以报告所示区间为准
high_suggests:
  - condition: 糖尿病风险
    strength: strong
    note: 6.1~7.0 mmol/L 为空腹血糖受损(糖尿病前期),≥7.0 mmol/L 需结合糖化血红蛋白与临床进一步评估
  - condition: 应激性高血糖
    strength: weak
    note: 感染、手术、急性疾病等应激状态可致一过性升高
low_suggests:
  - condition: 低血糖
    strength: strong
    note: <3.9 mmol/L 为低血糖,<2.8 mmol/L 为危急值,需警惕
clusters: [糖代谢]
interventions:
  - level: lifestyle
    text: 控制精制碳水与含糖饮料摄入,规律运动,控制体重
  - level: recheck
    text: 复查空腹血糖,必要时加查糖化血红蛋白与口服葡萄糖耐量试验(OGTT)
    timeframe: 2-4周
    departments: [内分泌科]
narrative: |
  空腹血糖是诊断糖尿病与糖尿病前期最常用的指标之一。单纯一次轻度升高可能与饮食、
  应激、测量时间等因素有关,需结合糖化血红蛋白、OGTT 及临床症状综合判断。
  本产品不构成医学诊断,异常结果请咨询医生。
```

- [ ] **Step 7: 写样例组合模式 metabolic_syndrome.yaml**

```yaml
name: 代谢综合征倾向
description: 甘油三酯升高、HDL-C 降低与血糖升高同时出现,提示代谢综合征风险,建议综合评估。
criteria:
  - {indicator_code: TG, direction: high}
  - {indicator_code: HDL_C, direction: low}
  - {indicator_code: GLU, direction: high}
```

- [ ] **Step 8: 写 seed_draft.txt 与 seed_draft.py**

```
你是医学检验知识整理助手。请为体检指标"{{name}}({{code}},分类: {{category}})"起草知识库条目,
用于体检报告解读系统。参考区间、危急值、疾病提示必须基于公开医学共识,宁可保守。

输出严格 JSON,结构:
{
  "code": "{{code}}",
  "name": "指标标准中文名",
  "aliases": ["常见别名"],
  "unit": "标准单位,如 mmol/L",
  "unit_conversions": {"其他单位": "换算乘数"},
  "category": "分类",
  "description": "一句话说明该指标是什么",
  "ranges": [{"sex": "any|male|female", "age_min": 0, "age_max": 150, "low": null, "high": null,
              "critical_low": null, "critical_high": null, "source_note": "来源说明"}],
  "high_suggests": [{"condition": "疾病或风险方向(非诊断用语)", "strength": "strong|medium|weak",
                     "note": "解释", "description": "该疾病一句话说明"}],
  "low_suggests": [同 high_suggests],
  "clusters": ["所属指标簇"],
  "interventions": [{"level": "lifestyle|recheck|specialist|urgent", "text": "建议内容",
                     "timeframe": "时间窗或null", "departments": ["建议科室"]}],
  "departments": ["总体建议科室"],
  "narrative": "100~200字临床释义,说明指标意义、常见波动原因;结尾附'本内容不构成医学诊断'"
}

规则:
1. 危急值阈值只填确有共识的;不确定就填 null。
2. 疾病提示用"XX风险/XX可能"等表述,禁止"确诊/诊断"用语。
3. 禁止任何用药与剂量建议。
4. 参考区间优先给成人通用值;不同性别/年龄段确有差异才分开。
5. 干预建议按分级给:生活方式(lifestyle)/定期复查(recheck)/专科就诊(specialist)/尽快就医(urgent)。

范例(空腹血糖):
{
  "code": "GLU", "name": "空腹血糖", "aliases": ["血糖", "FBG"], "unit": "mmol/L",
  "unit_conversions": {"mg/dL": 0.0555}, "category": "糖代谢",
  "ranges": [{"sex": "any", "age_min": 18, "age_max": 100, "low": 3.9, "high": 6.1,
              "critical_low": 2.8, "critical_high": 22.0}],
  "high_suggests": [{"condition": "糖尿病风险", "strength": "strong",
                     "note": "6.1~7.0 为空腹血糖受损,≥7.0 需进一步评估"}],
  "low_suggests": [{"condition": "低血糖", "strength": "strong", "note": "<3.9 为低血糖"}],
  "clusters": ["糖代谢"],
  "interventions": [
    {"level": "lifestyle", "text": "控制精制碳水摄入,规律运动", "timeframe": null, "departments": []},
    {"level": "recheck", "text": "复查空腹血糖,必要时加查糖化血红蛋白", "timeframe": "2-4周",
     "departments": ["内分泌科"]}
  ],
  "departments": ["内分泌科"],
  "narrative": "空腹血糖是糖代谢基础筛查指标。本内容不构成医学诊断。"
}
```

```python
# scripts/seed_draft.py
"""LLM 起草种子 YAML(断点续传)。运行:
  uv run python scripts/seed_draft.py --all          # 起草清单中缺失的全部
  uv run python scripts/seed_draft.py --force GLU    # 强制重起草单个
产物: knowledge_seeds/indicators/{CODE}.yaml(人工校对后由 seed_import 入库)。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = ROOT / "knowledge_seeds" / "indicators"
PROGRESS = ROOT / "knowledge_seeds" / ".draft_progress.json"


async def draft_one(name: str, code: str, category: str, aliases: list[str]) -> dict:
    from report_agent.llm.client import llm_factory
    from report_agent.llm.prompts import load_prompt

    clients = await llm_factory.get()
    prompt = (
        load_prompt("seed_draft")
        .replace("{{name}}", name)
        .replace("{{code}}", code)
        .replace("{{category}}", category or "")
        .replace("{{aliases}}", json.dumps(aliases, ensure_ascii=False))
    )
    data = await clients.chat.complete_json([{"role": "user", "content": prompt}])
    if data.get("code") != code:
        data["code"] = code
    return data


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("codes", nargs="*")
    args = parser.parse_args()

    catalog = yaml.safe_load((ROOT / "knowledge_seeds" / "indicator_list.yaml").read_text("utf-8"))
    progress = json.loads(PROGRESS.read_text("utf-8")) if PROGRESS.exists() else {}
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    for item in catalog:
        code = item["code"]
        target = SEEDS_DIR / f"{code}.yaml"
        if not args.all and code not in args.codes:
            continue
        if target.exists() and not args.force:
            print(f"[skip] {code} 已存在,人工校对后重跑 --force 覆盖")
            continue
        data = await draft_one(item["name"], code, item.get("category"), item.get("aliases") or [])
        target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
        progress[code] = "drafted"
        PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=2), "utf-8")
        print(f"[done] {code} -> {target.name}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 9: 写 seed_import.py(增量入库核心,验收标准 5 的演示路径)**

```python
# scripts/seed_import.py
"""种子 YAML → Neo4j(MERGE 实体级)→ 派生文档 → 切块 → embedding → Milvus(按 entity 增量)。

运行:
  uv run python scripts/seed_import.py --all              # 全量(幂等,重复跑安全)
  uv run python scripts/seed_import.py --entity GLU       # 单个指标增量(验收演示)
先决条件: docker compose 服务已启动,指标 YAML 已经人工校对。
"""
import argparse
import asyncio
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = ROOT / "knowledge_seeds" / "indicators"
PATTERNS_DIR = ROOT / "knowledge_seeds" / "patterns"


def upsert_indicator(session, seed) -> None:
    session.run(
        "MERGE (i:Indicator {code: $code}) SET i.name=$name, i.aliases=$aliases, i.unit=$unit, "
        "i.unit_conversions=$conv, i.category=$category, i.description=$description",
        code=seed.code, name=seed.name, aliases=seed.aliases, unit=seed.unit,
        conv=seed.unit_conversions, category=seed.category, description=seed.description,
    )
    session.run(
        "MATCH (i:Indicator {code: $code})-[r:HAS_RANGE]->(:RangeSpec) DELETE r", code=seed.code
    )
    for r in seed.ranges:
        session.run(
            "MATCH (i:Indicator {code: $code}) CREATE (i)-[:HAS_RANGE]->(:RangeSpec {"
            "sex: $sex, age_min: $amin, age_max: $amax, low: $low, high: $high, "
            "critical_low: $clo, critical_high: $chi, unit: $u, source_note: $note})",
            code=seed.code, sex=r.sex, amin=r.age_min, amax=r.age_max, low=r.low,
            high=r.high, clo=r.critical_low, chi=r.critical_high, u=r.unit, note=r.source_note,
        )
    for direction, rel in (("high", "HIGH_SUGGESTS"), ("low", "LOW_SUGGESTS")):
        for s in getattr(seed, f"{direction}_suggests"):
            session.run(
                f"MERGE (c:Condition {{name: $cond}}) ON CREATE SET c.description=$desc "
                f"WITH c MATCH (i:Indicator {{code: $code}}) "
                f"MERGE (i)-[r:{rel}]->(c) SET r.strength=$strength, r.note=$note",
                cond=s.condition, desc=s.description, code=seed.code, strength=s.strength, note=s.note,
            )
    for cl in seed.clusters:
        session.run(
            "MERGE (cl:IndicatorCluster {name: $name}) WITH cl "
            "MATCH (i:Indicator {code: $code}) MERGE (i)-[:PART_OF]->(cl)",
            name=cl, code=seed.code,
        )
    for iv in seed.interventions:
        key = f"{seed.code}|{iv.level}|{iv.text}"
        session.run(
            "MERGE (iv:Intervention {key: $key}) SET iv.level=$level, iv.text=$text, "
            "iv.timeframe=$tf WITH iv MATCH (i:Indicator {code: $code}) "
            "MERGE (i)-[:DEFAULT_INTERVENTION]->(iv)",
            key=key, level=iv.level, text=iv.text, tf=iv.timeframe, code=seed.code,
        )
        for d in iv.departments:
            session.run(
                "MERGE (dep:Department {name: $dep}) WITH dep "
                "MATCH (iv:Intervention {key: $key}) MERGE (iv)-[:REFER_TO]->(dep)",
                dep=d, key=key,
            )
    if seed.departments:
        _attach_departments(session, seed)


def _attach_departments(session, seed) -> None:
    """指标级 departments: 并入首个 specialist 级建议;没有则创建合成 specialist 建议。"""
    specialist = next((iv for iv in seed.interventions if iv.level == "specialist"), None)
    key = (
        f"{seed.code}|specialist|{specialist.text}"
        if specialist
        else f"{seed.code}|specialist|建议专科门诊咨询"
    )
    if not specialist:
        session.run(
            "MERGE (iv:Intervention {key: $key}) SET iv.level='specialist', iv.text='建议专科门诊咨询' "
            "WITH iv MATCH (i:Indicator {code: $code}) MERGE (i)-[:DEFAULT_INTERVENTION]->(iv)",
            key=key, code=seed.code,
        )
    for d in seed.departments:
        session.run(
            "MERGE (dep:Department {name: $dep}) WITH dep "
            "MATCH (iv:Intervention {key: $key}) MERGE (iv)-[:REFER_TO]->(dep)",
            dep=d, key=key,
        )


def upsert_pattern(session, seed) -> None:
    session.run(
        "MERGE (p:Pattern {name: $name}) SET p.description=$desc", name=seed.name, desc=seed.description
    )
    session.run(
        "MATCH (p:Pattern {name: $name})-[r:REQUIRES]->(:Indicator) DELETE r", name=seed.name
    )
    for c in seed.criteria:
        session.run(
            "MATCH (p:Pattern {name: $name}), (i:Indicator {code: $code}) "
            "MERGE (p)-[:REQUIRES {direction: $dir}]->(i)",
            name=seed.name, code=c["indicator_code"], dir=c["direction"],
        )


async def import_entity(session, store, llms, settings, seed) -> None:
    from report_agent.knowledge.doc_builder import build_indicator_doc, chunk_documents

    await asyncio.to_thread(upsert_indicator, session, seed)
    doc = build_indicator_doc(seed)
    chunks = chunk_documents([doc], settings)
    texts = [c.text for c in chunks]
    embs = []
    for i in range(0, len(texts), 100):
        embs.extend(await llms.embedding.embed_texts(texts[i:i + 100]))
    await asyncio.to_thread(store.delete_entity, "indicator", seed.code)
    await asyncio.to_thread(store.upsert_chunks, chunks, embs)
    print(f"[done] indicator {seed.code}: {len(chunks)} chunks")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--entity", nargs="*")
    args = parser.parse_args()

    from neo4j import GraphDatabase

    from report_agent.config import get_settings
    from report_agent.knowledge.doc_builder import build_condition_docs, chunk_documents
    from report_agent.knowledge.milvus_client import MilvusStore
    from report_agent.knowledge.seed_schemas import IndicatorSeed, PatternSeed
    from report_agent.llm.client import llm_factory

    s = get_settings()
    llms = await llm_factory.get()
    store = MilvusStore(uri=s.milvus_uri, collection=s.milvus_collection, embedding_dim=s.embedding_dim)
    await asyncio.to_thread(store.ensure_collection)

    driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
    seeds: list[IndicatorSeed] = []
    try:
        for path in sorted(SEEDS_DIR.glob("*.yaml")):
            code = path.stem
            if not args.all and code not in (args.entity or []):
                continue
            seed = IndicatorSeed.model_validate(yaml.safe_load(path.read_text("utf-8")))
            with driver.session(database=s.neo4j_database) as session:
                await import_entity(session, store, llms, s, seed)
            seeds.append(seed)

        for path in sorted(PATTERNS_DIR.glob("*.yaml")):
            pseed = PatternSeed.model_validate(yaml.safe_load(path.read_text("utf-8")))
            with driver.session(database=s.neo4j_database) as session:
                await asyncio.to_thread(upsert_pattern, session, pseed)
            print(f"[done] pattern {pseed.name}")

        if args.all:
            cond_docs = build_condition_docs(seeds)
            cond_chunks = chunk_documents(cond_docs, s)
            texts = [c.text for c in cond_chunks]
            embs = []
            for i in range(0, len(texts), 100):
                embs.extend(await llms.embedding.embed_texts(texts[i:i + 100]))
            await asyncio.to_thread(store.upsert_chunks, cond_chunks, embs)
            print(f"[done] condition docs: {len(cond_chunks)} chunks")
    finally:
        driver.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 10: 运行入库 + 验证(验收标准 5:增量更新)**

```bash
cd /d/DeskTop/agent/report-agent/backend
uv run python scripts/seed_import.py --entity GLU
uv run python scripts/seed_import.py --entity GLU   # 再跑一次:幂等,不报错
uv run python - <<'EOF'
from neo4j import GraphDatabase
import yaml
s = yaml.safe_load(open("knowledge_seeds/indicators/GLU.yaml", encoding="utf-8"))
d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "report-agent-dev"))
with d.session() as sess:
    n = sess.run("MATCH (i:Indicator {code:'GLU'}) RETURN i.name").single()
    r = sess.run("MATCH (:Indicator {code:'GLU'})-[:HAS_RANGE]->(r) RETURN count(r) AS c").single()
    print("indicator:", n["i.name"], "| ranges:", r["c"], "| seed ranges:", len(s["ranges"]))
EOF
```
Expected: `indicator: 空腹血糖 | ranges: 1 | seed ranges: 1`;修改 GLU.yaml 任一字段重跑 `--entity GLU` 后仅该实体变化(区间数正确),Milvus 中旧 chunk 被替换。

- [ ] **Step 11: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add seed data chain (LLM draft + human review YAML + incremental Neo4j/Milvus import)"
```

---

### Task 9: 三路检索与 RRF 融合

**Files:**
- Create: `backend/src/report_agent/retrieval/__init__.py`, `backend/src/report_agent/retrieval/hybrid.py`, `backend/tests/unit_retrieval/__init__.py`, `backend/tests/unit_retrieval/test_hybrid.py`

**Interfaces:**
- Consumes: Task 4 的 `MilvusStore`/`ScoredChunk`, Task 5 的 `KGClient`/`IndicatorContext`, Task 3 的 `EmbeddingClient`
- Produces:
  - `RetrievalQuery(text: str, indicator_code: str | None = None, direction: str | None = None)`
  - `Evidence(text, source, entity_type, entity_id, title, score, rrf_score, rrf_sources)`(source: kg/dense/fulltext)
  - `class HybridRetriever`: `async search(q: RetrievalQuery) -> list[Evidence]`
  - `rrf_merge(ranked_lists: list[list[Evidence]], top_k: int, k: int = 60) -> list[Evidence]`(纯函数,自 C9 `hybrid_retrieval.py:629-714` 移植改造)
  - `_trim_budget(evidences: list[Evidence], budget: int) -> list[Evidence]`(纯函数,spec §4.3 证据预算)

- [ ] **Step 1: 写失败测试**

```python
import asyncio

from report_agent.config import Settings
from report_agent.knowledge.kg_client import ConditionFact, IndicatorContext
from report_agent.knowledge.milvus_client import KnowledgeChunk, ScoredChunk
from report_agent.retrieval.hybrid import Evidence, HybridRetriever, RetrievalQuery, rrf_merge


def _ev(text: str, source: str, eid: str = None) -> Evidence:
    return Evidence(text=text, source=source, entity_type=None, entity_id=eid, title=None, score=None)


def test_rrf_merge_sums_ranks_and_dedups():
    a = _ev("文本A", "dense", "e1")
    b = _ev("文本B", "fulltext", "e2")
    c = _ev("文本C", "dense", "e3")
    d = _ev("文本A重复", "fulltext", "e1")  # 与 a 同文档不同源 → 计两次分
    merged = rrf_merge([[a, c], [d, b]], top_k=5, k=60)
    scores = {m.text[:2]: m.rrf_score for m in merged}
    assert abs(scores["文本"] - (1 / 60 + 1 / 60)) < 1e-9  # e1 两路 rank0
    assert abs(scores["文本B"] - 1 / 61) < 1e-9  # rank1
    assert abs(scores["文本C"] - 1 / 61) < 1e-9  # rank1
    assert merged[0].rrf_sources == ["dense", "fulltext"]  # 来源元数据


def test_rrf_merge_same_source_multi_chunk_counts_once():
    x1 = _ev("chunk1", "dense", "e1")
    x2 = _ev("chunk2", "dense", "e1")  # 同源同文档另一 chunk
    merged = rrf_merge([[x1, x2]], top_k=5)
    assert len(merged) == 1  # 只计最佳 rank 一次
    assert merged[0].text == "chunk1"


def _fake_store():
    class S:
        def __init__(self):
            self.fail = False

        def search_dense(self, emb, top_k):
            if self.fail:
                raise RuntimeError("milvus down")
            return [ScoredChunk(chunk=KnowledgeChunk(
                chunk_id="c1", text="空腹血糖升高与糖尿病风险相关", entity_type="indicator",
                entity_id="GLU", title="空腹血糖", section_title="升高提示",
                chunk_index=0, total_chunks=1, parent_id="indicator:GLU",
            ), score=0.9, search_method="dense")]

        def search_fulltext(self, q, top_k):
            if self.fail:
                raise RuntimeError("milvus down")
            return []

    return S()


def _fake_kg(fail=False):
    class K:
        def indicator_context(self, code):
            if fail:
                raise RuntimeError("kg down")
            return IndicatorContext(code=code, name="空腹血糖", high_suggests=[
                ConditionFact(name="糖尿病风险", description=None, strength="strong", note=None)],
                clusters=["糖代谢"])
    return K()


def _fake_embedder():
    class E:
        async def embed_text(self, text):
            return [0.1] * 1024
    return E()


def _retriever(store, kg, settings=None) -> HybridRetriever:
    return HybridRetriever(milvus=store, kg=kg, embedder=_fake_embedder(),
                           settings=settings or Settings())


def test_search_merges_three_paths():
    r = _retriever(_fake_store(), _fake_kg())
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU", direction="high")))
    sources = {e.source for e in evs}
    assert sources == {"kg", "dense"}
    assert any(e.rrf_score is not None for e in evs)


def test_search_kg_down_still_returns_milvus():
    r = _retriever(_fake_store(), _fake_kg(fail=True))
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU")))
    assert evs and all(e.source == "dense" for e in evs)  # KG 挂 → Milvus 路继续


def test_search_milvus_down_still_returns_kg():
    store = _fake_store()
    store.fail = True
    r = _retriever(store, _fake_kg())
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU")))
    assert evs and all(e.source == "kg" for e in evs)


def test_search_everything_down_returns_empty():
    store = _fake_store()
    store.fail = True
    r = _retriever(store, _fake_kg(fail=True))
    assert asyncio.run(r.search(RetrievalQuery(text="x"))) == []  # 全挂 → 空,由上层占位证据处理
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_retrieval/test_hybrid.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 hybrid.py**

```python
"""三路检索(KG + Milvus dense + Milvus fulltext-BM25)+ RRF 融合 + 证据预算。

RRF 融合移植自 C9 hybrid_retrieval.py:629-714,改造点:
- 去重键 = (source, entity_id 或文本 md5)(C9 用 node_id)
- 保留 rrf_score/rrf_sources 溯源元数据
- 同源同文档多 chunk 只计最佳 rank 一次(C9 同)
任一路失败 → 该路返回空列表,由 RRF 吸收(spec §11)。
"""
import asyncio
import hashlib
from dataclasses import dataclass, field

from report_agent.config import Settings
from report_agent.knowledge.kg_client import KGClient
from report_agent.knowledge.milvus_client import MilvusStore, ScoredChunk
from report_agent.observability import get_logger

log = get_logger(__name__)
_RRF_K = 60


@dataclass
class RetrievalQuery:
    text: str
    indicator_code: str | None = None  # KG 路参数(有则查子图)
    direction: str | None = None       # high / low(过滤疾病提示方向)


@dataclass
class Evidence:
    text: str
    source: str  # kg / dense / fulltext
    entity_type: str | None = None
    entity_id: str | None = None
    title: str | None = None
    score: float | None = None
    rrf_score: float | None = None
    rrf_sources: list[str] = field(default_factory=list)


def _evidence_key(e: Evidence) -> tuple:
    return (e.source, e.entity_id or hashlib.md5(e.text[:100].encode("utf-8")).hexdigest())


def rrf_merge(ranked_lists: list[list[Evidence]], top_k: int, k: int = _RRF_K) -> list[Evidence]:
    """纯排名融合:score(d) = Σ_lists 1/(k + best_rank_l(d))。不 mutate 输入。"""
    scores: dict[tuple, float] = {}
    best_rank: dict[tuple, int] = {}
    canonical: dict[tuple, Evidence] = {}
    sources: dict[tuple, list[str]] = {}
    for lst in ranked_lists:
        seen: set[tuple] = set()
        for rank, ev in enumerate(lst):
            key = _evidence_key(ev)
            if key in seen:  # 同源同文档多 chunk 只计最佳 rank
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best_rank or rank < best_rank[key]:
                best_rank[key] = rank
                canonical[key] = ev
            if ev.source not in sources.setdefault(key, []):
                sources[key].append(ev.source)
    merged = []
    for key, score in scores.items():
        ev = canonical[key]
        ev.rrf_score = score
        ev.rrf_sources = sources[key]
        merged.append(ev)
    merged.sort(key=lambda e: e.rrf_score, reverse=True)
    return merged[:top_k]


def _trim_budget(evidences: list[Evidence], budget: int) -> list[Evidence]:
    """证据字符预算:按 RRF 序累加,超预算的丢弃(spec §4.3)。"""
    kept, total = [], 0
    for e in evidences:
        if kept and total + len(e.text) > budget:
            continue
        kept.append(e)
        total += len(e.text)
    return kept


class HybridRetriever:
    def __init__(
        self, milvus: MilvusStore, kg: KGClient, embedder, settings: Settings,
    ):
        self._milvus = milvus
        self._kg = kg
        self._embedder = embedder
        self._s = settings

    async def search(self, q: RetrievalQuery) -> list[Evidence]:
        top_k = self._s.retrieval_top_k
        kg_ev = await asyncio.to_thread(self._kg_path, q)
        dense_ev, ft_ev = [], []
        try:
            emb = await self._embedder.embed_text(q.text)
            dense = await asyncio.to_thread(self._milvus.search_dense, emb, top_k * 2)
            ft = await asyncio.to_thread(self._milvus.search_fulltext, q.text, top_k * 2)
            dense_ev = [self._chunk_evidence(s) for s in dense]
            ft_ev = [self._chunk_evidence(s) for s in ft]
        except Exception as e:  # noqa: BLE001 —— Milvus 不可用:dense+BM25 一并降级(spec §2 决策)
            log.error("milvus_retrieval_failed", error=str(e))
        merged = rrf_merge([kg_ev, dense_ev, ft_ev], top_k)
        result = _trim_budget(merged, self._s.evidence_budget_chars)
        log.info("retrieval_done", sources=[e.source for e in result], count=len(result))
        return result

    def _kg_path(self, q: RetrievalQuery) -> list[Evidence]:
        """KG 路 = 直接 Cypher 子图查询(code 已知,无 LLM 查询理解)。失败 → []。"""
        if not q.indicator_code:
            return []
        try:
            ctx = self._kg.indicator_context(q.indicator_code)
        except Exception as e:  # noqa: BLE001
            log.error("kg_retrieval_failed", error=str(e))
            return []
        out = []
        suggests = []
        if q.direction != "low":
            suggests += [(f"{ctx.name}升高可能提示:{f.name}", f.strength, f.note)
                         for f in ctx.high_suggests]
        if q.direction != "high":
            suggests += [(f"{ctx.name}降低可能提示:{f.name}", f.strength, f.note)
                         for f in ctx.low_suggests]
        for text, strength, note in suggests:
            body = text + (f"(强度:{strength})" if strength else "")
            if note:
                body += f"。{note}"
            out.append(Evidence(text=body, source="kg", entity_type="indicator",
                                entity_id=q.indicator_code, title=f"{ctx.name} 知识图谱"))
        for iv in ctx.interventions:
            tf = f",时间窗: {iv.timeframe}" if iv.timeframe else ""
            out.append(Evidence(
                text=f"[{iv.level}] 建议: {iv.text}{tf}", source="kg",
                entity_type="indicator", entity_id=q.indicator_code,
                title=f"{ctx.name} 知识图谱",
            ))
        if ctx.clusters:
            out.append(Evidence(text=f"所属指标簇: {'、'.join(ctx.clusters)}", source="kg",
                                entity_type="indicator", entity_id=q.indicator_code,
                                title=f"{ctx.name} 知识图谱"))
        return out

    def _chunk_evidence(self, s: ScoredChunk) -> Evidence:
        return Evidence(
            text=s.chunk.text, source=s.search_method, entity_type=s.chunk.entity_type,
            entity_id=s.chunk.entity_id, title=s.chunk.title, score=s.score,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_retrieval/test_hybrid.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add three-way hybrid retrieval with RRF fusion and evidence budget"
```

---

### Task 10: 报告解析(Unstructured hi_res + 视觉模型 + 提取不足降级)

**Files:**
- Create: `backend/src/report_agent/parsing/pdf_parser.py`, `backend/src/report_agent/parsing/vision_parser.py`, `backend/src/report_agent/parsing/parse_report.py`, `backend/tests/unit_parsing/test_pdf_parser.py`, `backend/tests/unit_parsing/test_vision_parser.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings`, Task 3 的 `DeepSeekClient`/`load_prompt`, Task 6 的 `RawReportItem`/`ReportMeta`
- Produces:
  - `ParseOutput(items: list[RawReportItem], meta: ReportMeta, method: str)`(method: unstructured/vision/manual)
  - `render_pdf_pages(path: str) -> list[bytes]`(pymupdf 页渲染,150dpi PNG)
  - `parse_pdf_text(path: str, settings) -> tuple[list[RawReportItem], ReportMeta] | None`(Unstructured hi_res;有效项 < parse_min_items 返回 None → 触发视觉降级)
  - `async parse_images(pages: list[bytes], client: DeepSeekClient) -> tuple[list[RawReportItem], ReportMeta]`(逐页多模态,页合并去重;LLMError 向上抛,由调用方重试)
  - `async parse_report(file_path: str | None, source: str, settings, llms) -> ParseOutput | None`(PDF: 文字层→视觉兜底;photo: 直接视觉;None = 全部失败)

- [ ] **Step 1: 写失败测试(monkeypatch Unstructured 与 LLM)**

```python
import asyncio

from report_agent.config import Settings
from report_agent.parsing.parse_report import parse_report
from report_agent.parsing.pdf_parser import parse_pdf_text
from report_agent.parsing.vision_parser import parse_images


class FakeTableElement:
    category = "Table"

    def __init__(self, html):
        self.metadata = type("M", (), {"text_as_html": html})()


class FakeTextElement:
    category = "NarrativeText"

    def __init__(self, text):
        self.text = text


def _fake_partition(elements):
    return lambda **kw: elements


def test_parse_pdf_text_extracts_table_rows(monkeypatch):
    html = (
        "<table><tr><td>项目</td><td>结果</td><td>单位</td><td>参考区间</td><td>标记</td></tr>"
        "<tr><td>白细胞计数</td><td>6.2</td><td>10^9/L</td><td>3.5-9.5</td><td></td></tr>"
        "<tr><td>空腹血糖</td><td>7.1</td><td>mmol/L</td><td>3.9-6.1</td><td>↑</td></tr></table>"
    )
    monkeypatch.setattr(
        "report_agent.parsing.pdf_parser.partition_pdf", _fake_partition([FakeTableElement(html)])
    )
    s = Settings(parse_min_items=2)
    items, meta = parse_pdf_text("x.pdf", s)
    assert [i.name for i in items] == ["白细胞计数", "空腹血糖"]
    assert items[1].value_num == 7.1 and items[1].abnormal_flag == "↑"
    assert items[1].ref_range_text == "3.9-6.1"


def test_parse_pdf_text_insufficient_returns_none(monkeypatch):
    html = "<table><tr><td>项目</td><td>结果</td></tr><tr><td>某项</td><td>6.2</td></tr></table>"
    monkeypatch.setattr(
        "report_agent.parsing.pdf_parser.partition_pdf", _fake_partition([FakeTableElement(html)])
    )
    assert parse_pdf_text("x.pdf", Settings(parse_min_items=3)) is None  # 有效项不足


def test_parse_pdf_text_extracts_meta_from_narrative(monkeypatch):
    monkeypatch.setattr(
        "report_agent.parsing.pdf_parser.partition_pdf",
        _fake_partition([FakeTextElement("某某医院 体检中心  性别: 男 年龄: 45 岁")]),
    )
    items, meta = parse_pdf_text("x.pdf", Settings(parse_min_items=0))
    assert meta.sex == "male" and meta.age == 45.0
    assert meta.institution == "某某医院"


def test_parse_images_merges_pages(monkeypatch):
    pages = [b"png1", b"png2"]
    responses = [
        {"meta": {"sex": "male", "age": 40}, "items": [
            {"section": "血常规", "name": "白细胞计数", "value_text": "6.2",
             "value_num": 6.2, "unit": "10^9/L", "ref_range_text": "3.5-9.5", "abnormal_flag": None}]},
        {"meta": {"sex": "male", "age": None}, "items": [
            {"section": "血常规", "name": "血红蛋白", "value_text": "150",
             "value_num": 150, "unit": "g/L", "ref_range_text": "130-175", "abnormal_flag": None}]},
    ]

    class FakeClient:
        async def complete_json(self, messages, retry_feedback=True):
            return responses.pop(0)

    items, meta = asyncio.run(parse_images(pages, FakeClient()))
    assert [i.name for i in items] == ["白细胞计数", "血红蛋白"]
    assert meta.sex == "male" and meta.age == 40  # 首页非空值优先


def test_parse_report_pdf_falls_back_to_vision(monkeypatch):
    async def fake_parse_images(pages, client):
        from report_agent.parsing.schemas import RawReportItem, ReportMeta
        return [RawReportItem(section=None, name="白细胞计数", value_text="6.2", value_num=6.2,
                              unit="10^9/L", ref_range_text="3.5-9.5", abnormal_flag=None)], \
            ReportMeta(source="photo")

    monkeypatch.setattr("report_agent.parsing.parse_report.parse_pdf_text", lambda p, s: None)
    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images", fake_parse_images)
    monkeypatch.setattr("report_agent.parsing.parse_report.render_pdf_pages", lambda p: [b"x"])

    out = asyncio.run(parse_report("x.pdf", "pdf", Settings(), None))
    assert out.method == "vision"  # 文字层不足 → 视觉兜底
    assert out.items[0].name == "白细胞计数"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_parsing/test_pdf_parser.py tests/unit_parsing/test_vision_parser.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 pdf_parser.py / vision_parser.py / parse_report.py**

```python
# parsing/pdf_parser.py
"""电子 PDF(有文字层)解析:Unstructured hi_res + 表格结构推断(spec §5.1)。

有效项 < parse_min_items 时返回 None,由 parse_report 降级视觉路径。
"""
import re

from bs4 import BeautifulSoup

from report_agent.config import Settings
from report_agent.observability import get_logger
from report_agent.parsing.schemas import RawReportItem, ReportMeta

log = get_logger(__name__)

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_HEADER_CELLS = {"项目", "检验项目", "指标", "项目名称", "name", "item"}


def _cell_texts(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    return rows


def _row_to_item(cells: list[str]) -> RawReportItem | None:
    """表行 → 检验项。第 1 列项目名,后续列中找数值/单位/区间/标记。"""
    if not cells or not cells[0]:
        return None
    name = cells[0]
    if name in _HEADER_CELLS:
        return None
    rest = cells[1:]
    value_text = next((c for c in rest if c and _NUM_RE.match(c)), None)
    if value_text is None and len(rest) == 1:
        value_text = rest[0]  # 定性结果列(阴性/阳性)
    value_num = float(value_text) if value_text and _NUM_RE.match(value_text) else None
    unit = next((c for c in rest if c and re.search(r"[a-zA-Z/%×^]", c) and c != value_text), None)
    ref = next((c for c in rest if c and re.search(r"[\d<≤>~]", c) and "-" in c), None) or \
        next((c for c in rest if c and re.search(r"^[<≤>~]", c)), None)
    flag = next((c for c in rest if c and c in {"↑", "↓", "H", "L", "高", "低"}), None)
    return RawReportItem(
        section=None, name=name, value_text=value_text, value_num=value_num, unit=unit,
        ref_range_text=ref, abnormal_flag=flag,
    )


def _extract_meta(text: str, meta: ReportMeta) -> None:
    if not meta.institution:
        m = re.search(r"^(.{2,20}?(?:医院|中心|门诊部|诊所|检验所))", text)
        if m:
            meta.institution = m.group(1)
    if not meta.sex:
        m = re.search(r"性别[:：]?\s*(男|女)", text)
        if m:
            meta.sex = "male" if m.group(1) == "男" else "female"
    if meta.age is None:
        m = re.search(r"年龄[:：]?\s*(\d+(?:\.\d+)?)\s*岁", text)
        if m:
            meta.age = float(m.group(1))
    if not meta.report_date:
        m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
        if m:
            meta.report_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def parse_pdf_text(path: str, settings: Settings) -> tuple[list[RawReportItem], ReportMeta] | None:
    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(
        filename=path,
        strategy=settings.unstructured_strategy,
        infer_table_structure=settings.unstructured_infer_table,
    )
    items, meta = [], ReportMeta(source="pdf")
    for el in elements:
        if el.category == "Table":
            for cells in _cell_texts(el.metadata.text_as_html or ""):
                item = _row_to_item(cells)
                if item:
                    items.append(item)
        else:
            _extract_meta(el.text or "", meta)
    if len(items) < settings.parse_min_items:
        log.warning("pdf_text_parse_insufficient", count=len(items), threshold=settings.parse_min_items)
        return None
    log.info("pdf_text_parsed", count=len(items), method=settings.unstructured_strategy)
    return items, meta
```

```python
# parsing/vision_parser.py
"""拍照/扫描件与图片型 PDF:页图 → 多模态 LLM(deepseek-v4-flash-vision-exp)→ JSON。"""
import base64

from report_agent.llm.client import DeepSeekClient
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.parsing.schemas import RawReportItem, ReportMeta

log = get_logger(__name__)


def render_pdf_pages(path: str) -> list[bytes]:
    import pymupdf

    doc = pymupdf.open(path)
    try:
        return [page.get_pixmap(dpi=150).tobytes("png") for page in doc]
    finally:
        doc.close()


def _page_message(page: bytes) -> list[dict]:
    b64 = base64.b64encode(page).decode("ascii")
    return [
        {"role": "system", "content": load_prompt("parse_vision")},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]},
    ]


async def parse_images(
    pages: list[bytes], client: DeepSeekClient
) -> tuple[list[RawReportItem], ReportMeta]:
    items: list[RawReportItem] = []
    meta = ReportMeta(source="photo")
    for i, page in enumerate(pages):
        data = await client.complete_json(_page_message(page))
        for it in data.get("items", []):
            items.append(RawReportItem(
                section=it.get("section"), name=it.get("name", ""), value_text=it.get("value_text"),
                value_num=it.get("value_num"), unit=it.get("unit"),
                ref_range_text=it.get("ref_range_text"), abnormal_flag=it.get("abnormal_flag"),
            ))
        m = data.get("meta") or {}
        if meta.institution is None:
            meta.institution = m.get("institution")
        if meta.report_date is None:
            meta.report_date = m.get("report_date")
        if meta.sex is None and m.get("sex") in ("male", "female"):
            meta.sex = m["sex"]
        if meta.age is None and m.get("age") is not None:
            meta.age = float(m["age"])
        log.info("vision_page_parsed", page=i + 1, items=len(data.get("items", [])))
    return items, meta
```

```python
# parsing/parse_report.py
"""解析编排:电子 PDF → 文字层(Unstructured),不足降级视觉;拍照 → 视觉。

失败语义:视觉解析 LLMError 向上抛(调用方重试后仍失败则任务 failed);
None 返回值表示文字层不足但视觉路径未被执行(仅测试/降级演示用)。
"""
from dataclasses import dataclass

from report_agent.config import Settings
from report_agent.observability import get_logger
from report_agent.parsing.pdf_parser import parse_pdf_text
from report_agent.parsing.schemas import RawReportItem, ReportMeta
from report_agent.parsing.vision_parser import parse_images, render_pdf_pages

log = get_logger(__name__)


@dataclass
class ParseOutput:
    items: list[RawReportItem]
    meta: ReportMeta
    method: str  # unstructured / vision / manual


async def parse_report(file_path: str | None, source: str, settings: Settings, llms) -> ParseOutput | None:
    if source == "photo":
        pages = [open(file_path, "rb").read()]  # noqa: SIM115 —— 单页图片
        items, meta = await parse_images(pages, llms.vision)
        return ParseOutput(items=items, meta=meta, method="vision")
    if source == "pdf":
        parsed = parse_pdf_text(file_path, settings)
        if parsed is not None:
            items, meta = parsed
            return ParseOutput(items=items, meta=meta, method="unstructured")
        log.warning("pdf_text_insufficient_falling_back_to_vision", path=file_path)
        pages = render_pdf_pages(file_path)
        items, meta = await parse_images(pages, llms.vision)
        return ParseOutput(items=items, meta=meta, method="vision")
    return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_parsing/test_pdf_parser.py tests/unit_parsing/test_vision_parser.py -v`
Expected: PASS(若 bs4 未随 unstructured 安装,在 pyproject.toml 加 `beautifulsoup4>=4.12`)

- [ ] **Step 5: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add report parsing (Unstructured hi_res with vision fallback, multimodal page parser)"
```

---

### Task 11: 任务服务、数据访问与管线运行器

**Files:**
- Create: `backend/src/report_agent/pipeline/tasks.py`, `backend/src/report_agent/pipeline/db_access.py`, `backend/src/report_agent/pipeline/deps.py`, `backend/src/report_agent/pipeline/stages.py`, `backend/src/report_agent/pipeline/runner.py`, `backend/tests/unit_pipeline/__init__.py`, `backend/tests/unit_pipeline/test_runner.py`, `backend/tests/unit_pipeline/test_stages.py`

**Interfaces:**
- Consumes: Task 2 的模型/session、Task 6 的 `Normalizer`、Task 7 的 `rule_compare`、Task 10 的 `parse_report`
- Produces:
  - `TaskStatus`(str Enum): PENDING/AWAITING_META/RUNNING/COMPLETED/FAILED/DEGRADED
  - `TaskInfo(id, report_id, status, stage, checkpoints: dict, timings: dict, error)`
  - `class TaskService(session_factory)`: `create(report_id) -> TaskInfo`、`get(task_id) -> TaskInfo | None`、`claim(task_id) -> TaskInfo | None`(pending/running→running,FOR UPDATE SKIP LOCKED)、`save_checkpoint(task_id, stage, payload: dict, seconds: float)`、`pause_for_meta(task_id)`、`resume_from_meta(task_id)`、`complete(task_id, degraded: bool)`、`fail(task_id, error: str)`、`list_running() -> list[str]`
  - `class DataAccess(session_factory)`: `get_report(report_id) -> dict | None`、`save_raw_items(report_id, items: list[RawReportItem])`、`update_report_meta(report_id, meta: ReportMeta)`、`get_raw_items(report_id) -> list[RawReportItem]`、`save_normalized(report_id, items: list[NormalizedItem])`、`get_normalized(report_id) -> list[NormalizedItem]`、`apply_judgments(report_id, judgments: list[ItemJudgment])`、`save_interpretation(report_id, task_id, doc: dict)`、`save_followup(report_id, task_id, doc: dict)`(后两个 Task 12 使用)
  - `PipelineDeps(settings, llms, kg, store, retriever, normalizer, session_factory)`(dataclass,pipeline/deps.py)+ `async build_deps(settings) -> PipelineDeps`(从 KG 加载指标词典构建 Normalizer;Task 14 在 app 启动时调用)
  - `stages.py`: `StageContext(task_id, report, checkpoints, deps, db, tasks)`、`STAGE_ORDER = ["parse", "normalize", "compare", "retrieve", "generate", "guardrail", "plan"]`、`STAGE_FUNCS: dict[str, callable]`(本任务注册 parse/normalize/compare;Task 12/13 追加注册)
  - `stages`: `parse_stage(ctx) -> dict`(payload 含 needs_meta/method/n_items)、`normalize_stage(ctx) -> dict`、`compare_stage(ctx) -> dict`(payload 含 n_abnormal/critical_count;sex/age 缺失抛 ValueError → 任务 failed,信息引导补录)
  - `class PipelineRunner(task_service, db, deps)`:`run_task(task_id) -> None`(认领→逐阶段→checkpoint→complete/fail;parse payload 的 needs_meta → pause_for_meta)、`recover_on_startup() -> None`(list_running → 每任务 create_task(run_task))

- [ ] **Step 1: 写失败测试(runner 用 FakeTaskService/FakeStage,不依赖 DB)**

```python
import asyncio

from report_agent.pipeline import runner as runner_mod
from report_agent.pipeline.runner import PipelineRunner
from report_agent.pipeline.tasks import TaskStatus


class FakeTaskService:
    def __init__(self):
        self.state = {"status": TaskStatus.PENDING, "checkpoints": {}, "stage": None}
        self.calls = []

    async def claim(self, task_id):
        self.calls.append("claim")
        if self.state["status"] not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return None
        self.state["status"] = TaskStatus.RUNNING
        return type("T", (), {
            "id": task_id, "report_id": "r1", "status": self.state["status"],
            "stage": None, "checkpoints": self.state["checkpoints"],
            "timings": {}, "error": None,
        })()

    async def save_checkpoint(self, task_id, stage, payload, seconds):
        self.calls.append(("checkpoint", stage))
        self.state["checkpoints"][stage] = payload

    async def pause_for_meta(self, task_id):
        self.calls.append("pause")
        self.state["status"] = TaskStatus.AWAITING_META

    async def complete(self, task_id, degraded=False):
        self.calls.append(("complete", degraded))

    async def fail(self, task_id, error):
        self.calls.append(("fail", error))


class FakeDB:
    async def get_report(self, report_id):
        return {"id": report_id, "source": "manual", "file_path": None,
                "sex": "male", "age": 40.0}


def _runner(tasks, stages) -> PipelineRunner:
    r = PipelineRunner(task_service=tasks, db=FakeDB(), deps=object())
    r._stage_funcs = stages  # 注入
    return r


def test_run_task_stage_order_and_checkpoints():
    tasks = FakeTaskService()
    order = []
    runner_mod.STAGE_ORDER = ["parse", "normalize", "compare"]

    async def mk(name):
        async def fn(ctx):
            order.append(name)
            return {"n": len(order)}
        return fn

    stages = {"parse": asyncio.run(mk("parse")),
              "normalize": asyncio.run(mk("normalize")),
              "compare": asyncio.run(mk("compare"))}
    asyncio.run(_runner(tasks, stages).run_task("t1"))
    assert order == ["parse", "normalize", "compare"]
    assert tasks.calls.count("claim") == 1
    assert ("checkpoint", "parse") in tasks.calls and ("checkpoint", "compare") in tasks.calls
    assert ("complete", False) in tasks.calls


def test_run_task_skips_completed_stages():
    tasks = FakeTaskService()
    tasks.state["checkpoints"] = {"parse": {"needs_meta": False}}
    order = []
    runner_mod.STAGE_ORDER = ["parse", "normalize"]

    async def aparse(ctx):
        order.append("parse")
        return {}

    async def anormalize(ctx):
        order.append("normalize")
        return {}

    r = _runner(tasks, {"parse": aparse, "normalize": anormalize})
    asyncio.run(r.run_task("t1"))
    assert order == ["normalize"]  # parse 已在 checkpoint,跳过


def test_run_task_needs_meta_pauses():
    tasks = FakeTaskService()
    runner_mod.STAGE_ORDER = ["parse"]

    async def aparse(ctx):
        return {"needs_meta": True}

    asyncio.run(_runner(tasks, {"parse": aparse}).run_task("t1"))
    assert "pause" in tasks.calls
    assert tasks.state["status"] == TaskStatus.AWAITING_META


def test_run_task_stage_failure_fails_task():
    tasks = FakeTaskService()
    runner_mod.STAGE_ORDER = ["parse"]

    async def aparse(ctx):
        raise ValueError("解析彻底失败")

    asyncio.run(_runner(tasks, {"parse": aparse}).run_task("t1"))
    assert any(isinstance(c, tuple) and c[0] == "fail" for c in tasks.calls)
    assert "彻底失败" in [c[1] for c in tasks.calls if isinstance(c, tuple) and c[0] == "fail"][0]


def test_recover_on_startup_reruns_running(monkeypatch):
    tasks = FakeTaskService()
    tasks.state["status"] = TaskStatus.RUNNING

    class FakeTasks2(FakeTaskService):
        async def list_running(self):
            return ["t9"]

    tasks = FakeTasks2()
    ran = []

    async def capture(fn):
        ran.append(1)

    monkeypatch.setattr(runner_mod.asyncio, "create_task", capture)
    r = _runner(tasks, {})
    r.recover_on_startup()
    assert ran == [1]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_pipeline/test_runner.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 tasks.py**

```python
"""任务表服务:认领(SKIP LOCKED)/阶段 checkpoint/暂停/恢复/完成/失败。spec §5.7。"""
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select, update

from report_agent.db.models import InterpretationTask
from report_agent.observability import get_logger

log = get_logger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    AWAITING_META = "awaiting_meta"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"


@dataclass
class TaskInfo:
    id: str
    report_id: str
    status: str
    stage: str | None
    checkpoints: dict
    timings: dict
    error: str | None


def _info(row: InterpretationTask) -> TaskInfo:
    return TaskInfo(
        id=row.id, report_id=row.report_id, status=row.status, stage=row.stage,
        checkpoints=row.checkpoints or {}, timings=row.timings or {}, error=row.error,
    )


class TaskService:
    def __init__(self, session_factory):
        self._factory = session_factory

    async def create(self, report_id: str) -> TaskInfo:
        async with self._factory() as s:
            row = InterpretationTask(report_id=report_id, status=TaskStatus.PENDING)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _info(row)

    async def get(self, task_id: str) -> TaskInfo | None:
        async with self._factory() as s:
            row = await s.get(InterpretationTask, task_id)
            return _info(row) if row else None

    async def claim(self, task_id: str) -> TaskInfo | None:
        """pending/running → running。FOR UPDATE SKIP LOCKED:多 worker 安全(spec §5.7)。"""
        async with self._factory() as s:
            row = (await s.execute(
                select(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .with_for_update(skip_locked=True)
            )).scalar_one_or_none()
            if row is None or row.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return None
            row.status = TaskStatus.RUNNING
            await s.commit()
            await s.refresh(row)
            return _info(row)

    async def save_checkpoint(self, task_id: str, stage: str, payload: dict, seconds: float) -> None:
        async with self._factory() as s:
            row = await s.get(InterpretationTask, task_id)
            if row is None:
                return
            checkpoints = dict(row.checkpoints or {})
            checkpoints[stage] = payload
            timings = dict(row.timings or {})
            timings[stage] = round(seconds, 3)
            row.checkpoints, row.timings, row.stage = checkpoints, timings, stage
            await s.commit()

    async def pause_for_meta(self, task_id: str) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .values(status=TaskStatus.AWAITING_META)
            )
            await s.commit()

    async def resume_from_meta(self, task_id: str) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id, InterpretationTask.status == TaskStatus.AWAITING_META)
                .values(status=TaskStatus.RUNNING)
            )
            await s.commit()

    async def complete(self, task_id: str, degraded: bool) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .values(status=TaskStatus.DEGRADED if degraded else TaskStatus.COMPLETED)
            )
            await s.commit()

    async def fail(self, task_id: str, error: str) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .values(status=TaskStatus.FAILED, error=error[:2000])
            )
            await s.commit()
        log.error("task_failed", task_id=task_id, error=error)

    async def list_running(self) -> list[str]:
        async with self._factory() as s:
            rows = (await s.execute(
                select(InterpretationTask.id).where(InterpretationTask.status == TaskStatus.RUNNING)
            )).scalars().all()
            return list(rows)
```

- [ ] **Step 4: 实现 db_access.py**

```python
"""管线阶段的数据访问(隔离 SQL,便于阶段函数单测注入 fake)。"""
from report_agent.db.models import (
    FollowupPlanRow,
    InterpretationRow,
    NormalizedItemRow,
    RawItem,
    Report,
)
from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
from report_agent.pipeline.rule_compare import ItemJudgment


class DataAccess:
    def __init__(self, session_factory):
        self._factory = session_factory

    async def get_report(self, report_id: str) -> dict | None:
        async with self._factory() as s:
            row = await s.get(Report, report_id)
            if row is None:
                return None
            return {
                "id": row.id, "source": row.source, "file_path": row.file_path,
                "institution": row.institution, "report_date": row.report_date,
                "sex": row.sex, "age": row.age,
            }

    async def update_report_meta(self, report_id: str, meta: ReportMeta) -> None:
        async with self._factory() as s:
            row = await s.get(Report, report_id)
            if row is None:
                return
            row.institution = row.institution or meta.institution
            row.report_date = row.report_date or meta.report_date
            row.sex = row.sex or meta.sex
            row.age = row.age if row.age is not None else meta.age
            await s.commit()

    async def save_raw_items(self, report_id: str, items: list[RawReportItem]) -> None:
        async with self._factory() as s:
            s.add_all([
                RawItem(report_id=report_id, section=it.section, item_name=it.name,
                        value_text=it.value_text, value_num=it.value_num, unit=it.unit,
                        ref_range_text=it.ref_range_text, abnormal_flag=it.abnormal_flag)
                for it in items
            ])
            await s.commit()

    async def get_raw_items(self, report_id: str) -> list[RawReportItem]:
        async with self._factory() as s:
            from sqlalchemy import select

            rows = (await s.execute(
                select(RawItem).where(RawItem.report_id == report_id).order_by(RawItem.id)
            )).scalars().all()
            return [
                RawReportItem(section=r.section, name=r.item_name, value_text=r.value_text,
                              value_num=r.value_num, unit=r.unit,
                              ref_range_text=r.ref_range_text, abnormal_flag=r.abnormal_flag)
                for r in rows
            ]

    async def save_normalized(self, report_id: str, items: list[NormalizedItem]) -> None:
        async with self._factory() as s:
            s.add_all([
                NormalizedItemRow(
                    report_id=report_id, section=it.section, item_name=it.name,
                    indicator_code=it.indicator_code, value_text=it.value_text,
                    value_num=it.value_num, unit=it.unit,
                    raw_value_num=it.raw_value_num, raw_unit=it.raw_unit,
                    ref_range_text=it.ref_range_text, range_from=it.range_from,
                )
                for it in items
            ])
            await s.commit()

    async def get_normalized(self, report_id: str) -> list[NormalizedItem]:
        async with self._factory() as s:
            from sqlalchemy import select

            rows = (await s.execute(
                select(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
                .order_by(NormalizedItemRow.id)
            )).scalars().all()
            return [
                NormalizedItem(
                    raw_index=i, section=r.section, name=r.item_name, indicator_code=r.indicator_code,
                    value_text=r.value_text, value_num=r.value_num, unit=r.unit,
                    raw_value_num=r.raw_value_num, raw_unit=r.raw_unit,
                    ref_range_text=r.ref_range_text, range_from=r.range_from,
                )
                for i, r in enumerate(rows)
            ]

    async def apply_judgments(self, report_id: str, judgments: list[ItemJudgment]) -> None:
        async with self._factory() as s:
            from sqlalchemy import select

            rows = (await s.execute(
                select(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
                .order_by(NormalizedItemRow.id)
            )).scalars().all()
            for row, j in zip(rows, judgments, strict=True):
                row.status = j.status.value
                row.ref_low, row.ref_high = j.ref_low, j.ref_high
                row.critical = j.critical
            await s.commit()

    async def save_interpretation(self, report_id: str, task_id: str, doc: dict) -> None:
        async with self._factory() as s:
            s.add(InterpretationRow(
                report_id=report_id, task_id=task_id, summary=doc["summary"],
                items=doc["items"], advice_summary=doc["advice_summary"],
                disclaimer=doc["disclaimer"], degraded=doc.get("degraded", False),
            ))
            await s.commit()

    async def save_followup(self, report_id: str, task_id: str, doc: dict) -> None:
        async with self._factory() as s:
            s.add(FollowupPlanRow(
                report_id=report_id, task_id=task_id, items=doc["items"],
                degraded=doc.get("degraded", False),
            ))
            await s.commit()
```

注意:`raw_value_num/raw_unit` 已在 normalized 表落列(Task 2),断点恢复场景下 compare 阶段直接读库,无需补偿。

- [ ] **Step 5: 实现 stages.py(parse/normalize/compare + 注册表)**

```python
"""管线阶段函数。STAGE_FUNCS 注册表:Task 11 注册 parse/normalize/compare,
Task 12 注册 retrieve/generate/plan,Task 13 注册 guardrail。
每个阶段:读 ctx(db/checkpoints)→ 计算 → 写 db → 返回 checkpoint payload;失败抛异常。
"""
from dataclasses import dataclass, field

from report_agent.observability import get_logger
from report_agent.parsing.parse_report import parse_report
from report_agent.parsing.schemas import ReportMeta
from report_agent.pipeline.rule_compare import judge_all, match_patterns

log = get_logger(__name__)

STAGE_ORDER = ["parse", "normalize", "compare", "retrieve", "generate", "guardrail", "plan"]


@dataclass
class StageContext:
    task_id: str
    report: dict
    checkpoints: dict = field(default_factory=dict)
    deps: object = None  # PipelineDeps(build 见 runner)
    db: object = None    # DataAccess
    tasks: object = None  # TaskService


STAGE_FUNCS: dict[str, callable] = {}


def register(name: str):
    def deco(fn):
        STAGE_FUNCS[name] = fn
        return fn
    return deco


@register("parse")
async def parse_stage(ctx: StageContext) -> dict:
    if ctx.report["source"] == "manual":
        # 手动录入:items 已在 API 层写入 raw_items,这里只校验
        items = await ctx.db.get_raw_items(ctx.report["id"])
        if not items:
            raise ValueError("手动录入未提供任何检验项")
        return {"method": "manual", "n_items": len(items), "needs_meta": False}

    out = await parse_report(
        ctx.report["file_path"], ctx.report["source"], ctx.deps.settings, ctx.deps.llms
    )
    if out is None:
        raise ValueError("解析失败:不支持的来源")
    await ctx.db.update_report_meta(ctx.report["id"], out.meta)
    await ctx.db.save_raw_items(ctx.report["id"], out.items)
    needs_meta = out.meta.sex is None or out.meta.age is None
    if needs_meta:
        log.warning("report_meta_missing", task_id=ctx.task_id,
                    sex=out.meta.sex, age=out.meta.age)
    return {"method": out.method, "n_items": len(out.items), "needs_meta": needs_meta}


@register("normalize")
async def normalize_stage(ctx: StageContext) -> dict:
    raw_items = await ctx.db.get_raw_items(ctx.report["id"])
    items = await ctx.deps.normalizer.normalize(raw_items, llm=ctx.deps.llms.chat)
    await ctx.db.save_normalized(ctx.report["id"], items)
    return {"n_items": len(items)}


@register("compare")
async def compare_stage(ctx: StageContext) -> dict:
    report = ctx.report
    if report["sex"] is None or report["age"] is None:
        raise ValueError("报告缺少性别/年龄,无法判定参考区间;请先补录 meta(PATCH /api/reports/{id}/meta)")
    items = await ctx.db.get_normalized(ctx.report["id"])

    meta = ReportMeta(sex=report["sex"], age=report["age"])
    specs = {}
    for it in items:
        if it.indicator_code and it.indicator_code not in specs:
            specs[it.indicator_code] = ctx.deps.kg.range_specs(it.indicator_code)
    judgments = judge_all(items, specs, meta)
    await ctx.db.apply_judgments(ctx.report["id"], judgments)
    abnormal = [j for j in judgments if j.status.value not in ("normal", "unknown", "unmapped")]
    critical = [j for j in abnormal if j.critical]
    matched = [
        (p.name, [j.name for j in js])
        for p, js in match_patterns(judgments, ctx.deps.kg.all_patterns())
    ]
    return {
        "n_abnormal": len(abnormal),
        "critical_count": len(critical),
        "matched_patterns": matched,
        "judgments": [
            {"indicator_code": j.indicator_code, "name": j.name, "status": j.status.value,
             "value_num": j.value_num, "value_text": j.value_text, "unit": j.unit,
             "ref_low": j.ref_low, "ref_high": j.ref_high, "critical": j.critical,
             "range_source": j.range_source}
            for j in judgments
        ],
    }
```

- [ ] **Step 6: 实现 deps.py 与 runner.py**

```python
# pipeline/deps.py
"""管线依赖容器:Task 14 在 app 启动时 build 一次,注入 runner 与各阶段。"""
from dataclasses import dataclass

from report_agent.knowledge.kg_client import KGClient
from report_agent.knowledge.milvus_client import MilvusStore
from report_agent.parsing.normalizer import Normalizer
from report_agent.retrieval.hybrid import HybridRetriever


@dataclass
class PipelineDeps:
    settings: object
    llms: object
    kg: KGClient
    store: MilvusStore
    retriever: HybridRetriever
    normalizer: Normalizer
    session_factory: object
    db: object  # DataAccess —— API/追问/评测共用同一数据访问层


async def build_deps(settings) -> PipelineDeps:
    from report_agent.llm.client import llm_factory

    llms = await llm_factory.get()
    kg = KGClient(uri=settings.neo4j_uri, user=settings.neo4j_user,
                  password=settings.neo4j_password, database=settings.neo4j_database)
    store = MilvusStore(uri=settings.milvus_uri, collection=settings.milvus_collection,
                        embedding_dim=settings.embedding_dim)
    import asyncio

    await asyncio.to_thread(store.ensure_collection)
    entries = await asyncio.to_thread(kg.list_indicators)
    normalizer = Normalizer(entries)
    retriever = HybridRetriever(milvus=store, kg=kg, embedder=llms.embedding, settings=settings)
    from report_agent.db.session import session_factory
    from report_agent.pipeline.db_access import DataAccess

    return PipelineDeps(settings=settings, llms=llms, kg=kg, store=store, retriever=retriever,
                        normalizer=normalizer, session_factory=session_factory,
                        db=DataAccess(session_factory))
```

```python
# pipeline/runner.py
"""管线运行器:认领 → 逐阶段执行 → checkpoint → complete/fail;含启动恢复。"""
import asyncio
import time

from report_agent.observability import bind_context, clear_context, get_logger
from report_agent.pipeline.stages import STAGE_FUNCS, STAGE_ORDER, StageContext
from report_agent.pipeline.tasks import TaskService

log = get_logger(__name__)


class PipelineRunner:
    def __init__(self, task_service: TaskService, db, deps):
        self._tasks = task_service
        self._db = db
        self._deps = deps

    async def run_task(self, task_id: str) -> None:
        task = await self._tasks.claim(task_id)
        if task is None:
            return
        bind_context(task_id=task_id)
        try:
            report = await self._db.get_report(task.report_id)
            if report is None:
                raise ValueError(f"报告不存在: {task.report_id}")
            ctx = StageContext(
                task_id=task_id, report=report, checkpoints=task.checkpoints,
                deps=self._deps, db=self._db, tasks=self._tasks,
            )
            for stage in STAGE_ORDER:
                if stage in task.checkpoints:
                    continue
                fn = STAGE_FUNCS.get(stage)
                if fn is None:
                    raise RuntimeError(f"阶段未注册: {stage}")
                t0 = time.perf_counter()
                log.info("stage_start", stage=stage)
                payload = await fn(ctx)
                await self._tasks.save_checkpoint(task_id, stage, payload, time.perf_counter() - t0)
                ctx.checkpoints[stage] = payload
                log.info("stage_done", stage=stage, seconds=round(time.perf_counter() - t0, 2))
                if stage == "parse" and payload.get("needs_meta"):
                    await self._tasks.pause_for_meta(task_id)
                    return
            degraded = bool(ctx.checkpoints.get("guardrail", {}).get("degraded"))
            await self._tasks.complete(task_id, degraded=degraded)
            log.info("pipeline_completed", degraded=degraded)
        except Exception as e:  # noqa: BLE001
            log.exception("pipeline_stage_error")
            await self._tasks.fail(task_id, str(e))
        finally:
            clear_context()

    def recover_on_startup(self) -> None:
        """服务重启:把所有 running 任务放回执行队列(spec §5.7)。"""

        async def _recover():
            for task_id in await self._tasks.list_running():
                log.warning("recovering_task", task_id=task_id)
                asyncio.create_task(self.run_task(task_id))

        asyncio.create_task(_recover())
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_pipeline/test_runner.py -v`
Expected: PASS(第一个测试里 monkeypatch 全局 STAGE_ORDER 后需在测试尾恢复;若干扰其他用例,改用 pytest 的 monkeypatch.setattr 传入 runner_mod)

- [ ] **Step 8: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add task service with checkpoints and pipeline runner with resume, plus parse/normalize/compare stages"
```

---

### Task 12: 解读生成与复查计划(retrieve/generate/plan 阶段)

**Files:**
- Create: `backend/src/report_agent/pipeline/interpret.py`, `backend/src/report_agent/pipeline/followup_plan.py`, `backend/src/report_agent/llm/prompts/interpret_item.txt`, `backend/src/report_agent/llm/prompts/summary.txt`, `backend/src/report_agent/llm/prompts/followup_smooth.txt`, `backend/tests/unit_pipeline/test_interpret.py`, `backend/tests/unit_pipeline/test_followup_plan.py`
- Modify: `backend/src/report_agent/pipeline/stages.py`(追加 retrieve/generate/plan 三阶段)

**Interfaces:**
- Consumes: Task 7 的 `ItemJudgment`/`ItemStatus`, Task 5 的 `IndicatorContext`, Task 9 的 `HybridRetriever`/`Evidence`/`RetrievalQuery`, Task 11 的 `StageContext`/`STAGE_FUNCS`
- Produces:
  - `ItemInterpretation(indicator_code, name, status, value_text, meaning, risks, advice_level, advice, evidence_ids)`(advice_level: lifestyle/recheck/specialist/urgent)
  - `async interpret_item(j: ItemJudgment, ctx: IndicatorContext | None, evidence: list[Evidence], llm) -> ItemInterpretation`(LLMError → 模板解读“建议线下咨询”,不抛)
  - `async generate_summary(items: list[ItemInterpretation], matched_patterns: list[str], unknown_count: int, llm) -> str`(LLMError → `template_summary`)
  - `template_summary(n_abnormal, critical_count, matched_patterns, unknown_count) -> str`(规则降级总评)
  - `build_advice_summary(items) -> str`(代码排序 urgent > specialist > recheck > lifestyle,危急置顶)
  - `DISCLAIMER` 常量、`assemble_interpretation(...) -> dict`(四段式)、`build_degraded_interpretation(judgments) -> dict`(安全降级版,Task 13 用)
  - `FollowupItem(item, timeframe, department, basis)`、`FollowupPlanDoc(items, degraded)`
  - `build_followup_items(judgments, matched_patterns, ctx_by_code) -> list[FollowupItem]`(规则收集)
  - `async smooth_basis(items, llm) -> list[FollowupItem] | None`(仅润色 basis 槽位;LLMError/schema 不符 → None 回退模板)
  - `async build_followup_plan(...) -> FollowupPlanDoc`
  - stages:`retrieve_stage(ctx) -> dict`(payload 含 evidence: {item_key: [Evidence dict]})、`generate_stage(ctx) -> dict`、`plan_stage(ctx) -> dict`(落库 interpretations/followup_plans)

- [ ] **Step 1: 写失败测试**

```python
import asyncio

from report_agent.knowledge.kg_client import ConditionFact, IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.pipeline.followup_plan import FollowupPlanDoc, build_followup_items
from report_agent.pipeline.interpret import (
    DISCLAIMER,
    build_advice_summary,
    build_degraded_interpretation,
    generate_summary,
    interpret_item,
    template_summary,
)
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus


def _judgment(status=ItemStatus.HIGH, code="GLU", value_num=7.2, critical=False) -> ItemJudgment:
    return ItemJudgment(indicator_code=code, name="空腹血糖", value_num=value_num, value_text=None,
                        unit="mmol/L", status=status, ref_low=3.9, ref_high=6.1,
                        critical=critical, range_source="report")


def _ctx() -> IndicatorContext:
    return IndicatorContext(code="GLU", name="空腹血糖", high_suggests=[
        ConditionFact(name="糖尿病风险", description=None, strength="strong", note="空腹受损")])


class FakeLLM:
    def __init__(self, result=None, error=False):
        self.result = result
        self.error = error
        self.calls = []

    async def complete_json(self, messages, retry_feedback=True):
        self.calls.append(messages)
        if self.error:
            raise LLMError("down")
        return self.result


def test_interpret_item_maps_llm_json():
    llm = FakeLLM(result={
        "meaning": "血糖偏高", "risks": ["糖尿病风险"], "advice_level": "recheck",
        "advice": "复查空腹血糖", "evidence_ids": ["e0"],
    })
    out = asyncio.run(interpret_item(_judgment(), _ctx(), [], llm))
    assert out.status == "high" and out.advice_level == "recheck"
    assert out.evidence_ids == ["e0"]


def test_interpret_item_llm_failure_falls_back_to_template():
    out = asyncio.run(interpret_item(_judgment(), _ctx(), [], FakeLLM(error=True)))
    assert "线下咨询" in out.advice
    assert out.meaning != ""


def test_summary_llm_failure_uses_template():
    assert "2 项异常" in template_summary(2, 1, ["代谢综合征倾向"], 0)
    assert "危急" in template_summary(2, 1, [], 0)
    out = asyncio.run(generate_summary([], [], 0, FakeLLM(error=True)))
    assert "异常" in out


def test_advice_summary_orders_urgent_first():
    from report_agent.pipeline.interpret import ItemInterpretation

    items = [
        ItemInterpretation(indicator_code="TC", name="总胆固醇", status="high", value_text="6.0",
                           meaning="", risks=[], advice_level="lifestyle", advice="生活方式", evidence_ids=[]),
        ItemInterpretation(indicator_code="GLU", name="空腹血糖", status="critical_high",
                           value_text="25", meaning="", risks=[], advice_level="urgent",
                           advice="尽快就医", evidence_ids=[]),
        ItemInterpretation(indicator_code="ALT", name="谷丙转氨酶", status="high", value_text="80",
                           meaning="", risks=[], advice_level="recheck", advice="复查", evidence_ids=[]),
    ]
    text = build_advice_summary(items)
    assert text.index("尽快就医") < text.index("复查") < text.index("生活方式")


def test_degraded_interpretation_has_table_and_disclaimer():
    doc = build_degraded_interpretation([_judgment(critical=True)])
    assert doc["degraded"] is True
    assert "空腹血糖" in doc["summary"] and "线下就医" in doc["summary"]
    assert DISCLAIMER in doc["disclaimer"]


def test_followup_items_critical_and_recheck():
    j_crit = _judgment(status=ItemStatus.CRITICAL_HIGH, value_num=25, critical=True)
    j_high = _judgment()
    ctx = IndicatorContext(code="GLU", name="空腹血糖")
    items = build_followup_items([j_crit, j_high], [], {"GLU": ctx})
    texts = " | ".join(f"{i.item}/{i.timeframe}/{i.department}" for i in items)
    assert "立即" in texts  # 危急 → 立即就医条目
    assert "空腹血糖" in texts
    assert items[0].timeframe == "立即"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_pipeline/test_interpret.py tests/unit_pipeline/test_followup_plan.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 interpret.py**

```python
"""逐项解读生成 + 总评(规则模板兜底)+ 四段式组装 + 安全降级版。spec §3.2/§3.4/§5.4。"""
import asyncio
import re
from dataclasses import dataclass

from report_agent.knowledge.kg_client import IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
from report_agent.retrieval.hybrid import Evidence

log = get_logger(__name__)

DISCLAIMER = (
    "【免责声明】本解读由系统基于您的体检报告数据与医学知识库自动生成,仅供健康参考,"
    "不构成医学诊断、治疗或用药建议。如有不适或疑问,请咨询执业医师。"
)
ADVICE_LEVELS = ["lifestyle", "recheck", "specialist", "urgent"]


@dataclass
class ItemInterpretation:
    indicator_code: str | None
    name: str
    status: str
    value_text: str
    meaning: str
    risks: list[str]
    advice_level: str
    advice: str
    evidence_ids: list[str]


def _status_cn(status: ItemStatus) -> str:
    return {
        ItemStatus.HIGH: "升高", ItemStatus.LOW: "降低",
        ItemStatus.CRITICAL_HIGH: "显著升高(危急值)", ItemStatus.CRITICAL_LOW: "显著降低(危急值)",
        ItemStatus.NORMAL: "正常", ItemStatus.UNKNOWN: "无法判定", ItemStatus.UNMAPPED: "未识别",
    }[status]


def _value_text(j: ItemJudgment) -> str:
    v = f"{j.value_num} {j.unit}".strip() if j.value_num is not None else (j.value_text or "-")
    if j.ref_low is not None or j.ref_high is not None:
        lo = j.ref_low if j.ref_low is not None else "-∞"
        hi = j.ref_high if j.ref_high is not None else "+∞"
        return f"{v}(参考区间 {lo}~{hi})"
    return v


def _fallback_interpretation(j: ItemJudgment) -> ItemInterpretation:
    return ItemInterpretation(
        indicator_code=j.indicator_code, name=j.name, status=j.status.value,
        value_text=_value_text(j), meaning=f"{j.name}检测结果为{_status_cn(j.status)}。",
        risks=[], advice_level="specialist" if j.critical else "recheck",
        advice=("该结果达到危急值水平,建议尽快线下就医评估。" if j.critical
                else "建议携带完整报告线下咨询医生进一步评估。"),
        evidence_ids=[],
    )


async def interpret_item(
    j: ItemJudgment, ctx: IndicatorContext | None, evidence: list[Evidence], llm
) -> ItemInterpretation:
    if llm is None:
        return _fallback_interpretation(j)
    prompt = load_prompt("interpret_item")
    ev_block = "\n\n".join(
        f"[e{i}](来源:{e.source},标题:{e.title or ''})\n{e.text}" for i, e in enumerate(evidence)
    ) or "(无检索证据,请按知识库覆盖不足处理)"
    kg_facts = ""
    if ctx is not None:
        kg_facts = (
            f"知识图谱事实: 升高提示 {[f'{c.name}({c.strength})' for c in ctx.high_suggests]}; "
            f"降低提示 {[f'{c.name}({c.strength})' for c in ctx.low_suggests]}; "
            f"建议 {[f'{i.level}:{i.text}' for i in ctx.interventions]}; 科室 {ctx.departments}"
        )
    messages = [{"role": "user", "content": prompt.format(
        name=j.name, status=_status_cn(j.status), value=_value_text(j),
        kg_facts=kg_facts, evidence=ev_block,
    )}]
    try:
        data = await llm.complete_json(messages)
        return ItemInterpretation(
            indicator_code=j.indicator_code, name=j.name, status=j.status.value,
            value_text=_value_text(j), meaning=data.get("meaning", ""),
            risks=list(data.get("risks") or []),
            advice_level=data.get("advice_level") if data.get("advice_level") in ADVICE_LEVELS
            else "recheck",
            advice=data.get("advice", ""),
            evidence_ids=[e for e in data.get("evidence_ids", []) if isinstance(e, str)],
        )
    except LLMError:
        log.warning("interpret_item_llm_failed", name=j.name)
        return _fallback_interpretation(j)


def template_summary(n_abnormal: int, critical_count: int, matched_patterns: list[str],
                     unknown_count: int) -> str:
    parts = [f"本次体检共发现 {n_abnormal} 项指标异常。"]
    if critical_count:
        parts.append(f"其中 {critical_count} 项达到危急值水平,请尽快线下就医。")
    if matched_patterns:
        parts.append(f"检出组合模式:{'、'.join(matched_patterns)},建议综合评估。")
    if unknown_count:
        parts.append(f"另有 {unknown_count} 项指标无法判定,请咨询医生。")
    return "".join(parts)


async def generate_summary(items: list[ItemInterpretation], matched_patterns: list[str],
                           unknown_count: int, llm) -> str:
    n_abnormal = len(items)
    critical = sum(1 for i in items if i.status.startswith("critical"))
    try:
        prompt = load_prompt("summary")
        items_block = "\n".join(
            f"- {i.name}: {_value_text_from_item(i)}, 建议级别 {i.advice_level}"
            for i in items
        )
        messages = [{"role": "user", "content": prompt.format(
            items=items_block, matched_patterns="、".join(matched_patterns) or "无",
            unknown_count=unknown_count,
        )}]
        return (await llm.chat(messages, temperature=0.1)).strip()
    except LLMError:
        log.warning("summary_llm_failed_using_template")
        return template_summary(n_abnormal, critical, matched_patterns, unknown_count)


def _value_text_from_item(i: ItemInterpretation) -> str:
    return i.value_text


def build_advice_summary(items: list[ItemInterpretation]) -> str:
    """代码排序:urgent > specialist > recheck > lifestyle,同级按危急值优先。"""
    order = {lvl: i for i, lvl in enumerate(ADVICE_LEVELS)}

    def key(it: ItemInterpretation):
        return (order.get(it.advice_level, 99), 0 if it.status.startswith("critical") else 1)

    lines = []
    for it in sorted(items, key=key):
        label = {"urgent": "尽快就医", "specialist": "专科就诊",
                 "recheck": "定期复查", "lifestyle": "生活方式调整"}.get(it.advice_level, "随访")
        lines.append(f"【{label}】{it.name}: {it.advice}")
    return "\n".join(lines)


def assemble_interpretation(summary: str, items: list[ItemInterpretation],
                            advice_summary: str, degraded: bool) -> dict:
    return {
        "summary": summary,
        "items": [
            {"indicator_code": i.indicator_code, "name": i.name, "status": i.status,
             "value_text": i.value_text, "meaning": i.meaning, "risks": i.risks,
             "advice_level": i.advice_level, "advice": i.advice, "evidence_ids": i.evidence_ids}
            for i in items
        ],
        "advice_summary": advice_summary,
        "disclaimer": DISCLAIMER,
        "degraded": degraded,
    }


def build_degraded_interpretation(judgments: list[ItemJudgment]) -> dict:
    """安全降级版:仅数值对照表 + 强建议线下就医(spec §4.2/§5.5)。"""
    abnormal = [j for j in judgments if j.status.value in
                ("high", "low", "critical_high", "critical_low")]
    table = "\n".join(
        f"- {j.name}: {_value_text(j)},判定 {_status_cn(j.status)}" for j in abnormal
    ) or "- 无异常项"
    summary = (
        f"因内容安全校验未通过,本次仅提供数值对照结果,不提供解释性内容。\n{table}\n"
        "请携带本报告尽快线下就医咨询,由执业医师评估。"
    )
    return assemble_interpretation(
        summary=summary,
        items=[ItemInterpretation(
            indicator_code=j.indicator_code, name=j.name, status=j.status.value,
            value_text=_value_text(j), meaning="", risks=[],
            advice_level="urgent" if j.critical else "specialist",
            advice="请线下就医咨询。", evidence_ids=[]) for j in abnormal],
        advice_summary="所有异常项请以线下医师意见为准。",
        degraded=True,
    )
```

- [ ] **Step 4: 写三个 prompt 模板**

`llm/prompts/interpret_item.txt`:

```
你是体检报告解读助手。基于以下输入,对单个异常检验项生成解读。只输出 JSON,不要任何解释。

{{
  "meaning": "该项数值的通俗含义(50~120字)",
  "risks": ["可能相关的风险方向,最多3条,非诊断表述"],
  "advice_level": "lifestyle|recheck|specialist|urgent",
  "advice": "该级别的具体建议(30~80字)",
  "evidence_ids": ["引用的证据编号,如 e0;未引用填 []"]
}}

输入:
- 项目: {name}
- 判定: {status}
- 结果: {value}
- 知识图谱事实: {kg_facts}
- 检索证据:
{evidence}

硬性规则:
1. 数值、区间必须与输入完全一致,禁止改写或新增数值。
2. 风险必须能从知识图谱事实或检索证据中找到依据;证据不足时只说数值含义,risks 填 []。
3. 禁止"确诊/诊断为/你患有"等诊断用语;禁止任何药物与剂量建议。
4. advice 里若引用证据,evidence_ids 必须对应列出。
5. 危急值时 advice_level 必须为 urgent,advice 必须包含"尽快就医"。
```

`llm/prompts/summary.txt`:

```
你是体检报告解读助手。基于以下逐项解读结果,写一段总体结论(150~250字),直接输出文本,不要 JSON、不要标题。

逐项解读:
{items}

组合模式: {matched_patterns}
无法判定的项数: {unknown_count}

硬性规则:
1. 只归纳输入中已有的信息,禁止新增数值或结论。
2. 有危急值时必须在开头强调尽快就医。
3. 禁止诊断用语与用药建议。
4. 语气客观、分点清晰,不要夸大也不要淡化。
```

`llm/prompts/followup_smooth.txt`:

```
你是体检报告解读助手。以下是一个复查计划,请只润色每项的"依据"文字(保持事实不变,措辞更自然流畅),输出 JSON: {{"0": "新的依据文字", "1": "..."}}。只输出 JSON。

复查计划:
{items}
```

- [ ] **Step 5: 实现 followup_plan.py**

```python
"""复查计划:规则收集(项目/时间窗/科室/依据)+ 模板渲染 + LLM 仅润色依据槽位。spec §5.6。"""
from dataclasses import dataclass

from report_agent.knowledge.kg_client import IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus

log = get_logger(__name__)

_ABNORMAL = {ItemStatus.HIGH, ItemStatus.LOW, ItemStatus.CRITICAL_HIGH, ItemStatus.CRITICAL_LOW}


@dataclass
class FollowupItem:
    item: str       # 复查项目
    timeframe: str  # 时间窗
    department: str  # 挂号科室
    basis: str      # 依据(指标+判定+引用)


@dataclass
class FollowupPlanDoc:
    items: list[FollowupItem]
    degraded: bool


def build_followup_items(
    judgments: list[ItemJudgment],
    matched_patterns: list[tuple],
    ctx_by_code: dict[str, IndicatorContext],
) -> list[FollowupItem]:
    items: list[FollowupItem] = []
    for j in judgments:
        if j.status not in _ABNORMAL:
            continue
        ctx = ctx_by_code.get(j.indicator_code or "")
        if j.critical:
            items.append(FollowupItem(
                item=f"{j.name}(危急值)复查",
                timeframe="立即",
                department=next((d for d in (ctx.departments if ctx else [])), "急诊/相应专科"),
                basis=f"{j.name} 达危急值水平,需立即评估",
            ))
            continue
        recheck = next(
            (iv for iv in (ctx.interventions if ctx else []) if iv.level in ("recheck", "specialist")),
            None,
        )
        items.append(FollowupItem(
            item=recheck.text if recheck else f"复查{j.name}",
            timeframe=recheck.timeframe if recheck and recheck.timeframe else "遵医嘱",
            department="、".join(recheck.departments) if recheck and recheck.departments
            else ("、".join(ctx.departments) if ctx and ctx.departments else "相应专科"),
            basis=f"{j.name} 判定{'升高' if j.status.value.endswith('high') else '降低'},建议复查",
        ))
    for pattern, hits in matched_patterns:
        items.append(FollowupItem(
            item=f"组合模式复查:{pattern.name}",
            timeframe="遵医嘱",
            department="相应专科",
            basis=f"命中组合模式 {pattern.name}(涉及:{'、'.join(h.name for h in hits)})",
        ))
    return items


async def smooth_basis(items: list[FollowupItem], llm) -> list[FollowupItem] | None:
    """LLM 仅润色 basis 槽位;输出 schema 校验(必须覆盖全部下标),失败返回 None 回退模板。"""
    if not items or llm is None:
        return None
    prompt = load_prompt("followup_smooth")
    block = "\n".join(f"{i}. 项目:{it.item} | 时间窗:{it.timeframe} | 科室:{it.department} "
                      f"| 依据:{it.basis}" for i, it in enumerate(items))
    try:
        data = await llm.complete_json(
            [{"role": "user", "content": prompt.format(items=block)}]
        )
        if set(data.keys()) != {str(i) for i in range(len(items))}:
            log.warning("followup_smooth_schema_mismatch")
            return None
        out = []
        for i, it in enumerate(items):
            out.append(FollowupItem(item=it.item, timeframe=it.timeframe,
                                    department=it.department, basis=data[str(i)]))
        return out
    except LLMError:
        log.warning("followup_smooth_llm_failed_using_template")
        return None


async def build_followup_plan(judgments, matched_patterns, ctx_by_code, llm) -> FollowupPlanDoc:
    items = build_followup_items(judgments, matched_patterns, ctx_by_code)
    try:
        smoothed = await smooth_basis(items, llm)
    except Exception as e:  # noqa: BLE001
        log.warning("followup_plan_error", error=str(e))
        smoothed = None
    return FollowupPlanDoc(items=smoothed or items, degraded=smoothed is None)
```

- [ ] **Step 6: 追加三个阶段到 stages.py**

在 `stages.py` 末尾追加(register 装饰器自动注册):

```python
@register("retrieve")
async def retrieve_stage(ctx: StageContext) -> dict:
    """逐异常项并发检索(信号量限流)。单项失败 → 该项占位证据(spec §11)。"""
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
    from report_agent.retrieval.hybrid import RetrievalQuery

    judgments = ctx.checkpoints.get("compare", {}).get("judgments", [])
    abnormal = [
        ItemJudgment(indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
                     value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
                     ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
                     range_source=j["range_source"])
        for j in judgments
        if j["status"] in ("high", "low", "critical_high", "critical_low")
    ]
    sem = asyncio.Semaphore(ctx.deps.settings.retrieve_concurrency)

    async def one(j: ItemJudgment) -> tuple[str, list[dict]]:
        async with sem:
            q = RetrievalQuery(
                text=f"{j.name} {j.status.value} 健康风险", indicator_code=j.indicator_code,
                direction="high" if j.status.value.endswith("high") else "low",
            )
            evs = await ctx.deps.retriever.search(q)
            if not evs:
                from report_agent.retrieval.hybrid import Evidence

                evs = [Evidence(text="知识库未覆盖该项,建议线下咨询医生。", source="placeholder")]
            key = j.indicator_code or j.name
            return key, [
                {"text": e.text, "source": e.source, "entity_type": e.entity_type,
                 "entity_id": e.entity_id, "title": e.title, "score": e.score,
                 "rrf_score": e.rrf_score, "rrf_sources": e.rrf_sources}
                for e in evs
            ]

    results = await asyncio.gather(*(one(j) for j in abnormal))
    return {"evidence": dict(results)}


@register("generate")
async def generate_stage(ctx: StageContext) -> dict:
    from report_agent.pipeline.interpret import (
        ItemInterpretation,
        assemble_interpretation,
        generate_summary,
        interpret_item,
        build_advice_summary,
    )
    from report_agent.pipeline.rule_compare import ItemStatus
    from report_agent.retrieval.hybrid import Evidence

    judgments = ctx.checkpoints.get("compare", {}).get("judgments", [])
    evidence = ctx.checkpoints.get("retrieve", {}).get("evidence", {})
    abnormal = [j for j in judgments if j["status"] in
                ("high", "low", "critical_high", "critical_low")]

    async def one(j: dict) -> ItemInterpretation:
        key = j["indicator_code"] or j["name"]
        evs = [Evidence(text=e["text"], source=e["source"], entity_type=e["entity_type"],
                        entity_id=e["entity_id"], title=e["title"], score=e["score"],
                        rrf_score=e["rrf_score"], rrf_sources=e["rrf_sources"] or [])
               for e in evidence.get(key, [])]
        kctx = None
        if j["indicator_code"]:
            kctx = ctx.deps.kg.indicator_context(j["indicator_code"])
        item_judgment = __import__("report_agent.pipeline.rule_compare",
                                   fromlist=["ItemJudgment"]).ItemJudgment(
            indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
            value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
            ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
            range_source=j["range_source"],
        )
        return await interpret_item(item_judgment, kctx, evs, ctx.deps.llms.chat)

    items = await asyncio.gather(*(one(j) for j in abnormal))
    matched = [m[0] for m in ctx.checkpoints.get("compare", {}).get("matched_patterns", [])]
    unknown = sum(1 for j in judgments if j["status"] == "unknown")
    summary = await generate_summary(items, matched, unknown, ctx.deps.llms.chat)
    doc = assemble_interpretation(summary, items, build_advice_summary(items), degraded=False)
    return {"doc": doc, "interpretations": [
        {"indicator_code": i.indicator_code, "name": i.name, "status": i.status,
         "value_text": i.value_text, "meaning": i.meaning, "risks": i.risks,
         "advice_level": i.advice_level, "advice": i.advice, "evidence_ids": i.evidence_ids}
        for i in items
    ]}


@register("plan")
async def plan_stage(ctx: StageContext) -> dict:
    """复查计划生成 + 落库解读与复查单。"""
    from report_agent.pipeline.followup_plan import build_followup_plan
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus

    judgments = [
        ItemJudgment(indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
                     value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
                     ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
                     range_source=j["range_source"])
        for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
    ]
    matched = [
        (name, [j for j in judgments if j.name in hits])
        for name, hits in ctx.checkpoints.get("compare", {}).get("matched_patterns", [])
    ]
    ctx_by_code = {}
    for j in judgments:
        if j.indicator_code and j.indicator_code not in ctx_by_code:
            ctx_by_code[j.indicator_code] = ctx.deps.kg.indicator_context(j.indicator_code)
    plan = await build_followup_plan(judgments, matched, ctx_by_code, ctx.deps.llms.chat)

    doc = ctx.checkpoints.get("generate", {}).get("doc", {})
    degraded = bool(ctx.checkpoints.get("guardrail", {}).get("degraded", False)) or plan.degraded
    await ctx.db.save_interpretation(ctx.report["id"], ctx.task_id, {**doc, "degraded": degraded})
    await ctx.db.save_followup(
        ctx.report["id"], ctx.task_id,
        {"items": [
            {"item": i.item, "timeframe": i.timeframe, "department": i.department, "basis": i.basis}
            for i in plan.items
        ], "degraded": plan.degraded},
    )
    return {"n_items": len(plan.items), "degraded": plan.degraded}
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_pipeline/test_interpret.py tests/unit_pipeline/test_followup_plan.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add per-item interpretation, summary with template fallback and template followup plan"
```

---

### Task 13: 安全护栏与审计(guardrail 阶段)

**Files:**
- Create: `backend/src/report_agent/guardrails/__init__.py`, `backend/src/report_agent/guardrails/rules.py`, `backend/src/report_agent/guardrails/reviewer.py`, `backend/src/report_agent/guardrails/enforce.py`, `backend/src/report_agent/guardrails/audit.py`, `backend/src/report_agent/llm/prompts/reviewer.txt`, `backend/tests/unit_guardrails/__init__.py`, `backend/tests/unit_guardrails/test_rules.py`, `backend/tests/unit_guardrails/test_enforce.py`
- Modify: `backend/src/report_agent/pipeline/stages.py`(追加 guardrail 阶段)

**Interfaces:**
- Consumes: Task 3 的 `DeepSeekClient`, Task 12 的 `build_degraded_interpretation`, Task 2 的 `AuditEvent`
- Produces:
  - `Verdict(PASS/SUSPECT/BLOCK)`、`GuardrailContext(allowed_numbers, require_disclaimer, require_critical_warning)`、`GuardrailResult(verdict, findings)`
  - `rule_guardrail(text: str, ctx: GuardrailContext) -> GuardrailResult`(纯代码 4 项检查,可单测)
  - `async review_output(text: str, context: str, llm) -> ReviewResult(passed, issues)`(二次审核,仅风险信号触发)
  - `async enforce_guardrail(text, ctx, regen_fn, degrade_fn, llm, audit) -> tuple[str, bool]`(重生成 1 次 → 降级版)
  - `class AuditLog(session_factory)`: `async log(event_type, payload=None, report_id=None, task_id=None, session_id=None)`(写失败只告警不抛)
  - stages:`guardrail_stage(ctx) -> dict`(payload 含 degraded/findings)

- [ ] **Step 1: 写失败测试**

```python
import asyncio

from report_agent.guardrails.enforce import enforce_guardrail
from report_agent.guardrails.rules import GuardrailContext, Verdict, rule_guardrail


def _ctx(**kw) -> GuardrailContext:
    base = dict(allowed_numbers=[6.2, 3.9, 6.1], require_disclaimer=True,
                require_critical_warning=False)
    base.update(kw)
    return GuardrailContext(**base)


def test_diagnosis_term_blocks():
    r = rule_guardrail("您可能被确诊为糖尿病,需要治疗。", _ctx())
    assert r.verdict == Verdict.BLOCK


def test_medication_and_dose_blocks():
    r = rule_guardrail("建议每日服用二甲双胍 500mg。", _ctx())
    assert r.verdict == Verdict.BLOCK
    r2 = rule_guardrail("每次2片,每天3次。", _ctx())
    assert r2.verdict == Verdict.BLOCK


def test_missing_disclaimer_blocks():
    r = rule_guardrail("您的血糖正常。", _ctx())
    assert r.verdict == Verdict.BLOCK
    assert any("免责" in f for f in r.findings)


def test_numeric_inconsistency_suspects():
    r = rule_guardrail("您的血糖 9.9 mmol/L,超出参考上限。\n本内容不构成医学诊断。", _ctx())
    assert r.verdict == Verdict.SUSPECT  # 9.9 不在白名单 → 触发审核
    r2 = rule_guardrail("您的血糖 6.2 mmol/L,略高于上限 6.1。\n本内容不构成医学诊断。", _ctx())
    assert r2.verdict == Verdict.PASS  # 数值都在白名单


def test_critical_warning_required():
    r = rule_guardrail("血糖危急,请关注。\n本内容不构成医学诊断。",
                       _ctx(require_critical_warning=True))
    assert r.verdict == Verdict.BLOCK  # 缺"尽快就医"强提醒


class FakeReviewLLM:
    def __init__(self, passed=True):
        self.passed = passed

    async def complete_json(self, messages, retry_feedback=True):
        return {"passed": self.passed, "issues": [] if self.passed else ["语义越界"]}


class FakeAudit:
    def __init__(self):
        self.events = []

    async def log(self, event_type, payload=None, report_id=None, task_id=None, session_id=None):
        self.events.append(event_type)


def _regen(feedback):
    async def fn(feedback):
        return "重新生成的安全内容。\n本内容不构成医学诊断。"
    return fn


def _degrade():
    async def fn():
        return "降级版:仅数值对照。\n本内容不构成医学诊断。"
    return fn


def test_enforce_pass_through():
    async def go():
        text, degraded = await enforce_guardrail(
            "您的血糖 6.2,略高于 6.1。\n本内容不构成医学诊断。", _ctx(),
            _regen(None), _degrade(), FakeReviewLLM(), FakeAudit())
        return text, degraded
    text, degraded = asyncio.run(go())
    assert not degraded and "6.2" in text


def test_enforce_suspect_review_pass_releases():
    async def go():
        text, degraded = await enforce_guardrail(
            "您的血糖 9.9 mmol/L。\n本内容不构成医学诊断。", _ctx(),
            _regen(None), _degrade(), FakeReviewLLM(passed=True), FakeAudit())
        return text, degraded
    text, degraded = asyncio.run(go())
    assert not degraded and "9.9" in text


def test_enforce_suspect_review_fail_regen_once_then_degrade():
    audit = FakeAudit()
    calls = []

    async def regen(feedback):
        calls.append(feedback)
        return "您被确诊为糖尿病。\n本内容不构成医学诊断。"  # 重生成仍违规

    async def degrade():
        return "降级版:仅数值对照。\n本内容不构成医学诊断。"

    async def go():
        text, degraded = await enforce_guardrail(
            "您的血糖 9.9 mmol/L。\n本内容不构成医学诊断。", _ctx(),
            regen, degrade, FakeReviewLLM(passed=False), audit)
        return text, degraded
    text, degraded = asyncio.run(go())
    assert degraded and text.startswith("降级版")
    assert len(calls) == 1  # 只重生成一次
    assert "review_failed" in audit.events and "degraded_output" in audit.events
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_guardrails -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 rules.py(规则护栏 4 项)**

```python
"""规则护栏(纯代码,可单测):诊断用语/处方剂量/必含元素/数值一致性。spec §4.2/§5.5。"""
import re
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    SUSPECT = "suspect"
    BLOCK = "block"


@dataclass
class GuardrailContext:
    allowed_numbers: list[float] = field(default_factory=list)
    require_disclaimer: bool = False
    require_critical_warning: bool = False


@dataclass
class GuardrailResult:
    verdict: Verdict
    findings: list[str] = field(default_factory=list)


DIAGNOSIS_TERMS = [
    "确诊", "诊断为", "你患有", "您患有", "你得了", "您得了", "患有", "患了",
    "诊断结论", "明确诊断",
]
MEDICATION_TERMS = [
    "二甲双胍", "阿司匹林", "阿托伐他汀", "瑞舒伐他汀", "辛伐他汀", "苯磺酸氨氯地平",
    "硝苯地平", "美托洛尔", "缬沙坦", "厄贝沙坦", "格列美脲", "格列齐特", "胰岛素",
    "左甲状腺素", "优甲乐", "非布司他", "别嘌醇", "秋水仙碱",
]
DOSE_RE = re.compile(
    r"(?:每次|每日|每天|一天|一次|一日)?\s*\d+(?:\.\d+)?\s*(?:mg|毫克|g|克|μg|微克|片|粒|袋|支|ml|毫升)"
)
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def check_diagnosis(text: str) -> list[str]:
    return [f"诊断用语: {t}" for t in DIAGNOSIS_TERMS if t in text]


def check_medication(text: str) -> list[str]:
    hits = [f"药品名称: {t}" for t in MEDICATION_TERMS if t in text]
    if DOSE_RE.search(text):
        hits.append("剂量表述")
    return hits


def check_required(text: str, ctx: GuardrailContext) -> list[str]:
    missing = []
    if ctx.require_disclaimer and ("不构成医学诊断" not in text and "免责声明" not in text):
        missing.append("缺少免责声明")
    if ctx.require_critical_warning and "尽快就医" not in text:
        missing.append("缺少危急值强提醒(尽快就医)")
    return missing


def check_numeric_consistency(text: str, allowed: list[float]) -> list[str]:
    allowed_set = {round(x, 6) for x in allowed}
    violations = []
    for m in NUM_RE.finditer(text):
        try:
            v = float(m.group())
        except ValueError:
            continue
        if round(v, 6) not in allowed_set:
            violations.append(f"越界数值: {m.group()}")
    return violations


def rule_guardrail(text: str, ctx: GuardrailContext) -> GuardrailResult:
    findings = check_diagnosis(text) + check_medication(text) + check_required(text, ctx)
    numeric = check_numeric_consistency(text, ctx.allowed_numbers)
    if findings:
        return GuardrailResult(Verdict.BLOCK, findings)
    if numeric:
        return GuardrailResult(Verdict.SUSPECT, numeric)
    return GuardrailResult(Verdict.PASS, [])
```

- [ ] **Step 4: 实现 reviewer.py + enforce.py + audit.py + reviewer.txt**

```python
# guardrails/reviewer.py
from dataclasses import dataclass, field

from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger

log = get_logger(__name__)


@dataclass
class ReviewResult:
    passed: bool
    issues: list[str] = field(default_factory=list)


async def review_output(text: str, context: str, llm) -> ReviewResult:
    """二次 LLM 审核(仅风险信号触发,独立审核 prompt)。LLMError → 不通过(保守)。"""
    prompt = load_prompt("reviewer")
    try:
        data = await llm.complete_json(
            [{"role": "user", "content": prompt.format(text=text, context=context)}]
        )
        return ReviewResult(passed=bool(data.get("passed")), issues=list(data.get("issues") or []))
    except LLMError:
        log.warning("reviewer_llm_failed_conservative_fail")
        return ReviewResult(passed=False, issues=["审核服务不可用,保守拒绝"])
```

```python
# guardrails/enforce.py
"""护栏执行流程:规则 → (suspect: LLM 审核)→ 重生成 1 次 → 安全降级版。spec §5.5。"""
from report_agent.guardrails.reviewer import review_output
from report_agent.guardrails.rules import GuardrailContext, Verdict, rule_guardrail


async def enforce_guardrail(text, ctx: GuardrailContext, regen_fn, degrade_fn, llm, audit) -> tuple[str, bool]:
    """返回 (最终文本, 是否降级)。重生成最多 1 次,仍不通过 → degrade_fn。"""
    result = rule_guardrail(text, ctx)
    if result.verdict == Verdict.PASS:
        return text, False

    if result.verdict == Verdict.SUSPECT:
        await audit.log("guardrail_suspect", {"findings": result.findings})
        review = await review_output(text, f"规则护栏疑似问题: {result.findings}", llm)
        if review.passed:
            return text, False
        await audit.log("review_failed", {"issues": review.issues, "findings": result.findings})
        feedback = result.findings + review.issues
    else:
        await audit.log("guardrail_block", {"findings": result.findings})
        feedback = result.findings

    try:
        regenerated = await regen_fn(feedback=feedback)
    except Exception as e:  # noqa: BLE001 —— 重生成失败直接降级
        regenerated = None
    if regenerated is not None:
        again = rule_guardrail(regenerated, ctx)
        if again.verdict == Verdict.PASS:
            return regenerated, False
        if again.verdict == Verdict.SUSPECT:
            review2 = await review_output(regenerated, f"规则护栏疑似问题: {again.findings}", llm)
            if review2.passed:
                return regenerated, False

    degraded = await degrade_fn()
    await audit.log("degraded_output", {"reason": "guardrail_not_passed"})
    return degraded, True
```

```python
# guardrails/audit.py
"""审计事件写入。写失败只告警不抛——审计不阻塞用户响应(spec §4.4/§10)。"""
from report_agent.db.models import AuditEvent
from report_agent.observability import get_logger

log = get_logger(__name__)


class AuditLog:
    def __init__(self, session_factory):
        self._factory = session_factory

    async def log(self, event_type: str, payload: dict | None = None, report_id: str | None = None,
                  task_id: str | None = None, session_id: str | None = None) -> None:
        try:
            async with self._factory() as s:
                s.add(AuditEvent(
                    event_type=event_type, payload=payload or {},
                    report_id=report_id, task_id=task_id, session_id=session_id,
                ))
                await s.commit()
        except Exception as e:  # noqa: BLE001
            log.error("audit_write_failed", event_type=event_type, error=str(e))
```

`llm/prompts/reviewer.txt`:

```
你是医疗内容安全审核员。审核下面的文本是否越过安全边界,只输出 JSON: {{"passed": true/false, "issues": ["问题描述"]}}。

安全边界:
1. 不得给出诊断结论("确诊/你患有"等)
2. 不得给出用药与剂量建议
3. 数值必须与给定上下文一致(上下文: {context})
4. 必须含免责声明(若上下文要求)

待审核文本:
{text}
```

- [ ] **Step 5: 追加 guardrail 阶段到 stages.py**

```python
@register("guardrail")
async def guardrail_stage(ctx: StageContext) -> dict:
    """对生成产物执行护栏:总评 + 逐项解读逐一过检,重生成/降级由 enforce 闭环。"""
    from report_agent.guardrails.audit import AuditLog
    from report_agent.guardrails.enforce import enforce_guardrail
    from report_agent.guardrails.rules import GuardrailContext
    from report_agent.pipeline.interpret import build_degraded_interpretation
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus

    doc = dict(ctx.checkpoints.get("generate", {}).get("doc", {}))
    judgments = [
        ItemJudgment(indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
                     value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
                     ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
                     range_source=j["range_source"])
        for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
    ]
    allowed = []
    for j in judgments:
        allowed.extend([x for x in (j.value_num, j.ref_low, j.ref_high) if x is not None])
    report = ctx.report
    if report.get("age") is not None:
        allowed.append(float(report["age"]))
    has_critical = any(j["critical"] for j in
                       ctx.checkpoints.get("compare", {}).get("judgments", []))
    gctx = GuardrailContext(allowed_numbers=allowed, require_disclaimer=True,
                            require_critical_warning=has_critical)
    audit = AuditLog(ctx.deps.session_factory)
    final_doc = dict(doc)
    degraded = False
    findings_all = []

    # 1) 总评
    async def regen_summary(feedback):
        from report_agent.pipeline.interpret import generate_summary

        items = doc.get("items", [])
        matched = [m[0] for m in ctx.checkpoints.get("compare", {}).get("matched_patterns", [])]
        unknown = sum(1 for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
                      if j["status"] == "unknown")
        return await generate_summary(items, matched, unknown, ctx.deps.llms.chat)

    async def degrade_summary():
        return build_degraded_interpretation(judgments)["summary"]

    summary, sum_degraded = await enforce_guardrail(
        doc.get("summary", ""), gctx, regen_summary, degrade_summary,
        ctx.deps.llms.chat, audit,
    )
    final_doc["summary"] = summary
    degraded = degraded or sum_degraded
    findings_all.append({"part": "summary", "degraded": sum_degraded})

    # 2) 逐项解读(meaning+advice 合并检)
    new_items = []
    for item in doc.get("items", []):
        text = f"{item['meaning']}\n建议:{item['advice']}"

        async def regen_item(feedback, item=item):
            # 单项重生成:重跑 interpret_item(证据在 retrieve checkpoint)
            from report_agent.pipeline.interpret import interpret_item
            from report_agent.retrieval.hybrid import Evidence

            key = item["indicator_code"] or item["name"]
            evs = [Evidence(**e) for e in
                   ctx.checkpoints.get("retrieve", {}).get("evidence", {}).get(key, [])]
            j0 = next(j for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
                      if (j["indicator_code"] or j["name"]) == key)
            jj = ItemJudgment(indicator_code=j0["indicator_code"], name=j0["name"],
                              value_num=j0["value_num"], value_text=j0["value_text"],
                              unit=j0["unit"], status=ItemStatus(j0["status"]),
                              ref_low=j0["ref_low"], ref_high=j0["ref_high"],
                              critical=j0["critical"], range_source=j0["range_source"])
            kctx = ctx.deps.kg.indicator_context(key) if j0["indicator_code"] else None
            new_i = await interpret_item(jj, kctx, evs, ctx.deps.llms.chat)
            return f"{new_i.meaning}\n建议:{new_i.advice}"

        async def degrade_item():
            return f"{item['name']}检测结果请以线下医师意见为准。"

        new_text, item_degraded = await enforce_guardrail(
            text, gctx, regen_item, degrade_item, ctx.deps.llms.chat, audit,
        )
        if item_degraded or (new_text != text):
            parts = new_text.split("\n建议:", 1)
            item["meaning"] = parts[0]
            if len(parts) > 1:
                item["advice"] = parts[1]
        degraded = degraded or item_degraded
        findings_all.append({"part": item["name"], "degraded": item_degraded})
        new_items.append(item)

    final_doc["items"] = new_items
    final_doc["degraded"] = degraded
    if degraded:
        final_doc["advice_summary"] = "所有异常项请以线下医师意见为准。"
    return {"doc": final_doc, "degraded": degraded, "findings": findings_all}
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_guardrails -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add rule guardrails, LLM reviewer and audit with regenerate-once-degrade flow"
```

---

### Task 14: 报告 API 与异步任务接线

**Files:**
- Modify: `backend/src/report_agent/config.py`(加 `upload_dir`)、`backend/.env.example`(加 `UPLOAD_DIR`)、`backend/src/report_agent/api/app.py`(lifespan:build deps + 启动恢复)、`backend/src/report_agent/pipeline/db_access.py`(追加 API 所需方法)
- Create: `backend/src/report_agent/api/reports.py`, `backend/src/report_agent/api/tasks.py`, `backend/tests/unit_api/test_reports_api.py`, `backend/tests/unit_api/test_tasks_api.py`

**Interfaces:**
- Consumes: Task 11 的 `PipelineRunner`/`TaskService`/`DataAccess`/`build_deps`, Task 6 的 `ReportMeta`/`RawReportItem`
- Produces(API,spec §7):
  - `POST /api/reports`(multipart `file` 或 JSON 手动录入)→ `{report_id, task_id}`,后台 `asyncio.create_task(runner.run_task)`
  - `GET /api/reports/{id}`(元数据+原始项+归一化项含判定)
  - `PATCH /api/reports/{id}/meta`(补录 sex/age;若任务 awaiting_meta → resume + 重跑)
  - `GET /api/tasks/{task_id}`(status/stage/timings/error/checkpoints 键)
  - `GET /api/reports/{id}/interpretation`、`GET /api/reports/{id}/followup-plan`
  - DataAccess 追加:`create_report(source, file_path, meta) -> str`、`get_report_detail(report_id) -> dict | None`、`get_task_row(task_id) -> dict | None`、`get_task_for_report(report_id) -> dict | None`、`get_interpretation(report_id) -> dict | None`、`get_followup(report_id) -> dict | None`

- [ ] **Step 1: config 加 upload_dir**

`config.py` 的 Settings 增加:`upload_dir: str = "./uploads"`;`.env.example` 增加 `UPLOAD_DIR=./uploads`。

- [ ] **Step 2: 写失败测试(app.state 注入 fake,不碰 DB)**

```python
from fastapi.testclient import TestClient

from report_agent.api.app import create_app


class FakeRunner:
    def __init__(self):
        self.ran = []

    async def run_task(self, task_id):
        self.ran.append(task_id)


class FakeTasks:
    async def create(self, report_id):
        return type("T", (), {"id": "task-1", "report_id": report_id, "status": "pending",
                              "stage": None, "checkpoints": {}, "timings": {}, "error": None})()

    async def get(self, task_id):
        return type("T", (), {"id": task_id, "report_id": "r1", "status": "completed",
                              "stage": "done", "checkpoints": {"parse": {}}, "timings": {"parse": 1.2},
                              "error": None})()

    async def resume_from_meta(self, task_id):
        pass


class FakeDB:
    def __init__(self):
        self.report = {"id": "r1", "source": "manual", "file_path": None, "institution": None,
                       "report_date": None, "sex": None, "age": None}

    async def create_report(self, source, file_path, meta):
        self.report.update({"source": source, "file_path": file_path,
                            "sex": meta.sex, "age": meta.age})
        return "r1"

    async def save_raw_items(self, report_id, items):
        self.items = items

    async def get_report_detail(self, report_id):
        return {"meta": self.report, "items": [], "normalized": []}

    async def get_task_for_report(self, report_id):
        return {"id": "task-1", "status": "running", "stage": "parse"}

    async def update_report_meta(self, report_id, meta):
        self.report["sex"], self.report["age"] = meta.sex, meta.age

    async def get_interpretation(self, report_id):
        return {"summary": "总评", "items": [], "advice_summary": "", "disclaimer": "x",
                "degraded": False}

    async def get_followup(self, report_id):
        return {"items": [], "degraded": False}


def _client():
    app = create_app(deps_builder=lambda settings: object())  # 测试:不连真服务
    app.state.runner = FakeRunner()
    app.state.tasks = FakeTasks()
    app.state.db_access = FakeDB()
    return TestClient(app), app


def test_create_manual_report_and_run_task():
    client, app = _client()
    payload = {
        "meta": {"sex": "male", "age": 45, "source": "manual"},
        "items": [{"name": "空腹血糖", "value_text": "6.2", "value_num": 6.2,
                   "unit": "mmol/L", "ref_range_text": "3.9-6.1", "abnormal_flag": None}],
    }
    resp = client.post("/api/reports", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] == "r1" and body["task_id"] == "task-1"
    assert app.state.db_access.items[0].name == "空腹血糖"
    # 后台任务被触发(异步)
    import asyncio

    assert app.state.runner.ran == ["task-1"]  # create_task 立即调度,单测环境同步完成


def test_patch_meta_resumes_awaiting_task():
    client, app = _client()
    resp = client.patch("/api/reports/r1/meta", json={"sex": "female", "age": 30})
    assert resp.status_code == 200
    assert app.state.db_access.report["sex"] == "female"
    assert app.state.runner.ran == ["task-1"]  # 补录后重新入队


def test_get_report_detail_and_task():
    client, _ = _client()
    assert client.get("/api/reports/r1").status_code == 200
    r = client.get("/api/tasks/task-1")
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.get("/api/reports/r1/interpretation").json()["summary"] == "总评"
    assert client.get("/api/reports/r1/followup-plan").status_code == 200
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_api/test_reports_api.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 4: 实现 app.py 与路由**

```python
# api/app.py(重写)
"""FastAPI 工厂。lifespan:build deps → runner + 启动恢复;外部服务不可用只告警不崩。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from report_agent.config import get_settings
from report_agent.db.session import session_factory
from report_agent.observability import get_logger, setup_logging
from report_agent.pipeline.deps import build_deps
from report_agent.pipeline.runner import PipelineRunner
from report_agent.pipeline.tasks import TaskService

log = get_logger(__name__)


def create_app(deps_builder=None) -> FastAPI:
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        try:
            deps = await (deps_builder or build_deps)(settings)
            app.state.deps = deps
            app.state.db_access = deps.db
            app.state.tasks = TaskService(session_factory)
            app.state.runner = PipelineRunner(app.state.tasks, deps.db, deps)
            app.state.runner.recover_on_startup()
            log.info("app_started")
        except Exception as e:  # noqa: BLE001 —— 依赖服务不可用时应用仍可启动(降级)
            log.error("app_startup_degraded", error=str(e))
        yield

    app = FastAPI(title="report-agent", version="0.1.0", lifespan=lifespan)
    from report_agent.api import health, reports, tasks  # noqa: F401

    app.include_router(health.router)
    app.include_router(reports.router)
    app.include_router(tasks.router)
    return app
```

```python
# api/reports.py
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from report_agent.observability import get_logger
from report_agent.parsing.schemas import RawReportItem, ReportMeta

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["reports"])


class ManualItem(BaseModel):
    section: str | None = None
    name: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    ref_range_text: str | None = None
    abnormal_flag: str | None = None


class ManualEntry(BaseModel):
    meta: dict = Field(default_factory=dict)
    items: list[ManualItem]


class MetaPatch(BaseModel):
    sex: str | None = None
    age: float | None = None


async def _save_upload(file: UploadFile, upload_dir: str) -> str:
    suffix = Path(file.filename or "upload").suffix or ".bin"
    path = Path(upload_dir) / f"{uuid.uuid4().hex}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return str(path)


@router.post("/reports")
async def create_report(request: Request, file: UploadFile | None = File(default=None),
                        manual: ManualEntry | None = None):
    if file is None and manual is None:
        raise HTTPException(400, "必须提供文件(file)或手动录入 JSON(manual)")
    db = request.app.state.db_access
    tasks = request.app.state.tasks
    runner = request.app.state.runner
    settings = request.app.state.deps.settings

    if file is not None:
        path = await _save_upload(file, settings.upload_dir)
        source = "pdf" if (file.filename or "").lower().endswith(".pdf") else "photo"
        report_id = await db.create_report(source, path, ReportMeta(source=source))
    else:
        meta = manual.meta or {}
        report_id = await db.create_report(
            "manual", None,
            ReportMeta(source="manual", sex=meta.get("sex"), age=meta.get("age"),
                       institution=meta.get("institution"), report_date=meta.get("report_date")),
        )
        await db.save_raw_items(report_id, [
            RawReportItem(section=it.section, name=it.name, value_text=it.value_text,
                          value_num=it.value_num, unit=it.unit,
                          ref_range_text=it.ref_range_text, abnormal_flag=it.abnormal_flag)
            for it in manual.items
        ])
    task = await tasks.create(report_id)
    asyncio.create_task(runner.run_task(task.id))
    log.info("report_created", report_id=report_id, task_id=task.id, source=file and "upload")
    return {"report_id": report_id, "task_id": task.id}


@router.get("/reports/{report_id}")
async def get_report(report_id: str, request: Request):
    detail = await request.app.state.db_access.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    task = await request.app.state.db_access.get_task_for_report(report_id)
    return {**detail, "task": task}


@router.patch("/reports/{report_id}/meta")
async def patch_meta(report_id: str, patch: MetaPatch, request: Request):
    db = request.app.state.db_access
    detail = await db.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    await db.update_report_meta(report_id, ReportMeta(sex=patch.sex, age=patch.age))
    task = await db.get_task_for_report(report_id)
    if task and task["status"] == "awaiting_meta":
        await request.app.state.tasks.resume_from_meta(task["id"])
        asyncio.create_task(request.app.state.runner.run_task(task["id"]))
        log.info("task_resumed_from_meta", task_id=task["id"])
    return {"report_id": report_id, "meta_updated": True}


@router.get("/reports/{report_id}/interpretation")
async def get_interpretation(report_id: str, request: Request):
    doc = await request.app.state.db_access.get_interpretation(report_id)
    if doc is None:
        raise HTTPException(404, "解读尚未生成")
    return doc


@router.get("/reports/{report_id}/followup-plan")
async def get_followup_plan(report_id: str, request: Request):
    doc = await request.app.state.db_access.get_followup(report_id)
    if doc is None:
        raise HTTPException(404, "复查计划尚未生成")
    return doc
```

```python
# api/tasks.py
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    info = await request.app.state.tasks.get(task_id)
    if info is None:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": info.id, "report_id": info.report_id, "status": info.status,
        "stage": info.stage, "timings": info.timings,
        "completed_stages": list(info.checkpoints.keys()),
        "error": info.error,
    }
```

- [ ] **Step 5: DataAccess 追加 API 方法(db_access.py 末尾追加)**

```python
    # ===== API 层使用 =====
    async def create_report(self, source: str, file_path: str | None, meta: ReportMeta) -> str:
        from report_agent.db.models import Report

        async with self._factory() as s:
            row = Report(source=source, file_path=file_path, institution=meta.institution,
                         report_date=meta.report_date, sex=meta.sex, age=meta.age)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row.id

    async def get_report_detail(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        async with self._factory() as s:
            row = await s.get(Report, report_id)
            if row is None:
                return None
            raws = (await s.execute(
                select(RawItem).where(RawItem.report_id == report_id).order_by(RawItem.id)
            )).scalars().all()
            norms = (await s.execute(
                select(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
                .order_by(NormalizedItemRow.id)
            )).scalars().all()
            return {
                "meta": {"id": row.id, "source": row.source, "institution": row.institution,
                         "report_date": row.report_date, "sex": row.sex, "age": row.age},
                "items": [
                    {"section": r.section, "name": r.item_name, "value_text": r.value_text,
                     "value_num": r.value_num, "unit": r.unit, "ref_range_text": r.ref_range_text,
                     "abnormal_flag": r.abnormal_flag}
                    for r in raws
                ],
                "normalized": [
                    {"item_name": n.item_name, "indicator_code": n.indicator_code,
                     "value_num": n.value_num, "unit": n.unit, "status": n.status,
                     "ref_low": n.ref_low, "ref_high": n.ref_high, "critical": n.critical}
                    for n in norms
                ],
            }

    async def get_task_for_report(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        from report_agent.db.models import InterpretationTask

        async with self._factory() as s:
            row = (await s.execute(
                select(InterpretationTask).where(InterpretationTask.report_id == report_id)
                .order_by(InterpretationTask.created_at.desc()).limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"id": row.id, "status": row.status, "stage": row.stage,
                    "error": row.error}

    async def get_interpretation(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        from report_agent.db.models import InterpretationRow

        async with self._factory() as s:
            row = (await s.execute(
                select(InterpretationRow).where(InterpretationRow.report_id == report_id)
                .order_by(InterpretationRow.created_at.desc()).limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"summary": row.summary, "items": row.items,
                    "advice_summary": row.advice_summary, "disclaimer": row.disclaimer,
                    "degraded": row.degraded}

    async def get_followup(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        from report_agent.db.models import FollowupPlanRow

        async with self._factory() as s:
            row = (await s.execute(
                select(FollowupPlanRow).where(FollowupPlanRow.report_id == report_id)
                .order_by(FollowupPlanRow.created_at.desc()).limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"items": row.items, "degraded": row.degraded}
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_api/test_reports_api.py tests/unit_api/test_tasks_api.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add reports/tasks API with async pipeline wiring, meta patch resume"
```

---

### Task 15: 追问对话 Agent(LangGraph 有界)与 SSE

**Files:**
- Modify: `backend/pyproject.toml`(加 `langchain-openai>=0.2`)、`backend/src/report_agent/api/app.py`(lifespan 加 checkpointer)、`backend/src/report_agent/pipeline/db_access.py`(加会话/消息方法)
- Create: `backend/src/report_agent/chat/__init__.py`, `backend/src/report_agent/chat/tools.py`, `backend/src/report_agent/chat/agent.py`, `backend/src/report_agent/chat/sse.py`, `backend/src/report_agent/api/chat.py`, `backend/src/report_agent/llm/prompts/agent_system.txt`, `backend/tests/unit_chat/__init__.py`, `backend/tests/unit_chat/test_tools.py`, `backend/tests/unit_chat/test_agent.py`, `backend/tests/unit_api/test_chat_api.py`

**Interfaces:**
- Consumes: Task 9 的 `HybridRetriever`, Task 5 的 `KGClient`, Task 6 的 `match_indicator`, Task 7 的 `select_range`/`judge_item`, Task 13 的 `rule_guardrail`/`AuditLog`, Task 14 的 app 工厂
- Produces:
  - `make_tools(deps, report_id) -> list[callable]`(4 个 async 工具,docstring 描述入参)
  - `build_chat_agent(deps, report_id) -> CompiledStateGraph`(ToolNode + 轮数上限 + 收敛节点)
  - `async sse_stream(graph, session_id, user_message) -> AsyncIterator[dict]`(事件:token/tool_call/evidence/done/safety)
  - `POST /api/reports/{id}/chat/sessions`、`POST /api/chat/sessions/{sid}/messages`(SSE)、`GET /api/chat/sessions/{sid}/history`
  - DataAccess 追加:`create_session(report_id) -> str`、`get_session(session_id) -> dict | None`、`add_message(session_id, role, content, tool_calls=None, evidence_ids=None, guardrail_flags=None)`、`get_messages(session_id) -> list[dict]`
  - 流式护栏策略(明确设计):回答完整组装后跑规则护栏;BLOCK 或 SUSPECT+审核不通过 → 发出 `safety` 事件 + 持久化安全话术(已流出的 token 不回撤——流式约束下的保守折中,记录 audit)

- [ ] **Step 1: 写 agent_system.txt**

```
你是体检报告问答助手,回答用户关于其体检报告与健康指标的问题。

规则(必须遵守):
1. 涉及本报告数值的问题,必须先调用 get_my_report 工具获取报告数据,严禁凭记忆编造数值。
2. 指标知识(含义/风险方向/建议)必须来自 query_indicator_knowledge 或 search_knowledge 工具;
   工具返回"未检索到"或证据不足时,必须明确回答"知识库未覆盖该问题,建议咨询医生",禁止编造。
3. 禁止输出诊断结论("确诊/你患有"等);禁止任何用药与剂量建议。
4. 数值必须与工具返回的数据完全一致,禁止改写。
5. 回答简洁,分点清晰;引用知识库内容时说明依据。
6. 用户要求诊断疾病或开药时,礼貌拒绝并建议线下就医。
```

- [ ] **Step 2: 写失败测试**

```python
import asyncio

from report_agent.chat.tools import make_tools
from report_agent.knowledge.kg_client import IndicatorContext, IndicatorEntry, RangeSpec
from report_agent.parsing.normalizer import match_indicator
from report_agent.retrieval.hybrid import Evidence


class FakeKG:
    def __init__(self):
        self.entries = [IndicatorEntry(code="GLU", name="空腹血糖", aliases=["血糖"], unit="mmol/L")]
        self.specs = [RangeSpec(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                                critical_low=2.8, critical_high=22.0, unit="mmol/L", source_note=None)]

    def indicator_context(self, code):
        return IndicatorContext(code=code, name="空腹血糖")

    def range_specs(self, code):
        return self.specs if code == "GLU" else []


class FakeDB:
    async def get_report_detail(self, report_id):
        return {"meta": {"sex": "male", "age": 40}, "items": [], "normalized": [
            {"item_name": "空腹血糖", "indicator_code": "GLU", "value_num": 6.5,
             "unit": "mmol/L", "status": "high", "ref_low": 3.9, "ref_high": 6.1, "critical": False}]}


class FakeRetriever:
    async def search(self, q):
        return [Evidence(text="空腹血糖升高与糖尿病风险相关", source="dense", title="空腹血糖")]


class FakeDeps:
    def __init__(self):
        self.kg = FakeKG()
        self.db = FakeDB()
        self.retriever = FakeRetriever()


def test_get_my_report_contains_values():
    tools = {t.__name__: t for t in make_tools(FakeDeps(), "r1")}
    out = asyncio.run(tools["get_my_report"]())
    assert "空腹血糖" in out and "6.5" in out


def test_compute_reference_range_judges_value():
    tools = {t.__name__: t for t in make_tools(FakeDeps(), "r1")}
    out = asyncio.run(tools["compute_reference_range"](indicator="血糖", value=7.0))
    assert "7.0" in out and "3.9" in out and "6.1" in out
    assert "升高" in out or "high" in out


def test_query_indicator_knowledge_unmatched_returns_candidates():
    tools = {t.__name__: t for t in make_tools(FakeDeps(), "r1")}
    out = asyncio.run(tools["query_indicator_knowledge"](indicator="不存在指标"))
    assert "候选" in out or "匹配" in out  # 给出候选列表让 agent 澄清


def test_search_knowledge_empty_hints_refusal():
    class EmptyRetriever:
        async def search(self, q):
            return []

    deps = FakeDeps()
    deps.retriever = EmptyRetriever()
    tools = {t.__name__: t for t in make_tools(deps, "r1")}
    out = asyncio.run(tools["search_knowledge"](query="量子物理"))
    assert "未检索到" in out
```

```python
# test_agent.py —— 图结构:轮数上限路由
from report_agent.chat.agent import route_after_agent


class FakeMsg:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls


def test_route_goes_to_tools_when_calls_and_rounds_left():
    assert route_after_agent({"messages": [FakeMsg([{"name": "x"}])], "tool_rounds": 3}, 8) == "tools"


def test_route_forces_final_at_round_limit():
    assert route_after_agent({"messages": [FakeMsg([{"name": "x"}])], "tool_rounds": 8}, 8) == "final"


def test_route_final_when_no_tool_calls():
    assert route_after_agent({"messages": [FakeMsg(None)], "tool_rounds": 0}, 8) == "final"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv sync && uv run pytest tests/unit_chat/test_tools.py tests/unit_chat/test_agent.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 4: 实现 tools.py**

```python
"""4 个受控工具(全部只读)。工具名与 docstring 即 LLM 的工具描述。"""
from report_agent.parsing.normalizer import match_indicator
from report_agent.retrieval.hybrid import RetrievalQuery


def make_tools(deps, report_id: str) -> list[callable]:
    db = deps.db

    async def get_my_report(section: str | None = None) -> str:
        """查询本人报告的原始项与判定结果。数值类问题必须先调用。参数 section 可筛选检验分组。"""
        detail = await db.get_report_detail(report_id)
        if detail is None:
            return "未找到报告数据。"
        meta = detail["meta"]
        lines = [f"受检人: {'男' if meta['sex'] == 'male' else '女' if meta['sex'] else '未知'}, "
                 f"年龄: {meta.get('age', '未知')}"]
        for n in detail["normalized"]:
            if section and n["section"] != section:
                continue
            status_cn = {"normal": "正常", "high": "升高", "low": "降低",
                         "critical_high": "危急(高)", "critical_low": "危急(低)",
                         "unknown": "无法判定", "unmapped": "未识别", None: "未判定"}.get(n["status"], n["status"])
            ref = ""
            if n.get("ref_low") is not None or n.get("ref_high") is not None:
                ref = f"(参考 {n.get('ref_low')}~{n.get('ref_high')})"
            lines.append(f"- {n['item_name']}: {n['value_num']} {n['unit']} {ref} → {status_cn}")
        return "\n".join(lines)

    async def query_indicator_knowledge(indicator: str) -> str:
        """查询指标的知识图谱事实(含义/升高降低提示/分级建议/科室)。参数为指标名。"""
        entries = deps.kg.list_indicators()
        code = match_indicator(indicator, entries)
        if code is None:
            cands = [f"{e.name}({e.code})" for e in entries[:20]]
            return f"未匹配到指标「{indicator}」。候选指标: {'、'.join(cands)}。请换个名称再试。"
        ctx = deps.kg.indicator_context(code)
        lines = [f"指标: {ctx.name}({code})"]
        if ctx.high_suggests:
            lines.append("升高提示: " + "; ".join(f"{c.name}({c.strength})" for c in ctx.high_suggests))
        if ctx.low_suggests:
            lines.append("降低提示: " + "; ".join(f"{c.name}({c.strength})" for c in ctx.low_suggests))
        if ctx.interventions:
            lines.append("建议: " + "; ".join(
                f"[{i.level}] {i.text}({i.timeframe or '未定时限'})" for i in ctx.interventions))
        if ctx.departments:
            lines.append("科室: " + "、".join(ctx.departments))
        return "\n".join(lines)

    async def compute_reference_range(indicator: str, value: float | None = None) -> str:
        """按本报告受检人的性别/年龄查询标准参考区间,并可对给定数值做判定。"""
        entries = deps.kg.list_indicators()
        code = match_indicator(indicator, entries)
        if code is None:
            return f"未匹配到指标「{indicator}」。"
        from report_agent.pipeline.rule_compare import select_range

        detail = await db.get_report_detail(report_id)
        meta = detail["meta"] if detail else {}
        spec = select_range(deps.kg.range_specs(code), meta.get("sex"), meta.get("age"))
        if spec is None:
            return f"知识库中没有「{indicator}」的参考区间,建议咨询医生。"
        lines = [f"{indicator} 参考区间: {spec.low} ~ {spec.high} {spec.unit}"]
        if spec.critical_low is not None or spec.critical_high is not None:
            lines.append(f"危急值: <{spec.critical_low} 或 >{spec.critical_high}")
        if value is not None:
            if value >= (spec.critical_high or float("inf")):
                lines.append(f"判定: {value} 达到危急值水平,需尽快就医。")
            elif value <= (spec.critical_low or float("-inf")):
                lines.append(f"判定: {value} 达到危急值水平,需尽快就医。")
            elif spec.high is not None and value > spec.high:
                lines.append(f"判定: {value} 高于参考上限,属升高。")
            elif spec.low is not None and value < spec.low:
                lines.append(f"判定: {value} 低于参考下限,属降低。")
            else:
                lines.append(f"判定: {value} 在参考区间内。")
        return "\n".join(lines)

    async def search_knowledge(query: str) -> str:
        """检索医学知识库(指标知识图谱+向量+全文)。返回证据摘要与编号;无结果时必须拒答。"""
        code = match_indicator(query, deps.kg.list_indicators())
        q = RetrievalQuery(text=query, indicator_code=code)
        evs = await deps.retriever.search(q)
        if not evs:
            return "知识库未检索到相关证据。请明确告知用户该问题知识库未覆盖,建议咨询医生。"
        return "\n".join(
            f"[e{i}](来源:{e.source},标题:{e.title or ''})\n{e.text}" for i, e in enumerate(evs)
        )

    return [get_my_report, query_indicator_knowledge, compute_reference_range, search_knowledge]
```

- [ ] **Step 5: 实现 agent.py(有界图 + 收敛节点)**

```python
"""LangGraph 有界 Agent:4 工具 + 工具轮数上限 + 超限强制收敛。spec §6。

流式护栏:回答完整组装后由 sse 层跑规则护栏(spec §6.3 注明的流式折中策略)。
"""
from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from report_agent.chat.tools import make_tools
from report_agent.llm.prompts import load_prompt

CONVERGE_INSTRUCTION = (
    "\n\n【系统指令】工具调用轮数已达上限。基于已获取的全部信息直接作答;"
    "信息不足的部分明确说明并建议咨询医生。禁止再调用任何工具。"
)


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_rounds: int


def route_after_agent(state: ChatState, max_rounds: int) -> str:
    last = state["messages"][-1] if state["messages"] else None
    if getattr(last, "tool_calls", None) and state.get("tool_rounds", 0) < max_rounds:
        return "tools"
    return "final"


def build_chat_agent(deps, report_id: str, checkpointer=None):
    settings = deps.settings
    model = ChatOpenAI(
        model=settings.chat_model, base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key, temperature=0.1,
    )
    tools = make_tools(deps, report_id)
    system = SystemMessage(content=load_prompt("agent_system"))
    max_rounds = settings.agent_max_tool_rounds

    def agent_node(state: ChatState) -> dict:
        return {"messages": [model.bind_tools(tools).invoke([system, *state["messages"]])]}

    tool_node = ToolNode(tools)

    def tools_node(state: ChatState) -> dict:
        result = tool_node.invoke({"messages": state["messages"]})
        return {"messages": result["messages"], "tool_rounds": state.get("tool_rounds", 0) + 1}

    def final_node(state: ChatState) -> dict:
        return {"messages": [model.invoke(
            [SystemMessage(content=load_prompt("agent_system") + CONVERGE_INSTRUCTION),
             *state["messages"]]
        )]}

    graph = StateGraph(ChatState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("final", final_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", lambda s: route_after_agent(s, max_rounds),
        {"tools": "tools", "final": "final"},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 6: 实现 sse.py 与 api/chat.py**

```python
# chat/sse.py
"""astream_events → SSE 事件。流完后:规则护栏 + 消息持久化 + done/safety 事件。"""
import asyncio

from langchain_core.messages import HumanMessage

from report_agent.guardrails.audit import AuditLog
from report_agent.guardrails.reviewer import review_output
from report_agent.guardrails.rules import GuardrailContext, Verdict, rule_guardrail
from report_agent.observability import get_logger

log = get_logger(__name__)

SAFETY_TEXT = "本条回答未通过内容安全校验,已停止展示。请咨询医生获取专业意见。"


async def sse_stream(graph, deps, session_id: str, user_message: str, report_id: str):
    """产出 {event, data} 事件字典,供 EventSourceResponse 序列化。"""
    config = {"configurable": {"thread_id": session_id}}
    full_text = ""
    tool_calls = []
    try:
        async for ev in graph.astream_events(
            {"messages": [HumanMessage(content=user_message)]}, config=config, version="v2"
        ):
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                delta = ev["data"]["chunk"].content or ""
                if delta:
                    full_text += delta
                    yield {"event": "token", "data": delta}
            elif kind == "on_tool_start":
                yield {"event": "tool_call", "data": {"name": ev.get("name"), "status": "start"}}
            elif kind == "on_tool_end":
                yield {"event": "tool_call", "data": {"name": ev.get("name"), "status": "end"}}
    except Exception as e:  # noqa: BLE001
        log.error("chat_stream_error", error=str(e))
        yield {"event": "error", "data": "生成失败,请稍后重试"}
        return

    # ---- 流式护栏(完整回答组装后)----
    detail = await deps.db.get_report_detail(report_id)
    allowed = []
    if detail:
        for n in detail["normalized"]:
            allowed.extend([x for x in (n.get("value_num"), n.get("ref_low"), n.get("ref_high"))
                            if x is not None])
        if detail["meta"].get("age") is not None:
            allowed.append(float(detail["meta"]["age"]))
    gctx = GuardrailContext(allowed_numbers=allowed, require_disclaimer=False)
    audit = AuditLog(deps.session_factory)
    result = rule_guardrail(full_text, gctx)
    guardrail_flags = result.findings
    if result.verdict == Verdict.SUSPECT:
        await audit.log("guardrail_suspect", {"findings": result.findings}, session_id=session_id)
        review = await review_output(full_text, f"规则护栏疑似问题: {result.findings}", deps.llms.chat)
        if not review.passed:
            guardrail_flags += review.issues
            result = GuardrailResult(Verdict.BLOCK, guardrail_flags)
    if result.verdict == Verdict.BLOCK:
        await audit.log("guardrail_block", {"findings": guardrail_flags}, session_id=session_id)
        full_text = SAFETY_TEXT
        yield {"event": "safety", "data": SAFETY_TEXT}

    # ---- 持久化(异步,失败只告警)——通过独立 task 写,不阻塞事件流关闭
    asyncio.create_task(_persist(deps, session_id, user_message, full_text, guardrail_flags))
    yield {"event": "done", "data": {"session_id": session_id,
                                     "guardrail": result.verdict.value}}


async def _persist(deps, session_id, user_message, full_text, guardrail_flags) -> None:
    try:
        await deps.db.add_message(session_id, "user", user_message)
        await deps.db.add_message(session_id, "assistant", full_text,
                                  guardrail_flags=guardrail_flags or None)
    except Exception as e:  # noqa: BLE001 —— 写失败告警不阻塞(spec §4.3)
        log.error("chat_message_persist_failed", error=str(e))
```

```python
# api/chat.py
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from report_agent.chat.agent import build_chat_agent
from report_agent.chat.sse import sse_stream
from report_agent.observability import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    content: str


@router.post("/reports/{report_id}/chat/sessions")
async def create_session(report_id: str, request: Request):
    detail = await request.app.state.db_access.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    session_id = await request.app.state.db_access.create_session(report_id)
    return {"session_id": session_id}


@router.post("/chat/sessions/{session_id}/messages")
async def send_message(session_id: str, body: ChatIn, request: Request):
    session = await request.app.state.db_access.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    graph = build_chat_agent(request.app.state.deps, session["report_id"],
                             checkpointer=request.app.state.chat_checkpointer)

    async def gen():
        async for item in sse_stream(graph, request.app.state.deps, session_id,
                                     body.content, session["report_id"]):
            yield {"event": item["event"], "data": __import__("json").dumps(
                item["data"], ensure_ascii=False)}

    return EventSourceResponse(gen())


@router.get("/chat/sessions/{session_id}/history")
async def history(session_id: str, request: Request):
    if await request.app.state.db_access.get_session(session_id) is None:
        raise HTTPException(404, "会话不存在")
    return {"messages": await request.app.state.db_access.get_messages(session_id)}
```

- [ ] **Step 7: app.py lifespan 加 checkpointer 与 LangSmith 开关(与 db_access 会话方法)**

app.py lifespan 内追加:

```python
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                saver = AsyncPostgresSaver.from_conn_string(settings.postgres_dsn)
                await saver.setup()
                app.state.chat_checkpointer = saver
                if settings.langsmith_tracing and settings.langsmith_api_key:
                    import os

                    os.environ["LANGSMITH_TRACING"] = "true"
                    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
                    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
                    log.info("langsmith_enabled")
```

db_access.py 追加:

```python
    # ===== 追问会话 =====
    async def create_session(self, report_id: str) -> str:
        from report_agent.db.models import ChatSession

        async with self._factory() as s:
            row = ChatSession(report_id=report_id)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row.id

    async def get_session(self, session_id: str) -> dict | None:
        from report_agent.db.models import ChatSession

        async with self._factory() as s:
            row = await s.get(ChatSession, session_id)
            return {"id": row.id, "report_id": row.report_id} if row else None

    async def add_message(self, session_id: str, role: str, content: str, tool_calls=None,
                          evidence_ids=None, guardrail_flags=None) -> None:
        from report_agent.db.models import ChatMessage

        async with self._factory() as s:
            s.add(ChatMessage(session_id=session_id, role=role, content=content,
                              tool_calls=tool_calls, evidence_ids=evidence_ids,
                              guardrail_flags=guardrail_flags))
            await s.commit()

    async def get_messages(self, session_id: str) -> list[dict]:
        from sqlalchemy import select

        from report_agent.db.models import ChatMessage

        async with self._factory() as s:
            rows = (await s.execute(
                select(ChatMessage).where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )).scalars().all()
            return [
                {"role": r.role, "content": r.content, "guardrail_flags": r.guardrail_flags,
                 "created_at": r.created_at.isoformat() if r.created_at else None}
                for r in rows
            ]
```

- [ ] **Step 8: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_chat tests/unit_api/test_chat_api.py -v`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add bounded LangGraph chat agent with 4 tools, SSE streaming and guardrail"
```

---

### Task 16: 评测集、回归门禁与端到端冒烟

**Files:**
- Create: `backend/src/report_agent/eval_metrics.py`(纯函数,可单测)、`backend/scripts/gen_sample_pdf.py`、`backend/scripts/gen_eval_reports.py`、`backend/scripts/run_eval.py`、`backend/scripts/smoke.py`、`backend/tests/unit_api/test_eval_metrics.py`
- 人工产物(生成后人工校对再冻结基线):`backend/eval/reports/r01.json`…`r20.json`、`backend/eval/qa_pairs.jsonl`(30 条)、`backend/eval/baseline.json`

**Interfaces:**
- Consumes: 全部前置任务(端到端)
- Produces:
  - `eval_metrics`: `compute_code_f1(pred: list[str | None], gt: list[str]) -> float`、`status_accuracy(pred: list[str], gt: list[str]) -> float`、`compare_baseline(current: dict, baseline: dict) -> list[str]`(回退指标名列表)
  - `scripts/gen_eval_reports.py --count 20`(LLM 合成)→ `eval/reports/*.json`
  - `scripts/run_eval.py [--update-baseline] [--max-llm-reports N]` → `eval/result.json`;任一指标回退 → exit 1
  - `scripts/smoke.py`(端到端:上传→轮询→解读→复查单→追问→拒答)
  - `scripts/gen_sample_pdf.py`(pymupdf 生成样例电子 PDF)

- [ ] **Step 1: 写 eval_metrics.py 与测试**

```python
# eval_metrics.py
"""评测纯函数(评测集口径见 spec §9)。"""


def compute_code_f1(pred: list[str | None], gt: list[str]) -> float:
    """名称对齐 F1:pred[i] 与 gt[i] 逐项比对(code 相同为命中,None 为未命中)。"""
    n = len(gt)
    if n == 0:
        return 1.0
    tp = sum(1 for p, g in zip(pred, gt, strict=True) if p == g and p is not None)
    precision = tp / max(len(pred), 1)
    recall = tp / n
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def status_accuracy(pred: list[str], gt: list[str]) -> float:
    """异常判定准确率(规则比对层要求 1.0,spec §4.5)。"""
    n = len(gt)
    if n == 0:
        return 1.0
    return sum(1 for p, g in zip(pred, gt, strict=True) if p == g) / n


def compare_baseline(current: dict, baseline: dict) -> list[str]:
    """返回回退的指标名(当前值 < 基线值 0.01 即视为回退)。"""
    regressions = []
    for key, base in baseline.items():
        if key in current and isinstance(base, (int, float)):
            if current[key] < base - 0.01:
                regressions.append(key)
    return regressions
```

```python
# tests/unit_api/test_eval_metrics.py
from report_agent.eval_metrics import compare_baseline, compute_code_f1, status_accuracy


def test_code_f1_perfect_and_partial():
    assert compute_code_f1(["GLU", "WBC"], ["GLU", "WBC"]) == 1.0
    f1 = compute_code_f1(["GLU", None], ["GLU", "WBC"])
    assert 0.4 < f1 < 0.7  # P=1.0 R=0.5 → F1=0.667


def test_status_accuracy_requires_perfect():
    assert status_accuracy(["high"], ["high"]) == 1.0
    assert status_accuracy(["high"], ["low"]) == 0.0


def test_compare_baseline_detects_regression():
    reg = compare_baseline({"f1": 0.8, "acc": 1.0}, {"f1": 0.9, "acc": 1.0})
    assert reg == ["f1"]
    assert compare_baseline({"f1": 0.95}, {"f1": 0.9}) == []
```

- [ ] **Step 2: 运行测试确认通过**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run pytest tests/unit_api/test_eval_metrics.py -v`
Expected: PASS

- [ ] **Step 3: 写 gen_sample_pdf.py 与 gen_eval_reports.py**

```python
# scripts/gen_sample_pdf.py
"""生成样例电子 PDF(文字层 + 简单表格),供 smoke 与解析演示。
运行: uv run python scripts/gen_sample_pdf.py [输出路径,默认 eval/fixtures/sample_report.pdf]
"""
import sys
from pathlib import Path

import pymupdf


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval/fixtures/sample_report.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [
        "阳光体检中心", "姓名: 张三  性别: 男  年龄: 45  日期: 2026-08-20",
        "----------------------------------------------------------------",
        "检验项目            结果        单位      参考区间      标记",
        "白细胞计数          6.2         10^9/L    3.5-9.5",
        "红细胞计数          5.1         10^12/L   4.3-5.8",
        "血红蛋白            150         g/L       130-175",
        "空腹血糖            7.1         mmol/L    3.9-6.1        ↑",
        "总胆固醇            5.4         mmol/L    2.8-5.7",
        "甘油三酯            2.5         mmol/L    0.4-1.7        ↑",
        "谷丙转氨酶          80          U/L       9-50           ↑",
        "肌酐                72          umol/L    57-97",
        "----------------------------------------------------------------",
        "注: 本报告仅供临床参考。",
    ]
    y = 72
    for text in lines:
        page.insert_text((50, y), text, fontsize=11)
        y += 26
    doc.save(out)
    doc.close()
    print(f"[done] {out}")


if __name__ == "__main__":
    main()
```

```python
# scripts/gen_eval_reports.py
"""LLM 合成评测报告集(人工校对后冻结基线)。运行:
  uv run python scripts/gen_eval_reports.py --count 20
产物: eval/reports/r01.json ... rNN.json。评测 JSON 结构:
{
  "id": "r01",
  "meta": {"sex": "male|female", "age": 45},
  "raw_items": [{name, value_text, value_num, unit, ref_range_text, abnormal_flag, section}],
  "gt_codes": {"报告项目名": "标准code"},
  "gt_statuses": {"标准code": "normal|high|low|critical_high|critical_low|unknown|unmapped"}
}
"""
import argparse
import asyncio
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "reports"

TEMPLATE = """你是体检报告数据合成器。基于以下真实指标目录,合成一份虚构体检报告的部分检验结果,
用于评测一个报告解读系统。直接输出 JSON,不要解释。

指标目录(每行: code|标准名|单位|成人参考区间):
{catalog}

合成要求:
1. 性别随机 male/female,年龄 25~65。
2. 8~15 个检验项;其中 40%~70% 异常(偏高/偏低/危急值各有一些),保留 1~2 个正常项,
   可包含 1 个目录外项目(unknown)与 1 个别名变体(如"谷丙转氨酶"代替标准名)。
3. 参考区间优先给报告口径区间;危急值数值要显著越过区间。
4. gt_codes: 每个项目名对应的标准 code;目录外项目映射为 null 并在 gt_statuses 标 unmapped。
5. gt_statuses 按给定区间严格判定: normal/high/low/critical_high/critical_low/unknown/unmapped。

输出 JSON 结构:
{{"id": "{rid}", "meta": {{"sex": "...", "age": 45}},
  "raw_items": [{{"name": "...", "value_text": "...", "value_num": 6.2, "unit": "...",
                  "ref_range_text": "...", "abnormal_flag": "↑或null", "section": "..."}}],
  "gt_codes": {{"项目名": "code或null"}},
  "gt_statuses": {{"code": "status"}}}}
"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    import yaml

    from report_agent.llm.client import llm_factory

    catalog = yaml.safe_load((ROOT / "knowledge_seeds" / "indicator_list.yaml").read_text("utf-8"))
    catalog_text = "\n".join(
        f"{c['code']}|{c['name']}|{'、'.join(c.get('aliases') or [])}" for c in catalog
    )
    clients = await llm_factory.get()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(1, args.count + 1):
        rid = f"r{i:02d}"
        out = OUT_DIR / f"{rid}.json"
        if out.exists():
            print(f"[skip] {rid}")
            continue
        data = await clients.chat.complete_json(
            [{"role": "user", "content": TEMPLATE.format(catalog=catalog_text, rid=rid)}]
        )
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        print(f"[done] {rid}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: 写 run_eval.py**

```python
# scripts/run_eval.py
"""评测与回归门禁(spec §9)。运行:
  uv run python scripts/run_eval.py                      # 全量评测,输出 eval/result.json
  uv run python scripts/run_eval.py --update-baseline    # 人工确认结果后冻结基线
维度: 解析准确率(归一化 F1)/ 异常判定准确率(规则层,必须 1.0)/ 证据覆盖率 /
      数值一致性 / 安全指标 / 拒答正确率;与 baseline 比较,回退即 exit 1。
LLM 相关维度只跑 --max-llm-reports(默认 5)份以控制成本;规则维度全量 20 份。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "eval" / "reports"
QA_FILE = ROOT / "eval" / "qa_pairs.jsonl"
RESULT_FILE = ROOT / "eval" / "result.json"
BASELINE_FILE = ROOT / "eval" / "baseline.json"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--max-llm-reports", type=int, default=5)
    args = parser.parse_args()

    from report_agent.config import get_settings
    from report_agent.eval_metrics import compare_baseline, compute_code_f1, status_accuracy
    from report_agent.knowledge.kg_client import KGClient
    from report_agent.knowledge.milvus_client import MilvusStore
    from report_agent.parsing.normalizer import Normalizer
    from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
    from report_agent.pipeline.deps import build_deps
    from report_agent.pipeline.rule_compare import ItemStatus, judge_all

    settings = get_settings()
    deps = await build_deps(settings)

    f1s, accs = [], []
    evidence_covered = 0
    evidence_total = 0

    report_files = sorted(REPORTS_DIR.glob("*.json"))
    for path in report_files:
        data = json.loads(path.read_text("utf-8"))
        raws = [RawReportItem(**r) for r in data["raw_items"]]
        # 1) 解析准确率:归一化 F1(全量)
        items = await deps.normalizer.normalize(raws, llm=deps.llms.chat)
        pred = [it.indicator_code for it in items]
        gt = [data["gt_codes"].get(r["name"]) for r in data["raw_items"]]
        f1s.append(compute_code_f1(pred, gt))
        # 2) 规则层判定准确率(直接喂 gt 归一化,绕过 LLM 波动;必须 1.0)
        gt_items = []
        for i, r in enumerate(data["raw_items"]):
            code = data["gt_codes"].get(r["name"])
            gt_items.append(NormalizedItem(
                raw_index=i, section=r.get("section"), name=r["name"], indicator_code=code,
                value_text=r.get("value_text"), value_num=r.get("value_num"),
                unit=r.get("unit"), raw_value_num=r.get("value_num"), raw_unit=r.get("unit"),
                ref_range_text=r.get("ref_range_text"), range_from="report",
            ))
        specs = {}
        for it in gt_items:
            if it.indicator_code and it.indicator_code not in specs:
                specs[it.indicator_code] = deps.kg.range_specs(it.indicator_code)
        judgments = judge_all(gt_items, specs, ReportMeta(sex=data["meta"]["sex"],
                                                          age=data["meta"]["age"]))
        accs.append(status_accuracy(
            [j.status.value for j in judgments],
            [data["gt_statuses"].get(c, "unknown") for c in
             (it.indicator_code or "" for it in gt_items)],
        ))
        # 3) 证据覆盖率(全量,无 LLM)
        from report_agent.retrieval.hybrid import RetrievalQuery

        for j in judgments:
            if j.status.value not in ("high", "low", "critical_high", "critical_low"):
                continue
            evidence_total += 1
            evs = await deps.retriever.search(RetrievalQuery(
                text=f"{j.name} {j.status.value}", indicator_code=j.indicator_code))
            if any(e.source != "placeholder" for e in evs):
                evidence_covered += 1

    metrics = {
        "normalize_f1": round(sum(f1s) / len(f1s), 4) if f1s else 1.0,
        "rule_accuracy": round(sum(accs) / len(accs), 4) if accs else 1.0,
        "evidence_coverage": round(evidence_covered / evidence_total, 4) if evidence_total else 1.0,
        "numeric_consistency": 1.0,
        "safety_violations": 0,
        "refusal_correct": 1.0,
    }

    # 4) LLM 维度(数值一致性/安全)——成本控制,只跑前 N 份
    from report_agent.guardrails.rules import GuardrailContext, rule_guardrail
    from report_agent.pipeline.interpret import generate_summary, interpret_item
    from report_agent.retrieval.hybrid import RetrievalQuery

    for path in report_files[:args.max_llm_reports]:
        data = json.loads(path.read_text("utf-8"))
        allowed = [r["value_num"] for r in data["raw_items"] if r.get("value_num") is not None]
        # 复用第 2 步的 gt 归一化口径生成判定
        gt_items = [
            NormalizedItem(
                raw_index=i, section=r.get("section"), name=r["name"],
                indicator_code=data["gt_codes"].get(r["name"]),
                value_text=r.get("value_text"), value_num=r.get("value_num"),
                unit=r.get("unit"), raw_value_num=r.get("value_num"), raw_unit=r.get("unit"),
                ref_range_text=r.get("ref_range_text"), range_from="report",
            )
            for i, r in enumerate(data["raw_items"])
        ]
        specs = {}
        for it in gt_items:
            if it.indicator_code and it.indicator_code not in specs:
                specs[it.indicator_code] = deps.kg.range_specs(it.indicator_code)
        judgments = judge_all(gt_items, specs, ReportMeta(sex=data["meta"]["sex"],
                                                          age=data["meta"]["age"]))
        numeric_ok = 0
        numeric_total = 0
        for j in judgments:
            if j.status.value not in ("high", "low", "critical_high", "critical_low"):
                continue
            evs = await deps.retriever.search(RetrievalQuery(
                text=f"{j.name} {j.status.value}", indicator_code=j.indicator_code))
            kctx = deps.kg.indicator_context(j.indicator_code) if j.indicator_code else None
            interp = await interpret_item(j, kctx, evs, deps.llms.chat)
            text = f"{interp.meaning}\n建议:{interp.advice}"
            numeric_total += 1
            g = rule_guardrail(text, GuardrailContext(allowed_numbers=allowed))
            if g.verdict.value != "block":
                numeric_ok += 1
            else:
                metrics["safety_violations"] += 1
        summary = await generate_summary([], [], 0, deps.llms.chat)
        numeric_total += 1
        g = rule_guardrail(summary, GuardrailContext(allowed_numbers=allowed))
        if g.verdict.value != "block":
            numeric_ok += 1
        else:
            metrics["safety_violations"] += 1
        metrics["numeric_consistency"] = numeric_ok / numeric_total if numeric_total else 1.0

    # 5) 拒答 QA(30 条,需要 eval/fixtures 报告已入库:先跑一次 smoke.py 或手工上传样例)
    from report_agent.chat.agent import build_chat_agent

    qa_correct, qa_total = 0, 0
    if QA_FILE.exists():
        for line in QA_FILE.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            qa = json.loads(line)
            qa_total += 1
            graph = build_chat_agent(deps, "eval_fixture")
            result = await graph.ainvoke(
                {"messages": [{"role": "user", "content": qa["question"]}], "tool_rounds": 0}
            )
            answer = result["messages"][-1].content or ""
            ok = True
            for s in qa.get("must_contain", []):
                if s not in answer:
                    ok = False
            for s in qa.get("must_not_contain", []):
                if s in answer:
                    ok = False
            if ok:
                qa_correct += 1
    metrics["refusal_correct"] = round(qa_correct / qa_total, 4) if qa_total else 1.0

    if args.update_baseline:
        BASELINE_FILE.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), "utf-8")
        print("[baseline updated]", metrics)
        return

    RESULT_FILE.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), "utf-8")
    print("[result]", metrics)
    if BASELINE_FILE.exists():
        baseline = json.loads(BASELINE_FILE.read_text("utf-8"))
        regressions = compare_baseline(metrics, baseline)
        if metrics["rule_accuracy"] < 1.0:
            print("[FAIL] 规则比对层准确率必须为 100%")
            sys.exit(1)
        if metrics["safety_violations"] > 0:
            print("[FAIL] 安全指标必须为零违规")
            sys.exit(1)
        if regressions:
            print(f"[FAIL] 指标回退: {regressions}")
            sys.exit(1)
    print("[PASS] 评测通过")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: 写 smoke.py(端到端,含拒答场景)**

```python
# scripts/smoke.py
"""端到端冒烟:样例 PDF → 上传 → 轮询 → 解读 → 复查单 → 追问 → 知识库外拒答。
运行前提: docker compose 已启动、KB 已入库(seed_import --entity GLU 至少一个指标)、LLM key 已配置。
运行: uv run python scripts/smoke.py
降级演示(验收标准 3): 先 docker stop report-neo4j 再跑本脚本,KG 路失效仍能产出解读
(dense+BM25 两路继续),恢复容器后 docker start report-neo4j。
"""
import asyncio
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        # 1) 生成样例 PDF 并上传
        from scripts.gen_sample_pdf import main as gen_pdf

        gen_pdf()
        with open("eval/fixtures/sample_report.pdf", "rb") as f:
            resp = await client.post(f"{BASE}/api/reports", files={"file": ("sample.pdf", f, "application/pdf")})
        assert resp.status_code == 200, resp.text
        report_id, task_id = resp.json()["report_id"], resp.json()["task_id"]
        print(f"[1] 已创建报告 {report_id},任务 {task_id}")

        # 2) 轮询任务(管线 30s~2min)
        for _ in range(120):
            t = (await client.get(f"{BASE}/api/tasks/{task_id}")).json()
            if t["status"] in ("completed", "degraded", "failed"):
                break
            await asyncio.sleep(1.5)
        assert t["status"] != "failed", t.get("error")
        print(f"[2] 任务完成: {t['status']},阶段耗时 {t['timings']}")

        # 3) 解读与复查单
        interp = (await client.get(f"{BASE}/api/reports/{report_id}/interpretation")).json()
        assert "免责" in interp["disclaimer"] or "不构成医学诊断" in interp["disclaimer"]
        assert interp["summary"] and interp["items"]
        print(f"[3] 解读生成: {len(interp['items'])} 项解读, degraded={interp['degraded']}")
        plan = (await client.get(f"{BASE}/api/reports/{report_id}/followup-plan")).json()
        print(f"[4] 复查计划: {len(plan['items'])} 条")

        # 4) 追问(SSE)
        sess = (await client.post(f"{BASE}/api/reports/{report_id}/chat/sessions")).json()
        sid = sess["session_id"]
        async with client.stream("POST", f"{BASE}/api/chat/sessions/{sid}/messages",
                                 json={"content": "我的血糖情况怎么样?"}) as resp:
            events = [line async for line in resp.aiter_lines() if line.startswith("data: ")]
        assert any("token" in e for e in events)
        print(f"[5] 追问流式完成: {len(events)} 个 SSE 事件")

        # 5) 知识库外拒答
        async with client.stream("POST", f"{BASE}/api/chat/sessions/{sid}/messages",
                                 json={"content": "请介绍一下量子场论的最新进展"}) as resp:
            text = "".join([line async for line in resp.aiter_lines()])
        assert ("拒" in text and "医生" in text) or "未覆盖" in text, text[:200]
        print("[6] 知识库外问题已拒答")

    print("\nSMOKE PASS")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: 生成评测集(需 LLM key)并人工校对**

```bash
cd /d/DeskTop/agent/report-agent/backend
uv run python scripts/gen_eval_reports.py --count 20
```
人工校对 20 份 `eval/reports/*.json` 的 gt_codes/gt_statuses(重点:危急值判定、别名映射、unknown 项),并手写 `eval/qa_pairs.jsonl`(30 条,格式 `{"id": "qa01", "question": "...", "expect": "answer|refuse", "must_contain": [], "must_not_contain": []}`,四类:报告数值类/知识类/知识库外拒答类/诊断请求边界类)。

- [ ] **Step 7: 全量评测 + 冻结基线**

```bash
cd /d/DeskTop/agent/report-agent/backend
uv run python scripts/run_eval.py                 # 首次:规则层必须 1.0、零违规
uv run python scripts/run_eval.py --update-baseline   # 人工确认结果后冻结
```

- [ ] **Step 8: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add backend/
git commit -m "feat: add eval suite (synthetic reports, QA pairs, regression gate) and smoke script"
```

---

### Task 17: 收尾(README、环境变量终审、验收核对)

**Files:**
- Create: `README.md`(仓库根)、`frontend/README.md`(前端预留说明)
- Modify: `backend/.env.example`(终审:与 Settings 字段一一对应)

- [ ] **Step 1: 写 README.md(仓库根)**

```markdown
# report-agent 体检报告问答助手

上传体检报告 → 结构化解析 → 异常判定 → 四段式解读(总体结论/逐项解读/分级建议/免责声明)→ 结构化复查计划;
并支持基于本人报告的多轮追问(SSE 流式)。产品定位为报告解读 + 健康建议,**不构成医学诊断**。

## 目录

- `docs/requirements.md` — 需求文档
- `docs/superpowers/specs/2026-09-02-report-agent-design.md` — 系统设计
- `docs/superpowers/plans/2026-09-02-report-agent.md` — 本实现规划
- `backend/` — 后端(FastAPI + LangGraph + Neo4j + Milvus + PostgreSQL)
- `frontend/` — 前端(预留,本期不实现)

## 快速开始

```bash
cd backend
cp .env.example .env          # 填入 DEEPSEEK_API_KEY / EMBEDDING_API_KEY
docker compose up -d          # postgres + neo4j + milvus
uv sync
uv run alembic upgrade head
uv run python scripts/seed_draft.py --all      # LLM 起草种子(人工校对 YAML 后入库)
uv run python scripts/seed_import.py --all     # 入库(增量: --entity CODE)
uv run uvicorn report_agent.api.app:create_app --factory --port 8000
uv run python scripts/smoke.py                 # 端到端冒烟
uv run python scripts/run_eval.py              # 评测回归门禁
```

## API(详见 spec §7)

POST /api/reports(上传/手动录入)→ GET /api/tasks/{id}(轮询)→
GET /api/reports/{id}/interpretation + /followup-plan →
POST /api/reports/{id}/chat/sessions → POST /api/chat/sessions/{sid}/messages(SSE)

## 验收标准对照(spec §7)

1. PDF/拍照端到端 → `scripts/smoke.py`
2. 规则层单测 100% → `uv run pytest tests/unit_rules -v --cov=report_agent.pipeline.rule_compare --cov-report=term-missing`;安全零违规 → `run_eval.py` 的 safety_violations
3. 降级路径演示 → `docker stop report-neo4j` 后重跑 smoke(解读仍产出)
4. 知识库外拒答 → smoke 第 6 步 + 评测 QA 拒答类
5. 增量更新 → `seed_import.py --entity GLU`(单实体 delete+insert / MERGE,不触发全量重建)
```

- [ ] **Step 2: 写 frontend/README.md**

```markdown
# frontend(预留)

前端页面由团队另行开发,本期不实现。后端 API 契约见 ../docs/superpowers/specs/2026-09-02-report-agent-design.md §7。
```

- [ ] **Step 3: .env.example 终审**

Run: `cd /d/DeskTop/agent/report-agent/backend && uv run python -c "from report_agent.config import Settings; import re; print(sorted(Settings.model_fields.keys()))"`,与 `.env.example` 中的变量一一核对(大写化),缺失的补上(执行过程中新增的 `UPLOAD_DIR` 必须在内)。

- [ ] **Step 4: 全量验证(收尾门禁)**

```bash
cd /d/DeskTop/agent/report-agent/backend
uv run ruff check src tests scripts
uv run pytest                     # 全部单测绿,规则层覆盖率 100%
uv run python scripts/smoke.py    # 依赖真实服务与 LLM
uv run python scripts/run_eval.py # 无回退
```

- [ ] **Step 5: 提交**

```bash
cd /d/DeskTop/agent/report-agent
git add README.md frontend/ backend/.env.example
git commit -m "docs: add README, frontend placeholder and final env example"
```

---

## 执行顺序与依赖关系

Task 1 → 2 → 3 → (4, 5 可并行) → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17。
其中 4/5/10 可与前面并行推进(无相互依赖),但每个任务独立提交、独立可测。

## 已知风险与验证项(执行期必须闭环)

| 风险 | 闭环位置 |
|---|---|
| Milvus jieba analyzer + BM25 是否可用 | Task 4 Step 6 真实验证,失败走 PG FTS 兜底(spec §15.1) |
| deepseek-v4-flash-vision-exp JSON 稳定性 | Task 10 用 complete_json 反馈重试;smoke 验证 |
| Unstructured hi_res 中文表格质量/耗时 | Task 10 Step 4 单测 + smoke 真机验证;strategy env 可切 fast |
| LangSmith 与 DeepSeek 兼容性 | 可选开关,失败仅降级为纯日志(spec §15.3) |
| 评测集 gt 需要人工校对 | Task 16 Step 6 人工步骤,冻结基线前必须完成 |






