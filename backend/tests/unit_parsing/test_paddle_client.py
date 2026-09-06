"""Paddle 客户端单测:mock httpx,覆盖提交/轮询各状态/失败/超时(spec §4)。"""
import json

import httpx

from report_agent.config import Settings
from report_agent.parsing.paddle_client import PaddleClient, PaddleError


def _settings(**kw) -> Settings:
    return Settings(paddle_token="tok", **kw)


def _client(tmp_path, monkeypatch, submit_resp=None, poll_resps=None, jsonl=None):
    """构造 fake httpx 响应序列的 PaddleClient。"""
    calls: dict[str, list] = {"post": [], "get": []}

    class FakeResp:
        def __init__(self, status_code=200, text="", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code != 200:
                raise httpx.HTTPStatusError("err", request=object(), response=self)

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls["post"].append(url)
        return submit_resp

    def fake_get(url, headers=None, timeout=None):
        calls["get"].append(url)
        if url.endswith(".jsonl") or "jsonUrl" in url:
            return jsonl
        return poll_resps.pop(0)

    monkeypatch.setattr("report_agent.parsing.paddle_client.httpx.post", fake_post)
    monkeypatch.setattr("report_agent.parsing.paddle_client.httpx.get", fake_get)
    return PaddleClient(_settings()), calls


def _done_resp(json_url):
    return type("R", (), {"status_code": 200, "text": "",
                          "json": lambda self: {"data": {"state": "done",
                                                         "resultUrl": {"jsonUrl": json_url}}}})()


def _jsonl_resp(pages_md):
    lines = [json.dumps({"result": {"layoutParsingResults": [
        {"markdown": {"text": md}}]}}) for md in pages_md]
    return type("R", (), {"status_code": 200, "text": "\n".join(lines) + "\n",
                          "json": lambda self: {},
                          "raise_for_status": lambda self: None})()


def test_parse_pdf_success(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    submit = type("R", (), {"status_code": 200, "text": "",
                            "json": lambda self: {"data": {"jobId": "job-1"}}})()
    client, calls = _client(tmp_path, monkeypatch, submit_resp=submit,
                            poll_resps=[_done_resp("https://x/out.jsonl")],
                            jsonl=_jsonl_resp(["页一", "页二"]))
    pages = client.parse_pdf(str(pdf))
    assert pages == ["页一", "页二"]
    assert calls["get"][0].endswith("job-1")  # 轮询端点
    assert calls["get"][-1] == "https://x/out.jsonl"  # 结果下载


def test_parse_pdf_poll_running_then_done(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    submit = type("R", (), {"status_code": 200, "text": "",
                            "json": lambda self: {"data": {"jobId": "job-1"}}})()
    running = type("R", (), {"status_code": 200, "text": "",
                             "json": lambda self: {"data": {"state": "running"}}})()
    client, _ = _client(tmp_path, monkeypatch, submit_resp=submit,
                        poll_resps=[running, _done_resp("https://x/out.jsonl")],
                        jsonl=_jsonl_resp(["页一"]))
    assert client.parse_pdf(str(pdf)) == ["页一"]


def test_parse_pdf_job_failed_raises(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    submit = type("R", (), {"status_code": 200, "text": "",
                            "json": lambda self: {"data": {"jobId": "job-1"}}})()
    failed = type("R", (), {"status_code": 200, "text": "",
                            "json": lambda self: {"data": {"state": "failed",
                                                           "errorMsg": "解析失败"}}})()
    client, _ = _client(tmp_path, monkeypatch, submit_resp=submit, poll_resps=[failed])
    try:
        client.parse_pdf(str(pdf))
        assert False, "应抛 PaddleError"
    except PaddleError as e:
        assert "解析失败" in str(e)


def test_parse_pdf_submit_non_200_raises(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    submit = type("R", (), {"status_code": 401, "text": "bad token",
                            "json": lambda self: {}})()
    client, _ = _client(tmp_path, monkeypatch, submit_resp=submit)
    try:
        client.parse_pdf(str(pdf))
        assert False, "应抛 PaddleError"
    except PaddleError as e:
        assert "401" in str(e)


def test_parse_pdf_timeout_raises(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    submit = type("R", (), {"status_code": 200, "text": "",
                            "json": lambda self: {"data": {"jobId": "job-1"}}})()
    pending = type("R", (), {"status_code": 200, "text": "",
                             "json": lambda self: {"data": {"state": "pending"}}})()
    client, _ = _client(tmp_path, monkeypatch, submit_resp=submit,
                        poll_resps=[pending] * 10)
    client._timeout = 0.01  # 立即超时
    client._poll_interval = 0.0
    try:
        client.parse_pdf(str(pdf))
        assert False, "应抛 PaddleError"
    except PaddleError as e:
        assert "超时" in str(e)


def test_parse_pdf_missing_file_raises(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    try:
        client.parse_pdf(str(tmp_path / "nope.pdf"))
        assert False, "应抛 PaddleError"
    except PaddleError as e:
        assert "文件不存在" in str(e)
