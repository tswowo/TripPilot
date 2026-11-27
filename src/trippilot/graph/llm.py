"""大模型客户端适配层"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class PatchedChatDeepSeek(ChatDeepSeek):
    """
    1. 开启 thinking 时，将 AIMessage.additional_kwargs['reasoning_content'] 还原进请求载荷，
       既保持多跳工具调用的思考连续性，也兼容开启严格校验的推理网关
    2. 将带 tool_calls 消息的 content=None 规整为空串 ""，避免部分 OpenAI 兼容网关报 Schema 校验错误。
    """

    def _get_request_payload(
            self,
            input_: LanguageModelInput,
            *,
            stop: list[str] | None = None,
            **kwargs: Any,
    ) -> dict[str, Any]:
        orig_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(orig_messages, stop=stop, **kwargs)

        extra = getattr(self, "extra_body", None) or {}
        thinking_cfg = extra.get("thinking") or {}
        thinking_enabled = isinstance(thinking_cfg, dict) and thinking_cfg.get("type") == "enabled"

        ai_originals = [m for m in orig_messages if isinstance(m, AIMessage)]
        ai_payloads = [m for m in payload.get("messages", []) if m.get("role") == "assistant"]

        for p_msg, orig_msg in zip(ai_payloads, ai_originals, strict=False):
            reasoning = (orig_msg.additional_kwargs or {}).get("reasoning_content")
            if thinking_enabled and isinstance(reasoning, str):
                p_msg["reasoning_content"] = reasoning
            if p_msg.get("tool_calls"):
                if p_msg.get("content") is None:
                    p_msg["content"] = ""
                if thinking_enabled and "reasoning_content" not in p_msg:
                    p_msg["reasoning_content"] = ""

        return payload
