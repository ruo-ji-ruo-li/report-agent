# Plan 1: 基础设施 + 知识规则底座 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起 report-agent 后端工程骨架(uv/docker-compose/config/DB 模型/LLM 客户端/Neo4j 客户端/structlog),并完成纯代码知识规则底座(单位换算、指标词典、参考区间、规则比对、组合模式),全部规则模块单测 100% 分支覆盖。

**Architecture:** 本计划 = 设计文档 §16 建设顺序的第 1-2 阶段。纯代码规则模块互不依赖外部服务,全部单测零网络;基础设施层提供配置与客户端供后续计划(知识库、检索、管线)消费。执行环境:Windows + bash,项目在 `D:\DeskTop\agent\report-agent`(不是会话默认目录 `all-in-rag`!)。所有命令在 `backend/` 目录下执行。

**Tech Stack:** Python 3.13,uv,SQLAlchemy 2.0 async + asyncpg、pydantic-settings、openai SDK(DeepSeek 兼容)、neo4j driver、structlog、pytest(+asyncio/cov)、docker compose(postgres:16 / neo4j:5 / milvus 2.5.14)。

**Spec:** `docs/superpowers/specs/2026-09-02-report-agent-design.md`(该计划实现 §3.3、§4.1、§16.1-16.2)

## Global Constraints

- 目标仓库:`D:\DeskTop\agent\report-agent`;后端代码全部位于 `backend/`;源布局 `src/report_agent/`(src layout)
- Python >= 3.13,依赖管理用 uv(`uv sync` 安装,`uv add` 加依赖),不直接手改 lock
- 代码标识符用英文;docstring 与注释用中文;提交信息遵循 conventional commits(如 `feat:`, `test:`, `chore:`)且以 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` 结尾
- 枚举/状态值全部以小写字符串落库(DB 列用 String,不用 PG native enum),便于后续演进
- JSONB 列用 `JSON().with_variant(JSONB(), "postgresql")`,保证 sqlite 测试可用
- 规则层(units / indicator_dict / ranges / rules / patterns)单测以 `--cov-branch --cov-fail-under=100` 为门禁,只针对被测模块计覆盖
- 测试零外部依赖:不连真实 Neo4j/Milvus/Postgres/DeepSeek;所有外部组件以构造注入/桩替代
- `.env` 不入库(已在 .gitignore);配置默认值与 docker-compose 凭据一致(`report_agent` / `report_agent_dev`)
- 本计划不引入 FastAPI/LangGraph/pymilvus 等后续阶段依赖(uv add 留到各自计划)

## File Structure

```
report-agent/
├── .gitignore
├── README.md
├── docs/superpowers/specs/2026-09-02-report-agent-design.md      # 已有(只读)
├── backend/
│   ├── pyproject.toml                                            # uv + pytest 配置
│   ├── .env.example                                              # 全部环境变量样例
│   ├── docker-compose.yml                                        # postgres + neo4j + milvus(etcd/minio)
│   ├── .python-version
│   ├── src/report_agent/
│   │   ├── __init__.py
│   │   ├── config.py                                             # Settings(pydantic-settings,全 env)
│   │   ├── observability/__init__.py                             # structlog JSON 初始化 + 上下文绑定
│   │   ├── db/__init__.py
│   │   ├── db/base.py                                            # DeclarativeBase + engine/会话工厂
│   │   ├── db/models.py                                          # 9 张表(设计 §4.1)
│   │   ├── llm/__init__.py
│   │   ├── llm/client.py                                         # LLMClient:重试/退避/JSON 提取
│   │   ├── knowledge/__init__.py
│   │   ├── knowledge/units.py                                    # UnitConverter(纯代码)
│   │   ├── knowledge/indicator_dict.py                           # IndicatorDict + normalize_name(纯代码)
│   │   ├── knowledge/ranges.py                                   # RangeSpec + resolve(纯代码)
│   │   ├── knowledge/kg_client.py                                # Neo4jClient(注入 driver)
│   │   ├── pipeline/__init__.py
│   │   ├── pipeline/rules.py                                     # Judgment/RuleStatus/judge_* (纯代码)
│   │   └── pipeline/patterns.py                                  # PatternDef/PatternMatcher(纯代码)
│   └── tests/
│       ├── conftest.py                                           # 公共 fixture(可选,零依赖)
│       ├── test_config.py
│       ├── observability/test_init.py
│       ├── db/test_models.py
│       ├── llm/test_client.py
│       ├── knowledge/test_units.py
│       ├── knowledge/test_indicator_dict.py
│       ├── knowledge/test_ranges.py
│       ├── knowledge/test_kg_client.py
│       ├── pipeline/test_rules.py
│       └── pipeline/test_patterns.py
```

依赖方向(只许单向):`config ← {observability, db, llm, knowledge}`;`knowledge.indicator_dict/ranges/units`、`pipeline.rules/patterns` 为叶模块(除 Python 标准库与 dataclasses 外零 import);`pipeline.patterns` import `pipeline.rules` 的 `Judgment`。

---

### Task 1: 工程骨架 + structlog

**Files:**
- Create: `.gitignore`, `README.md`, `backend/.python-version`, `backend/.env.example`, `backend/pyproject.toml`, `backend/docker-compose.yml`;空包文件 `src/report_agent/{__init__,observability/__init__,db/__init__,llm/__init__,knowledge/__init__,pipeline/__init__}.py`(目录 `backend/src/report_agent/…`), `tests/conftest.py`(空文件,预留公共 fixture 位;tests 子目录**无需** `__init__.py`,pytest 按 rootdir 收集), `tests/observability/test_init.py`

**Interfaces:**
- Consumes: 无
- Produces: `report_agent.observability.init_logging(*, level: str|int, json_logs: bool) -> None`(幂等);`observability` 包与 `config`/`db`/`llm`/`knowledge`/`pipeline` 空包可 import;docker-compose 三套件可 `docker compose config` 校验

- [ ] **Step 1: 写仓库根文件与 backend 工程文件**

`.gitignore`(仓库根):
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.coverage
htmlcov/
.env
backend/data/
```

`README.md`(仓库根,简短;下一任务不覆盖本文件):
```markdown
# report-agent 体检报告问答助手

- 需求:docs/requirements.md
- 设计:docs/superpowers/specs/2026-09-02-report-agent-design.md
- 后端:backend/(src layout,uv 管理);前端 frontend/ 预留
- 快速开始:见本文件末尾「快速开始(backend)」,由 Task 11 补齐
```

`backend/.python-version`:
```
3.13
```

`backend/.env.example`(默认值即 docker-compose 凭据):
```bash
# --- LLM (DeepSeek) ---
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
VISION_MODEL=deepseek-v4-flash-vision-exp
LLM_TEMPERATURE=0.1
LLM_MAX_RETRIES=3
LLM_TIMEOUT_SECONDS=60
LLM_BACKOFF_BASE=1.0

# --- Embedding (DashScope) ---
DASHSCOPE_API_KEY=
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIMENSION=1024

# --- PostgreSQL / Neo4j / Milvus(docker-compose 默认凭据) ---
POSTGRES_DSN=postgresql+asyncpg://report_agent:report_agent_dev@localhost:5432/report_agent
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=report_agent_dev
NEO4J_DATABASE=neo4j
MILVUS_URI=http://localhost:19530
MILVUS_COLLECTION=report_knowledge

# --- 检索 / 证据(后续计划消费) ---
RETRIEVAL_TOP_K=5
RETRIEVAL_CANDIDATE_MULTIPLIER=2
RETRIEVAL_RRF_K=60
EVIDENCE_BUDGET_CHARS=6000

# --- 运行 ---
APP_ENV=dev
LOG_LEVEL=INFO
LOG_JSON=true
```

`backend/pyproject.toml`:
```toml
[project]
name = "report-agent"
version = "0.1.0"
description = "体检报告问答助手后端(report-agent)"
requires-python = ">=3.13"
dependencies = [
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.29",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "openai>=1.40",
    "structlog>=24.1",
    "neo4j>=5.20",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "aiosqlite>=0.20",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/report_agent"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
testpaths = ["tests"]
```

`backend/docker-compose.yml`(参考 C9 的 milvus 栈,增加 postgres 与 neo4j;用 named volume 避免 Windows 宿主目录权限问题):
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: report_agent
      POSTGRES_USER: report_agent
      POSTGRES_PASSWORD: report_agent_dev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U report_agent -d report_agent"]
      interval: 10s
      timeout: 5s
      retries: 5

  neo4j:
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/report_agent_dev
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4jdata:/data
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p report_agent_dev 'RETURN 1' >/dev/null 2>&1"]
      interval: 15s
      timeout: 10s
      retries: 10
      start_period: 30s

  etcd:
    image: quay.io/coreos/etcd:v3.5.16
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - etcddata:/etcd
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 30s
      timeout: 20s
      retries: 3

  minio:
    image: minio/minio:RELEASE.2023-03-20T20-16-18Z
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    ports:
      - "9001:9001"
      - "9000:9000"
    volumes:
      - miniodata:/minio_data
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  milvus:
    image: milvusdb/milvus:v2.5.14
    command: ["milvus", "run", "standalone"]
    security_opt:
      - seccomp:unconfined
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    ports:
      - "19530:19530"
      - "9091:9091"
    volumes:
      - milvusdata:/var/lib/milvus
    depends_on:
      etcd:
        condition: service_healthy
      minio:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      start_period: 90s
      timeout: 20s
      retries: 3

