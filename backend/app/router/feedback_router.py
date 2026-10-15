"""关注主题与资讯反馈接口。"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from core.database import get_db
from models.news import FocusTopic, IndustryNews, NewsFeedback
from models.user import User
from router.auth_router import get_current_user_required
from service.feedback_learning_service import FeedbackLearningService
from service.lma_induction_service import LmaInductionService

router = APIRouter(prefix="/intelligence", tags=["资讯智能学习"])


class TopicCreate(BaseModel):
    topic_type: Literal["industry", "customer", "process", "technology", "product"]
    name: str = Field(min_length=1, max_length=200)
    query_text: str = Field(min_length=1, max_length=2000)
    weight: float = Field(default=1.0, ge=-2, le=2)


class TopicUpdate(BaseModel):
    query_text: str | None = Field(default=None, min_length=1, max_length=2000)
    weight: float | None = Field(default=None, ge=-2, le=2)
    is_active: bool | None = None
    # L1.5：把自动派生的候选主题「确认」为正式关注（auto -> manual）
    source: Literal["manual", "auto"] | None = None


class FeedbackRequest(BaseModel):
    interested: bool | None = None
    relevant: bool | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_signal(self):
        if self.interested is None and self.relevant is None and not self.note:
            raise ValueError("至少提供 interested、relevant 或 note 中的一项")
        return self


def topic_dict(topic: FocusTopic) -> dict:
    return {"id": str(topic.id), "topic_type": topic.topic_type, "name": topic.name,
            "query_text": topic.query_text, "weight": topic.weight,
            "positive_count": topic.positive_count, "negative_count": topic.negative_count,
            "is_active": topic.is_active, "created_at": topic.created_at,
            "updated_at": topic.updated_at,
            # L1.5
            "source": topic.source or "manual",
            "origin_news_id": str(topic.origin_news_id) if topic.origin_news_id else None,
            "auto_hits": topic.auto_hits or 0,
            # LMA 弱负信号：屏蔽累积的降权与命中次数（不改 weight / 正负计数）
            "weak_penalty": float(topic.weak_penalty or 0.0),
            "weak_hits": int(topic.weak_hits or 0)}


@router.get("/topics")
async def list_topics(include_inactive: bool = Query(False),
                      source: Literal["manual", "auto"] | None = Query(None, description="按来源筛选"),
                      current_user: User = Depends(get_current_user_required),
                      db: Session = Depends(get_db)):
    query = db.query(FocusTopic).filter(FocusTopic.user_id == current_user.id)
    if not include_inactive:
        query = query.filter(FocusTopic.is_active.is_(True))
    if source:
        query = query.filter(FocusTopic.source == source)
    return [topic_dict(topic) for topic in query.order_by(FocusTopic.weight.desc()).all()]


@router.post("/topics", status_code=status.HTTP_201_CREATED)
async def create_topic(payload: TopicCreate,
                       current_user: User = Depends(get_current_user_required),
                       db: Session = Depends(get_db)):
    existing = db.query(FocusTopic).filter(
        FocusTopic.user_id == current_user.id, FocusTopic.topic_type == payload.topic_type,
        FocusTopic.name == payload.name).first()
    if existing:
        if (existing.source or "manual") != "auto":
            raise HTTPException(status_code=409, detail="同类型关注主题已存在")
        # 该主题是系统自动派生的：视为用户「确认关注」，升级为手动主题
        existing.source = "manual"
        existing.query_text = payload.query_text
        existing.weight = payload.weight
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return topic_dict(existing)
    topic = FocusTopic(user_id=current_user.id, **payload.model_dump())
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic_dict(topic)


@router.patch("/topics/{topic_id}")
async def update_topic(topic_id: UUID, payload: TopicUpdate,
                       current_user: User = Depends(get_current_user_required),
                       db: Session = Depends(get_db)):
    topic = db.query(FocusTopic).filter(
        FocusTopic.id == topic_id, FocusTopic.user_id == current_user.id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="关注主题不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(topic, key, value)
    db.commit()
    db.refresh(topic)
    return topic_dict(topic)


@router.delete("/topics/{topic_id}")
async def delete_topic(topic_id: UUID,
                       current_user: User = Depends(get_current_user_required),
                       db: Session = Depends(get_db)):
    topic = db.query(FocusTopic).filter(
        FocusTopic.id == topic_id, FocusTopic.user_id == current_user.id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="关注主题不存在")
    db.delete(topic)
    db.commit()
    return {"success": True, "deleted_id": str(topic_id)}


@router.post("/news/{news_id}/feedback")
async def save_news_feedback(news_id: UUID, payload: FeedbackRequest,
                             current_user: User = Depends(get_current_user_required),
                             db: Session = Depends(get_db)):
    news = db.query(IndustryNews).filter(IndustryNews.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="资讯不存在")
    existing = db.query(NewsFeedback).filter(
        NewsFeedback.news_id == news_id, NewsFeedback.user_id == current_user.id).first()
    fields = payload.model_fields_set
    feedback, changed, created, retired = FeedbackLearningService(db).save_feedback(
        news=news, user_id=current_user.id,
        interested=payload.interested if "interested" in fields else (existing.interested if existing else None),
        relevant=payload.relevant if "relevant" in fields else (existing.relevant if existing else None),
        note=payload.note if "note" in fields else (existing.note if existing else None))
    return {"success": True, "feedback": {"news_id": str(feedback.news_id),
            "interested": feedback.interested, "relevant": feedback.relevant,
            "note": feedback.note}, "updated_topics": [topic_dict(t) for t in changed],
            "created_topics": [topic_dict(t) for t in created],
            "retired_topics": [topic_dict(t) for t in retired]}


# ===========================================================================
# L2a 归纳引擎（LLM 慢回路）：建议箱 + 归纳历史 + 回滚
# 注意：归纳会同步调用大模型（数秒到数十秒），端点必须是同步 def，
#      写成 async 会阻塞事件循环（与 /news/search 同理）。
# ===========================================================================

class InductionRequest(BaseModel):
    # 手动触发时是否忽略「攒批阈值」直接归纳
    force: bool = True


@router.get("/induction/status")
async def induction_status(current_user: User = Depends(get_current_user_required),
                           db: Session = Depends(get_db)):
    """是否该归纳了：待归纳反馈数、距上次天数、待确认建议数。"""
    return LmaInductionService(db).induction_status(current_user.id)


@router.post("/induction")
def run_induction(payload: InductionRequest | None = None,
                  current_user: User = Depends(get_current_user_required),
                  db: Session = Depends(get_db)):
    """跑一次归纳：产出建议进「待确认」箱，并留一张可回滚快照。

    模型**不会**直接修改关注主题；用户逐条接受后才落库。
    """
    force = payload.force if payload else True
    result = LmaInductionService(db).induct(
        current_user.id, trigger="manual", force=force)
    # 业务性失败（没样本 / 模型不可用）用 200 + ok=false 返回，便于前端给提示
    return result


@router.get("/suggestions")
async def list_suggestions(status: Literal["pending", "accepted", "rejected", "all"] = Query("pending"),
                           kind: Literal["add", "adjust", "merge", "drop", "insight"] | None = Query(None),
                           current_user: User = Depends(get_current_user_required),
                           db: Session = Depends(get_db)):
    return LmaInductionService(db).list_suggestions(
        current_user.id, status=status, kind=kind)


@router.post("/suggestions/{suggestion_id}/accept")
async def accept_suggestion(suggestion_id: UUID,
                            current_user: User = Depends(get_current_user_required),
                            db: Session = Depends(get_db)):
    try:
        return {"success": True, **LmaInductionService(db).accept_suggestion(
            suggestion_id, current_user.id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(suggestion_id: UUID,
                            current_user: User = Depends(get_current_user_required),
                            db: Session = Depends(get_db)):
    try:
        return {"success": True, **LmaInductionService(db).reject_suggestion(
            suggestion_id, current_user.id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/revisions")
async def list_revisions(limit: int = Query(20, ge=1, le=100),
                         current_user: User = Depends(get_current_user_required),
                         db: Session = Depends(get_db)):
    return LmaInductionService(db).list_revisions(current_user.id, limit=limit)


@router.post("/revisions/{revision_id}/rollback")
async def rollback_revision(revision_id: UUID,
                            current_user: User = Depends(get_current_user_required),
                            db: Session = Depends(get_db)):
    """回滚到某次归纳之前（精确撤销该次归纳的影响，不误伤之后手动建的主题）。"""
    try:
        return {"success": True, **LmaInductionService(db).rollback_revision(
            revision_id, current_user.id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
