ALTER TABLE trippilot_guides
    ADD COLUMN IF NOT EXISTS user_id bigint REFERENCES users (id),
    ADD COLUMN IF NOT EXISTS original_filename text,
    ADD COLUMN IF NOT EXISTS file_type varchar(8),
    ADD COLUMN IF NOT EXISTS file_size integer,
    ADD COLUMN IF NOT EXISTS vector_status varchar(16) NOT NULL DEFAULT 'pending'
        CHECK (vector_status IN ('pending', 'processing', 'completed', 'failed')),
    ADD COLUMN IF NOT EXISTS vector_total_chunks integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS vector_completed_chunks integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS vector_error text,
    ADD COLUMN IF NOT EXISTS vector_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS vector_finished_at timestamptz;

COMMENT ON COLUMN trippilot_guides.user_id IS '上传用户；NULL 表示离线导入的公共攻略';
COMMENT ON COLUMN trippilot_guides.vector_status IS 'pending / processing / completed / failed';
COMMENT ON COLUMN trippilot_guides.vector_total_chunks IS '待向量化 Chunk 总数';
COMMENT ON COLUMN trippilot_guides.vector_completed_chunks IS '已成功写入向量的 Chunk 数量';

CREATE INDEX IF NOT EXISTS idx_trippilot_guides_user_created
    ON trippilot_guides (user_id, create_time DESC);

ALTER TABLE trippilot_guides
    ADD CONSTRAINT ck_trippilot_guides_file_size CHECK (file_size IS NULL OR file_size > 0),
    ADD CONSTRAINT ck_trippilot_guides_vector_progress CHECK (
        vector_total_chunks >= 0
        AND vector_completed_chunks >= 0
        AND vector_completed_chunks <= vector_total_chunks
    );
