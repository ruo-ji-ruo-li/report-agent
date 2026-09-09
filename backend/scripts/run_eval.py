"""评测与回归门禁(spec §9)。运行:
  uv run python scripts/run_eval.py                      # 全量评测,输出 eval/result.json
  uv run python scripts/run_eval.py --update-baseline    # 人工确认结果后冻结基线
维度: 解析准确率(归一化 F1)/ 异常判定准确率(规则层,必须 1.0)/ 证据覆盖率 /
      数值一致性 / 安全指标 / 拒答正确率 / LLM 评分(参考指标);与 baseline 比较,回退即 exit 1。
LLM 相关维度只跑 --max-llm-reports(默认 5)份以控制成本;规则维度全量。
编排逻辑在 report_agent.eval.runner(评测升级 spec §3)。
"""
import argparse
import asyncio
import sys


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--max-llm-reports", type=int, default=5)
    return parser.parse_args(argv)


async def main() -> None:
    from report_agent.config import get_settings
    from report_agent.eval.runner import run
    from report_agent.pipeline.deps import build_deps

    args = parse_args()
    settings = get_settings()
    deps = await build_deps(settings)
    sys.exit(await run(deps, max_llm_reports=args.max_llm_reports,
                       update_baseline=args.update_baseline))


if __name__ == "__main__":
    asyncio.run(main())
