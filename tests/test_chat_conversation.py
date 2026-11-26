"""会话归属校验单元测试（离线验证 ChatRequest UUID 校验与越权判断逻辑）。"""

import uuid

import pytest
from pydantic import ValidationError

from trippilot.api.chat import ChatRequest
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



