-- L1.5 反馈自动派生候选主题（幂等）
-- 目的：打破「反馈只能改已有主题、不能建主题」的冷启动死锁。
--   manual = 用户在「我的关注」手动创建（旧数据默认值）
--   auto   = 由正向反馈自动派生，可在前端「确认为关注」升级为 manual

ALTER TABLE focus_topics ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'manual';
ALTER TABLE focus_topics ADD COLUMN IF NOT EXISTS origin_news_id UUID;
ALTER TABLE focus_topics ADD COLUMN IF NOT EXISTS auto_hits INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_focus_topics_source ON focus_topics (source);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_focus_topics_source'
    ) THEN
        ALTER TABLE focus_topics
            ADD CONSTRAINT ck_focus_topics_source CHECK (source IN ('manual', 'auto'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_focus_topics_origin_news'
    ) THEN
        -- 触发资讯被删除时只置空，不影响关注主题本身
        ALTER TABLE focus_topics
            ADD CONSTRAINT fk_focus_topics_origin_news
            FOREIGN KEY (origin_news_id) REFERENCES industry_news (id) ON DELETE SET NULL;
    END IF;
END $$;
