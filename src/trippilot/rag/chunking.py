import hashlib
import re
from collections.abc import Iterator

from trippilot.rag.models import GuideChunk, GuideDocument

CHUNKING_VERSION = 1
_HEADING_RE = re.compile(r"(?m)^(?P<marks>#{1,6})[ \t]+(?P<title>[^\n]+?)[ \t]*$")


def chunk_document(
    document: GuideDocument,
    *,
    max_chars: int = 800,
    overlap: int = 100,
) -> list[GuideChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap 必须满足 0 <= overlap < max_chars")

    chunks: list[GuideChunk] = []
    for start, end, heading_path in _iter_sections(document):
        for chunk_start, chunk_end in _split_range(
            document.content,
            start,
            end,
            max_chars=max_chars,
            overlap=overlap,
        ):
            content = document.content[chunk_start:chunk_end]
            chunks.append(
                GuideChunk(
                    doc_id=document.doc_id,
                    chunk_no=len(chunks),
                    heading_path=heading_path,
                    content=content,
                    start_offset=chunk_start,
                    end_offset=chunk_end,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    chunking_version=CHUNKING_VERSION,
                )
            )
    return chunks


def _iter_sections(document: GuideDocument) -> Iterator[tuple[int, int, str]]:
    headings = list(_HEADING_RE.finditer(document.content))
    preamble_end = headings[0].start() if headings else len(document.content)
    preamble = document.content[:preamble_end]
    if preamble.strip() and not _is_blockquote_only(preamble):
        yield 0, preamble_end, document.city

    stack: list[tuple[int, str]] = []
    for index, heading in enumerate(headings):
        level = len(heading.group("marks"))
        title = heading.group("title").strip()
        if level == 1:
            stack.clear()
        else:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))

        end = headings[index + 1].start() if index + 1 < len(headings) else len(document.content)
        if not document.content[heading.end() : end].strip():
            continue
        heading_path = " > ".join([document.city, *(name for _, name in stack)])
        yield heading.start(), end, heading_path


def _is_blockquote_only(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return bool(lines) and all(line.startswith(">") for line in lines)


def _split_range(
    content: str,
    start: int,
    end: int,
    *,
    max_chars: int,
    overlap: int,
) -> Iterator[tuple[int, int]]:
    cursor = start
    while cursor < end:
        raw_end = min(cursor + max_chars, end)
        if raw_end < end:
            paragraph_end = content.rfind("\n\n", cursor + max_chars // 2, raw_end)
            if paragraph_end != -1:
                raw_end = paragraph_end + 2

        chunk_start = cursor
        chunk_end = raw_end
        while chunk_start < chunk_end and content[chunk_start].isspace():
            chunk_start += 1
        while chunk_end > chunk_start and content[chunk_end - 1].isspace():
            chunk_end -= 1

        if chunk_start < chunk_end:
            yield chunk_start, chunk_end
        if raw_end == end:
            break
        cursor = max(cursor + 1, chunk_end - overlap)
