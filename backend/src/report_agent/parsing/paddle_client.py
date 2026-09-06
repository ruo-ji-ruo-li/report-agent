"""PaddleOCR-VL 文档解析客户端:job 提交 + 轮询 + JSONL 下载(spec §4)。

同步 httpx 实现,调用方以 asyncio.to_thread 包裹(与 Milvus 客户端同模式)。
图片一律不下载,只取 markdown.text(合并后图片信息无用途,spec §5.1)。
"""
import json
import time
from pathlib import Path

import httpx

from report_agent.config import Settings
from report_agent.observability import get_logger

log = get_logger(__name__)


class PaddleError(Exception):
    """Paddle job 提交/执行失败或超时。上层按 spec §11 降级视觉。"""


# 可选参数按用户提供的官方示例代码原样(spec §4.2);mergeTables/restructurePages
# 让 Paddle 侧先行合并跨页表格,但截断仍会出现,代码层截断处理见 table_extractor(spec §7)
_OPTIONAL_PAYLOAD = {
    "markdownIgnoreLabels": ["header", "header_image", "footer", "footer_image",
                             "number", "footnote", "aside_text"],
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useLayoutDetection": True,
    "useChartRecognition": False,
    "useSealRecognition": True,
    "useOcrForImageBlock": False,
    "mergeTables": True,
    "relevelTitles": True,
    "layoutShapeMode": "auto",
    "promptLabel": "ocr",
    "repetitionPenalty": 1,
    "temperature": 0,
    "topP": 1,
    "minPixels": 147384,
    "maxPixels": 2822400,
    "layoutNms": True,
    "restructurePages": True,
}

# 轮询间隔下限:防病态配置(如 0)令 CPU 空转打爆上游,并保证短超时语义确定——
# 在高精度时钟平台(Windows)上 sleep(0) 几乎不耗时,若间隔为 0 会瞬间耗尽
# 轮询次数仍等不到 wall-clock 超时;加下限后超时先于请求次数耗尽触发。
_MIN_POLL_INTERVAL: float = 0.005


class PaddleClient:
    def __init__(self, settings: Settings):
        self._url = settings.paddle_api_url
        self._headers = {"Authorization": f"bearer {settings.paddle_token}"}
        self._model = settings.paddle_model
        self._poll_interval = settings.paddle_poll_interval
        self._timeout = settings.paddle_poll_timeout

    def _submit(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise PaddleError(f"文件不存在: {file_path}")
        data = {"model": self._model, "optionalPayload": json.dumps(_OPTIONAL_PAYLOAD)}
        try:
            with path.open("rb") as f:
                resp = httpx.post(self._url, headers=self._headers, data=data,
                                  files={"file": f}, timeout=60.0)
        except (httpx.HTTPError, OSError) as e:
            # OSError: 文件打开/读取失败(占用、权限、存在性竞态)同样收敛为 PaddleError
            raise PaddleError(f"Paddle 提交失败: {e}") from e
        if resp.status_code != 200:
            raise PaddleError(f"Paddle 提交失败(status={resp.status_code}): {resp.text[:200]}")
        try:
            return resp.json()["data"]["jobId"]
        except (KeyError, ValueError, TypeError) as e:
            # TypeError: resp.json() 返回 None 时 None["data"] 抛出,一并收敛
            raise PaddleError(f"Paddle 提交响应无 jobId: {resp.text[:200]}") from e

    def _poll(self, job_id: str) -> str:
        """轮询直到 done(返回 jsonl_url)/ failed / 超时(spec §4.3)。"""
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                resp = httpx.get(f"{self._url}/{job_id}", headers=self._headers, timeout=30.0)
            except httpx.HTTPError as e:
                raise PaddleError(f"Paddle 轮询失败: {e}") from e
            if resp.status_code != 200:
                raise PaddleError(f"Paddle 轮询失败(status={resp.status_code})")
            try:
                data = resp.json()["data"]
            except (KeyError, ValueError, TypeError) as e:
                # TypeError: resp.json() 返回 None/数组时按异常响应收敛
                raise PaddleError(f"Paddle 轮询响应异常: {resp.text[:200]}") from e
            state = data.get("state")  # 缺 state 视为未到终态,交由超时收敛为 PaddleError
            if state == "done":
                try:
                    json_url = data["resultUrl"]["jsonUrl"]
                except (KeyError, TypeError) as e:
                    raise PaddleError(f"Paddle 轮询响应缺 jsonUrl: {resp.text[:200]}") from e
                return json_url
            if state == "failed":
                raise PaddleError(f"Paddle job 失败: {data.get('errorMsg')}")
            if time.monotonic() > deadline:
                raise PaddleError(f"Paddle job 超时({self._timeout}s)")
            time.sleep(max(self._poll_interval, _MIN_POLL_INTERVAL))

    def _fetch_jsonl(self, jsonl_url: str) -> list[str]:
        try:
            resp = httpx.get(jsonl_url, timeout=60.0)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise PaddleError(f"Paddle 结果下载失败: {e}") from e
        pages: list[str] = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                result = json.loads(line)["result"]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue  # 空行/非 JSON/非对象行容忍
            if not isinstance(result, dict):
                continue  # result 非对象:整行跳过
            for res in result.get("layoutParsingResults") or []:
                try:
                    pages.append(res["markdown"]["text"])
                except (KeyError, TypeError):
                    continue  # 单条缺 markdown/text 或类型异常:跳过该条,不中断整份下载
        return pages

    def parse_pdf(self, file_path: str) -> list[str]:
        """解析 PDF → 每页 markdown 文本列表(spec §4.1)。"""
        job_id = self._submit(file_path)
        log.info("paddle_job_submitted", job_id=job_id)
        jsonl_url = self._poll(job_id)
        pages = self._fetch_jsonl(jsonl_url)
        log.info("paddle_job_done", job_id=job_id, pages=len(pages))
        return pages