volumes:
  pgdata:
  neo4jdata:
  etcddata:
  miniodata:
  milvusdata:
```

创建空包文件(每个文件仅一行 docstring,如 `"""LLM 调用层。"""`):`backend/src/report_agent/__init__.py`、`observability/__init__.py`、`db/__init__.py`、`llm/__init__.py`、`knowledge/__init__.py`、`pipeline/__init__.py`。`config.py`、`db/base.py` 由 Task 2 / Task 3 创建,此处不建。目录结构:
```
backend/src/report_agent/{__init__.py, observability/, db/, llm/, knowledge/, pipeline/}
backend/tests/{conftest.py(空), observability/}
```

- [ ] **Step 2: 写失败的 observability 测试**

`backend/tests/observability/test_init.py`:
```python
import structlog
import pytest
from report_agent.observability import init_logging


def test_init_logging_json_with_context(capsys):
    init_logging(level="INFO", json_logs=True)
    log = structlog.get_logger()
    with structlog.contextvars.bound_contextvars(task_id="t-1", stage="compare"):
        log.info("阶段完成", 耗时_ms=12)
    out = capsys.readouterr().out
    import json

    record = json.loads(out)
    assert record["event"] == "阶段完成"
    assert record["task_id"] == "t-1"
    assert record["stage"] == "compare"
    assert record["耗时_ms"] == 12
    assert record["level"] == "info"


def test_init_logging_plain_console_when_json_disabled(capsys):
    init_logging(level="INFO", json_logs=False)
    structlog.get_logger().info("hello", 键="值")
    out = capsys.readouterr().out
    assert "hello" in out and "值" in out


def test_init_logging_is_idempotent():
    init_logging(level="DEBUG", json_logs=True)
    init_logging(level="DEBUG", json_logs=True)  # 不抛异常
```

- [ ] **Step 3: 运行验证失败**

Run: `cd backend && uv run pytest tests/observability/test_init.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report_agent.observability'`

- [ ] **Step 4: 实现 observability**

`backend/src/report_agent/observability/__init__.py`:
```python
"""structlog JSON 日志初始化与上下文绑定(设计 §10)。"""
import logging

import structlog

__all__ = ["init_logging"]


def init_logging(*, level: str | int = "INFO", json_logs: bool = True) -> None:
    """幂等地初始化 structlog。

    json_logs=True 时输出 JSON 行(生产形态);False 时用彩色控制台渲染(本地调试)。
    第三方库日志保持 stdlib 默认输出,级别与 level 对齐。
    """
    level_num = logging.getLevelName(level) if isinstance(level, str) else level
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False)
            if json_logs
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_num),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level_num, format="%(message)s", force=True)
```

- [ ] **Step 5: 运行验证通过**

Run: `cd backend && uv run pytest tests/observability/test_init.py -v`
Expected: 3 passed(JSON 记录含 event/level/task_id/耗时_ms;控制台渲染含中文值)

- [ ] **Step 6: 校验工程与 compose**

Run: `cd backend && uv sync && uv run python -c "import report_agent, report_agent.observability, report_agent.db, report_agent.llm, report_agent.knowledge, report_agent.pipeline; print('imports ok')"` 和 `docker compose config -q`
Expected: 打印 `imports ok`;compose 无报错(若本机无 Docker CLI,向用户说明并跳过此条,其余照常)

- [ ] **Step 7: Commit**

```bash
cd backend
git add -A
git commit -m "chore: scaffold backend project with uv, docker-compose and structlog

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Settings 配置(config.py)

**Files:**
- Modify: `src/report_agent/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: 无(独立)
- Produces: `Settings`(pydantic-settings 类,字段见下);`get_settings() -> Settings`(functools.lru_cache)。Task 4/5 从 `Settings` 取 `deepseek_*`、`neo4j_*` 等字段;后续计划按需扩字段,不改现有字段名

- [ ] **Step 1: 写失败测试**

`backend/tests/test_config.py`:
```python
import pytest
from report_agent.config import Settings, get_settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == ""
    assert s.deepseek_base_url == "https://api.deepseek.com"
    assert s.llm_model == "deepseek-chat"
    assert s.vision_model == "deepseek-v4-flash-vision-exp"
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.neo4j_password == "report_agent_dev"
    assert s.postgres_dsn.startswith("postgresql+asyncpg://")
    assert s.milvus_uri == "http://localhost:19530"
    assert s.retrieval_top_k == 5
    assert s.evidence_budget_chars == 6000


def test_env_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "deepseek-custom")
    monkeypatch.setenv("RETRIEVAL_TOP_K", "8")
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == "sk-test"
    assert s.llm_model == "deepseek-custom"
    assert s.retrieval_top_k == 8


def test_log_bool_parsing(monkeypatch):
    monkeypatch.setenv("LOG_JSON", "false")
    s = Settings(_env_file=None)
    assert s.log_json is False


def test_get_settings_cached():
    assert get_settings() is get_settings()
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'Settings'`

- [ ] **Step 3: 实现 config.py**

`backend/src/report_agent/config.py`:
```python
"""全环境变量配置(设计 §3.2:修 C9 env 未接线的缺陷)。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """所有配置从环境变量 / backend/.env 读取,字段名 ↔ 大写环境变量。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # 运行
    app_env: str = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    # LLM (DeepSeek)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    vision_model: str = "deepseek-v4-flash-vision-exp"
    llm_temperature: float = 0.1
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 60
    llm_backoff_base: float = 1.0

    # Embedding (DashScope)
    dashscope_api_key: str = ""
    embedding_model: str = "text-embedding-v3"
    embedding_dimension: int = 1024

    # 存储
    postgres_dsn: str = (
        "postgresql+asyncpg://report_agent:report_agent_dev@localhost:5432/report_agent"
    )
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "report_agent_dev"
    neo4j_database: str = "neo4j"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "report_knowledge"

    # 检索 / 证据(本计划仅定义默认值,供后续计划消费)
    retrieval_top_k: int = 5
    retrieval_candidate_multiplier: int = 2
    retrieval_rrf_k: int = 60
    evidence_budget_chars: int = 6000


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/config.py tests/test_config.py
git commit -m "feat: add pydantic-settings based env configuration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Postgres 模型(9 张表)+ db/base

**Files:**
- Modify: `src/report_agent/db/base.py`, `src/report_agent/db/__init__.py`
- Create: `src/report_agent/db/models.py`, `tests/db/test_models.py`

**Interfaces:**
- Consumes: `Settings.postgres_dsn`(Task 2)
- Produces: `db.base.Base`(DeclarativeBase)、`db.base.create_async_engine_from_settings(settings) -> AsyncEngine`、`db.base.async_session_factory(engine) -> async_sessionmaker`;模型类 `Report / RawReportItem / NormalizedReportItem / InterpretationTask / Interpretation / FollowupPlan / ChatSession / ChatMessage / AuditEvent`(设计 §4.1 表字段一一对应)

- [ ] **Step 1: 写失败测试**

`backend/tests/db/test_models.py`:
```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable

from report_agent.db.base import Base
from report_agent.db import models  # noqa: F401  # 触发模型注册

