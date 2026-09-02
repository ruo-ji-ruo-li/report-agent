"""迁移 0001_init 与 models.py 的轻量一致性差分检查(不需要真库)。

思路: 在 sqlite 上执行两条等价的建表路径——
1) 手写迁移: 经 alembic MigrationContext/Operations 直接跑 versions/0001_init.py
   的 upgrade()(与真实 `alembic upgrade` 相同的执行机制,不依赖 env.py);
2) 模型: models.Base.metadata.create_all();
然后反射两边 schema 做差分: 表集合 / 列(类型、长度、nullable、server_default)/
主键 / 外键(目标、ondelete)/ unique 约束 / 非 unique 索引。

已知局限(有意接受, 最终语义以真实 postgres 为准, 见任务报告 deferred 项):
- sqlite 无 now()/CURRENT_TIMESTAMP 差异且无 timezone 语义: 测试把迁移里的
  server_default text("now()") 垫成 CURRENT_TIMESTAMP(与模型 func.now() 在
  sqlite 下的渲染一致), 因此时间戳列的时区标志另用静态检查(test_datetime_columns_
  are_timezone_aware)单独核对;
- sqlite 反射把 DateTime(timezone=True/False) 都显示为无时区 DATETIME, 无法
  覆盖时区; postgres 实跑留给后续任务在有库环境执行 `alembic upgrade head`。
"""
import ast
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from report_agent.db import models

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0001_init.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("alembic_0001_init", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(engine: sa.Engine) -> dict:
    insp = sa.inspect(engine)
    snapshot = {}
    for table in sorted(insp.get_table_names()):
        columns = {}
        for col in insp.get_columns(table):
            typ = col["type"]
            columns[col["name"]] = {
                "nullable": col["nullable"],
                "type": typ.__class__.__name__,
                "length": getattr(typ, "length", None),
                "server_default": str(col["server_default"]) if col.get("server_default") is not None else None,
            }
        snapshot[table] = {
            "columns": columns,
            "pk": sorted(insp.get_pk_constraint(table)["constrained_columns"]),
            "fks": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk.get("ondelete"),
                )
                for fk in insp.get_foreign_keys(table)
            ),
            "uniques": sorted(frozenset(u["column_names"]) for u in insp.get_unique_constraints(table)),
            # unique 约束在 sqlite 反射中可能以 autoindex 形式出现, 唯一性已由 uniques 比较覆盖,
            # 这里只比非 unique 索引(名称 + 列)以核对模型 index=True 的对应关系。
            "indexes": sorted(
                (ix["name"], tuple(ix["column_names"]))
                for ix in insp.get_indexes(table)
                if not ix["unique"]
            ),
        }
    return snapshot


def _stub_now(monkeypatch):
    """sqlite 没有 now(): 把迁移中的 sa.text("now()") 换成 CURRENT_TIMESTAMP。
    模型侧 func.now() 由 sqlite 方言直接渲染为 CURRENT_TIMESTAMP, 两边保持一致。"""
    real_text = sa.text

    def fake_text(sql, *args, **kwargs):
        if sql == "now()":
            sql = "CURRENT_TIMESTAMP"
        return real_text(sql, *args, **kwargs)

    monkeypatch.setattr(sa, "text", fake_text)


def test_migration_upgrade_matches_models(tmp_path, monkeypatch):
    _stub_now(monkeypatch)
    migration = _load_migration_module()

    migrated_engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with migrated_engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        migration.op = Operations(ctx)  # 等价于 env.py 中 alembic 注入的模块级 op
        migration.upgrade()

    model_engine = sa.create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    models.Base.metadata.create_all(model_engine)

    migrated, modeled = _snapshot(migrated_engine), _snapshot(model_engine)
    assert set(migrated) == set(modeled)
    for table in sorted(set(migrated) & set(modeled)):
        assert migrated[table] == modeled[table], f"table {table} differs"
    assert len(migrated) == 9


def test_datetime_columns_are_timezone_aware_and_complete():
    """sqlite 差分看不到 timezone 标志, 这里用 AST 静态核对迁移中每一处 DateTime 列
    (按 表名×列名 计)与模型完全一致且全部 timezone=True。"""
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))

    migration_dt = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "attr", None) != "create_table":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or getattr(sub.func, "attr", None) != "Column":
                continue
            if len(sub.args) < 2 or not isinstance(sub.args[1], ast.Call):
                continue
            type_call = sub.args[1]
            if getattr(type_call.func, "attr", None) != "DateTime":
                continue
            tz = any(
                kw.arg == "timezone" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in type_call.keywords
            )
            migration_dt[(node.args[0].value, sub.args[0].value)] = tz

    model_dt = {
        (table.name, col.name): isinstance(col.type, sa.DateTime)
        for table in models.Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, sa.DateTime)
    }
    assert migration_dt == model_dt
    assert all(migration_dt.values()), "migration DateTime 列必须全部 timezone=True"
    assert len(migration_dt) == 8  # 9 表共 8 处时间戳列(仅 interpretation_tasks 有 updated_at)
