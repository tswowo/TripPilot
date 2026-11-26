"""DeepSeek 官方 Search & Fetch API 本地工具封装。

严格对齐官方文档：https://api-docs.deepseek.com/zh-cn/guides/search/
1. POST /v1/tools/search：
   - 支持 query（1~128 字符，支持 site: / OR / -site: / 双引号短语）、
     ori_question（1~128 字符，辅助搜索引擎意图理解）、count（1~50）、need_content。
   - 响应保留 site_name、date（RFC 3339 发布时间，旅游公告防过期关键字段）、
     rerank_score（0~3，>=1.5 为优质结果）、authority（-1~4，>=3 为高权威站点）与 summary（600~800 字模型摘要）。
2. POST /v1/tools/fetch：
   - url 最大 8192 字符，必须为带 host 的 http/https URL 且禁止包含用户名/密码（userinfo）。
   - 官方示例建议 timeout=60s；HTTP 404 代表目标 URL 不可用/反爬拦截/无法提取正文。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field

from trippilot.config import Settings, get_settings

MAX_FETCH_CHARS = 80000


def normalize_api_origin(base_url: str) -> str:
    """兼容 https://api.deepseek.com 与 https://api.deepseek.com/v1 两种配置写法。"""
    return base_url.strip().rstrip("/").removesuffix("/v1")


def validate_fetch_url(url: str) -> str:
    """按官方文档校验 Fetch URL：<=8192 字符、http/https、含 host、不含用户名或密码。"""
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 8192:
        raise ToolException("URL 长度必须在 1~8192 字符之间。")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolException("URL 必须是包含有效域名的 http:// 或 https:// 地址。")
    if parsed.username is not None or parsed.password is not None:
        raise ToolException("URL 不允许包含用户名或密码凭证。")
    return cleaned


def format_search_results(payload: dict[str, Any]) -> str:
    """将 /v1/tools/search 响应整理为带时效性、权威度与长摘要的 Markdown 列表。"""
    results = payload.get("results") or []
    if not results:
        return "未检索到相关网页结果，请尝试精简关键词或调整搜索语法后重试。"

    blocks: list[str] = []
    for idx, item in enumerate(results, start=1):
        title = (item.get("title") or "无标题").strip()
        url = (item.get("url") or "").strip()
        excerpt = (item.get("summary") or item.get("snippet") or "").strip()

        meta_parts: list[str] = []
        if item.get("site_name"):
            meta_parts.append(f"站点: {item['site_name']}")
        if item.get("date"):
            meta_parts.append(f"时间: {item['date']}")
        if item.get("rerank_score") is not None:
            meta_parts.append(f"相关分: {item['rerank_score']:.2f}")
        if item.get("authority") is not None and item["authority"] >= 0:
            meta_parts.append(f"权威度: {item['authority']}")

        meta_suffix = f" ({' | '.join(meta_parts)})" if meta_parts else ""
        blocks.append(f"[{idx}] {title}{meta_suffix}\nURL: {url}\n摘要: {excerpt}")
    return "\n\n".join(blocks)


def format_fetch_result(url: str, payload: dict[str, Any], max_chars: int = MAX_FETCH_CHARS) -> str:
    """将 /v1/tools/fetch 响应整理为带字符预算上限的 Markdown 正文。"""
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()
    if not content:
        raise ToolException(f"网页抓取成功但未提取到可读正文（URL: {url}），请更换其他来源链接。")

    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    header = f"# {title}\n来源: {url}\n\n" if title else f"来源: {url}\n\n"
    suffix = f"\n\n...[正文超过 {max_chars} 字符，已截断]" if truncated else ""
    return f"{header}{content}{suffix}"


def _raise_for_deepseek_status(resp: httpx.Response, *, scene: str) -> None:
    """将 HTTP 状态码翻译为可供大模型自纠错的结构化中文提示。"""
    code = resp.status_code
    if code < 400:
        return
    if code == 404 and scene == "fetch":
        raise ToolException(
            "目标网页无法抓取（HTTP 404：URL 不可用、目标站点反爬拦截或无法提取正文），"
            "请改用其他搜索结果链接，或直接基于已有搜索摘要回答。"
        )
    if code == 401:
        raise ToolException("DeepSeek API Key 鉴权失败（HTTP 401），请检查服务端密钥配置。")
    if code == 402:
        raise ToolException("DeepSeek 账户余额不足（HTTP 402），联网工具暂时不可用。")
    if code == 429:
        raise ToolException("触发 DeepSeek 联网工具限流（上限 5 QPS，HTTP 429），请勿并发密集重试。")
    if code in (400, 422):
        raise ToolException(f"联网工具参数校验失败（HTTP {code}）：{resp.text[:200]}")
    raise ToolException(f"联网工具请求异常（HTTP {code}），请稍后重试或基于已知信息作答。")


