"""全部环境变量配置。任何新配置字段先在这里声明,再更新 .env.example。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "report-agent"

    # LLM(DeepSeek,OpenAI 兼容)
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    chat_model: str = "deepseek-chat"
    vision_model: str = "deepseek-v4-flash-vision-exp"

    # Embedding(DashScope OpenAI 兼容端点)
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = 1024

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "report-agent-dev"
    neo4j_database: str = "neo4j"

    # Milvus
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "medical_knowledge"

    # Postgres
    postgres_dsn: str = "postgresql+asyncpg://report:report-agent-dev@localhost:5432/report_agent"

    # 管线与检索
    retrieval_top_k: int = 5
    retrieve_concurrency: int = 5
    evidence_budget_chars: int = 6000
    chunk_size: int = 500
    chunk_overlap: int = 50
    short_doc_max_chars: int = 1200

    # 追问 Agent
    agent_max_tool_rounds: int = 8
    refusal_rrf_threshold: float = 0.0

    # 解析
    unstructured_strategy: str = "hi_res"
    unstructured_infer_table: bool = True
    # hi_res 表格重建走 tesseract OCR(不走 PDF 文字层):默认 eng 会把中文识别为乱码拉丁
    unstructured_ocr_languages: list[str] = ["chi_sim", "eng"]
    parse_min_items: int = 3

    # Paddle OCR(spec §4.3)
    paddle_api_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    paddle_token: str = ""  # API key,真实值放本地 .env,不提交
    paddle_model: str = "PaddleOCR-VL-1.6"
    paddle_poll_interval: float = 2.0  # 轮询间隔(秒;原示例 5s 过长)
    paddle_poll_timeout: float = 600.0  # job 总超时(秒)

    # 上传
    upload_dir: str = "./uploads"

    # LangSmith(可选)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "report-agent"


@lru_cache
def get_settings() -> Settings:
    return Settings()
