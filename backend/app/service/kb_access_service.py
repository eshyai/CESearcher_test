"""知识库可见范围与 Milvus 集合命名（统一入口）。

这一层解决两个问题：

1. **可见范围**：谁能在检索/深研时看到哪些知识库。规则与 `GET /knowledge-bases`
   完全一致 = 自己创建的 ∪ 显式加入成员的 ∪ `visibility=team`（部门共享只读）。
   任何"按知识库检索"的地方都必须走这里，否则会出现 A 的私有库进 B 的回答。

2. **集合命名**：历史实现把 Milvus 集合命名为 `kb_{知识库名}`，而知识库重名校验
   只查"同一用户"，于是两个用户建同名库会写进同一个集合，数据直接串在一起。
   现改为 `kb_{uuid hex}`（全局唯一）。历史集合 `kb_{名称}` 仍会被检索，
   所以 `kb_collection_candidates` 同时返回新旧两种名字，读取端逐个尝试。
"""
import logging
from typing import Iterable, List, Optional, Set

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models.knowledge import KnowledgeBase, KnowledgeBaseMember

logger = logging.getLogger(__name__)


def kb_collection_name(kb: KnowledgeBase) -> str:
    """当前使用的 Milvus 集合名：按 UUID 命名，天然全局唯一。"""
    return f"kb_{kb.id.hex}"


def legacy_kb_collection_name(kb: KnowledgeBase) -> str:
    """历史集合名 `kb_{名称}`。迁移前上传的文档仍留在这类集合里。"""
    return f"kb_{kb.name}".lower().replace(" ", "_")


def kb_collection_candidates(kb: KnowledgeBase) -> List[str]:
    """某知识库可能对应到的全部集合名（新命名优先，其次历史命名）。"""
    names = [kb_collection_name(kb)]
    legacy = legacy_kb_collection_name(kb)
    if legacy and legacy not in names:
        names.append(legacy)
    return names


def accessible_knowledge_bases(db: Session, user_id) -> List[KnowledgeBase]:
    """当前用户可访问的知识库列表（未删除）。user_id 为空时返回空列表。"""
    if not user_id:
        return []
    memberships = [
        row[0] for row in db.query(KnowledgeBaseMember.knowledge_base_id)
        .filter(KnowledgeBaseMember.user_id == user_id).all()
    ]
    conds = [KnowledgeBase.user_id == user_id, KnowledgeBase.visibility == "team"]
    if memberships:
        conds.append(KnowledgeBase.id.in_(memberships))
    return (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.deleted_at.is_(None), or_(*conds))
        .order_by(KnowledgeBase.updated_at.desc())
        .all()
    )


def accessible_kb_ids(db: Session, user_id) -> Set:
    """当前用户可访问的知识库 ID 集合。"""
    return {kb.id for kb in accessible_knowledge_bases(db, user_id)}


def allowed_kb_collections(db: Session, user_id) -> Set[str]:
    """当前用户在向量检索时被允许命中的 Milvus 集合名集合。

    额外并入历史遗留的 "knowledge_base" 集合**仅当**它确实对应用户可见库
    （无法判定归属时一律排除——宁可少检索，不可串数据）。
    """
    allowed: Set[str] = set()
    for kb in accessible_knowledge_bases(db, user_id):
        allowed.update(kb_collection_candidates(kb))
    return allowed


def resolve_collection_for_read(milvus, kb: KnowledgeBase) -> Optional[str]:
    """读取切片时定位实际集合：新命名优先，没有则回退历史命名。

    返回首个真实存在的集合名；都不存在返回 None。
    """
    from pymilvus import utility

    for name in kb_collection_candidates(kb):
        try:
            if utility.has_collection(name):
                return name
        except Exception as e:  # pragma: no cover - 依赖外部服务
            logger.warning(f"[kb] 检查集合 {name} 失败: {e}")
    return None


def describe_allowed(user_id, allowed: Iterable[str]) -> str:
    """日志用的简短描述。"""
    names = sorted(allowed)
    return f"user={user_id} allowed_collections={names if names else '[]'}"
