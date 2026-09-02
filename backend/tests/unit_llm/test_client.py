import asyncio

import pytest
from openai import APITimeoutError

from report_agent.llm.client import DeepSeekClient, LLMError
from report_agent.llm.prompts import load_prompt


def test_load_prompt_found():
    text = load_prompt("parse_vision")
    assert "体检报告" in text  # parse_vision.txt 必须在 llm/prompts/ 下


def test_load_prompt_missing():
    with pytest.raises(FileNotFoundError):
        load_prompt("no_such_prompt")


def test_chat_retries_then_raises():
    calls = []

    async def failing(messages, **kw):
        calls.append(messages)
        raise APITimeoutError(request=object())

    c = DeepSeekClient(client=object(), max_retries=2, base_delay=0.0)
    c._chat_raw = failing  # 注入失败实现,绕过真网络
    with pytest.raises(LLMError):
        asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    assert len(calls) == 3  # 1 次初始 + 2 次重试


def test_complete_json_repairs_bad_json():
    responses = ["{broken", '{"ok": 1}']
    c = DeepSeekClient(client=object(), max_retries=1, base_delay=0.0)
    calls = []

    async def fake_chat_raw(messages, **kw):
        calls.append(messages)
        return responses[len(calls) - 1]

    c._chat_raw = fake_chat_raw  # 注入
    result = asyncio.run(c.complete_json([{"role": "user", "content": "x"}]))
    assert result == {"ok": 1}
    assert len(calls) == 2
    assert "JSON" in calls[1][-1]["content"]  # 第二次带了修复反馈
