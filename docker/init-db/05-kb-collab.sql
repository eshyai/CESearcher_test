-- 知识库协作：可见性档位（幂等初始化）
--
-- visibility:
--   private（默认）—— 仅创建者 + 显式加入的成员可见/可操作（等价于改造前行为）
--   team           —— 部门内所有登录用户可见，默认 viewer，可被提权为 editor/manager
--
-- 与 is_shared 的区别：is_shared 是"是否启用成员名单"的历史字段；
-- visibility 决定是否对整个部门开放只读。两者互不替代。
--
-- 不引入 org_id 的原因：当前只有一个部门，加 org_id 后所有行都是同一个值，
-- 不产生任何判别力，却要在每次查询/新建上多带一个字段。将来真要跨组织时，
-- 在 knowledge_bases 上补 org_id，把 team 语义收窄为"同 org 内可见"即可，
-- 现有数据无需迁移，改造成本几乎为零。

ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'private';

DO $$
BEGIN
    ALTER TABLE knowledge_bases
        ADD CONSTRAINT ck_kb_visibility CHECK (visibility IN ('private', 'team'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_kb_visibility ON knowledge_bases(visibility);