EXPECTED_TABLES = {
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


def test_all_nine_tables_registered():
    names = set(Base.metadata.tables)
    assert names == EXPECTED_TABLES


def test_reports_columns():
    cols = {c.name: c for c in Base.metadata.tables["reports"].columns}
    for name in ("id", "source", "file_path", "institution", "report_date",
                 "sex", "age", "user_id", "created_at"):
        assert name in cols
    assert cols["source"].type.length >= 16  # pdf/photo/manual
    assert cols["sex"].type.length >= 8     # male/female
    assert str(cols["id"].type) == "UUID" or "UUID" in str(cols["id"].type)


def test_jsonb_columns_on_postgres_dialect():
    tasks = Base.metadata.tables["interpretation_tasks"]
    compiled = str(CreateTable(tasks).compile(dialect=postgresql.dialect()))
    assert "JSONB" in compiled  # checkpoints / timings / error 在 PG 上是 JSONB


def test_foreign_keys_point_to_reports():
    raw = Base.metadata.tables["report_items_raw"]
    fk = next(iter(raw.foreign_keys))
    assert fk.column.table.name == "reports"
    assert fk.ondelete == "CASCADE"
    norm = Base.metadata.tables["report_items_normalized"]
    norm_fk_names = {fk.target_fullname for fk in norm.foreign_keys}
    assert "reports.id" in norm_fk_names and "report_items_raw.id" in norm_fk_names


def test_audit_events_has_no_fk():
    audit = Base.metadata.tables["audit_events"]
    assert len(list(audit.foreign_keys)) == 0  # 审计快照不随源数据删除


async def test_create_all_on_sqlite(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        inspector = await conn.run_sync(sa.inspect)
        assert set(inspector.get_table_names()) == EXPECTED_TABLES
    await engine.dispose()
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/db/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError` / 表缺失

- [ ] **Step 3: 实现 db/base.py**

`backend/src/report_agent/db/base.py`:
```python
"""SQLAlchemy 声明基类与异步引擎工厂。"""
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from report_agent.config import Settings

__all__ = ["Base", "create_async_engine_from_settings", "async_session_factory"]


class Base(DeclarativeBase):
    pass


def create_async_engine_from_settings(settings: Settings) -> AsyncEngine:
    """按 Settings.postgres_dsn 创建异步引擎。"""
    return create_async_engine(settings.postgres_dsn, echo=False)


def async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

`backend/src/report_agent/db/__init__.py` 改为导出:
```python
"""Postgres 访问层(设计 §4.1)。"""
from report_agent.db.base import Base, async_session_factory, create_async_engine_from_settings

__all__ = ["Base", "create_async_engine_from_settings", "async_session_factory"]
```

- [ ] **Step 4: 实现 db/models.py**

`backend/src/report_agent/db/models.py`:
```python
"""9 张表的结构化存储模型(设计 §4.1)。枚举值一律存小写字符串。"""
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import JSON, DateTime, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from report_agent.db.base import Base

# JSONB 仅 PG 原生;其它方言(sqlite 测试)退化为通用 JSON
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class Report(Base):
    """体检报告元数据(§4.1 reports)。"""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(16))  # pdf / photo / manual
    file_path: Mapped[str | None] = mapped_column(String(512))
    institution: Mapped[str | None] = mapped_column(String(128))
    report_date: Mapped[date | None]
    sex: Mapped[str | None] = mapped_column(String(8))  # male / female,可为空(awaiting_meta)
    age: Mapped[int | None]
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)  # 预留多用户
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RawReportItem(Base):
    """解析原始输出,不可变事实(§4.1 report_items_raw)。"""

    __tablename__ = "report_items_raw"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[str | None] = mapped_column(String(64))  # 检验分组,如"血常规"
    item_name: Mapped[str] = mapped_column(String(128))
    value_text: Mapped[str | None] = mapped_column(String(64))  # 原始数值文本或定性结果
    value_num: Mapped[float | None]
    unit: Mapped[str | None] = mapped_column(String(32))
    ref_range_text: Mapped[str | None] = mapped_column(String(128))  # 报告自带区间原文
    abnormal_flag: Mapped[str | None] = mapped_column(String(16))  # 报告自带 ↑ / ↓ / H / L
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NormalizedReportItem(Base):
    """归一化 + 判定结果,可重跑的派生层(§4.1 report_items_normalized)。

    status: normal / high / low / critical_high / critical_low / unknown / unmapped
    """

    __tablename__ = "report_items_normalized"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    raw_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, sa.ForeignKey("report_items_raw.id", ondelete="CASCADE"), index=True
    )
    indicator_code: Mapped[str | None] = mapped_column(String(64), index=True)  # 空=unmapped
    indicator_name: Mapped[str | None] = mapped_column(String(128))
    value_num: Mapped[float | None]
    unit: Mapped[str | None] = mapped_column(String(32))
    value_text: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    ref_low: Mapped[float | None]
    ref_high: Mapped[float | None]
    is_abnormal: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InterpretationTask(Base):
    """异步任务与断点(§4.1 interpretation_tasks)。"""

    __tablename__ = "interpretation_tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    # pending / awaiting_meta / running / completed / failed / degraded
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    stage: Mapped[str | None] = mapped_column(String(32))  # 最后完成的阶段名
    checkpoints: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    timings: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    error: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Interpretation(Base):
    """四段式解读产物(§4.1 interpretations)。"""

    __tablename__ = "interpretations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("interpretation_tasks.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str] = mapped_column(sa.Text)
    items: Mapped[list] = mapped_column(JSONVariant, default=list)  # 逐项解读数组
    advice_summary: Mapped[dict] = mapped_column(JSONVariant, default=dict)  # 分级建议汇总
    disclaimer: Mapped[str] = mapped_column(sa.Text)
    degraded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FollowupPlan(Base):
    """复查计划(§4.1 followup_plans)。"""

    __tablename__ = "followup_plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("interpretation_tasks.id", ondelete="CASCADE")
    )
    items: Mapped[list] = mapped_column(JSONVariant, default=list)  # 项目/时间窗/科室/依据
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatSession(Base):
    """追问会话,锚定报告(§4.1 chat_sessions)。"""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatMessage(Base):
    """对话记录,独立于 LangGraph checkpoint(§4.1 chat_messages)。"""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant / tool
    content: Mapped[str] = mapped_column(sa.Text)
    tool_calls: Mapped[dict | None] = mapped_column(JSONVariant)
    evidence_ids: Mapped[list | None] = mapped_column(JSONVariant)
    guardrail_flags: Mapped[list | None] = mapped_column(JSONVariant)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditEvent(Base):
    """审计事件快照(§4.1 audit_events);无外键,不随源数据删除。"""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONVariant)
    report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 5: 运行验证通过**

Run: `cd backend && uv run pytest tests/db/test_models.py -v`
Expected: 5 passed + 1 async test passed

- [ ] **Step 6: Commit**

```bash
git add src/report_agent/db/ tests/db/
git commit -m "feat: add 9 postgres table models per design

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: LLM 客户端(重试 / 退避 / JSON 提取)

**Files:**
- Create: `src/report_agent/llm/__init__.py`(导出)、`src/report_agent/llm/client.py`
- Create: `tests/llm/test_client.py`

**Interfaces:**
- Consumes: `Settings.deepseek_api_key / deepseek_base_url / llm_model / llm_temperature / llm_max_retries / llm_timeout_seconds / llm_backoff_base`(Task 2)
- Produces:
  - `LLMError(RuntimeError)`,`JSONExtractError(LLMError, raw_text: str)`
  - `LLMClient(api_key, *, base_url=None, model=None, temperature=0.1, max_retries=3, timeout_seconds=60, backoff_base=1.0, create_fn=None)`
    - `async chat(messages: list[dict], *, temperature=None, max_tokens=None) -> str`(指数退避重试 3 次,1s/2s/4s)
    - `async chat_json(messages, *, temperature=None) -> dict`(chat + JSON 提取,提取失败抛 `JSONExtractError`)
  - `extract_json(text: str) -> dict`(模块级:去代码围栏后取首个 `{...}`,失败抛 `JSONExtractError`)
  - 不传 api_key 构造时抛 `ValueError`;`create_fn(api_key=, base_url=, timeout=)` 注入点用于测试与后续 provider 切换(设计 §5.5 私有化预留)

- [ ] **Step 1: 写失败测试**

`backend/tests/llm/test_client.py`:
```python
import asyncio
from types import SimpleNamespace

import pytest

from report_agent.llm.client import (
    JSONExtractError,
    LLMClient,
    LLMError,
    extract_json,
)


def _stub_chat(calls: list, fail_first: int = 0, content: str = "ok"):
    """返回可注入的 create_fn 桩:前 fail_first 次抛异常,之后返回 content。"""

    def create_fn(api_key, base_url=None, timeout=None):
        class _Stub:
            class _Completions:
                class _Messages:
                    async def create(self, **kwargs):
                        calls.append(kwargs)
                        if len(calls) <= fail_first:
                            raise TimeoutError("stub timeout")
                        return SimpleNamespace(
                            choices=[
                                SimpleNamespace(message=SimpleNamespace(content=content))
                            ]
                        )

                def __init__(self):
                    self.messages = self._Messages()

            def __init__(self):
                self.chat = self._Completions()

        return _Stub()

    return create_fn


def test_no_api_key_raises():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        LLMClient(api_key="")


def test_chat_retries_with_backoff(monkeypatch):
    calls: list[dict] = []
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("report_agent.llm.client.asyncio.sleep", fake_sleep)
    client = LLMClient(
        api_key="sk-test",
        model="deepseek-chat",
        max_retries=3,
        backoff_base=1.0,
        create_fn=_stub_chat(calls, fail_first=2, content="你好"),
    )
    result = asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
    assert result == "你好"
    assert len(calls) == 3          # 失败 2 次 + 成功 1 次
    assert sleeps == [1.0, 2.0]     # 指数退避


def test_chat_exhausts_retries_raises():
    calls: list[dict] = []
    client = LLMClient(
        api_key="sk-test",
        max_retries=2,
        create_fn=_stub_chat(calls, fail_first=99),
    )
    with pytest.raises(LLMError):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))


def test_chat_passes_model_temperature():
    calls: list[dict] = []
    client = LLMClient(
        api_key="sk-test",
        model="deepseek-chat",
        temperature=0.3,
        create_fn=_stub_chat(calls, content="ok"),
    )
    asyncio.run(client.chat([{"role": "user", "content": "hi"}], max_tokens=64))
    kwargs = calls[0]
    assert kwargs["model"] == "deepseek-chat"
    assert kwargs["temperature"] == 0.3
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["max_tokens"] == 64


def test_chat_json_parses_fenced_json():
    calls: list[dict] = []
    client = LLMClient(
        api_key="sk-test",
        create_fn=_stub_chat(calls, content='```json\n{"a": 1}\n```'),
    )
    assert asyncio.run(client.chat_json([{"role": "user", "content": "x"}])) == {"a": 1}


def test_extract_json_plain_and_nested():
    assert extract_json('{"a": {"b": [1, 2]}}') == {"a": {"b": [1, 2]}}
    assert extract_json('前文\n{"a": 1}\n后文') == {"a": 1}


def test_extract_json_invalid_raises():
    with pytest.raises(JSONExtractError) as ei:
        extract_json("不是 json 的文本")
    assert "不是 json 的文本" in ei.value.raw_text
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/llm/test_client.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 client.py**

`backend/src/report_agent/llm/client.py`:
```python
"""DeepSeek LLM 客户端:OpenAI 兼容接口 + 指数退避重试 + JSON 提取(设计 §3.3/§5.5)。"""
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class LLMError(RuntimeError):
    """LLM 调用在重试耗尽后仍失败。"""


class JSONExtractError(LLMError):
    """模型输出无法解析为 JSON。"""

    def __init__(self, raw_text: str):
        super().__init__("无法从模型输出提取 JSON")
        self.raw_text = raw_text


def extract_json(text: str) -> dict:
    """从模型输出提取 JSON:先整体解析,失败则剥代码围栏再取首个 {...}。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise JSONExtractError(text)
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JSONExtractError(text) from exc


class LLMClient:
    """OpenAI SDK 指向 DeepSeek 的异步客户端;create_fn 注入点用于测试与换 provider。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        max_retries: int = 3,
        timeout_seconds: int = 60,
        backoff_base: float = 1.0,
        create_fn=None,
    ):
        if not api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY,请配置 .env")
        self.model = model or "deepseek-chat"
        self.temperature = temperature
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        if create_fn is not None:
            self._client = create_fn(
                api_key=api_key,
                base_url=base_url or DEFAULT_DEEPSEEK_BASE_URL,
                timeout=timeout_seconds,
            )
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url or DEFAULT_DEEPSEEK_BASE_URL,
                timeout=timeout_seconds,
            )

    async def _create(self, messages, temperature, max_tokens) -> str:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens  # 为 None 时不传,避免 API 拒绝 null 字段
        resp = await self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def chat(self, messages, *, temperature=None, max_tokens=None) -> str:
        """指数退避重试(1s/2s/4s…),耗尽抛 LLMError。"""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._create(messages, temperature or self.temperature, max_tokens)
            except Exception as exc:  # noqa: BLE001 - 网络/API 错误一律重试
                last_exc = exc
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.backoff_base * (2**attempt))
        logger.error("llm 调用重试耗尽", exc=str(last_exc))
        raise LLMError(f"LLM 调用失败: {last_exc}") from last_exc

    async def chat_json(self, messages, *, temperature=None) -> dict:
        """chat 后提取 JSON;解析失败抛 JSONExtractError(不重试,属确定性失败)。"""
        text = await self.chat(messages, temperature=temperature)
        return extract_json(text)
