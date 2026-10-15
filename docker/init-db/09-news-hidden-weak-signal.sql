-- 屏蔽的「弱负信号」（LMA）——幂等
--
-- 背景：资讯屏蔽此前只做**过滤器**（news_hidden，见 08-news-hidden.sql），
--   被屏蔽的资讯只在该用户的列表/检索里消失，不给偏好学习任何输入。
--   本迁移把屏蔽升级为一条**弱负信号**：屏蔽时对本用户命中的关注主题施加一点降权。
--
-- 三条边界（与强负样本「不感兴趣 / 不相关」严格区分）：
--   1. 只改 focus_topics.weak_penalty（≤0），**不动 weight，也不动 positive_count /
--      negative_count**——强负样本才会更新计数；
--   2. 屏蔽**不写 news_feedback**，因此不会混进 L1.5 / L2a 的反馈样本；
--   3. 取消屏蔽按 news_hidden.weak_signals 记录的量**精确反向回滚**。
--
-- 说明：后端启动时 Base.metadata.create_all 会新建缺失的表，但**不会给已有表补列**，
--   所以已有库必须执行本迁移。语句全部幂等，重复执行安全。
--   列名/类型与 SQLAlchemy 模型（models/news.py::FocusTopic / NewsHidden）保持一致。
--
-- 迁移执行（项目约定，注意用 docker cp 而非 PowerShell 管道，避免编码丢内容）：
--   docker cp docker/init-db/09-news-hidden-weak-signal.sql industry_postgres:/tmp/
--   docker exec industry_postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/09-news-hidden-weak-signal.sql'

-- focus_topics：弱降权累计与命中次数
ALTER TABLE focus_topics ADD COLUMN IF NOT EXISTS weak_penalty DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE focus_topics ADD COLUMN IF NOT EXISTS weak_hits    INTEGER          NOT NULL DEFAULT 0;

COMMENT ON COLUMN focus_topics.weak_penalty IS '屏蔽累积的弱降权（≤0，只影响个性化排序，不影响 weight/计数）';
COMMENT ON COLUMN focus_topics.weak_hits    IS '被屏蔽资讯命中的次数（弱负信号）';

-- news_hidden：本次屏蔽施加了哪些降权（取消屏蔽时据此精确回滚）
ALTER TABLE news_hidden ADD COLUMN IF NOT EXISTS weak_signals TEXT;

COMMENT ON COLUMN news_hidden.weak_signals IS '本次屏蔽施加的弱降权 JSON（取消屏蔽时据此精确回滚）';
