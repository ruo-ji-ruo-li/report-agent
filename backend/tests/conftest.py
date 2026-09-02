"""测试公共配置。

1. src-layout 项目需保证 `report_agent` 可被各测试导入: 这里把 `backend/src`
   提前插入 sys.path(项目已配 hatchling editable 安装时二者指向同一份源码, 不冲突)。
2. autouse 打桩外部依赖健康检查: 未接线服务不应触发真实网络连接, 统一抛异常,
   由 `health._check` 捕获返回 False —— 使 /api/health 测试快速且封闭。
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _raise_connection_error(*_args, **_kwargs):
    raise ConnectionError("stubbed: external service unavailable")


@pytest.fixture(autouse=True)
def _stub_external_health_checks(monkeypatch):
    """外部依赖健康检查打桩。health 端点内 `_pg_check` 走模块引用, check_milvus/
    check_neo4j 走 call-time from-import, 均从此处声明的模块属性解析, 打桩可生效。"""
    monkeypatch.setattr("report_agent.api.health._pg_check", _raise_connection_error)
    monkeypatch.setattr("report_agent.knowledge.milvus_client.check_milvus", _raise_connection_error)
    monkeypatch.setattr("report_agent.knowledge.kg_client.check_neo4j", _raise_connection_error)
