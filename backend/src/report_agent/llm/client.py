"""DeepSeek / DashScope-embedding 统一客户端(均为 OpenAI 兼容端点)。

重试策略:指数退避 base_delay * 2**attempt,上限 max_retries 次。
所有模块共享同一 client 实例(单例工厂),不要各自 new。
"""
import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from report_agent.config import get_settings
from report_agent.observability import get_logger

log = get_logger(__name__)

RETRIABLE = ("timeout", "rate_limit", "connection", "server")


class LLMError(Exception):
    """LLM 调用在全部重试后仍失败。上层按 spec §11 降级。"""


def _is_retriable(exc: Exception) -> bool:
    # openai 异常优先按类型判定(如 APITimeoutError 是 APIConnectionError 子类,
    # 其 str 为 "Request timed out." 不含连续子串 "timeout",字符串兜底不可靠)。
    if isinstance(exc, (asyncio.TimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return True
    s = str(exc).lower()
    return any(k in s for k in RETRIABLE)


@dataclass
class DeepSeekClient:
    client: AsyncOpenAI
    model: str = "deepseek-chat"
    max_retries: int = 3
    base_delay: float = 1.0
    timeout: float = 60.0

    async def _chat_raw(self, messages: list[dict], **kw) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, timeout=self.timeout, **kw
        )
        return resp.choices[0].message.content or ""

    async def _run_with_retry(self, fn, *args, **kw):
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args, **kw)
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt >= self.max_retries or not _is_retriable(e):
                    break
                delay = self.base_delay * (2**attempt)
                log.warning("llm_retry", attempt=attempt + 1, delay=delay, error=str(e))
                await asyncio.sleep(delay)
        raise LLMError(f"LLM 调用失败(已重试 {self.max_retries} 次): {last}") from last

    async def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 2048) -> str:
        t0 = time.perf_counter()
        try:
            out = await self._run_with_retry(
                self._chat_raw, messages, temperature=temperature, max_tokens=max_tokens
            )
            log.info("llm_call", model=self.model, purpose="chat", chars=len(out),
                     seconds=round(time.perf_counter() - t0, 2))
            return out
        except LLMError:
            log.error("llm_call_failed", model=self.model, purpose="chat")
            raise

    async def chat_stream(self, messages: list[dict], temperature: float = 0.1,
                          max_tokens: int = 2048) -> AsyncIterator[str]:
        t0 = time.perf_counter()
        stream = await self._run_with_retry(
            self.client.chat.completions.create,
            model=self.model, messages=messages, stream=True,
            temperature=temperature, max_tokens=max_tokens, timeout=self.timeout,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
        log.info("llm_call", model=self.model, purpose="chat_stream",
                 seconds=round(time.perf_counter() - t0, 2))

    async def complete_json(
        self, messages: list[dict], retry_feedback: bool = True, max_tokens: int = 4096
    ) -> dict:
        """要求 JSON 输出;解析失败时带反馈重试一次。

        max_tokens 默认 4096 而非 chat 的 2048:完整 JSON 文档(视觉解析/评测集合成的
        15 项报告)在 2048 预算下会被截断——截断必非合法 JSON,且重试同参数必再截断,
        反馈重试机制失效(真实生成评测集时 r09/r17 均因此失败)。
        """
        for i in range(2):
            raw = await self.chat(messages, temperature=0.1, max_tokens=max_tokens)
            try:
                return json.loads(_strip_json_fence(raw))
            except json.JSONDecodeError:
                if not retry_feedback or i == 1:
                    raise LLMError(f"LLM 未返回合法 JSON: {raw[:200]}")
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "你的输出不是合法 JSON,请重新只输出 JSON。"},
                ]
        raise LLMError("unreachable")


def _strip_json_fence(raw: str) -> str:
    """剥 markdown 代码围栏(```json ... ```),模型偶发带围栏输出 JSON。"""
    s = raw.strip()
    if s.startswith("```") and s.endswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            return s[first_newline + 1 : -3].strip()
    return s


@dataclass
class EmbeddingClient:
    client: AsyncOpenAI
    model: str
    dim: int
    max_retries: int = 3
    base_delay: float = 1.0
    timeout: float = 30.0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.client.embeddings.create(
                    model=self.model, input=texts, timeout=self.timeout
                )
                return [d.embedding for d in resp.data]
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt >= self.max_retries or not _is_retriable(e):
                    break
                await asyncio.sleep(self.base_delay * (2**attempt))
                log.warning("embedding_retry", attempt=attempt + 1, error=str(e))
        raise LLMError(f"Embedding 调用失败: {last}") from last

    async def embed_text(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


@dataclass
class LLMClients:
    chat: DeepSeekClient
    vision: DeepSeekClient
    embedding: EmbeddingClient


@dataclass
class _LazyFactory:
    """单例工厂:所有模块共享同一批 client 实例。"""

    _instance: "LLMClients | None" = None
    _lock: asyncio.Lock | None = None

    async def get(self) -> LLMClients:
        if self._instance is not None:
            return self._instance
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._instance is not None:
                return self._instance
            s = get_settings()
            chat_client = AsyncOpenAI(base_url=s.deepseek_base_url, api_key=s.deepseek_api_key)
            vision_client = AsyncOpenAI(base_url=s.deepseek_base_url, api_key=s.deepseek_api_key)
            emb_client = AsyncOpenAI(base_url=s.embedding_base_url, api_key=s.embedding_api_key)
            self._instance = LLMClients(
                chat=DeepSeekClient(chat_client, model=s.chat_model),
                vision=DeepSeekClient(vision_client, model=s.vision_model),
                embedding=EmbeddingClient(emb_client, model=s.embedding_model, dim=s.embedding_dim),
            )
            return self._instance


llm_factory = _LazyFactory()