```

`backend/src/report_agent/llm/__init__.py`:
```python
"""LLM 调用层(设计 §3.3)。"""
from report_agent.llm.client import LLMClient, LLMError, extract_json

__all__ = ["LLMClient", "LLMError", "extract_json"]
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && uv run pytest tests/llm/test_client.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/llm/ tests/llm/
git commit -m "feat: add deepseek llm client with backoff retry and json extraction

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Neo4j 客户端

**Files:**
- Create: `src/report_agent/knowledge/kg_client.py`(更新 `knowledge/__init__.py` 导出)
- Create: `tests/knowledge/test_kg_client.py`

**Interfaces:**
- Consumes: `Settings.neo4j_uri / neo4j_user / neo4j_password / neo4j_database`
- Produces: `Neo4jClient(uri, user, password, *, database="neo4j", driver_factory=None)`
  - `async run(query: str, **params) -> list[dict]`(在后台线程执行 driver 阻塞调用)
  - `async is_healthy() -> bool`;`close()`;异步上下文协议 `async with Neo4jClient(...) as c`
  - `driver_factory(uri, auth=(user, password))` 注入点;测试用桩 driver

- [ ] **Step 1: 写失败测试**

`backend/tests/knowledge/test_kg_client.py`:
```python
import asyncio

from report_agent.knowledge.kg_client import Neo4jClient


class StubSession:
    def __init__(self, records):
        self._records = records

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        return self._records


class StubDriver:
    def __init__(self, records):
        self.records = records
        self.closed = False

    def session(self, database=None):
        return StubSession(self.records)

    def close(self):
        self.closed = True


def _client(records):
    return Neo4jClient(
        "bolt://x:7687", "neo4j", "pw",
        driver_factory=lambda uri, auth=None: StubDriver(records),
    )


def test_run_returns_list_of_dicts():
    c = _client([{"a": 1}, {"b": 2}])
    out = asyncio.run(c.run("MATCH (n) RETURN n.a AS a"))
    assert out == [{"a": 1}, {"b": 2}]


def test_run_passes_params():
    seen = {}

    class ParamSession(StubSession):
        def run(self, query, **params):
            seen["query"] = query
            seen["params"] = params
            return [{"ok": True}]

    class ParamDriver(StubDriver):
        def session(self, database=None):
            return ParamSession([])

    c = Neo4jClient(
        "bolt://x", "u", "p", driver_factory=lambda uri, auth=None: ParamDriver([])
    )
    asyncio.run(c.run("MATCH (n {code: $code}) RETURN n", code="GLU"))
    assert seen["query"] == "MATCH (n {code: $code}) RETURN n"
    assert seen["params"] == {"code": "GLU"}


def test_is_healthy_true_and_false():
    assert asyncio.run(_client([{"1": 1}]).is_healthy()) is True

    class BoomSession(StubSession):
        def run(self, query, **params):
            raise RuntimeError("boom")

    class BoomDriver(StubDriver):
        def session(self, database=None):
            return BoomSession([])

    c = Neo4jClient(
        "bolt://x", "u", "p", driver_factory=lambda uri, auth=None: BoomDriver([])
    )
    assert asyncio.run(c.is_healthy()) is False


def test_close_and_async_context():
    holder: dict[str, StubDriver] = {}

    def factory(uri, auth=None):
        d = StubDriver([])
        holder["d"] = d
        return d

    async def scenario():
        async with Neo4jClient("bolt://x", "u", "p", driver_factory=factory) as cc:
            await cc.run("RETURN 1")

    asyncio.run(scenario())
    assert holder["d"].closed is True
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/knowledge/test_kg_client.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 kg_client.py**

`backend/src/report_agent/knowledge/kg_client.py`:
```python
"""Neo4j 客户端:参数化查询 + 健康检查(设计 §4.2 事实源访问)。

driver 阻塞调用一律经 asyncio.to_thread 放入后台线程,避免卡事件循环。
driver_factory 注入点用于单测与后续换连接池策略。
"""
import asyncio
import logging
from typing import Any, Callable

from neo4j import GraphDatabase

logger = logging.getLogger(__name__)


class Neo4jClient:
    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        database: str = "neo4j",
        driver_factory: Callable | None = None,
    ):
        self._database = database
        factory = driver_factory or GraphDatabase.driver
        self._driver = factory(uri, auth=(user, password))

    def _run_sync(self, query: str, params: dict) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            return [dict(record) for record in session.run(query, **params)]

    async def run(self, query: str, **params) -> list[dict[str, Any]]:
        """执行 Cypher,返回 dict 列表。外部错误原样抛出,由调用方降级。"""
        return await asyncio.to_thread(self._run_sync, query, params)

    async def is_healthy(self) -> bool:
        try:
            await self.run("RETURN 1 AS ok")
            return True
        except Exception as exc:  # noqa: BLE001 - 健康检查吞全部错误
            logger.warning("neo4j 健康检查失败: %s", exc)
            return False

    def close(self) -> None:
        self._driver.close()

    async def __aenter__(self) -> "Neo4jClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await asyncio.to_thread(self.close)
