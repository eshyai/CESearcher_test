-- L2a 归纳引擎（LLM 慢回路，幂等）
--
-- 设计要点：模型永远不直接改数据，只产出「建议 + 理由 + 证据」；
--   1) 每次归纳先拍一张 focus_topics 全量快照（focus_topic_revisions.snapshot），用于一键回滚；
--   2) 归纳结论全部进 preference_suggestions 的 pending 箱，由用户逐条接受/拒绝。
-- 目标：解决 L1.5 遗留的三个瓶颈——无归纳、无衰减淘汰、跨用户为零中的前两个。

-- ---------------------------------------------------------------------------
-- 1. 归纳快照（回滚依据）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS focus_topic_revisions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    trigger       VARCHAR(30) NOT NULL DEFAULT 'manual',
    sample_count  INTEGER     NOT NULL DEFAULT 0,
    topic_count   INTEGER     NOT NULL DEFAULT 0,
    current       BOOLEAN     NOT NULL DEFAULT TRUE,
    status        VARCHAR(20) NOT NULL DEFAULT 'applied',
    snapshot      TEXT,
    diff          TEXT,
    summary       TEXT,
    error         TEXT,
    created_at    TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_focus_topic_revisions_user_time
    ON focus_topic_revisions (user_id, created_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_focus_topic_revisions_trigger') THEN
        ALTER TABLE focus_topic_revisions
            ADD CONSTRAINT ck_focus_topic_revisions_trigger
            CHECK (trigger IN ('manual', 'batch', 'schedule'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_focus_topic_revisions_status') THEN
        ALTER TABLE focus_topic_revisions
            ADD CONSTRAINT ck_focus_topic_revisions_status
            CHECK (status IN ('applied', 'rolled_back'));
    END IF;
END $$;

-- 每个用户只能有一条 current=TRUE（回滚只需要找最近一条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_focus_topic_revisions_current
    ON focus_topic_revisions (user_id) WHERE current;

-- ---------------------------------------------------------------------------
-- 2. 建议箱（待确认）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS preference_suggestions (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID         NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    revision_id  UUID         REFERENCES focus_topic_revisions (id) ON DELETE SET NULL,
    kind         VARCHAR(20)  NOT NULL,
    name         VARCHAR(200) NOT NULL,
    target_id    UUID         REFERENCES focus_topics (id) ON DELETE SET NULL,
    payload      TEXT,
    reason       TEXT,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0,
    evidence     TEXT,
    status       VARCHAR(20)  NOT NULL DEFAULT 'pending',
    resolved_at  TIMESTAMP,
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_preference_suggestions_user_status
    ON preference_suggestions (user_id, status, confidence DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_preference_suggestions_kind') THEN
        ALTER TABLE preference_suggestions
            ADD CONSTRAINT ck_preference_suggestions_kind
            CHECK (kind IN ('add', 'adjust', 'merge', 'drop', 'insight'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_preference_suggestions_status') THEN
        ALTER TABLE preference_suggestions
            ADD CONSTRAINT ck_preference_suggestions_status
            CHECK (status IN ('pending', 'accepted', 'rejected'));
    END IF;

    -- 同一次归纳里同 kind 同名只留一条（重跑归纳不会堆重复建议）
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_pref_suggestion_user_rev_kind_name') THEN
        ALTER TABLE preference_suggestions
            ADD CONSTRAINT uq_pref_suggestion_user_rev_kind_name
            UNIQUE (user_id, revision_id, kind, name);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. focus_topics 补 auto_hits 之外的归纳痕迹（哪些主题是被归纳建议加进来的）
-- ---------------------------------------------------------------------------
ALTER TABLE focus_topics ADD COLUMN IF NOT EXISTS suggestion_id UUID;
CREATE INDEX IF NOT EXISTS idx_focus_topics_suggestion ON focus_topics (suggestion_id);
