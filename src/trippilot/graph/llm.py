"""大模型客户端适配与构建工厂。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_deepseek import ChatDeepSeek
from pydantic import SecretStr

from trippilot.config import Settings, get_settings


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


def build_model_pair(
        settings: Settings | None = None,
        tools: Sequence[BaseTool] | None = None,
) -> tuple[Runnable, Runnable]:
    """构建并绑定工具集的 (fast_model, thinking_model) 双模实例对。"""
    cfg = settings or get_settings()
    secret_key = SecretStr(cfg.deepseek_api_key)
    active_tools = list(tools) if tools else []

    base_fast = PatchedChatDeepSeek(
        model=cfg.deepseek_model,
        api_key=secret_key,
        api_base=cfg.deepseek_base_url,
        temperature=0.3,
        extra_body={"thinking": {"type": "disabled"}},
    )
    fast_model: Runnable = base_fast.bind_tools(active_tools) if active_tools else base_fast

    base_thinking = PatchedChatDeepSeek(
        model=cfg.deepseek_model,
        api_key=secret_key,
        api_base=cfg.deepseek_base_url,
        extra_body={"thinking": {"type": "enabled"}},
    )
    thinking_model: Runnable = (
        base_thinking.bind_tools(active_tools) if active_tools else base_thinking
    )

    return fast_model, thinking_model