```

`backend/src/report_agent/knowledge/__init__.py`:
```python
"""知识与检索层(设计 §4.2-§4.3)。"""
from report_agent.knowledge.kg_client import Neo4jClient

__all__ = ["Neo4jClient"]
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && uv run pytest tests/knowledge/test_kg_client.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/knowledge/ tests/knowledge/
git commit -m "feat: add neo4j client with health check

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 单位换算(UnitConverter,纯代码)

**Files:**
- Create: `src/report_agent/knowledge/units.py`
- Create: `tests/knowledge/test_units.py`

**Interfaces:**
- Consumes: 无(叶模块)
- Produces:
  - `UnitConversionError(ValueError)`
  - `UnitConverter`:
    - `convert(value: float, from_unit: str, to_unit: str, *, indicator_code: str | None = None) -> float`
    - `to_canonical(value, from_unit, *, indicator_code: str | None = None, canonical_unit: str) -> float`
  - 族换算表:质量(kg/g/mg/µg/ug/mcg/ng)、体积(L/dL/mL/µL/ul);按指标覆盖表 `GLU: mg/dL→mmol/L`(因子 1/18.02)为范例,后续种子数据按指标扩展

- [ ] **Step 1: 写失败测试**

`backend/tests/knowledge/test_units.py`:
```python
import pytest

from report_agent.knowledge.units import UnitConversionError, UnitConverter


@pytest.fixture
def conv():
    return UnitConverter()


def test_same_unit_identity(conv):
    assert conv.convert(5.0, "mmol/L", "mmol/L") == 5.0


def test_mass_family(conv):
    assert conv.convert(1.0, "g", "mg") == 1000.0
    assert conv.convert(500.0, "mg", "g") == 0.5
    assert conv.convert(1.0, "kg", "g") == 1000.0
    assert conv.convert(1.0, "ug", "mg") == 0.001
    assert conv.convert(1.0, "mcg", "mg") == 0.001  # mcg 别名
    assert conv.convert(1.0, "µg", "mg") == 0.001   # 希腊字母微克


def test_volume_family(conv):
    assert conv.convert(1.0, "L", "dL") == 10.0
    assert conv.convert(1.0, "mL", "L") == 0.001
    assert conv.convert(1.0, "dl", "ml") == 100.0  # 大小写不敏感


def test_indicator_specific_factor(conv):
    # 空腹血糖:1 mmol/L = 18.02 mg/dL
    assert conv.convert(5.5, "mmol/L", "mg/dL", indicator_code="GLU") == pytest.approx(5.5 * 18.02, rel=1e-9)
    assert conv.convert(99.0, "mg/dL", "mmol/L", indicator_code="GLU") == pytest.approx(99.0 / 18.02, rel=1e-9)


def test_unsupported_pair_raises(conv):
    with pytest.raises(UnitConversionError):
        conv.convert(1.0, "mmol/L", "mg/dL")  # 无指标覆盖时不许乱换算


def test_unknown_unit_raises(conv):
    with pytest.raises(UnitConversionError):
        conv.convert(1.0, "个", "g")


def test_to_canonical(conv):
    assert conv.to_canonical(100.0, "mg", canonical_unit="g") == 0.1
    assert conv.to_canonical(5.5, "mmol/L", indicator_code="GLU", canonical_unit="mmol/L") == 5.5
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/knowledge/test_units.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 units.py**

`backend/src/report_agent/knowledge/units.py`:
```python
"""单位换算(设计 §5.2.2,纯代码)。

层级:同单位恒等 → 指标级覆盖表(摩尔质量类换算,按 indicator_code)→
通用族换算(质量/体积的 SI 前缀)。都不命中则抛 UnitConversionError,由
调用方(归一化)保留原值并标记 unverified。
"""
from __future__ import annotations


class UnitConversionError(ValueError):
    """无法完成的单位换算。"""


# 族表:别名(大小写不敏感)→ 相对基准(g / L)的系数
_MASS: dict[str, float] = {
    "kg": 1000.0,
    "g": 1.0,
    "mg": 1e-3,
    "ug": 1e-6,
    "µg": 1e-6,
    "mcg": 1e-6,
    "ng": 1e-9,
}
_VOLUME: dict[str, float] = {
    "l": 1.0,
    "dl": 0.1,
    "ml": 1e-3,
    "ul": 1e-6,
    "µl": 1e-6,
}

# 指标级覆盖:(code, from, to) → 乘子。范例:GLU mmol/L ↔ mg/dL(1 mmol/L=18.02 mg/dL)。
# 后续在知识库种子数据中按指标扩展;扩展时只加行,不改本模块结构。
_INDICATOR_FACTORS: dict[tuple[str, str, str], float] = {
    ("GLU", "mmol/l", "mg/dl"): 18.02,
    ("GLU", "mg/dl", "mmol/l"): 1 / 18.02,
}


def _norm(unit: str) -> str:
    return unit.strip().lower()


class UnitConverter:
    def convert(
        self,
        value: float,
        from_unit: str,
        to_unit: str,
        *,
        indicator_code: str | None = None,
    ) -> float:
        f, t = _norm(from_unit), _norm(to_unit)
        if f == t:
            return value
        if indicator_code is not None:
            factor = _INDICATOR_FACTORS.get((indicator_code, f, t))
            if factor is not None:
                return value * factor
        mf = _MASS.get(f)
        mt = _MASS.get(t)
        if mf is not None and mt is not None:
            return value * mf / mt
        vf = _VOLUME.get(f)
        vt = _VOLUME.get(t)
        if vf is not None and vt is not None:
            return value * vf / vt
        raise UnitConversionError(f"无法换算 {from_unit} → {to_unit}(指标 {indicator_code})")

    def to_canonical(
        self,
        value: float,
        from_unit: str,
        *,
        indicator_code: str | None = None,
        canonical_unit: str,
    ) -> float:
        return self.convert(value, from_unit, canonical_unit, indicator_code=indicator_code)
```

- [ ] **Step 4: 运行验证通过(含 100% 分支门禁)**

Run: `cd backend && uv run pytest tests/knowledge/test_units.py --cov=report_agent.knowledge.units --cov-branch --cov-report=term-missing --cov-fail-under=100 -v`
Expected: 7 passed,覆盖率 100%

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/knowledge/units.py tests/knowledge/test_units.py
git commit -m "feat: add pure unit converter with 100% branch coverage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 指标词典 + 名称规范化(纯代码)

**Files:**
- Create: `src/report_agent/knowledge/indicator_dict.py`
- Create: `tests/knowledge/test_indicator_dict.py`

**Interfaces:**
- Consumes: 无(叶模块;数据由构造时注入,后续计划从 Neo4j/种子 YAML 组装 `Indicator` 列表)
- Produces:
  - `Indicator(code, name, aliases=(), unit="", category="", normal_qual=None)`(frozen dataclass)
  - `normalize_name(name: str) -> str`:NFKC + casefold + 去空白(含全角)
  - `IndicatorDict(indicators: Iterable[Indicator])`
    - `lookup(raw_name: str) -> Indicator | None`:规范化后精确匹配 name/aliases
    - `candidates(raw_name: str, limit: int = 5) -> list[Indicator]`:规范化后包含匹配(供后续 LLM 批量映射的候选列表)
    - `__len__`、`all() -> list[Indicator]`

- [ ] **Step 1: 写失败测试**

`backend/tests/knowledge/test_indicator_dict.py`:
```python
import pytest

from report_agent.knowledge.indicator_dict import Indicator, IndicatorDict, normalize_name

INDICATORS = [
    Indicator(code="WBC", name="白细胞计数", aliases=("白细胞", "WBC", "白血球"),
              unit="10^9/L", category="血常规", normal_qual=None),
    Indicator(code="GLU", name="空腹血糖", aliases=("血糖", "FBG", "空腹葡萄糖"),
              unit="mmol/L", category="糖代谢", normal_qual=None),
    Indicator(code="URIC", name="尿酸", aliases=("UA",), unit="umol/L", category="肾功能"),
    Indicator(code="PRO", name="尿蛋白", unit="", category="尿常规", normal_qual="阴性"),
]


@pytest.fixture
def d():
    return IndicatorDict(INDICATORS)


def test_normalize_name_variants():
    # 全半角、大小写、空白(含全角空格)一律折叠
    assert normalize_name("白细胞计数") == normalize_name("白细胞计数")
    assert normalize_name("FBG") == normalize_name("fbg")
    assert normalize_name("尿 蛋白") == normalize_name("尿蛋白")
    assert normalize_name("ＧＬＵ") == normalize_name("glu")  # 全角字母


def test_lookup_exact_name_and_alias(d):
    assert d.lookup("白细胞计数").code == "WBC"
    assert d.lookup("血糖").code == "GLU"
    assert d.lookup("FBG").code == "GLU"
    assert d.lookup("fbg ").code == "GLU"


