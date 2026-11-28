"""将上传的 Markdown / TXT 适配为统一攻略文档。"""

import hashlib
import re
import uuid

from trippilot.rag.markdown import parse_guide_markdown
from trippilot.rag.models import GuideDocument

_ALLOWED_CATEGORIES = {"景区规则", "美食避坑", "动线指南"}
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---(?:\n|\Z)", re.DOTALL)
_H1_RE = re.compile(r"(?m)^#\s+(?P<title>[^\n]+?)\s*$")


def parse_uploaded_document(
    *,
    source: str,
    filename: str,
    file_type: str,
    title: str | None = None,
    city: str | None = None,
    category: str | None = None,
) -> GuideDocument:
    values = (source, filename, title or "", city or "", category or "")
    if any("\x00" in value for value in values):
        raise ValueError("文件内容与元数据不能包含 NUL 字符")

    doc_id = str(uuid.uuid4())
    text = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if file_type == "md" and _FRONTMATTER_RE.match(text):
        document = parse_guide_markdown(text)
        if document.category not in _ALLOWED_CATEGORIES:
            raise ValueError("分类必须为：景区规则、美食避坑、动线指南")
        document = _replace_fields(document, doc_id=doc_id)
        if title is not None and title.strip():
            document = _replace_fields(document, title=title.strip())
        if city is not None and city.strip():
            document = _replace_fields(document, city=city.strip())
        if category is not None and category.strip():
            if category.strip() not in _ALLOWED_CATEGORIES:
                raise ValueError("分类必须为：景区规则、美食避坑、动线指南")
            document = _replace_fields(document, category=category.strip())
        return document

    content = text.strip()
    if not content:
        raise ValueError("文档正文不能为空")

    heading = _H1_RE.search(content)
    fallback_title = heading.group("title").strip() if heading else filename.rsplit(".", 1)[0]
    resolved_title = (title or "").strip() or fallback_title or doc_id
    resolved_category = (category or "").strip() or "动线指南"
    if resolved_category not in _ALLOWED_CATEGORIES:
        raise ValueError("分类必须为：景区规则、美食避坑、动线指南")
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ">"))
    ]
    summary = (lines[0] if lines else resolved_title)[:200]

    return GuideDocument(
        doc_id=doc_id,
        city=(city or "").strip() or "未分类",
        category=resolved_category,
        title=resolved_title,
        summary=summary,
        content=content,
        source_id=doc_id,
        source_url=f"local://guides/{doc_id}",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        metadata={},
    )


def _replace_fields(document: GuideDocument, **changes: str) -> GuideDocument:
    values = {
        "doc_id": document.doc_id,
        "city": document.city,
        "category": document.category,
        "title": document.title,
        "summary": document.summary,
        "content": document.content,
        "source_id": document.source_id,
        "source_url": document.source_url,
        "content_sha256": document.content_sha256,
        "metadata": document.metadata,
    }
    values.update(changes)
    metadata = dict(values["metadata"])
    for name in ("doc_id", "city", "category", "title", "summary", "source_id", "source_url"):
        if name in metadata:
            metadata[name] = values[name]
    values["metadata"] = metadata
    return GuideDocument(**values)
