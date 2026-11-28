CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS trippilot_guides
(
    doc_id         text PRIMARY KEY,
    city           text        NOT NULL,
    category       varchar(32) NOT NULL CHECK (category IN ('景区规则', '美食避坑', '动线指南')),
    title          text        NOT NULL,
    summary        text        NOT NULL,
    content        text        NOT NULL,
    source_id      text        NOT NULL,
    source_url     text        NOT NULL,
    content_sha256 char(64)    NOT NULL,
    metadata       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    create_time    timestamptz NOT NULL DEFAULT now(),
    update_time    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE trippilot_guides IS '旅行攻略文档表';
COMMENT ON COLUMN trippilot_guides.content IS '去除 frontmatter 并统一换行后的 Markdown 正文';
COMMENT ON COLUMN trippilot_guides.content_sha256 IS '规范化正文的 SHA-256';
COMMENT ON COLUMN trippilot_guides.metadata IS '来源、许可、时效等扩展元数据';

CREATE INDEX IF NOT EXISTS idx_trippilot_guides_city_category
    ON trippilot_guides (city, category);

CREATE TABLE IF NOT EXISTS trippilot_guide_chunks
(
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doc_id               text         NOT NULL REFERENCES trippilot_guides (doc_id) ON DELETE CASCADE,
    chunk_no             integer      NOT NULL,
    heading_path         text         NOT NULL,
    content              text         NOT NULL,
    start_offset         integer      NOT NULL CHECK (start_offset >= 0),
    end_offset           integer      NOT NULL,
    content_sha256       char(64)     NOT NULL,
    chunking_version     integer      NOT NULL,
    embedding            vector(1024),
    embedding_model      varchar(100),
    embedding_dimensions integer,
    create_time          timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uk_trippilot_guide_chunks_doc_chunk UNIQUE (doc_id, chunk_no),
    CONSTRAINT ck_trippilot_guide_chunks_offsets CHECK (end_offset > start_offset),
    CONSTRAINT ck_trippilot_guide_chunks_embedding_dimensions
        CHECK (embedding_dimensions IS NULL OR embedding_dimensions = 1024),
    CONSTRAINT ck_trippilot_guide_chunks_embedding_complete
        CHECK (
            (embedding IS NULL AND embedding_model IS NULL AND embedding_dimensions IS NULL)
            OR
            (embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dimensions = 1024)
        )
);

COMMENT ON TABLE trippilot_guide_chunks IS '旅行攻略检索 Chunk 表';
COMMENT ON COLUMN trippilot_guide_chunks.start_offset IS '相对文档正文的起始字符偏移，包含';
COMMENT ON COLUMN trippilot_guide_chunks.end_offset IS '相对文档正文的结束字符偏移，不包含';
COMMENT ON COLUMN trippilot_guide_chunks.embedding IS '百炼文本向量模型生成的 1024 维向量';