def test_lookup_unknown_returns_none(d):
    assert d.lookup("不知道啥指标") is None
    assert d.lookup("") is None


def test_lookup_no_false_partial_match(d):
    # "糖" 不是别名,精确 lookup 不应命中空腹血糖(部分匹配属 candidates 职责)
    assert d.lookup("糖") is None


def test_candidates_substring(d):
    codes = [i.code for i in d.candidates("血糖")]
    assert codes == ["GLU"]
    codes2 = [i.code for i in d.candidates("白")]
    assert "WBC" in codes2


def test_candidates_unknown_empty(d):
    assert d.candidates("完全不存在的词") == []


def test_len_and_all(d):
    assert len(d) == 4
    assert len(d.all()) == 4


def test_normal_qual_exposed(d):
    assert d.lookup("尿蛋白").normal_qual == "阴性"
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/knowledge/test_indicator_dict.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 indicator_dict.py**

`backend/src/report_agent/knowledge/indicator_dict.py`:
```python
"""指标词典:别名映射与名称规范化(设计 §5.2.1,纯代码)。

规则匹配(规范化精确)是主路径;LLM 批量映射(后续计划)只消费 candidates() 的
候选列表,匹配不了 → unmapped 继续,不中断流程。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def normalize_name(name: str) -> str:
    """NFKC(全角→半角)+ casefold + 去全部空白;等价写法的名称归一为同一 key。"""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return "".join(folded.split())


@dataclass(frozen=True)
class Indicator:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    unit: str = ""
    category: str = ""
    normal_qual: str | None = None  # 定性正常期望值,如"阴性"


class IndicatorDict:
    def __init__(self, indicators):
        self._by_key: dict[str, Indicator] = {}
        for ind in indicators:
            keys = {normalize_name(ind.name), *(normalize_name(a) for a in ind.aliases)}
            for key in keys:
                self._by_key.setdefault(key, ind)  # 先到先得,冲突时保留更靠前的条目

    def lookup(self, raw_name: str) -> Indicator | None:
        """规范化精确匹配 name/aliases;None=未映射(unmapped)。"""
        if not raw_name or not raw_name.strip():
            return None
        return self._by_key.get(normalize_name(raw_name))

    def candidates(self, raw_name: str, limit: int = 5) -> list[Indicator]:
        """规范化后包含匹配(子串),供 LLM 批量映射的候选列表。"""
        q = normalize_name(raw_name)
        if not q:
            return []
        out: list[Indicator] = []
        seen: set[str] = set()
        for ind in self._by_key.values():
            if ind.code in seen:
                continue
            keys = [normalize_name(ind.name), *(normalize_name(a) for a in ind.aliases)]
            if any(q in k for k in keys):
                out.append(ind)
                seen.add(ind.code)
            if len(out) >= limit:
                break
        return out

    def all(self) -> list[Indicator]:
        return list(self._by_key.values())

    def __len__(self) -> int:
        return len(self._by_key)
```

- [ ] **Step 4: 运行验证通过(含 100% 分支门禁)**

Run: `cd backend && uv run pytest tests/knowledge/test_indicator_dict.py --cov=report_agent.knowledge.indicator_dict --cov-branch --cov-report=term-missing --cov-fail-under=100 -v`
Expected: 8 passed,覆盖率 100%

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/knowledge/indicator_dict.py tests/knowledge/test_indicator_dict.py
git commit -m "feat: add indicator dictionary with alias matching

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 参考区间解析(纯代码)

**Files:**
- Create: `src/report_agent/knowledge/ranges.py`
- Create: `tests/knowledge/test_ranges.py`

**Interfaces:**
- Consumes: 无(叶模块)
- Produces:
  - `RangeSpec(sex="any", age_min=None, age_max=None, low=None, high=None, critical_low=None, critical_high=None, unit="", source_note="")`(frozen)
  - `resolve_ranges(specs: Sequence[RangeSpec], *, sex: str | None = None, age: int | None = None) -> RangeSpec | None`
    选择语义(设计 §5.2.4):性别的精确匹配优先于 any;年龄须满足 `age_min <= age < age_max`(无边界=开放);同优先级多条命中取**年龄窗口最窄**;无命中返回 None

- [ ] **Step 1: 写失败测试**

