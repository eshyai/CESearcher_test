"""资讯个人屏蔽服务（过滤 + 弱负信号）。

设计边界（改这块前务必先读 ``models/news.py::NewsHidden`` 的注释）：

- ``industry_news`` 是**按行业共享的语料**（没有 user_id），所以「删除」天然是全局动作：
  会同时影响所有用户，并且因 ``news_feedback.news_id`` 的 ``ON DELETE CASCADE``
  连带删掉所有人对该条资讯的反馈——而反馈正是 L1 / L1.5 / L2a 的输入。
- 因此本功能**只做个人过滤**：屏蔽后仅在该用户的列表与检索结果里被排除。
  共享库、其他用户视图、Milvus 向量索引都不动，且随时可逆。
- 屏蔽**不写反馈**：反馈是「信号」（喂学习），屏蔽是「过滤」（查询层排除），
  两者混在一起会让偏好学习把「顺手屏蔽」误当强负样本。

弱负信号（LMA）
---------------
屏蔽在「过滤」之上再给一点**弱负信号**：对本用户命中的关注主题施加
``WEAK_HIDE_DELTA`` 的降权，累计到 ``focus_topics.weak_penalty``（下限 ``WEAK_PENALTY_FLOOR``）。

三条硬边界（与「不感兴趣 / 不相关」这类强负样本严格区分）：

1. **只动 weak_penalty**：不改 ``weight``，也不改 ``positive_count / negative_count``；
2. **不写 ``news_feedback``**：因此不会混进 L1.5 / L2a 的反馈样本；
3. **可精确回滚**：本次施加的每个 ``(topic_id, delta)`` 写进 ``news_hidden.weak_signals``，
   取消屏蔽时按记录的量反向加回——而不是「按当前命中的主题重算」
   （那是错的：期间用户可能新建/改名/停用了主题，重算会对不上账）。

强度语义：``WEAK_HIDE_DELTA`` 的量级远小于一次「不感兴趣」（后者一次 -0.35 且会改计数），
所以弱信号只用来**微调排序**，不足以让主题自动退役。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from models.news import FocusTopic, IndustryNews, NewsHidden
from service.feedback_learning_service import news_text
from service.interest_scoring import TextIndex, compile_topic, query_matches

# 每次屏蔽、对每个被命中的主题施加的弱降权（负值）
WEAK_HIDE_DELTA = -0.05
# 弱降权累计下限：屏蔽再多也最多降这么多，保证它始终只是「微调」而非「强负」
WEAK_PENALTY_FLOOR = -0.5


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


# ---------------------------------------------------------------------------
# 弱负信号：施加 / 回滚
# ---------------------------------------------------------------------------

def _matched_topics(db: Session, uid: uuid.UUID, news: IndustryNews) -> List[FocusTopic]:
    """本条资讯命中了该用户的哪些**生效中**关注主题（复用 L1.5 的匹配算法）。"""
    topics = (
        db.query(FocusTopic)
        .filter(FocusTopic.user_id == uid, FocusTopic.is_active.is_(True))
        .all()
    )
    if not topics:
        return []
    index = TextIndex(news_text(news))
    return [t for t in topics if query_matches(compile_topic(t), index)]


def apply_weak_penalty(db: Session, uid: uuid.UUID, news: IndustryNews) -> List[Dict[str, Any]]:
    """对命中主题施加弱降权，返回本次实际生效的 ``[{topic_id, name, delta, weak_penalty}]``。

    已到下限的主题不再重复计数、``delta`` 记 0 也不入返回（避免堆无效记录）。
    **不 commit**：由调用方与屏蔽记录一并提交，保证两者原子。
    """
    signals: List[Dict[str, Any]] = []
    for topic in _matched_topics(db, uid, news):
        current = float(topic.weak_penalty or 0.0)
        target = round(max(WEAK_PENALTY_FLOOR, current + WEAK_HIDE_DELTA), 4)
        delta = round(target - current, 4)
        if delta >= 0:  # 已到下限：不再降权，也不记账
            continue
        topic.weak_penalty = target
        topic.weak_hits = int(topic.weak_hits or 0) + 1
        signals.append({
            "topic_id": str(topic.id),
            "name": topic.name,
            "delta": delta,
            "weak_penalty": target,
        })
    return signals


def _reverse_weak_penalty(db: Session, uid: uuid.UUID, signals: List[Dict[str, Any]]) -> List[str]:
    """按 ``signals`` 精确反向回滚弱降权，返回被还原的主题名。**不 commit**。"""
    restored: List[str] = []
    for sig in signals or []:
        tid = coerce_uuid(sig.get("topic_id"))
        if not tid:
            continue
        topic = (
            db.query(FocusTopic)
            .filter(FocusTopic.id == tid, FocusTopic.user_id == uid)
            .first()
        )
        if topic is None:
            continue  # 主题已被删除：跳过（不阻断取消屏蔽）
        delta = float(sig.get("delta") or 0.0)
        current = float(topic.weak_penalty or 0.0)
        topic.weak_penalty = round(min(0.0, max(WEAK_PENALTY_FLOOR, current - delta)), 4)
        topic.weak_hits = max(0, int(topic.weak_hits or 0) - 1)
        restored.append(topic.name)
    return restored


def _load_signals(row: NewsHidden) -> List[Dict[str, Any]]:
    if not row.weak_signals:
        return []
    try:
        data = json.loads(row.weak_signals)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# 屏蔽 / 取消 / 列表
# ---------------------------------------------------------------------------

def hide_news(
    db: Session,
    user_id: Any,
    news_id: Any,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """屏蔽一条资讯（幂等）。重复屏蔽不报错，也不覆盖原 ``reason``。

    同时施加弱负降权（见模块 docstring）。返回的 ``weak_topics`` 是本次**真正生效**
    的降权列表；重复屏蔽时为空（不会二次降权）。

    Returns:
        ``{"status": "created"|"exists", "hidden": {...}, "weak_topics": [...]}``

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
        return {"status": "exists", "hidden": existing.to_dict(), "weak_topics": []}

    row = NewsHidden(user_id=uid, news_id=nid, reason=(reason or "")[:200] or None)
    db.add(row)

    # 弱负信号：只对本用户命中的生效主题降权（资讯不存在时静默跳过）
    news = db.query(IndustryNews).filter(IndustryNews.id == nid).first()
    signals = apply_weak_penalty(db, uid, news) if news is not None else []
    row.weak_signals = json.dumps(signals, ensure_ascii=False) if signals else None

    db.commit()
    db.refresh(row)
    return {"status": "created", "hidden": row.to_dict(), "weak_topics": signals}


def unhide_news(db: Session, user_id: Any, news_id: Any) -> bool:
    """取消屏蔽（幂等）。返回是否真的删掉了一条记录。

    同时按屏蔽时记录的 ``weak_signals`` 精确回滚弱降权。
    """
    uid, nid = coerce_uuid(user_id), coerce_uuid(news_id)
    if not uid or not nid:
        return False
    row = (
        db.query(NewsHidden)
        .filter(NewsHidden.user_id == uid, NewsHidden.news_id == nid)
        .first()
    )
    if row is None:
        return False
    _reverse_weak_penalty(db, uid, _load_signals(row))
    db.delete(row)
    db.commit()
    return True


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
        # 这条屏蔽一共降权了哪些主题（可解释性）
        item["weak_topics"] = [
            {"topic_id": s.get("topic_id"), "name": s.get("name"), "delta": s.get("delta")}
            for s in _load_signals(hidden)
        ]
        items.append(item)
    return items, total


def hidden_count(db: Session, user_id: Any) -> int:
    """该用户已屏蔽条数（前端徽标用）。"""
    uid = coerce_uuid(user_id)
    if not uid:
        return 0
    return db.query(NewsHidden.id).filter(NewsHidden.user_id == uid).count()
