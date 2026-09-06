"""迁移 0001→0002 链与 models.py 的轻量一致性差分检查(不需要真库)。

思路同旧版:sqlite 上依次执行 0001、0002 的 upgrade(),与 models create_all()
反射差分;时间戳 server_default 垫 CURRENT_TIMESTAMP(stub_now),时区标志
用 AST 静态核对(create_table 与 add_column 的 DateTime 列一并收集)。
"""
import ast
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from report_agent.db import models

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATIONS = [_VERSIONS / "0001_init.py", _VERSIONS / "0002_parse_upgrade.py"]


def _load_migration_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(engine: sa.Engine) -> dict:
    """反射 schema 差分快照(与旧版一致:表/列/主键/外键/unique/非 unique 索引)。"""
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
            "indexes": sorted(
                (ix["name"], tuple(ix["column_names"]))
                for ix in insp.get_indexes(table)
                if not ix["unique"]
            ),
        }
    return snapshot


def _stub_now(monkeypatch):
    """sqlite 没有 now():把迁移中的 sa.text("now()") 换成 CURRENT_TIMESTAMP。
    模型侧 func.now() 由 sqlite 方言直接渲染为 CURRENT_TIMESTAMP,两边保持一致。"""
    real_text = sa.text

    def fake_text(sql, *args, **kwargs):
        if sql == "now()":
            sql = "CURRENT_TIMESTAMP"
        return real_text(sql, *args, **kwargs)

    monkeypatch.setattr(sa, "text", fake_text)


def test_migration_chain_matches_models(tmp_path, monkeypatch):
    _stub_now(monkeypatch)
    migrated_engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with migrated_engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        for path in _MIGRATIONS:
            mod = _load_migration_module(path)
            mod.op = Operations(ctx)
            mod.upgrade()

    model_engine = sa.create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    models.Base.metadata.create_all(model_engine)

    migrated, modeled = _snapshot(migrated_engine), _snapshot(model_engine)
    assert set(migrated) == set(modeled)
    for table in sorted(set(migrated) & set(modeled)):
        assert migrated[table] == modeled[table], f"table {table} differs"
    assert len(migrated) == 11  # 原 9 表 + unparsed_tables + item_section_mapping


def _collect_dt(migration_dt: dict, table_name: str, col_node) -> None:
    if len(col_node.args) < 2 or not isinstance(col_node.args[1], ast.Call):
        return
    type_call = col_node.args[1]
    if getattr(type_call.func, "attr", None) != "DateTime":
        return
    tz = any(
        kw.arg == "timezone" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in type_call.keywords
    )
    migration_dt[(table_name, col_node.args[0].value)] = tz


def test_datetime_columns_are_timezone_aware_and_complete():
    """AST 静态核对:迁移(create_table + add_column)中每处 DateTime 列与模型一一对应
    且全部 timezone=True;11 表 × 2 = 22 处。"""
    migration_dt = {}
    for path in _MIGRATIONS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "create_table":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and getattr(sub.func, "attr", None) == "Column":
                        _collect_dt(migration_dt, node.args[0].value, sub)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_column":
                _collect_dt(migration_dt, node.args[0].value, node.args[1])

    model_dt = {
        (table.name, col.name): isinstance(col.type, sa.DateTime)
        for table in models.Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, sa.DateTime)
    }
    assert migration_dt == model_dt
    assert all(migration_dt.values()), "迁移 DateTime 列必须全部 timezone=True"
    assert len(migration_dt) == 22
