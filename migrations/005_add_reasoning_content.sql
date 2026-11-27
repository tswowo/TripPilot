-- 增量迁移：为 messages 增加 reasoning_content 思考链字段

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS reasoning_content text NOT NULL DEFAULT '';

UPDATE messages
SET reasoning_content = ''
WHERE reasoning_content IS NULL;

COMMENT ON COLUMN messages.reasoning_content IS '思考链内容';
