-- 共享资讯库：收录池 + 批注（幂等）
--
-- 背景与边界（改这块前必读）：
--   `industry_news` 是按行业共享的语料（无 user_id），**原文只读**——本功能不修改标题/正文，
--   所有协作都落在批注上，因此**不需要字段级版本审计**（省掉 news_revisions 那一层）。
--   「收录」= 把一条资讯选进共享库池子；「下架」= status 置 archived，**软删不删数据**。
--   批注只有作者能改/删，删除同样软删（deleted_at），保留审计线索。
--
-- 说明：后端启动时 `Base.metadata.create_all` 也会建这两张表（模型见 models/news_library.py），
--   本文件供「全新初始化」与「已有库补建」使用，语句全部幂等，重复执行安全。
--
-- 已有库补建（initdb 只在空库跑，老库必须手工执行一次）：
--   docker cp docker/init-db/10-news-library.sql industry_postgres:/tmp/
--   docker exec industry_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/10-news-library.sql'

CREATE TABLE IF NOT EXISTS news_library_items (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    news_id     UUID        NOT NULL REFERENCES industry_news (id) ON DELETE CASCADE,
    added_by    UUID        REFERENCES users (id) ON DELETE SET NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'active',
    archived_by UUID        REFERENCES users (id) ON DELETE SET NULL,
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMP
);

-- 一条资讯只能收录一次（重复收录靠它做幂等）
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_news_library_news') THEN
        ALTER TABLE news_library_items
            ADD CONSTRAINT uq_news_library_news UNIQUE (news_id);
    END IF;
END $$;

-- 列表主查询：按 status 过滤 + 按收录时间倒序
CREATE INDEX IF NOT EXISTS ix_news_library_items_status ON news_library_items (status);
CREATE INDEX IF NOT EXISTS ix_news_library_items_created_at ON news_library_items (created_at);

CREATE TABLE IF NOT EXISTS news_annotations (
    id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    news_id    UUID          NOT NULL REFERENCES industry_news (id) ON DELETE CASCADE,
    user_id    UUID          REFERENCES users (id) ON DELETE CASCADE,
    content    VARCHAR(2000) NOT NULL,
    created_at TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    deleted_at TIMESTAMP
);

-- 详情页按资讯取批注 / 权限判定按作者
CREATE INDEX IF NOT EXISTS ix_news_annotations_news_id ON news_annotations (news_id);
CREATE INDEX IF NOT EXISTS ix_news_annotations_user_id ON news_annotations (user_id);