`backend/tests/knowledge/test_ranges.py`:
```python
import pytest

from report_agent.knowledge.ranges import RangeSpec, resolve_ranges


def make(low=3.9, high=6.1, **kw):
    return RangeSpec(low=low, high=high, **kw)


def test_exact_sex_preferred_over_any():
    specs = [
        make(sex="any", low=3.9, high=6.1),
        make(sex="male", low=4.0, high=6.1),
        make(sex="female", low=3.8, high=6.0),
    ]
    assert resolve_ranges(specs, sex="male", age=30).low == 4.0
    assert resolve_ranges(specs, sex="female", age=30).low == 3.8


def test_sex_any_fallback():
    specs = [make(sex="any", low=3.9, high=6.1)]
    assert resolve_ranges(specs, sex="male", age=30).low == 3.9
    assert resolve_ranges(specs, sex=None, age=30).low == 3.9  # 性别未知也可用 any


def test_age_window_boundaries():
    specs = [make(sex="any", age_min=18, age_max=60), make(sex="any", age_min=60, age_max=100)]
    assert resolve_ranges(specs, sex="male", age=18) is specs[0]
    assert resolve_ranges(specs, sex="male", age=59) is specs[0]
    assert resolve_ranges(specs, sex="male", age=60) is specs[1]  # age_max 右开
    assert resolve_ranges(specs, sex="male", age=17) is None      # 无开放区间


def test_narrowest_window_wins():
    specs = [
        make(sex="any", age_min=18, age_max=100, low=3.9),
        make(sex="any", age_min=40, age_max=60, low=4.4),  # 更窄,年龄 50 应命中
    ]
    r = resolve_ranges(specs, sex="female", age=50)
    assert r.low == 4.4


def test_age_unknown_only_open_ranges():
    specs = [make(sex="any", age_min=18, age_max=100)]
    assert resolve_ranges(specs, sex="male", age=None) is None
    specs2 = [make(sex="any"), make(sex="any", age_min=18, age_max=100)]
    assert resolve_ranges(specs2, sex="male", age=None) is specs2[0]  # 开放区间


def test_no_match_returns_none():
    assert resolve_ranges([], sex="male", age=30) is None
    assert resolve_ranges([make(sex="female")], sex="male", age=30) is None


def test_one_sided_and_none_bounds_allowed():
    spec = make(sex="any", low=None, high=6.1)
    assert resolve_ranges([spec], sex="any", age=None) is spec
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/knowledge/test_ranges.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 ranges.py**

`backend/src/report_agent/knowledge/ranges.py`:
```python
"""参考区间选择(设计 §5.2.4,纯代码)。

只做"选哪条区间",不做数值判定(判定在 pipeline.rules)。
RangeSpec 数值边界均可为空:一侧开放(如 low=None 表示只有上限);
age_min/age_max 为 None 表示该侧开放。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RangeSpec:
    sex: str = "any"  # male / female / any
    age_min: int | None = None
    age_max: int | None = None  # 右开:age < age_max 命中
    low: float | None = None
    high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None
    unit: str = ""
    source_note: str = ""


def _age_fits(spec: RangeSpec, age: int | None) -> bool:
    if age is None:
        return spec.age_min is None and spec.age_max is None  # 年龄未知只认开放区间
    if spec.age_min is not None and age < spec.age_min:
        return False
    if spec.age_max is not None and age >= spec.age_max:
        return False
    return True


def _sex_fits(spec: RangeSpec, sex: str | None) -> bool:
    if spec.sex == "any":
        return True
    return sex is not None and spec.sex == sex


def _window_width(spec: RangeSpec) -> float:
    lo = spec.age_min if spec.age_min is not None else -math.inf
    hi = spec.age_max if spec.age_max is not None else math.inf
    return hi - lo


def resolve_ranges(
    specs: Sequence[RangeSpec], *, sex: str | None = None, age: int | None = None
) -> RangeSpec | None:
    """按 (指标×性别×年龄段) 选参考区间。

    先按性别过滤:存在 sex 精确匹配候选时丢弃 any 候选;随后在年龄命中集中取
    年龄窗口最窄的一条。无命中返回 None(调用方标记 unknown)。
    """
    age_fit = [s for s in specs if _age_fits(s, age)]
    if not age_fit:
        return None
    exact = [s for s in age_fit if s.sex != "any" and _sex_fits(s, sex)]
    pool = exact if exact else [s for s in age_fit if s.sex == "any"]
    if not pool:
        return None
    return min(pool, key=_window_width)
```

- [ ] **Step 4: 运行验证通过(含 100% 分支门禁)**

Run: `cd backend && uv run pytest tests/knowledge/test_ranges.py --cov=report_agent.knowledge.ranges --cov-branch --cov-report=term-missing --cov-fail-under=100 -v`
Expected: 7 passed,覆盖率 100%

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/knowledge/ranges.py tests/knowledge/test_ranges.py
git commit -m "feat: add reference range resolution logic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 规则比对(judge_numeric / judge_qualitative,纯代码,单测 100%)

**Files:**
- Create: `src/report_agent/pipeline/rules.py`
- Create: `tests/pipeline/test_rules.py`

**Interfaces:**
- Consumes: 无(叶模块,依赖仅 dataclasses/enum)
- Produces:
  - `RuleStatus(StrEnum)`: `NORMAL="normal" / HIGH="high" / LOW="low" / CRITICAL_HIGH="critical_high" / CRITICAL_LOW="critical_low" / UNKNOWN="unknown"`
  - `Judgment(status: RuleStatus, abnormal: bool = False, critical: bool = False)`(frozen dataclass)
  - `judge_numeric(value: float, low=None, high=None, critical_low=None, critical_high=None) -> Judgment`
  - `judge_qualitative(value_text: str | None, normal_qual: str | None) -> Judgment`
  - 判定语义(设计 §5.3,写进 docstring 并在测试固化):NaN → unknown;两侧参考均缺 → unknown;危急值**包含边界**(value ≤ critical_low 或 ≥ critical_high);普通异常**不含边界**(value < low 或 > high);定性值与期望值规范化相等 → normal,不等 → abnormal=True 且 status=unknown(方向不可知),无期望值 → unknown

- [ ] **Step 1: 写失败测试**

`backend/tests/pipeline/test_rules.py`:
```python
import math

import pytest

from report_agent.pipeline.rules import (
    Judgment,
    RuleStatus,
    judge_numeric,
    judge_qualitative,
)


@pytest.mark.parametrize(
    "value,low,high,clow,chigh,expected",
    [
        (5.0, 3.9, 6.1, None, None, RuleStatus.NORMAL),
        (3.9, 3.9, 6.1, None, None, RuleStatus.NORMAL),   # ==low 正常
        (6.1, 3.9, 6.1, None, None, RuleStatus.NORMAL),   # ==high 正常
        (3.8, 3.9, 6.1, None, None, RuleStatus.LOW),
        (6.2, 3.9, 6.1, None, None, RuleStatus.HIGH),
        (2.7, 3.9, 6.1, 2.8, 22.0, RuleStatus.CRITICAL_LOW),   # < critical_low
        (2.8, 3.9, 6.1, 2.8, 22.0, RuleStatus.CRITICAL_LOW),   # ==critical_low 也是危急
        (22.0, 3.9, 6.1, 2.8, 22.0, RuleStatus.CRITICAL_HIGH), # ==critical_high 危急
        (30.0, 3.9, 6.1, 2.8, 22.0, RuleStatus.CRITICAL_HIGH),
        (10.0, None, 6.1, None, None, RuleStatus.HIGH),   # 只有上限
        (5.0, 3.9, None, None, None, RuleStatus.NORMAL),  # 只有下限
        (1.0, 3.9, None, None, None, RuleStatus.LOW),
        (5.0, None, None, None, None, RuleStatus.UNKNOWN),  # 无参考
        (math.nan, 3.9, 6.1, None, None, RuleStatus.UNKNOWN),  # NaN
        (math.inf, 3.9, 6.1, 22.0, 22.0, RuleStatus.CRITICAL_HIGH),  # inf 归危急
        (-math.inf, 3.9, 6.1, 2.8, 22.0, RuleStatus.CRITICAL_LOW),
    ],
)
def test_judge_numeric_matrix(value, low, high, clow, chigh, expected):
    j = judge_numeric(value, low=low, high=high, critical_low=clow, critical_high=chigh)
    assert j.status == expected
    assert j.abnormal == (expected not in (RuleStatus.NORMAL, RuleStatus.UNKNOWN))
    assert j.critical == (expected in (RuleStatus.CRITICAL_LOW, RuleStatus.CRITICAL_HIGH))


def test_judge_qualitative_cases():
    assert judge_qualitative("阴性", "阴性") == Judgment(RuleStatus.NORMAL)
    assert judge_qualitative("++", "阴性") == Judgment(RuleStatus.UNKNOWN, abnormal=True)
    assert judge_qualitative("阴 性", "阴性") == Judgment(RuleStatus.NORMAL)  # 空格容错
    assert judge_qualitative("++", None) == Judgment(RuleStatus.UNKNOWN)      # 无期望值
    assert judge_qualitative(None, "阴性") == Judgment(RuleStatus.UNKNOWN)    # 缺值


def test_judgment_equality_fields():
    a = Judgment(RuleStatus.HIGH)
    assert a.abnormal is True and a.critical is False
    b = Judgment(RuleStatus.CRITICAL_LOW, abnormal=True, critical=True)
    assert b.status == RuleStatus.CRITICAL_LOW and b.critical is True
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/pipeline/test_rules.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 rules.py**

`backend/src/report_agent/pipeline/rules.py`:
```python
"""区间判定与危急值(设计 §5.3,纯代码,禁止 LLM 参与)。

