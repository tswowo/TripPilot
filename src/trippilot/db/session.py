"""SQLAlchemy 2.0 异步连接池与事务会话管理（复用 psycopg v3 驱动）。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from trippilot.config import get_settings


class Base(DeclarativeBase):
    """ORM 声明式基类"""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def to_sqlalchemy_uri(uri: str) -> str:
    """将标准 PostgreSQL URI 转换为 SQLAlchemy psycopg v3 异步协议头。"""
    cleaned = uri.strip()
    if not cleaned:
        raise RuntimeError("缺少 POSTGRES_URI，无法初始化数据库连接池")
    if cleaned.startswith("postgresql+psycopg://"):
        return cleaned
    if cleaned.startswith("postgresql://"):
        return cleaned.replace("postgresql://", "postgresql+psycopg://", 1)
    if cleaned.startswith("postgres://"):
        return cleaned.replace("postgres://", "postgresql+psycopg://", 1)
    return cleaned


def get_engine() -> AsyncEngine:
    """懒加载获取全局异步数据库引擎（内置连接池，对标 HikariCP）。"""
    global _engine
    if _engine is None:
        uri = to_sqlalchemy_uri(get_settings().postgres_uri)
        _engine = create_async_engine(
            uri,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """懒加载获取异步 Session 工厂。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """短事务上下文管理器（对标 Spring @Transactional）：
    进入时借连接并开启事务，正常退出自动 commit，异常自动 rollback，随后立即归还连接。
    """
    factory = get_session_factory()
    async with factory.begin() as session:
        yield session


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends 注入依赖（适用于常规非流式短耗时 CRUD 接口）。"""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """应用关闭时优雅释放连接池。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
