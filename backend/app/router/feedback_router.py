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
            "updated_at": topic.updated_at}


@router.get("/topics")
async def list_topics(include_inactive: bool = Query(False),
                      current_user: User = Depends(get_current_user_required),
                      db: Session = Depends(get_db)):
    query = db.query(FocusTopic).filter(FocusTopic.user_id == current_user.id)
    if not include_inactive:
        query = query.filter(FocusTopic.is_active.is_(True))
    return [topic_dict(topic) for topic in query.order_by(FocusTopic.weight.desc()).all()]


@router.post("/topics", status_code=status.HTTP_201_CREATED)
async def create_topic(payload: TopicCreate,
                       current_user: User = Depends(get_current_user_required),
                       db: Session = Depends(get_db)):
    existing = db.query(FocusTopic).filter(
        FocusTopic.user_id == current_user.id, FocusTopic.topic_type == payload.topic_type,
        FocusTopic.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="同类型关注主题已存在")
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
    feedback, changed = FeedbackLearningService(db).save_feedback(
        news=news, user_id=current_user.id,
        interested=payload.interested if "interested" in fields else (existing.interested if existing else None),
        relevant=payload.relevant if "relevant" in fields else (existing.relevant if existing else None),
        note=payload.note if "note" in fields else (existing.note if existing else None))
    return {"success": True, "feedback": {"news_id": str(feedback.news_id),
            "interested": feedback.interested, "relevant": feedback.relevant,
            "note": feedback.note}, "updated_topics": [topic_dict(t) for t in changed]}
