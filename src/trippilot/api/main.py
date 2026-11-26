from uuid import uuid4

from fastapi import FastAPI, HTTPException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

from trippilot.config import get_settings

app = FastAPI()


class PlanRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=10)
    days: int = Field(ge=1, le=10)
    budget: float = Field(gt=0)
    preferences: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    request_id: str
    status: str
    destination: str
    days: int
    budget: float
    preferences: list[str]
    content: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/plan", response_model=PlanResponse)
async def create_plan(request: PlanRequest) -> PlanResponse:
    settings = get_settings()

    if not settings.deepseek_api_key:
        raise HTTPException(status_code=500, detail="DeepSeek API key not configured")

    model = ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        temperature=0.3,
        timeout=settings.request_timeout_s,
    )

    preferences = ",".join(request.preferences) or "无偏好"
    messages: list[BaseMessage] = [
        SystemMessage(
            content=(
                "你是旅行规划助手。根据用户需求给出清晰的旅行建议。"
                "你没有实时查询能力，不要编造具体车次、票价、酒店价格或余票；"
                "不确定的信息要明确说明。"
            )
        ),
        HumanMessage(
            content=(
                f"目的地：{request.destination}\n"
                f"旅行天数：{request.days}天\n"
                f"总预算：{request.budget}元\n"
                f"偏好：{preferences}"
            )
        )
    ]

    result = await model.ainvoke(messages)

    return PlanResponse(
        request_id=uuid4().hex[:8],
        status="generated",
        destination=request.destination,
        days=request.days,
        budget=request.budget,
        preferences=request.preferences,
        content=str(result.content),
    )
