"""通用第三方 MCP 工具治理与适配底座（MCP Adaptation & Curation Engine）。

将“通用机制”与“具体厂商（如高德）的业务策略”彻底解耦：
- 启动期（Schema Curation）：
  1. 黑名单过滤（`excluded_tools`）
  2. 描述契约补强（`description_patches`）
  3. 本地虚拟参数 Schema 注入（`virtual_arg_schemas`，如给远端不支持分页的接口注入 `count`）
  4. 按工具名固定字典序排列（保护大模型 Prefix KV Cache）
- 运行期（Interceptor Execution）：
  1. 前置（Pre-hook）：从 `MCPToolCallRequest.args` 中自动剥离本地虚拟参数，防止远端 MCP Server 参数校验报错
  2. 后置（Post-hook）：解析 `CallToolResult` 中的 JSON 文本，交由策略层的 `payload_transformer` 清洗/瘦身，再回写为 `TextContent`
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
    ToolCallInterceptor,
)
from mcp.types import CallToolResult, TextContent

# 签名：(tool_name, parsed_json_payload, extracted_virtual_args) -> transformed_json_payload
PayloadTransformer = Callable[[str, Any, dict[str, Any]], Any]


@dataclass(frozen=True)
class MCPAdaptationPolicy:
    """单个第三方 MCP Server 的声明式治理策略。"""

    excluded_tools: frozenset[str] = frozenset()
    description_patches: dict[str, str] = field(default_factory=dict)
    # 结构：{tool_name: {arg_name: json_schema_property_dict}}
    virtual_arg_schemas: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    payload_transformer: PayloadTransformer | None = None


def enrich_tool_schema(tool: BaseTool, policy: MCPAdaptationPolicy) -> BaseTool:
    """根据策略为单个 MCP 工具补强 description 契约，并向 JSON Schema 注入本地虚拟参数。"""
    patch = policy.description_patches.get(tool.name)
    if patch and patch not in (tool.description or ""):
        base_desc = (tool.description or "").strip()
        tool.description = f"{base_desc}\n{patch}" if base_desc else patch

    extra_props = policy.virtual_arg_schemas.get(tool.name)
    if extra_props and isinstance(tool.args_schema, dict):
        schema = copy.deepcopy(tool.args_schema)
        props = schema.setdefault("properties", {})
        for arg_name, prop_schema in extra_props.items():
            if arg_name not in props:
                props[arg_name] = copy.deepcopy(prop_schema)
        tool.args_schema = schema

    return tool


def curate_mcp_tools(
    raw_tools: Sequence[BaseTool],
    policy: MCPAdaptationPolicy,
) -> list[BaseTool]:
    """按策略过滤黑名单工具、增强 Schema，并按名称字典序固定排序以稳定 Prefix KV Cache。"""
    curated = [
        enrich_tool_schema(tool, policy)
        for tool in raw_tools
        if tool.name not in policy.excluded_tools
    ]
    return sorted(curated, key=lambda t: t.name)


def build_policy_interceptor(policy: MCPAdaptationPolicy) -> ToolCallInterceptor:
    """根据治理策略生成挂载于 MultiServerMCPClient 的洋葱拦截器。"""

    async def _interceptor(
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Any],
    ) -> MCPToolCallResult:
        args = dict(request.args or {})
        virtual_args: dict[str, Any] = {}

        # 1. Pre-hook：提取并剥离该工具声明的本地虚拟参数（不发往远端 MCP Server）
        declared_virtuals = policy.virtual_arg_schemas.get(request.name)
        if declared_virtuals and args:
            mutated = False
            for v_name in declared_virtuals:
                if v_name in args:
                    virtual_args[v_name] = args.pop(v_name)
                    mutated = True
            if mutated:
                request = request.override(args=args)

        # 2. 调用远端 MCP Server
        result = await handler(request)

        # 3. Post-hook：对成功返回的 JSON 文本执行策略转换器
        if (
            policy.payload_transformer is not None
            and isinstance(result, CallToolResult)
            and not result.isError
            and result.content
        ):
            first_block = result.content[0]
            if isinstance(first_block, TextContent) and first_block.text:
                try:
                    parsed = json.loads(first_block.text)
                except json.JSONDecodeError:
                    return result

                transformed = policy.payload_transformer(
                    request.name,
                    parsed,
                    virtual_args,
                )
                new_text = json.dumps(transformed, ensure_ascii=False)
                new_content = [
                    TextContent(type="text", text=new_text),
                    *result.content[1:],
                ]
                return result.model_copy(update={"content": new_content})

        return result

    return _interceptor


def parse_mcp_text_payload(result: Any) -> dict[str, Any]:
    """通用 MCP 工具返回值解析辅助函数：将 [{'type':'text','text':'<JSON>'}] 解析为 dict。"""
    if isinstance(result, dict):
        return result
    if isinstance(result, list) and result and isinstance(result[0], dict):
        text = result[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return {"text": str(result)}