判定语义(测试已固化):
- 数值 NaN → UNKNOWN;两侧参考均缺失 → UNKNOWN
- 危急值包含边界:value ≤ critical_low 或 value ≥ critical_high
- 普通异常不含边界:value < low 或 value > high;low/high 可单侧缺失
- 定性(阴阳性等):规范化相等 → NORMAL;不等 → abnormal=True 且 status=UNKNOWN
  (方向不可知,只标异常);无期望值 → UNKNOWN
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuleStatus(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"
    UNKNOWN = "unknown"


_ABNORMAL_STATUSES = {
    RuleStatus.HIGH,
    RuleStatus.LOW,
    RuleStatus.CRITICAL_HIGH,
    RuleStatus.CRITICAL_LOW,
}
_CRITICAL_STATUSES = {RuleStatus.CRITICAL_HIGH, RuleStatus.CRITICAL_LOW}


@dataclass(frozen=True)
class Judgment:
    status: RuleStatus
    abnormal: bool = False
    critical: bool = False


def _qual_eq(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return "".join(a.casefold().split()) == "".join(b.casefold().split())


def judge_numeric(
    value: float,
    low: float | None = None,
    high: float | None = None,
    critical_low: float | None = None,
    critical_high: float | None = None,
) -> Judgment:
    """数值比对:参考区间判定 + 危急值识别(全代码)。"""
    if value != value:  # NaN
        return Judgment(RuleStatus.UNKNOWN)
    if low is None and high is None:
        return Judgment(RuleStatus.UNKNOWN)
    if critical_low is not None and value <= critical_low:
        status = RuleStatus.CRITICAL_LOW
    elif critical_high is not None and value >= critical_high:
        status = RuleStatus.CRITICAL_HIGH
    elif high is not None and value > high:
        status = RuleStatus.HIGH
    elif low is not None and value < low:
        status = RuleStatus.LOW
    else:
        status = RuleStatus.NORMAL
    return Judgment(
        status=status,
        abnormal=status in _ABNORMAL_STATUSES,
        critical=status in _CRITICAL_STATUSES,
    )


def judge_qualitative(value_text: str | None, normal_qual: str | None) -> Judgment:
    """定性结果与词典期望值比对(设计 §5.3)。"""
    if value_text is None or not value_text.strip():
        return Judgment(RuleStatus.UNKNOWN)
    if normal_qual is None or not normal_qual.strip():
        return Judgment(RuleStatus.UNKNOWN)
    if _qual_eq(value_text, normal_qual):
        return Judgment(RuleStatus.NORMAL)
    return Judgment(RuleStatus.UNKNOWN, abnormal=True)
```

- [ ] **Step 4: 运行验证通过(含 100% 分支门禁)**

Run: `cd backend && uv run pytest tests/pipeline/test_rules.py --cov=report_agent.pipeline.rules --cov-branch --cov-report=term-missing --cov-fail-under=100 -v`
Expected: 18 passed(参数化矩阵 16 行 + 定性 1 + equality 1),覆盖率 100%

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/pipeline/rules.py tests/pipeline/test_rules.py
git commit -m "feat: add rule comparator for reference ranges and critical values

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: 组合模式识别(PatternMatcher,纯代码)

**Files:**
- Create: `src/report_agent/pipeline/patterns.py`
- Create: `tests/pipeline/test_patterns.py`

**Interfaces:**
- Consumes: `pipeline.rules.Judgment / RuleStatus`(Task 9)
- Produces:
  - `PatternCriterion(indicator_code: str, direction: Literal["high","low","abnormal"] | str)`(frozen)
  - `PatternDef(code: str, name: str, description: str = "", criteria: tuple[PatternCriterion, ...] = ())`(frozen)
    - `PatternDef.from_dict(d: dict) -> PatternDef`、`to_dict() -> dict`(与种子 YAML 的 criteria_json 结构一致)
  - `PatternMatcher`:`match(pattern, judgments: Mapping[str, Judgment]) -> bool`(全部条件满足才命中;缺指标 → 不命中;空 criteria → False);`match_all(patterns, judgments) -> list[PatternDef]`
  - 方向语义:high → status ∈ {high, critical_high};low → {low, critical_low};abnormal → `judgment.abnormal`

- [ ] **Step 1: 写失败测试**

`backend/tests/pipeline/test_patterns.py`:
```python
import pytest

from report_agent.pipeline.rules import Judgment, RuleStatus
from report_agent.pipeline.patterns import (
    PatternCriterion,
    PatternDef,
    PatternMatcher,
)

HIGH = Judgment(RuleStatus.HIGH, abnormal=True)
CRITICAL_HIGH = Judgment(RuleStatus.CRITICAL_HIGH, abnormal=True, critical=True)
LOW = Judgment(RuleStatus.LOW, abnormal=True)
NORMAL = Judgment(RuleStatus.NORMAL)
UNKNOWN = Judgment(RuleStatus.UNKNOWN, abnormal=True)  # 定性异常,方向不可知


def test_direction_semantics():
    m = PatternMatcher()
    p = PatternDef(code="p1", name="测试", criteria=(PatternCriterion("A", "high"),))
    assert m.match(p, {"A": HIGH}) is True
    assert m.match(p, {"A": CRITICAL_HIGH}) is True   # critical 视为 high
    assert m.match(p, {"A": LOW}) is False
    assert m.match(p, {"A": NORMAL}) is False
    p_low = PatternDef(code="p2", name="低", criteria=(PatternCriterion("B", "low"),))
    assert m.match(p_low, {"B": LOW}) is True
    p_abn = PatternDef(code="p3", name="异常", criteria=(PatternCriterion("C", "abnormal"),))
    assert m.match(p_abn, {"C": UNKNOWN}) is True  # 定性异常计入 abnormal
    assert m.match(p_abn, {"C": NORMAL}) is False


def test_all_criteria_required_and_missing_code():
    m = PatternMatcher()
    p = PatternDef(
        code="metabolic",
        name="代谢紊乱",
        criteria=(PatternCriterion("A", "high"), PatternCriterion("B", "high")),
    )
    assert m.match(p, {"A": HIGH, "B": HIGH}) is True
    assert m.match(p, {"A": HIGH}) is False      # 缺 B
    assert m.match(p, {"A": HIGH, "B": LOW}) is False


def test_empty_criteria_never_match():
    m = PatternMatcher()
    p = PatternDef(code="empty", name="空", criteria=())
    assert m.match(p, {"A": HIGH}) is False


def test_match_all_returns_matched_only():
    m = PatternMatcher()
    p1 = PatternDef(code="p1", name="a", criteria=(PatternCriterion("A", "high"),))
    p2 = PatternDef(code="p2", name="b", criteria=(PatternCriterion("B", "high"),))
    got = m.match_all([p1, p2], {"A": HIGH})
    assert [p.code for p in got] == ["p1"]


def test_from_dict_roundtrip_and_validation():
    d = {
        "code": "mets",
        "name": "代谢综合征风险",
        "description": "多指标联合",
        "criteria": [
            {"indicator_code": "TG", "direction": "high"},
            {"indicator_code": "HDL", "direction": "low"},
        ],
    }
    p = PatternDef.from_dict(d)
    assert p.to_dict() == d
    with pytest.raises(ValueError, match="direction"):
        PatternDef.from_dict({"code": "x", "name": "x",
                              "criteria": [{"indicator_code": "A", "direction": "侧向"}]})
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && uv run pytest tests/pipeline/test_patterns.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 patterns.py**

`backend/src/report_agent/pipeline/patterns.py`:
```python
"""指标组合模式识别(设计 §5.3,纯代码)。

Pattern.criteria_json 在种子 YAML 中与 PatternCriterion.to_dict() 同构,
入库后由本模块还原执行——规则结构化、可单测,不依赖 LLM 解读规则文本。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from report_agent.pipeline.rules import Judgment, RuleStatus

_DIRECTION = Literal["high", "low", "abnormal"]

_HIGH_STATUSES = {RuleStatus.HIGH, RuleStatus.CRITICAL_HIGH}
_LOW_STATUSES = {RuleStatus.LOW, RuleStatus.CRITICAL_LOW}


@dataclass(frozen=True)
class PatternCriterion:
    indicator_code: str
    direction: str  # high / low / abnormal(运行时校验)

    def matches(self, judgment: Judgment) -> bool:
        if self.direction == "high":
            return judgment.status in _HIGH_STATUSES
        if self.direction == "low":
            return judgment.status in _LOW_STATUSES
        if self.direction == "abnormal":
            return judgment.abnormal
        raise ValueError(f"未知方向: {self.direction}")

    @classmethod
    def from_dict(cls, d: dict) -> "PatternCriterion":
        code = d.get("indicator_code") or d.get("indicatorCode")
        direction = d.get("direction")
        if not code or direction not in ("high", "low", "abnormal"):
            raise ValueError(f"非法的模式条件: {d}")
        return cls(code, direction)

    def to_dict(self) -> dict:
        return {"indicator_code": self.indicator_code, "direction": self.direction}


@dataclass(frozen=True)
class PatternDef:
    code: str
    name: str
    description: str = ""
    criteria: tuple[PatternCriterion, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> "PatternDef":
        return cls(
            code=d["code"],
            name=d["name"],
            description=d.get("description", ""),
            criteria=tuple(PatternCriterion.from_dict(c) for c in d.get("criteria", [])),
        )

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "criteria": [c.to_dict() for c in self.criteria],
        }


class PatternMatcher:
    """全部条件满足 → 命中;空 criteria 永不命中(防误配全命中)。"""

    def match(self, pattern: PatternDef, judgments: Mapping[str, Judgment]) -> bool:
        if not pattern.criteria:
            return False
        for c in pattern.criteria:
            judgment = judgments.get(c.indicator_code)
            if judgment is None or not c.matches(judgment):
                return False
        return True

    def match_all(
        self, patterns, judgments: Mapping[str, Judgment]
    ) -> list[PatternDef]:
        return [p for p in patterns if self.match(p, judgments)]
```

- [ ] **Step 4: 运行验证通过(含 100% 分支门禁)**

Run: `cd backend && uv run pytest tests/pipeline/test_patterns.py --cov=report_agent.pipeline.patterns --cov-branch --cov-report=term-missing --cov-fail-under=100 -v`
Expected: 5 passed,覆盖率 100%

- [ ] **Step 5: Commit**

```bash
git add src/report_agent/pipeline/patterns.py tests/pipeline/test_patterns.py
git commit -m "feat: add pattern matcher for indicator combinations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: 全量回归 + README 收尾(里程碑提交)

**Files:**
- Modify: `README.md`(仓库根,补 backend 开发指引)

- [ ] **Step 1: 跑全量测试(含全部规则模块 100% 门禁)**

Run(在 `backend/` 下):
```
uv run pytest --cov=report_agent.knowledge.units --cov=report_agent.knowledge.indicator_dict --cov=report_agent.knowledge.ranges --cov=report_agent.pipeline.rules --cov=report_agent.pipeline.patterns --cov-branch --cov-fail-under=100 -v
```
Expected: 全部 passed(预期 69 个用例:3+4+6+7+4+7+8+7+18+5),覆盖率 100%

- [ ] **Step 2: 补 README 快速开始**

仓库根 `README.md` 追加(替换原"快速开始见 backend/README.md"一句):
```markdown
## 快速开始(backend)

```bash
cd backend
cp .env.example .env      # 填入 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY
docker compose up -d      # postgres / neo4j / milvus(含 etcd、minio)
uv sync                   # 安装依赖
uv run pytest             # 运行测试(规则层 100% 分支覆盖)
```
```
(README 代码块内嵌套三反引号,书写时按 markdown 规则转义/分层,以渲染结果为准)

- [ ] **Step 3: 验证目录结构符合设计 §3.2**

Run: `cd backend && find src/report_agent -name "*.py" | sort`
Expected: 包含 config.py / observability/ / db/(base.py, models.py)/ llm/(client.py)/ knowledge/(units.py, indicator_dict.py, ranges.py, kg_client.py)/ pipeline/(rules.py, patterns.py) 且无多余模块

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add backend quick start, milestone: infra + rules foundation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: 输出交付说明(给用户/后续计划)**

交付摘要应包含:本计划产出的全部接口签名(见各任务 Interfaces 块)、规则层 100% 覆盖率测试通过、`docker compose up -d` 后三个中间件就绪。**未实现、留给 Plan 2 的**:Milvus 客户端与 schema(jieba analyzer + BM25 Function 验证,设计 §15.1)、Embedding 客户端(DashScope text-embedding-v3)、种子 YAML 起草/校对/入库链、三路检索 + RRF。
