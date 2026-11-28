import hashlib
import json
import re
from typing import Any

from trippilot.rag.models import GuideDocument

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<data>.*?)\n---(?:\n|\Z)", re.DOTALL)
_REQUIRED_FIELDS = (
    "doc_id",
    "city",
    "category",
    "title",
    "summary",
    "source_id",
    "source_url",
)


def parse_guide_markdown(source: str) -> GuideDocument:
    text = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError("攻略 Markdown 缺少 frontmatter")

    try:
        metadata: Any = json.loads(match.group("data"))
    except json.JSONDecodeError as exc:
        raise ValueError("攻略 frontmatter 不是合法 JSON") from exc
    if not isinstance(metadata, dict):
        raise TypeError("攻略 frontmatter 必须是 JSON 对象")

    values: dict[str, str] = {}
    for name in _REQUIRED_FIELDS:
        value = metadata.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"攻略 frontmatter 缺少有效字段：{name}")
        values[name] = value.strip()

    content = text[match.end() :].strip()
    if not content:
        raise ValueError("攻略正文不能为空")

    return GuideDocument(
        **values,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        metadata=metadata,
    )
