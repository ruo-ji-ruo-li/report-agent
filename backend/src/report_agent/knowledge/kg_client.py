"""Neo4j 知识访问层(KG 是事实源,spec §4.2)。

约定:driver 不可用/查询失败一律降级为返回空结果并记 error 日志,
上层(RRF/规则)自然吸收空结果——spec §11 降级总览。
"""
import json
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


def _parse_conversions(raw) -> dict[str, float]:
    """unit_conversions 兼容解析:Neo4j 不存嵌套 Map,写侧序列化为 JSON 字符串;dict 为旧数据直读。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


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
                unit=r.get("unit"), unit_conversions=_parse_conversions(r.get("unit_conversions")),
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
    def patterns_for(self, codes: set[str]) -> list[PatternSpec]:
        """按报告指标码定向取模式(KG 点查设计 §4.1):只返回 REQUIRES 指向
        codes 内指标的模式,行数与报告指标数成正比,与 KG 模式总量无关。"""
        rows = self._query(
            "MATCH (p:Pattern)-[r:REQUIRES]->(i:Indicator) "
            "WHERE i.code IN $codes "
            "RETURN p.name AS name, p.description AS description, "
            "p.criteria_json AS criteria_json, "
            "COLLECT({code: i.code, direction: r.direction}) AS criteria",
            codes=list(codes),
        )
        out = []
        for r in rows:
            criteria = [
                PatternCriterion(indicator_code=c["code"], direction=c["direction"])
                for c in r.get("criteria", [])
                if c.get("code")
            ]
            # 完整性防线(KG 点查设计 §4.1):criteria 数与 criteria_json 基准不一致
            # → 部分取回(要求了报告外指标,本来也不可能命中)或导入未就绪,静默跳过。
            # criteria_json 缺失(None,旧数据)视为无基准,不校验(向后兼容)
            raw_json = r.get("criteria_json")
            baseline = None
            if raw_json is not None:
                try:
                    baseline = json.loads(raw_json)
                except json.JSONDecodeError:
                    log.warning("pattern_criteria_json_invalid", pattern=r["name"])
                    continue
            if isinstance(baseline, list) and len(baseline) != len(criteria):
                continue
            out.append(PatternSpec(
                name=r["name"], description=r.get("description") or "", criteria=criteria,
            ))
        return out
