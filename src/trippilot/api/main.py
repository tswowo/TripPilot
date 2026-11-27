from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from trippilot.api.auth import router as auth_router
from trippilot.api.chat import router as chat_router
from trippilot.api.conversations import router as conversations_router
from trippilot.config import get_settings
from trippilot.db.session import dispose_engine, get_engine
from trippilot.graph.builder import build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.secret_key:
        raise RuntimeError("缺少 SECRET_KEY，无法签发 JWT")

    try:
        async with AsyncPostgresSaver.from_conn_string(settings.postgres_uri) as saver:
            await saver.setup()
            app.state.graph = build_graph(saver)
            yield
    finally:
        await dispose_engine()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)
