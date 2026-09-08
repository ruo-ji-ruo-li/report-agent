"""seed_import 约束/索引单测(KG 点查设计 §3):ensure_constraints 幂等创建
6 个唯一约束 + Alias.key 普通索引。scripts 不在 sys.path,测试自行插入。"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import seed_import


class FakeSession:
    def __init__(self):
        self.calls: list[str] = []

    def run(self, cypher, **params):
        self.calls.append(cypher)


def test_ensure_constraints_creates_all_statements():
    s = FakeSession()
    seed_import.ensure_constraints(s)
    assert len(s.calls) == 7
    for name in (
        "indicator_code_unique", "pattern_name_unique", "condition_name_unique",
        "cluster_name_unique", "department_name_unique", "intervention_key_unique",
        "alias_key_idx",
    ):
        assert any(name in c for c in s.calls), f"缺少 {name}"
    # Neo4j 5 幂等语法:约束与索引都带 IF NOT EXISTS
    assert all("IF NOT EXISTS" in c for c in s.calls)
