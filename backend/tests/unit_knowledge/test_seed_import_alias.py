"""upsert_indicator 别名节点写入单测(KG 点查设计 §5.1/§5.2):name+aliases 原样
MERGE 为 Alias 节点、HAS_ALIAS 入出边收敛列表、孤儿清扫覆盖 Alias。"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import seed_import

from report_agent.knowledge.seed_schemas import IndicatorSeed


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))


def _seed() -> IndicatorSeed:
    return IndicatorSeed(code="GLU", name="空腹血糖", aliases=["血糖", "FBG"],
                         unit="mmol/L", unit_conversions={"mg/dL": 0.0555},
                         category="糖代谢", description="d")


def test_upsert_indicator_writes_alias_nodes_for_name_and_aliases():
    s = FakeSession()
    seed_import.upsert_indicator(s, _seed())
    # 出边收敛语句包含 HAS_ALIAS
    assert any("HAS_ALIAS" in c[0] and "DELETE r" in c[0] for c in s.calls)
    # name + 全部 aliases 原样 MERGE 为 Alias 节点,顺序保持
    keys = [c[1]["key"] for c in s.calls if "Alias" in c[0] and "MERGE" in c[0]]
    assert keys == ["空腹血糖", "血糖", "FBG"]
    # 原样存储:不做归一化("FBG" 不转小写)
    assert "fbg" not in keys
    # 孤儿清扫覆盖 Alias
    assert any("MATCH (a:Alias) WHERE NOT (a)<--()" in c[0] for c in s.calls)


def test_upsert_indicator_dedupes_duplicate_keys():
    s = FakeSession()
    seed_import.upsert_indicator(s, _seed().model_copy(update={"aliases": ["血糖", "血糖"]}))
    keys = [c[1]["key"] for c in s.calls if "Alias" in c[0] and "MERGE" in c[0]]
    assert keys == ["空腹血糖", "血糖"]
