"""Settings.psycopg_dsn 派生属性的单测(纯字符串处理, 不连库)。

背景: postgres_dsn 是 SQLAlchemy 方言 URL(默认 +asyncpg), 而 langgraph 追问记忆
checkpointer 走 psycopg, 只认 postgresql:// —— 派生属性负责归一, 见 config.py。
"""
from report_agent.config import Settings


def test_psycopg_dsn_strips_dialect_suffix():
    settings = Settings(postgres_dsn="postgresql+asyncpg://u:p@localhost:5432/report_agent")
    assert settings.psycopg_dsn == "postgresql://u:p@localhost:5432/report_agent"


def test_psycopg_dsn_passthrough_plain_scheme():
    settings = Settings(postgres_dsn="postgresql://u:p@localhost:5432/report_agent")
    assert settings.psycopg_dsn == "postgresql://u:p@localhost:5432/report_agent"


def test_paddle_defaults():
    settings = Settings()
    assert settings.paddle_api_url.endswith("/api/v2/ocr/jobs")
    assert settings.paddle_token == ""
    assert settings.paddle_model == "PaddleOCR-VL-1.6"
    assert settings.paddle_poll_interval == 2.0
    assert settings.paddle_poll_timeout == 600.0
