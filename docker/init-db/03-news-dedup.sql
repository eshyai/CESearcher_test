-- 资讯去重指纹（幂等迁移，已有数据卷手动执行；全新数据卷首次初始化自动执行）
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS content_fingerprint VARCHAR(16);
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS duplicate_of_id UUID;

CREATE INDEX IF NOT EXISTS idx_news_content_fingerprint ON industry_news(content_fingerprint);
CREATE INDEX IF NOT EXISTS idx_news_is_duplicate ON industry_news(is_duplicate) WHERE is_duplicate;