class WebSearchInput(BaseModel):
    """对齐 POST /v1/tools/search 官方请求体契约"""
    query: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "搜索词（1~128 个字符，不可全为空白）。支持双引号精确匹配、site: 与 -site: 过滤，"
            "多个 site 条件用空格加大写 OR 连接（例如：'南京博物院 预约 site:njmuseum.com OR site:gov.cn'）。"
        ),
    )
    ori_question: str | None = Field(
        default=None,
        max_length=128,
        description="触发本次搜索的用户原始自然语言问题（1~128 字符，可选），传 null 视为未提供，有助于搜索引擎理解意图。",
    )
    count: int = Field(
        default=10,
        ge=1,
        le=50,
        description="返回结果条数（默认10条，允许1~50条）。",
    )
    localization: str = Field(
        default="",
        description="用于优先返回本地化搜索结果的 IP 地址。"
    )
    need_content: bool = Field(
        default=False,
        description="是否在 content 中返回网页正文，为 false 时 content 恒为空字符串。"
    )


class WebFetchInput(BaseModel):
    """对齐 POST /v1/tools/fetch 请求体契约。"""

    url: str = Field(
        ...,
        min_length=8,
        max_length=8192,
        description="要抓取正文的完整 HTTP/HTTPS 网页地址（最长 8192 字符，不得包含用户名或密码）。",
    )


def build_deepseek_web_tools(
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
) -> list[BaseTool]:
    """构造绑定当前配置的 web_search 与 web_fetch 本地工具实例。"""
    cfg = settings or get_settings()
    origin = normalize_api_origin(cfg.deepseek_base_url)

    def _headers() -> dict[str, str]:
        if not cfg.deepseek_api_key:
            raise ToolException("服务端未配置 DEEPSEEK_API_KEY，无法调用联网工具。")
        return {
            "Authorization": f"Bearer {cfg.deepseek_api_key}",
            "Content-Type": "application/json",
        }

    @tool("web_search", args_schema=WebSearchInput)
    async def web_search(
            query: str,
            ori_question: str | None = None,
            count: int = 5,
    ) -> str:
        """调用 DeepSeek Search API 搜索实时网页信息（景区预约/闭馆公告、交通政策、最新攻略等）。
            你需要自行组装 query 字符串，支持这些搜索语法：
            一 site:xxx 限定站点；多个站点使用大写 OR 连接，示例：南京博物院 预约 site:njmuseum.com OR site:gov.cn
            二 -site:xxx 排除站点；-关键词 排除关键词；"短语" 精确匹配；-"短语" 排除带空格短语
            三 lang:zh,en 指定语言；after:yyyy / after:yyyy-MM-dd 限定发布时间之后；before:yyyy-MM-dd 限定之前
            约束：query 总字符 1~128，不能全空白；多个site之间必须使用大写OR。
            返回标题、URL、站点发布时间、权威度与 700~1000 字摘要"""
        cleaned_query = query.strip()[:128]
        if not cleaned_query:
            raise ToolException("搜索关键词不能为空。")

        body: dict[str, Any] = {
            "query": cleaned_query,
            "count": count,
            "need_content": False,
        }
        if ori_question and ori_question.strip():
            body["ori_question"] = ori_question.strip()[:128]

        try:
            async with httpx.AsyncClient(
                    trust_env=False,
                    timeout=20.0,
                    transport=transport,
            ) as client:
                resp = await client.post(
                    f"{origin}/v1/tools/search",
                    headers=_headers(),
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ToolException(f"联网搜索网络请求失败：{exc}") from exc

        _raise_for_deepseek_status(resp, scene="search")
        return format_search_results(resp.json())

    @tool("web_fetch", args_schema=WebFetchInput)
    async def web_fetch(url: str) -> str:
        """调用 DeepSeek Fetch API 抓取单个网页并将正文转换为 Markdown 返回。
        适合在 web_search 摘要不足以覆盖细节，或用户消息中直接提供了网页链接时调用。"""
        cleaned_url = validate_fetch_url(url)

        try:
            async with httpx.AsyncClient(
                    trust_env=False,
                    timeout=60.0,
                    transport=transport,
            ) as client:
                resp = await client.post(
                    f"{origin}/v1/tools/fetch",
                    headers=_headers(),
                    json={"url": cleaned_url},
                )
        except httpx.HTTPError as exc:
            raise ToolException(f"网页抓取网络请求失败：{exc}") from exc

        _raise_for_deepseek_status(resp, scene="fetch")
        return format_fetch_result(cleaned_url, resp.json())

    return [web_search, web_fetch]
