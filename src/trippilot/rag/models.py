from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GuideDocument:
    doc_id: str
    city: str
    category: str
    title: str
    summary: str
    content: str
    source_id: str
    source_url: str
    content_sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GuideChunk:
    doc_id: str
    chunk_no: int
    heading_path: str
    content: str
    start_offset: int
    end_offset: int
    content_sha256: str
    chunking_version: int
