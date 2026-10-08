-- 资讯 LLM 结构化抽取（幂等迁移，已有数据卷手动执行；全新数据卷首次初始化自动执行）
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS extracted_topics TEXT;
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS event_type VARCHAR(50);
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS impact_direction VARCHAR(20);
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS key_numbers TEXT;
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS entities TEXT;
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS summary VARCHAR(500);
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(20) NOT NULL DEFAULT 'pending';
ALTER TABLE industry_news ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_news_event_type ON industry_news(event_type);
CREATE INDEX IF NOT EXISTS idx_news_extraction_status ON industry_news(extraction_status);
