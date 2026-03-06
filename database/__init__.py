from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from config import settings

# Cambiar el driver a asyncpg para soporte asincrónico
_db_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
# Eliminar el argumento sslmode de la URL de conexión
_db_url = _db_url.replace("?sslmode=require", "")

engine = create_async_engine(
    _db_url,
    echo=False,  # Eliminar connect_args para asyncpg
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[override]
    """FastAPI dependency that yields an async database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables on startup (called from lifespan)."""
    from database import models  # noqa: F401 — registers models with Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
