from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, status
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import START, MessagesState, StateGraph
from psycopg import AsyncConnection, errors
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from trippilot.config import get_settings
from trippilot.utils.id_util import uuid7
from trippilot.utils.login_util import create_access_token, get_current_user

SYSTEM_PROMPT = (
    "你是旅行规划助手。根据用户需求给出清晰的旅行建议。"
    "你没有实时查询能力，不要编造具体车次、票价、酒店价格或余票；"
    "不确定的信息要明确说明。"
)


def build_graph(checkpointer: AsyncPostgresSaver):
    settings = get_settings()
    model = ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        temperature=0.3,
    )

    async def call_model(state: MessagesState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        response = await model.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")
    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.secret_key:
        raise RuntimeError("缺少 SECRET_KEY，无法签发 JWT")
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_uri) as saver:
        await saver.setup()
        app.state.graph = build_graph(saver)
        yield


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = Field(default=None, min_length=1)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/register")
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


@app.post("/api/login")
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


@app.get("/api/me")
async def me(payload: Annotated[dict, Depends(get_current_user)]):
    return {"id": payload["uid"], "username": payload["sub"]}


@app.post("/api/chat")
async def chat(request: ChatRequest, payload: Annotated[dict, Depends(get_current_user)]):
    conversation_id = request.conversation_id or str(uuid7())
    # TODO: 建 conversations 表后校验会话归属（user_id 必须等于 payload["uid"]）
    config = {"configurable": {"thread_id": conversation_id}}
    result = await app.state.graph.ainvoke(
        {"messages": [HumanMessage(content=request.message)]},
        config,
    )
    reply = result["messages"][-1].content
    return {"conversation_id": conversation_id, "content": str(reply)}
