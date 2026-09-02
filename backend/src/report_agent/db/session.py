from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from report_agent.config import get_settings

engine: AsyncEngine = create_async_engine(get_settings().postgres_dsn, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with session_factory() as session:
        yield session
