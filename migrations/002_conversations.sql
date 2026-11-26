CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY,
    user_id bigint NOT NULL,
    title varchar(200) NOT NULL DEFAULT '新会话',
    create_time timestamptz NOT NULL DEFAULT now(),
    update_time timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON conversations (user_id, update_time DESC);