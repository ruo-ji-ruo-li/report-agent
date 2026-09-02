import asyncio

from fastapi import APIRouter

from report_agent.config import get_settings
from report_agent.observability import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["health"])


async def _check(name: str, fn) -> bool:
    try:
        await asyncio.wait_for(fn(), timeout=3)
        return True
    except Exception as e:  # noqa: BLE001 —— 健康检查必须吞掉一切异常
        log.warning("health_check_failed", check=name, error=str(e))
        return False


async def _pg_check() -> None:
    from sqlalchemy import text

    from report_agent.db.session import engine

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@router.get("/health")
async def health():
    from report_agent.knowledge.kg_client import check_neo4j
    from report_agent.knowledge.milvus_client import check_milvus

    s = get_settings()
    checks = {
        "postgres": await _check("postgres", _pg_check),
        # neo4j/milvus 驱动为同步,放线程池执行
        "milvus": await _check("milvus", lambda: asyncio.to_thread(check_milvus, s.milvus_uri)),
        "neo4j": await _check(
            "neo4j", lambda: asyncio.to_thread(check_neo4j, s.neo4j_uri, s.neo4j_user, s.neo4j_password)
        ),
    }
    return {"status": "ok", "checks": checks}
