"""端到端冒烟:样例 PDF → 上传 → 轮询 → 解读 → 复查单 → 追问 → 知识库外拒答。
运行前提: docker compose 已启动、KB 已入库(seed_import --entity GLU 至少一个指标)、LLM key 已配置。
运行: uv run python scripts/smoke.py
降级演示(验收标准 3): 先 docker stop report-neo4j 再跑本脚本,KG 路失效仍能产出解读
(dense+BM25 两路继续),恢复容器后 docker start report-neo4j。
"""
import asyncio
import sys
from pathlib import Path

import httpx

# scripts/ 非安装包:把 backend 根加进 sys.path,使其中的兄弟脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://localhost:8000"


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        # 1) 生成样例 PDF 并上传
        from scripts.gen_sample_pdf import main as gen_pdf

        gen_pdf()
        with open("eval/fixtures/sample_report.pdf", "rb") as f:  # noqa: ASYNC230 —— 本地小文件上传,阻塞可接受(对齐 api/reports.py)
            # 解析层升级后上传即带表单元数据(与 gen_sample_pdf 样例一致:张三/男/45 岁):
            # 不带 sex/age → reports 列为 NULL → parse 阶段 needs_meta → 任务停在 awaiting_meta,
            # 下方轮询永不达终态,故随上传一并提交(spec §11.1)
            resp = await client.post(f"{BASE}/api/reports",
                                     data={"name": "张三", "sex": "male", "age": "45"},
                                     files={"file": ("sample.pdf", f, "application/pdf")})
        assert resp.status_code == 200, resp.text
        report_id, task_id = resp.json()["report_id"], resp.json()["task_id"]
        print(f"[1] 已创建报告 {report_id},任务 {task_id}")

        # 2) 轮询任务(管线 30s~2min)
        for _ in range(120):
            t = (await client.get(f"{BASE}/api/tasks/{task_id}")).json()
            if t["status"] in ("completed", "degraded", "failed"):
                break
            await asyncio.sleep(1.5)
        assert t["status"] != "failed", t.get("error")
        print(f"[2] 任务完成: {t['status']},阶段耗时 {t['timings']}")

        # 3) 解读与复查单
        interp = (await client.get(f"{BASE}/api/reports/{report_id}/interpretation")).json()
        assert "免责" in interp["disclaimer"] or "不构成医学诊断" in interp["disclaimer"]
        assert interp["summary"] and interp["items"]
        print(f"[3] 解读生成: {len(interp['items'])} 项解读, degraded={interp['degraded']}")
        plan = (await client.get(f"{BASE}/api/reports/{report_id}/followup-plan")).json()
        print(f"[4] 复查计划: {len(plan['items'])} 条")

        # 4) 追问(SSE)
        sess = (await client.post(f"{BASE}/api/reports/{report_id}/chat/sessions")).json()
        sid = sess["session_id"]
        async with client.stream("POST", f"{BASE}/api/chat/sessions/{sid}/messages",
                                 json={"content": "我的血糖情况怎么样?"}) as resp:
            # (brief 偏差,最小修正: 事件类型在 "event: token" 行上,data: 行只带内容
            # 载荷(brief 的 startswith("data: ") 过滤会丢掉类型行,"token" 断言必失败;
            # 端点契约见 tests/unit_api/test_chat_api.py "event: token" in body)
            raw_lines = [line async for line in resp.aiter_lines()
                         if line.startswith(("event: ", "data: "))]
        events = [ln for ln in raw_lines if ln.startswith("data: ")]
        assert any(ln.startswith("event: token") for ln in raw_lines)
        print(f"[5] 追问流式完成: {len(events)} 个 SSE 事件")

        # 5) 知识库外拒答
        async with client.stream("POST", f"{BASE}/api/chat/sessions/{sid}/messages",
                                 json={"content": "请介绍一下量子场论的最新进展"}) as resp:
            text = "".join([line async for line in resp.aiter_lines()])
        assert ("拒" in text and "医生" in text) or "未覆盖" in text, text[:200]
        print("[6] 知识库外问题已拒答")

    print("\nSMOKE PASS")


if __name__ == "__main__":
    asyncio.run(main())
