"""资讯反馈学习：透明、可审计的轻量排序，不直接修改模型参数。"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models.news import FocusTopic, IndustryNews, NewsFeedback
from service.interest_scoring import feedback_signal, text_matches, updated_weight


def topic_matches(topic: FocusTopic, news: IndustryNews) -> bool:
    haystack = " ".join(filter(None, [news.title, news.content, news.keywords])).lower()
    return text_matches(f"{topic.name} {topic.query_text}", haystack)


class FeedbackLearningService:
    def __init__(self, db: Session):
        self.db = db

    def save_feedback(self, *, news, user_id, interested, relevant, note):
        feedback = self.db.query(NewsFeedback).filter(
            NewsFeedback.news_id == news.id, NewsFeedback.user_id == user_id).first()
        previous_signal = feedback_signal(feedback.interested, feedback.relevant) if feedback else 0.0
        new_signal = feedback_signal(interested, relevant)
        if feedback:
            feedback.interested, feedback.relevant, feedback.note = interested, relevant, note
        else:
            feedback = NewsFeedback(news_id=news.id, user_id=user_id, interested=interested,
                                    relevant=relevant, note=note)
            self.db.add(feedback)

        changed = []
        delta = new_signal - previous_signal
        if delta:
            topics = self.db.query(FocusTopic).filter(
                FocusTopic.user_id == user_id, FocusTopic.is_active.is_(True)).all()
            for topic in topics:
                if topic_matches(topic, news):
                    topic.weight = updated_weight(float(topic.weight or 0), delta)
                    if delta > 0:
                        topic.positive_count = (topic.positive_count or 0) + 1
                    else:
                        topic.negative_count = (topic.negative_count or 0) + 1
                    changed.append(topic)
        self.db.commit()
        self.db.refresh(feedback)
        return feedback, changed

    def enrich_and_rank(self, items: list[IndustryNews], user_id) -> list[dict]:
        topics = self.db.query(FocusTopic).filter(
            FocusTopic.user_id == user_id, FocusTopic.is_active.is_(True)).all()
        ids = [item.id for item in items]
        feedback_by_news = {item.news_id: item for item in self.db.query(NewsFeedback).filter(
            NewsFeedback.user_id == user_id, NewsFeedback.news_id.in_(ids)).all()} if ids else {}
        ranked = []
        for news in items:
            matched = [topic for topic in topics if topic_matches(topic, news)]
            topic_score = sum(float(topic.weight or 0) for topic in matched)
            published = news.publish_time or news.collected_at
            if published and published.tzinfo:
                now = datetime.now(timezone.utc)
            else:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
            age_days = max(0.0, (now - (published or now)).total_seconds() / 86400)
            freshness = max(0.0, 1.0 - age_days / 30.0)
            data = news.to_dict()
            data["personalized_score"] = round(0.7 * topic_score + 0.3 * freshness, 4)
            data["matched_topics"] = [topic.name for topic in matched]
            existing = feedback_by_news.get(news.id)
            data["feedback"] = ({"interested": existing.interested, "relevant": existing.relevant,
                                 "note": existing.note} if existing else None)
            ranked.append(data)
        return sorted(ranked, key=lambda item: (item["personalized_score"], item.get("publish_time") or ""), reverse=True)
