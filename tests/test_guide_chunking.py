import hashlib
import json
from itertools import pairwise
from pathlib import Path

import pytest

from trippilot.rag.chunking import CHUNKING_VERSION, chunk_document
from trippilot.rag.markdown import parse_guide_markdown


def _markdown(body: str, *, newline: str = "\n") -> str:
    metadata = {
        "doc_id": "wikivoyage:zh:nanjing",
        "city": "南京",
        "category": "动线指南",
        "title": "南京旅行攻略",
        "summary": "南京旅行资料",
        "source_id": "wikivoyage-zh-test",
        "source_url": "https://zh.wikivoyage.org/wiki/南京",
        "license": "CC BY-SA 4.0",
    }
    frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
    return f"---{newline}{frontmatter}{newline}---{newline}{newline}{body}".replace("\n", newline)


def test_parse_guide_markdown_extracts_metadata_and_body():
    document = parse_guide_markdown(_markdown("# 南京旅行攻略\n\n欢迎来到南京。"))

    assert document.doc_id == "wikivoyage:zh:nanjing"
    assert document.city == "南京"
    assert document.category == "动线指南"
    assert document.title == "南京旅行攻略"
    assert document.content == "# 南京旅行攻略\n\n欢迎来到南京。"
    assert document.content_sha256 == hashlib.sha256(document.content.encode("utf-8")).hexdigest()
    assert document.metadata["license"] == "CC BY-SA 4.0"


def test_parse_guide_markdown_normalizes_crlf_and_bom():
    source = "\ufeff" + _markdown("# 南京旅行攻略\n\n正文。", newline="\r\n")

    document = parse_guide_markdown(source)

    assert document.content == "# 南京旅行攻略\n\n正文。"


@pytest.mark.parametrize("missing", ["doc_id", "city", "category", "title", "source_id"])
def test_parse_guide_markdown_rejects_missing_required_metadata(missing: str):
    metadata = {
        "doc_id": "guide:nanjing",
        "city": "南京",
        "category": "动线指南",
        "title": "南京旅行攻略",
        "summary": "南京旅行资料",
        "source_id": "source:nanjing",
        "source_url": "https://example.com/nanjing",
    }
    del metadata[missing]
    source = f"---\n{json.dumps(metadata, ensure_ascii=False)}\n---\n\n正文"

    with pytest.raises(ValueError, match=missing):
        parse_guide_markdown(source)


def test_parse_guide_markdown_rejects_invalid_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_guide_markdown("---\nnot-json\n---\n\n正文")


def test_chunk_document_tracks_heading_hierarchy_and_offsets():
    body = (
        "# 南京旅行攻略\n\n前言。\n\n"
        "## 交通\n\n地铁很方便。\n\n"
        "### 往返机场\n\n乘坐机场线。\n\n"
        "## 美食\n\n鸭血粉丝汤。"
    )
    document = parse_guide_markdown(_markdown(body))

    chunks = chunk_document(document)

    assert [chunk.chunk_no for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].heading_path == "南京"
    assert chunks[1].heading_path == "南京 > 交通"
    assert chunks[2].heading_path == "南京 > 交通 > 往返机场"
    assert chunks[3].heading_path == "南京 > 美食"
    for chunk in chunks:
        assert document.content[chunk.start_offset : chunk.end_offset] == chunk.content
        assert chunk.content_sha256 == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        assert chunk.chunking_version == CHUNKING_VERSION


def test_chunk_document_skips_leading_source_notice():
    body = "> 来源：示例攻略，仅供参考。\n\n# 南京旅行攻略\n\n正文。"
    document = parse_guide_markdown(_markdown(body))

    chunks = chunk_document(document)

    assert len(chunks) == 1
    assert chunks[0].content == "# 南京旅行攻略\n\n正文。"
    assert document.content[chunks[0].start_offset : chunks[0].end_offset] == chunks[0].content


def test_chunk_document_prefers_paragraph_boundary_and_overlaps():
    first_paragraph = "甲" * 450
    second_paragraph = "乙" * 450
    body = f"## 交通\n\n{first_paragraph}\n\n{second_paragraph}"
    document = parse_guide_markdown(_markdown(body))

    chunks = chunk_document(document, max_chars=800, overlap=100)

    assert len(chunks) == 2
    assert chunks[0].content.endswith(first_paragraph)
    assert chunks[1].content.startswith("甲" * 100)
    assert chunks[1].content.endswith(second_paragraph)
    for previous, current in pairwise(chunks):
        assert previous.content[-100:] == current.content[:100]


def test_chunk_document_hard_splits_an_oversized_paragraph():
    body = "## 交通\n\n" + "甲" * 1700
    document = parse_guide_markdown(_markdown(body))

    chunks = chunk_document(document, max_chars=800, overlap=100)

    assert [len(chunk.content) for chunk in chunks] == [800, 800, 307]
    for chunk in chunks:
        assert document.content[chunk.start_offset : chunk.end_offset] == chunk.content


def test_chunk_document_is_deterministic_and_hash_changes_with_content():
    first = parse_guide_markdown(_markdown("## 交通\n\n地铁很方便。"))
    second = parse_guide_markdown(_markdown("## 交通\n\n地铁很方便。"))
    changed = parse_guide_markdown(_markdown("## 交通\n\n地铁非常方便。"))

    assert chunk_document(first) == chunk_document(second)
    assert first.content_sha256 != changed.content_sha256
    assert chunk_document(first) != chunk_document(changed)


def test_chunk_document_rejects_invalid_overlap():
    document = parse_guide_markdown(_markdown("正文"))

    with pytest.raises(ValueError, match="overlap"):
        chunk_document(document, max_chars=100, overlap=100)


def test_existing_guides_can_be_parsed_and_chunked():
    guides_dir = Path(__file__).parents[1] / "data" / "guides"
    manifest = json.loads((guides_dir / "manifest.json").read_text(encoding="utf-8"))
    paths = sorted((guides_dir / "wikivoyage").glob("*.md"))

    assert len(paths) == manifest["guide_count"]
    for path in paths:
        document = parse_guide_markdown(path.read_text(encoding="utf-8"))
        chunks = chunk_document(document)
        assert chunks
        assert all(
            document.content[chunk.start_offset : chunk.end_offset] == chunk.content
            for chunk in chunks
        )
