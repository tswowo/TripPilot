-- 增量迁移：为 conversations 与 messages 增加逻辑删除键 is_deleted（0-有效，1-无效）

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS is_deleted smallint NOT NULL DEFAULT 0
        CONSTRAINT ck_conversations_is_deleted CHECK (is_deleted IN (0, 1));

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS is_deleted smallint NOT NULL DEFAULT 0
        CONSTRAINT ck_messages_is_deleted CHECK (is_deleted IN (0, 1));

COMMENT ON COLUMN conversations.is_deleted IS '逻辑删除标识：0-有效，1-无效';
COMMENT ON COLUMN messages.is_deleted IS '逻辑删除标识：0-有效，1-无效';

-- 利用 PostgreSQL 部分索引（Partial Index）只索引有效数据，兼顾过滤性能与索引体积
CREATE INDEX IF NOT EXISTS idx_conversations_active_user_updated
    ON conversations (user_id, update_time DESC)
    WHERE is_deleted = 0;

CREATE INDEX IF NOT EXISTS idx_messages_active_conversation_id_id
    ON messages (conversation_id, id DESC)
    WHERE is_deleted = 0;
