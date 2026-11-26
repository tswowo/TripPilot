CREATE TABLE messages
(
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid        NOT NULL,
    turn_id         uuid        NOT NULL,
    role            varchar(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    status          varchar(16) NOT NULL DEFAULT 'completed' CHECK (status IN ('streaming', 'completed', 'failed')),
    error_code      varchar(50),
    error_message   text,
    create_time     timestamptz NOT NULL DEFAULT now(),
    update_time     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_messages_conversation_turn_role
        UNIQUE (conversation_id, turn_id, role)
);

-- PG 单独写字段注释
COMMENT ON COLUMN messages.turn_id IS '轮次id';
COMMENT ON COLUMN messages.role IS '角色';
COMMENT ON COLUMN messages.content IS '内容';
COMMENT ON COLUMN messages.status IS '状态；当前非流式直接 completed；为以后 SSE 预留';
COMMENT ON COLUMN messages.error_code IS '错误码';
COMMENT ON COLUMN messages.error_message IS '错误信息';
COMMENT ON COLUMN messages.create_time IS '创建时间';
COMMENT ON COLUMN messages.update_time IS '更新时间';

CREATE INDEX idx_messages_conversation_id_id
    ON messages (conversation_id, id);
