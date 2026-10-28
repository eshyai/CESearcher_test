"""共享资讯库：收录池 + 批注（业务逻辑唯一入口）

设计边界（改这块前必读）：
- ``industry_news`` 是**按行业共享的语料**（无 user_id），本模块**从不修改原文**：
  标题/正文/标签都只读。所有协作落在批注上，所以不需要字段级版本表。
- **收录** = 把资讯选进共享库（``news_library_items.status='active'``）；
  **下架** = 置 ``archived`` + 记 ``archived_by``，**软删不删数据**，可再次收录恢复。
- **批注**：任何登录用户可加；**只有作者能改/删**；删除是软删（``deleted_at``）。
- 与 ``news_hidden`` 的区别：屏蔽是**个人**过滤（看不到），共享库下架是**全局**动作（所有人看不到）。
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models.news import IndustryNews
from models.news_library import NewsLibraryItem, NewsAnnotation
from models.user import User

logger = logging.getLogger(__name__)

ANNOTATION_MAX_LEN = 2000
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
VALID_STATUS = {STATUS_ACTIVE, STATUS_ARCHIVED}


class NewsLibraryError(Exception):
    """业务异常：由路由层翻译成 HTTP 状态码"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _safe_json(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def _news_fields(news: IndustryNews) -> Dict[str, Any]:
    """共享库展示所需的资讯字段（原文只读，这里只做投影）"""
    return {
        "id": str(news.id),
        "title": news.title,
        "summary": news.summary,
        "content": news.content,
        "source": news.source,
        "source_url": news.source_url,
        "category": news.category,
        "publish_time": _iso(news.publish_time),
        "collected_at": _iso(news.collected_at),
        "event_type": news.event_type,
        "extracted_topics": _safe_json(news.extracted_topics, []),
        "entities": _safe_json(news.entities, []),
    }


def _annotation_counts(db: Session, news_ids: List[uuid.UUID]) -> Dict[str, int]:
    if not news_ids:
        return {}
    rows = (
        db.query(NewsAnnotation.news_id, func.count(NewsAnnotation.id))
        .filter(
            NewsAnnotation.news_id.in_(news_ids),
            NewsAnnotation.deleted_at.is_(None),
        )
        .group_by(NewsAnnotation.news_id)
        .all()
    )
    return {str(news_id): int(count) for news_id, count in rows}


def _require_news(db: Session, news_id: uuid.UUID) -> IndustryNews:
    news = db.query(IndustryNews).filter(IndustryNews.id == news_id).first()
    if news is None:
        raise NewsLibraryError(404, "资讯不存在")
    return news


def _require_item(db: Session, news_id: uuid.UUID) -> NewsLibraryItem:
    item = (
        db.query(NewsLibraryItem)
        .filter(NewsLibraryItem.news_id == news_id, NewsLibraryItem.status == STATUS_ACTIVE)
        .first()
    )
    if item is None:
        raise NewsLibraryError(404, "这条资讯不在共享库里")
    return item


# ---------------------------------------------------------------------------
# 收录池
# ---------------------------------------------------------------------------

