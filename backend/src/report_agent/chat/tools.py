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
        lines = [(f"受检人: {'男' if meta['sex'] == 'male' else '女' if meta['sex'] else '未知'}, "
                  f"年龄: {meta.get('age', '未知')}")]
        for n in detail["normalized"]:
            if section and n.get("section") != section:
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
            if (value >= (spec.critical_high or float("inf"))
                    or value <= (spec.critical_low or float("-inf"))):
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
