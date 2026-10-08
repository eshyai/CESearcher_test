-- 资讯反馈学习与共享知识库（幂等初始化）
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS is_shared BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS industry_news (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    industry_id VARCHAR(50), title VARCHAR(500) NOT NULL, content TEXT,
    source VARCHAR(200), source_url TEXT, category VARCHAR(50) DEFAULT '新闻',
    department VARCHAR(200), publish_time TIMESTAMP, collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    keywords VARCHAR(500), is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS focus_topics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_type VARCHAR(30) NOT NULL, name VARCHAR(200) NOT NULL, query_text TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    positive_count INTEGER NOT NULL DEFAULT 0, negative_count INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_focus_topic_user_type_name UNIQUE (user_id, topic_type, name)
);

CREATE TABLE IF NOT EXISTS news_feedback (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    news_id UUID NOT NULL REFERENCES industry_news(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interested BOOLEAN, relevant BOOLEAN, note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_news_feedback_news_user UNIQUE (news_id, user_id)
);

CREATE TABLE IF NOT EXISTS knowledge_base_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'viewer', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_kb_member UNIQUE (knowledge_base_id, user_id),
    CONSTRAINT ck_kb_member_role CHECK (role IN ('viewer', 'editor', 'manager'))
);

CREATE TABLE IF NOT EXISTS knowledge_audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(50) NOT NULL, target_type VARCHAR(30) NOT NULL DEFAULT 'knowledge_base',
    target_id VARCHAR(100), detail TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_focus_topics_user ON focus_topics(user_id);
CREATE INDEX IF NOT EXISTS idx_news_feedback_user ON news_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_news_feedback_news ON news_feedback(news_id);
CREATE INDEX IF NOT EXISTS idx_kb_members_user ON knowledge_base_members(user_id);
CREATE INDEX IF NOT EXISTS idx_kb_audit_kb ON knowledge_audit_logs(knowledge_base_id);
