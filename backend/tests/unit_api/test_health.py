from fastapi.testclient import TestClient

from report_agent.api.app import create_app


def test_health_returns_checks(monkeypatch):
    # 外部服务不可达时健康检查不抛异常,返回 False
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["checks"].keys()) == {"postgres", "milvus", "neo4j"}
