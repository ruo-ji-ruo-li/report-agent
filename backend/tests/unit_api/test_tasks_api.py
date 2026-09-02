"""任务查询端点单测(app.state 注入 fake,不触 DB)。

(brief Files 声明创建本文件、Step 6 运行它,但未给测试内容;pytest 对 0 收集
以 exit code 5 退出,空文件无法满足 Step 6 Expected PASS。补最小契约用例:
GET /api/tasks/{task_id} 的键映射与 404,仅覆盖 Step 4 tasks.py 路由设计。)
"""
from fastapi.testclient import TestClient

from report_agent.api.app import create_app


class FakeTasks:
    async def get(self, task_id):
        if task_id != "task-1":
            return None
        return type("T", (), {"id": task_id, "report_id": "r1", "status": "running",
                              "stage": "normalize", "checkpoints": {"parse": {"n": 1}},
                              "timings": {"parse": 0.5}, "error": None})()


def _client():
    app = create_app(deps_builder=lambda settings: object())  # 测试:不连真服务
    app.state.tasks = FakeTasks()
    return TestClient(app), app


def test_get_task_field_mapping_and_404():
    client, _ = _client()
    r = client.get("/api/tasks/task-1")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "task-1"
    assert body["report_id"] == "r1"
    assert body["status"] == "running"
    assert body["stage"] == "normalize"
    assert body["completed_stages"] == ["parse"]  # checkpoints 键
    assert body["timings"] == {"parse": 0.5}
    assert body["error"] is None
    assert client.get("/api/tasks/nope").status_code == 404