def list_items(
    db: Session,
    status: str = STATUS_ACTIVE,
    keyword: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """共享库列表：按状态过滤 + 关键词（标题/摘要/正文）+ 收录时间倒序"""
    if status not in VALID_STATUS:
        raise NewsLibraryError(400, "无效的状态")

    query = (
        db.query(NewsLibraryItem, IndustryNews, User.username)
        .join(IndustryNews, IndustryNews.id == NewsLibraryItem.news_id)
        .outerjoin(User, User.id == NewsLibraryItem.added_by)
        .filter(NewsLibraryItem.status == status)
    )
    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                IndustryNews.title.ilike(like),
                IndustryNews.summary.ilike(like),
                IndustryNews.content.ilike(like),
            )
        )

    total = query.count()
    rows = (
        query.order_by(NewsLibraryItem.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    counts = _annotation_counts(db, [news.id for _, news, _ in rows])
    data: List[Dict[str, Any]] = []
    for item, news, username in rows:
        payload = _news_fields(news)
        payload.update({
            "status": item.status,
            "added_by_name": username,
            "added_at": _iso(item.created_at),
            "annotation_count": counts.get(str(news.id), 0),
        })
        data.append(payload)
    return data, total


def add_item(db: Session, news_id: uuid.UUID, user_id) -> Tuple[Dict[str, Any], bool]:
    """收录一条资讯。幂等：已收录直接返回；已下架的收录即恢复。"""
    news = _require_news(db, news_id)
    existing = db.query(NewsLibraryItem).filter(NewsLibraryItem.news_id == news_id).first()

    if existing is not None:
        if existing.status == STATUS_ARCHIVED:
            # 重新收录 = 恢复上架，保留最初收录人与时间
            existing.status = STATUS_ACTIVE
            existing.archived_at = None
            existing.archived_by = None
            db.commit()
            db.refresh(existing)
            logger.info(f"[news_library] 恢复上架 news={news_id} by={user_id}")
            return _detail_payload(db, existing, news, user_id), False
        return _detail_payload(db, existing, news, user_id), False

    item = NewsLibraryItem(news_id=news_id, added_by=user_id, status=STATUS_ACTIVE)
    db.add(item)
    db.commit()
    db.refresh(item)
    logger.info(f"[news_library] 收录 news={news_id} by={user_id}")
    return _detail_payload(db, item, news, user_id), True


def archive_item(db: Session, news_id: uuid.UUID, user_id) -> str:
    """下架（软删）。返回 'archived' | 'absent'（幂等）。"""
    item = (
        db.query(NewsLibraryItem)
        .filter(NewsLibraryItem.news_id == news_id, NewsLibraryItem.status == STATUS_ACTIVE)
        .first()
    )
    if item is None:
        return "absent"
    item.status = STATUS_ARCHIVED
    item.archived_at = datetime.utcnow()
    item.archived_by = user_id
    db.commit()
    logger.info(f"[news_library] 下架 news={news_id} by={user_id}")
    return "archived"


# ---------------------------------------------------------------------------
# 批注
# ---------------------------------------------------------------------------

def _annotation_payload(row: NewsAnnotation, username: Optional[str], viewer_id) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "news_id": str(row.news_id),
        "user_id": str(row.user_id) if row.user_id else None,
        "username": username,
        "content": row.content,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "mine": bool(row.user_id) and str(row.user_id) == str(viewer_id),
    }


def list_annotations(db: Session, news_id: uuid.UUID, viewer_id) -> List[Dict[str, Any]]:
    rows = (
        db.query(NewsAnnotation, User.username)
        .outerjoin(User, User.id == NewsAnnotation.user_id)
        .filter(
            NewsAnnotation.news_id == news_id,
            NewsAnnotation.deleted_at.is_(None),
        )
        .order_by(NewsAnnotation.created_at.asc())
        .all()
    )
    return [_annotation_payload(row, username, viewer_id) for row, username in rows]


def _validate_content(content: str) -> str:
    text = (content or "").strip()
    if not text:
        raise NewsLibraryError(400, "批注内容不能为空")
    if len(text) > ANNOTATION_MAX_LEN:
        raise NewsLibraryError(400, f"批注不能超过 {ANNOTATION_MAX_LEN} 字")
    return text


def add_annotation(db: Session, news_id: uuid.UUID, user_id, content: str) -> Dict[str, Any]:
    text = _validate_content(content)
    _require_news(db, news_id)
    row = NewsAnnotation(news_id=news_id, user_id=user_id, content=text)
    db.add(row)
    db.commit()
    db.refresh(row)
    username = db.query(User.username).filter(User.id == user_id).first()
    return _annotation_payload(row, username[0] if username else None, user_id)


def update_annotation(db: Session, annotation_id: uuid.UUID, user_id, content: str) -> Dict[str, Any]:
    text = _validate_content(content)
    row = (
        db.query(NewsAnnotation)
        .filter(NewsAnnotation.id == annotation_id, NewsAnnotation.deleted_at.is_(None))
        .first()
    )
    if row is None:
        raise NewsLibraryError(404, "批注不存在")
    if str(row.user_id) != str(user_id):
        raise NewsLibraryError(403, "只能修改自己写的批注")
    row.content = text
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    username = db.query(User.username).filter(User.id == row.user_id).first()
    return _annotation_payload(row, username[0] if username else None, user_id)


def delete_annotation(db: Session, annotation_id: uuid.UUID, user_id) -> bool:
    row = (
        db.query(NewsAnnotation)
        .filter(NewsAnnotation.id == annotation_id, NewsAnnotation.deleted_at.is_(None))
        .first()
    )
    if row is None:
        raise NewsLibraryError(404, "批注不存在")
    if str(row.user_id) != str(user_id):
        raise NewsLibraryError(403, "只能删除自己写的批注")
    row.deleted_at = datetime.utcnow()
    db.commit()
    return True


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------

def _detail_payload(
    db: Session,
    item: NewsLibraryItem,
    news: IndustryNews,
    viewer_id,
) -> Dict[str, Any]:
    annotations = list_annotations(db, news.id, viewer_id)
    adder = db.query(User.username).filter(User.id == item.added_by).first() if item.added_by else None
    payload = _news_fields(news)
    payload.update({
        "status": item.status,
        "added_by_name": adder[0] if adder else None,
        "added_at": _iso(item.created_at),
        "annotation_count": len(annotations),
        "annotations": annotations,
    })
    return payload


def get_detail(db: Session, news_id: uuid.UUID, viewer_id) -> Dict[str, Any]:
    """详情：没收录也允许看（前端从 /news 点进来时可能还没收录），但要能区分状态。"""
    news = _require_news(db, news_id)
    item = db.query(NewsLibraryItem).filter(NewsLibraryItem.news_id == news_id).first()
    if item is None:
        payload = _news_fields(news)
        payload.update({
            "status": None,
            "added_by_name": None,
            "added_at": None,
            "annotation_count": 0,
            "annotations": [],
        })
        return payload
    return _detail_payload(db, item, news, viewer_id)
