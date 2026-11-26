"""用户表数据访问层。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from trippilot.db.models import User
from trippilot.db.session import session_scope


async def create_user(username: str, password_hash: str) -> User | None:
    """创建新用户；若用户名冲突触发唯一约束则返回 None。"""
    user = User(username=username, password_hash=password_hash)
    try:
        async with session_scope() as db:
            db.add(user)
    except IntegrityError:
        return None
    return user


async def get_user_by_username(username: str) -> User | None:
    """按用户名查询用户实体。"""
    async with session_scope() as db:
        return await db.scalar(select(User).where(User.username == username))
