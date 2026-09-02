"""报告 API 单测:app.state 注入 fake,不触 DB/不连外部服务。

(brief Step 2 三个用例;任务状态断言用轮询等待 —— create_task 在 TestClient 的
portal 事件循环里执行,不能同步断言)

brief 偏差 1 处(最小修正):FakeDB.get_task_for_report 返回 status 由 "running"
改为 "awaiting_meta"。原值与 PATCH 路由语义矛盾 —— 路由只在任务 awaiting_meta 时
resume + 重跑,而本用例(test_patch_meta_resumes_awaiting_task)就是要验证补录后
重新入队;fake 应建模 parse 阶段因缺 meta 暂停后的真实库状态(awaiting_meta)。
保持 "running" 会让轮询 3s 超时、用例确定性失败。
"""
from fastapi.testclient import TestClient

from report_agent.api.app import create_app


class FakeRunner:
    def __init__(self):
        self.ran = []

    async def run_task(self, task_id):
        self.ran.append(task_id)


class FakeTasks:
    async def create(self, report_id):
        return type("T", (), {"id": "task-1", "report_id": report_id, "status": "pending",
                              "stage": None, "checkpoints": {}, "timings": {}, "error": None})()

    async def get(self, task_id):
        return type("T", (), {"id": task_id, "report_id": "r1", "status": "completed",
                              "stage": "done", "checkpoints": {"parse": {}}, "timings": {"parse": 1.2},
                              "error": None})()

    async def resume_from_meta(self, task_id):
        pass


class FakeDB:
    def __init__(self):
        self.report = {"id": "r1", "source": "manual", "file_path": None, "institution": None,
                       "report_date": None, "sex": None, "age": None}

    async def create_report(self, source, file_path, meta):
        self.report.update({"source": source, "file_path": file_path,
                            "sex": meta.sex, "age": meta.age})
        return "r1"

    async def save_raw_items(self, report_id, items):
        self.items = items

    async def get_report_detail(self, report_id):
        return {"meta": self.report, "items": [], "normalized": []}

    async def get_task_for_report(self, report_id):
        return {"id": "task-1", "status": "awaiting_meta", "stage": "parse"}

    async def update_report_meta(self, report_id, meta):
        self.report["sex"], self.report["age"] = meta.sex, meta.age

    async def get_interpretation(self, report_id):
        return {"summary": "总评", "items": [], "advice_summary": "", "disclaimer": "x",
                "degraded": False}

    async def get_followup(self, report_id):
        return {"items": [], "degraded": False}


def _client():
    app = create_app(deps_builder=lambda settings: object())  # 测试:不连真服务
    # 路由在分支前取 request.app.state.deps.settings(brief 原样),补假 deps 供其解引用
    app.state.deps = type("D", (), {"settings": None})()
    app.state.runner = FakeRunner()
    app.state.tasks = FakeTasks()
    app.state.db_access = FakeDB()
    return TestClient(app), app


def test_create_manual_report_and_run_task():
    client, app = _client()
    payload = {
        "meta": {"sex": "male", "age": 45, "source": "manual"},
        "items": [{"name": "空腹血糖", "value_text": "6.2", "value_num": 6.2,
                   "unit": "mmol/L", "ref_range_text": "3.9-6.1", "abnormal_flag": None}],
    }
    resp = client.post("/api/reports", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_id"] == "r1" and body["task_id"] == "task-1"
    assert app.state.db_access.items[0].name == "空腹血糖"
    # 后台任务异步调度:轮询等待(create_task 在 portal 事件循环里执行,不能同步断言)
    import time

    deadline = time.time() + 3
    while not app.state.runner.ran and time.time() < deadline:
        time.sleep(0.02)
    assert app.state.runner.ran == ["task-1"]


def test_patch_meta_resumes_awaiting_task():
    client, app = _client()
    resp = client.patch("/api/reports/r1/meta", json={"sex": "female", "age": 30})
    assert resp.status_code == 200
    assert app.state.db_access.report["sex"] == "female"
    import time

    deadline = time.time() + 3
    while not app.state.runner.ran and time.time() < deadline:
        time.sleep(0.02)
    assert app.state.runner.ran == ["task-1"]  # 补录后重新入队


def test_get_report_detail_and_task():
    client, _ = _client()
    assert client.get("/api/reports/r1").status_code == 200
    r = client.get("/api/tasks/task-1")
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.get("/api/reports/r1/interpretation").json()["summary"] == "总评"
    assert client.get("/api/reports/r1/followup-plan").status_code == 200
