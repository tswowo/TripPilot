from datetime import timedelta
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from trippilot.config import get_settings
from trippilot.utils.login_util import create_access_token, get_current_user

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


@router.post("/register")
async def register(request: LoginRequest):
    settings = get_settings()
    password_hash = bcrypt.hashpw(request.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        async with (
            await AsyncConnection.connect(settings.postgres_uri) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
                "RETURNING id, username",
                (request.username, password_hash),
            )
            user = await cur.fetchone()
            await conn.commit()
    except errors.UniqueViolation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户已存在")
    return {"id": user["id"], "username": user["username"]}


@router.post("/login")
async def login(request: LoginRequest):
    settings = get_settings()
    async with (
        await AsyncConnection.connect(settings.postgres_uri) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT id, username, password_hash FROM users WHERE username = %s",
            (request.username,),
        )
        user = await cur.fetchone()

    if not user or not bcrypt.checkpw(
        request.password.encode("utf-8"),
        user["password_hash"].encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    token = create_access_token(
        data={"sub": user["username"], "uid": user["id"]},
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(payload: Annotated[dict, Depends(get_current_user)]):
    return {"id": payload["uid"], "username": payload["sub"]}