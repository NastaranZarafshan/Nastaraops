from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def create_schema() -> None:
    # MVP bootstrap; production should use Alembic migrations.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        # Prevent concurrent API/worker/mailer startup from racing on CREATE TABLE.
        await conn.execute(text("SELECT pg_advisory_xact_lock(418272)"))
        await conn.run_sync(Base.metadata.create_all)
