"""seed_import 图写入收敛(I1):fake-session 渲染断言,不依赖真实 Neo4j。

upsert_indicator 的收敛语义(修复 I1):
- 实体重跑时先收敛删除该 Indicator 的全部出边——HAS_RANGE 目标节点 DETACH 删除
  (防 RangeSpec 孤儿累积)、HIGH_SUGGESTS|LOW_SUGGESTS|PART_OF|DEFAULT_INTERVENTION
  边全部删除——再按 YAML 重建;种子 YAML 删除/改名项才会从图消失;
- 重建结束后全局孤儿清扫(RangeSpec / 无主 Intervention),不留垃圾节点。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from seed_import import upsert_indicator

from report_agent.knowledge.seed_schemas import (
    IndicatorSeed,
    InterventionYaml,
    RangeYaml,
    SuggestYaml,
)

CONVERGE_EDGES = (
    "MATCH (i:Indicator {code: $code})"
    "-[r:HIGH_SUGGESTS|LOW_SUGGESTS|PART_OF|DEFAULT_INTERVENTION]->() DELETE r"
)
DETACH_RANGES = (
    "MATCH (i:Indicator {code: $code})-[r:HAS_RANGE]->(rs:RangeSpec) DETACH DELETE rs"
)
SWEEP_RANGESPEC = "MATCH (rs:RangeSpec) WHERE NOT (rs)<--() DELETE rs"
SWEEP_INTERVENTION = "MATCH (iv:Intervention) WHERE NOT (iv)<--() DETACH DELETE iv"


class FakeSession:
    """捕获 upsert 语句序列的渲染桩;upsert_* 只用 run() 且忽略返回值。"""

    def __init__(self):
        self.statements: list[tuple[str, dict]] = []

    def run(self, cypher, **params):
        self.statements.append((cypher, params))


def _seed(high_conditions=("糖尿病风险",)) -> IndicatorSeed:
    return IndicatorSeed(
        code="GLU", name="空腹血糖", aliases=["血糖"], unit="mmol/L",
        unit_conversions={"mg/dL": 0.0555}, category="糖代谢", description="d",
        ranges=[RangeYaml(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                          critical_low=2.8, critical_high=22.0, source_note="检验科参考")],
        high_suggests=[SuggestYaml(condition=c, strength="strong") for c in high_conditions],
        low_suggests=[SuggestYaml(condition="低血糖", strength="strong")],
        clusters=["糖代谢"],
        interventions=[InterventionYaml(level="recheck", text="复查空腹血糖",
                                        timeframe="2-4周", departments=["内分泌科"])],
        departments=["内分泌科"],
        narrative="n",
    )


def test_upsert_indicator_statement_sequence_converges():
    """单次导入的语句序:SET → 出边收敛 → RangeSpec DETACH 重建 → 重建各边 → 孤儿清扫。"""
    sess = FakeSession()
    upsert_indicator(sess, _seed())

    texts = [q for q, _ in sess.statements]
    assert "MERGE (i:Indicator {code: $code}) SET" in texts[0]
    # 收敛删除紧跟 SET(重建各边之前),参数锚定该实体
    assert texts[1] == CONVERGE_EDGES
    assert sess.statements[1][1] == {"code": "GLU"}
    # RangeSpec 重建路径:先 DETACH DELETE 旧节点(防孤儿累积),后 CREATE 新区间
    assert texts[2] == DETACH_RANGES
    assert sess.statements[2][1] == {"code": "GLU"}
    first_range_create = next(i for i, q in enumerate(texts) if "CREATE (i)-[:HAS_RANGE]" in q)
    assert first_range_create > 2
    # 收敛先于任何关系重建(HIGH_SUGGESTS rel 重建片段出现在收敛之后)
    first_rel_rebuild = next(
        i for i, q in enumerate(texts) if "[r:HIGH_SUGGESTS]" in q or "[r:LOW_SUGGESTS]" in q
    )
    assert first_rel_rebuild > 1
    # 孤儿清扫在重建(含 _attach_departments 合成建议)之后执行
    assert SWEEP_RANGESPEC in texts and SWEEP_INTERVENTION in texts
    sweep_iv = texts.index(SWEEP_INTERVENTION)
    assert sweep_iv == len(texts) - 1
    assert any("建议专科门诊咨询" in q for q in texts[:sweep_iv])


def test_reimport_converges_removed_suggestions():
    """先导入含 [糖尿病风险] 的 seed,再导入改为 [应激性高血糖] 的 seed:
    第二次重跑必须执行边收敛,且旧提示不再出现在任何语句参数中。"""
    first = FakeSession()
    upsert_indicator(first, _seed(high_conditions=("糖尿病风险",)))
    assert any(
        params.get("cond") == "糖尿病风险" for _, params in first.statements
    )

    second = FakeSession()
    upsert_indicator(second, _seed(high_conditions=("应激性高血糖",)))
    texts = [q for q, _ in second.statements]
    # 第二次同样先收敛出边(仅锚定 code),再重建
    assert texts[1] == CONVERGE_EDGES
    assert second.statements[1][1] == {"code": "GLU"}
    # 新提示的关系重建出现在收敛之后(收敛生效的语义前提)
    rebuild = next(i for i, q in enumerate(texts) if "[r:HIGH_SUGGESTS]" in q)
    assert rebuild > 1
    # 旧提示已从图中移除:重跑全程无任何语句携带被删除的 condition
    assert all(params.get("cond") != "糖尿病风险" for _, params in second.statements)
    assert any(params.get("cond") == "应激性高血糖" for _, params in second.statements)
