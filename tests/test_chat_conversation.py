"""会话归属校验与 SSE 帧格式单元测试（离线可跑）。"""

import json
import uuid

import pytest
from pydantic import ValidationError

from trippilot.api.chat import ChatRequest, SSE_HEADERS, format_sse
from trippilot.utils.id_util import uuid7


def test_chat_request_validates_uuid_format():
    valid_id = str(uuid7())
    req = ChatRequest(message="去南京玩两天", conversation_id=valid_id)
    assert req.conversation_id == uuid.UUID(valid_id)

    req_new = ChatRequest(message="去南京玩两天")
    assert req_new.conversation_id is None

    with pytest.raises(ValidationError):
        ChatRequest(message="测试非法UUID", conversation_id="not-a-uuid")

    with pytest.raises(ValidationError):
        ChatRequest(message="", conversation_id=valid_id)


def test_format_sse_and_headers():
    frame = format_sse("delta", {"content": "南京南站"})
    assert frame.startswith("event: delta\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.splitlines()[1].removeprefix("data: "))
    assert payload == {"content": "南京南站"}
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert SSE_HEADERS["Cache-Control"] == "no-cache"




