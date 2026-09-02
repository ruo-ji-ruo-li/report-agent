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
    from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
    from report_agent.pipeline.deps import build_deps
    from report_agent.pipeline.rule_compare import judge_all

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
        # (brief 偏差,最小修正: gt_codes 为 null 的目录外项规则层必出 "unmapped",
        #  brief 的兜底 "unknown" 使含 unknown 项的报告永远达不到 1.0,与合成模板
        #  每报告含 1 个目录外项的要求自相矛盾 → 无 code 项兜底对齐 "unmapped")
        accs.append(status_accuracy(
            [j.status.value for j in judgments],
            [data["gt_statuses"].get(c, "unmapped" if not c else "unknown") for c in
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

    numeric_ok, numeric_total = 0, 0
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
    # (brief 偏差,最小修正: 规则层 100% 与安全零违规是 spec §4.5 的绝对验收线,
    #  brief 把它们嵌在 baseline 存在分支内导致首次评测(尚无基线)即使不达标也
    #  打印 [PASS];硬门禁无条件执行,仅"回退比较"依赖基线存在)
    if metrics["rule_accuracy"] < 1.0:
        print("[FAIL] 规则比对层准确率必须为 100%")
        sys.exit(1)
    if metrics["safety_violations"] > 0:
        print("[FAIL] 安全指标必须为零违规")
        sys.exit(1)
    if BASELINE_FILE.exists():
        baseline = json.loads(BASELINE_FILE.read_text("utf-8"))
        regressions = compare_baseline(metrics, baseline)
        if regressions:
            print(f"[FAIL] 指标回退: {regressions}")
            sys.exit(1)
    print("[PASS] 评测通过")


if __name__ == "__main__":
    asyncio.run(main())
