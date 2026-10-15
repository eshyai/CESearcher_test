"""资讯个人屏蔽服务。

设计边界（改这块前务必先读 ``models/news.py::NewsHidden`` 的注释）：

- ``industry_news`` 是**按行业共享的语料**（没有 user_id），所以「删除」天然是全局动作：
  会同时影响所有用户，并且因 ``news_feedback.news_id`` 的 ``ON DELETE CASCADE``
  连带删掉所有人对该条资讯的反馈——而反馈正是 L1 / L1.5 / L2a 的输入。
- 因此本功能**只做个人过滤**：屏蔽后仅在该用户的列表与检索结果里被排除。
  共享库、其他用户视图、Milvus 向量索引都不动，且随时可逆。
- 屏蔽**不写反馈**：反馈是「信号」（喂学习），屏蔽是「过滤」（查询层排除），
  两者混在一起会让偏好学习把「顺手屏蔽」误当强负样本。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from models.news import IndustryNews, NewsHidden


def coerce_uuid(value: Any) -> Optional[uuid.UUID]:
    """把路径参数/字符串安全转成 UUID，非法则返回 None。"""
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def hidden_news_ids(db: Session, user_id: Any) -> Set[str]:
    """该用户已屏蔽的资讯 id 集合（字符串形式，便于与 ``str(news.id)`` 比对）。

    未登录（user_id 为空）时返回空集合 = 不过滤，与改动前的行为一致。
    """
    uid = coerce_uuid(user_id)
    if not uid:
        return set()
    rows = db.query(NewsHidden.news_id).filter(NewsHidden.user_id == uid).all()
    return {str(r[0]) for r in rows}


def is_hidden(db: Session, user_id: Any, news_id: Any) -> bool:
    """该用户是否已屏蔽这条资讯。"""
    uid, nid = coerce_uuid(user_id), coerce_uuid(news_id)
    if not uid or not nid:
        return False
    return (
        db.query(NewsHidden.id)
        .filter(NewsHidden.user_id == uid, NewsHidden.news_id == nid)
        .first()
        is not None
    )


def hide_news(
    db: Session,
    user_id: Any,
    news_id: Any,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """屏蔽一条资讯（幂等）。重复屏蔽不报错，也不覆盖原 ``reason``。

    Returns:
        ``{"status": "created"|"exists", "hidden": {...}}``

    Raises:
        ValueError: ``user_id`` / ``news_id`` 非法。
    """
    uid, nid = coerce_uuid(user_id), coerce_uuid(news_id)
    if not uid:
        raise ValueError("user_id 非法")
    if not nid:
        raise ValueError("news_id 非法")

    existing = (
        db.query(NewsHidden)
        .filter(NewsHidden.user_id == uid, NewsHidden.news_id == nid)
        .first()
    )
    if existing:
        return {"status": "exists", "hidden": existing.to_dict()}

    row = NewsHidden(user_id=uid, news_id=nid, reason=(reason or "")[:200] or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "created", "hidden": row.to_dict()}


def unhide_news(db: Session, user_id: Any, news_id: Any) -> bool:
    """取消屏蔽（幂等）。返回是否真的删掉了一条记录。"""
    uid, nid = coerce_uuid(user_id), coerce_uuid(news_id)
    if not uid or not nid:
        return False
    deleted = (
        db.query(NewsHidden)
        .filter(NewsHidden.user_id == uid, NewsHidden.news_id == nid)
        .delete(synchronize_session=False)
    )
    db.commit()
    return bool(deleted)


def list_hidden_news(
    db: Session,
    user_id: Any,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """该用户已屏蔽的资讯列表（按屏蔽时间倒序），返回 ``(items, total)``。

    返回的是资讯本身的字段（与 ``/news/list`` 的卡片同构）+ ``hidden_at``；
    若资讯已被物理删除（共享库侧清理），该条自动跳过。
    """
    uid = coerce_uuid(user_id)
    if not uid:
        return [], 0

    base = (
        db.query(NewsHidden, IndustryNews)
        .join(IndustryNews, IndustryNews.id == NewsHidden.news_id)
        .filter(NewsHidden.user_id == uid)
    )
    total = base.count()
    rows = (
        base.order_by(NewsHidden.created_at.desc())
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )

    items: List[Dict[str, Any]] = []
    for hidden, news in rows:
        item = news.to_dict()
        item["hidden_at"] = hidden.created_at.isoformat() if hidden.created_at else None
        item["hidden_reason"] = hidden.reason
        items.append(item)
    return items, total


def hidden_count(db: Session, user_id: Any) -> int:
    """该用户已屏蔽条数（前端徽标用）。"""
    uid = coerce_uuid(user_id)
    if not uid:
        return 0
    return db.query(NewsHidden.id).filter(NewsHidden.user_id == uid).count()
