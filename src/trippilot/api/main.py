from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from trippilot.api.auth import router as auth_router
from trippilot.api.chat import router as chat_router
from trippilot.config import get_settings
from trippilot.graph import build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.secret_key:
        raise RuntimeError("缺少 SECRET_KEY，无法签发 JWT")
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_uri) as saver:
        await saver.setup()
        app.state.graph = build_graph(saver)
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(chat_router)