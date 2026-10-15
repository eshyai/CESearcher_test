-- 资讯个人屏蔽（幂等）
--
-- 背景：`industry_news` 是按行业共享的语料（无 user_id），所以任何「删除」都是全局动作，
--   会同时影响所有用户，并且会因为 `news_feedback.news_id` 的 ON DELETE CASCADE
--   连带删掉所有人的反馈（而反馈是 L1 / L1.5 / L2a 的输入）。
--
-- 因此本功能只做**个人过滤**：命中本表的资讯仅在**该用户**的列表与检索结果里被排除。
--   - 共享库、其他用户的视图、Milvus 向量索引都不受影响；
--   - 随时可逆（DELETE 一行即恢复显示）；
--   - 与 news_feedback 互不干扰：屏蔽不写反馈，避免污染偏好学习样本。
--
-- 说明：后端启动时 `Base.metadata.create_all` 也会建这张表（模型见 models/news.py::NewsHidden），
--   本文件供「全新初始化」与「已有库补建」使用，语句全部幂等，重复执行安全。
--   索引命名与 SQLAlchemy 由 `index=True` 生成的名称保持一致（ix_<表>_<列>），
--   复合查询所需的索引由唯一约束 uq_news_hidden_user_news(user_id, news_id) 的 btree 直接提供。
--
-- 迁移执行（项目约定，注意用 docker cp 而非 PowerShell 管道，避免编码丢内容）：
--   docker cp docker/init-db/08-news-hidden.sql industry_postgres:/tmp/
--   docker exec industry_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/08-news-hidden.sql'

CREATE TABLE IF NOT EXISTS news_hidden (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    news_id    UUID        NOT NULL REFERENCES industry_news (id) ON DELETE CASCADE,
    reason     VARCHAR(200),
    created_at TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- 同一用户对同一条资讯只允许一条屏蔽记录（重复屏蔽幂等）。
-- 该唯一约束同时充当 (user_id, news_id) 的查询索引，无需再建复合索引。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_news_hidden_user_news') THEN
        ALTER TABLE news_hidden
            ADD CONSTRAINT uq_news_hidden_user_news UNIQUE (user_id, news_id);
    END IF;
END $$;

-- 检出链路的两条主查询形态：按 user_id 取屏蔽集合 / 按 news_id 反查
CREATE INDEX IF NOT EXISTS ix_news_hidden_user_id ON news_hidden (user_id);
CREATE INDEX IF NOT EXISTS ix_news_hidden_news_id ON news_hidden (news_id);
