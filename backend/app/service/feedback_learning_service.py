"""资讯反馈学习：透明、可审计的轻量排序，不直接修改模型参数。

### L1.5 补漏（2026-10-10）

旧实现只有「反馈 → 修改**已有**主题权重」一条路，用户不先去「我的关注」手建主题，
点再多反馈也毫无效果（冷启动死锁）。现在补两条：

1. **正向反馈 + 无任何主题命中 → 自动派生候选主题**（``source='auto'``，
   初始权重 ``AUTO_TOPIC_INIT_WEIGHT``，带 ``origin_news_id`` 与 ``reason``）；
2. **自动主题被负向反馈打到退役线以下 → 自动停用**，让冷启动产生的噪声能自净
   （手动创建的主题永不自动停用）。

匹配算法同步升级（分词 + 同义归并 + 覆盖率阈值），见 ``service/interest_scoring.py``。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models.news import FocusTopic, IndustryNews, NewsFeedback
from service.interest_scoring import (
    TextIndex,
    compile_topic,
    feedback_signal,
    query_matches,
    updated_weight,
)
from service.topic_suggestion import (
    DEFAULT_CANDIDATE_LIMIT,
    candidate_topics_from_news,
    parse_json_list,
)

# 自动派生的候选主题：初始权重低于手动创建的 1.0，避免噪声主题喧宾夺主
AUTO_TOPIC_INIT_WEIGHT = 0.5
# 自动主题权重跌破该线即自动停用（0.5 起步约 4 次负反馈触达）
AUTO_TOPIC_RETIRE_WEIGHT = -0.6
MAX_AUTO_TOPICS_PER_FEEDBACK = DEFAULT_CANDIDATE_LIMIT

SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"


def news_text(news: IndustryNews) -> str:
    return " ".join(filter(None, [news.title, news.content, news.keywords]))


def topic_matches(topic: FocusTopic, news: IndustryNews) -> bool:
    """单条判定（保留旧签名，内部按需建索引）。批量场景请用 ``TextIndex`` 复用。"""
    return query_matches(compile_topic(topic), TextIndex(news_text(news)))


class FeedbackLearningService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # 反馈落库 + 权重更新 + 候选主题派生
    # ------------------------------------------------------------------

    def save_feedback(self, *, news, user_id, interested, relevant, note):
        """写入反馈，更新命中主题权重；必要时自动派生候选主题。

        返回 ``(feedback, changed, created, retired)``：
        ``changed`` 权重被更新的已有主题；``created`` 本次自动创建的候选主题；
        ``retired`` 因负反馈被自动停用的候选主题。
        """
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

        changed: list[FocusTopic] = []
        created: list[FocusTopic] = []
        retired: list[FocusTopic] = []

        delta = new_signal - previous_signal
        if delta:
            topics = self.db.query(FocusTopic).filter(
                FocusTopic.user_id == user_id, FocusTopic.is_active.is_(True)).all()
            index = TextIndex(news_text(news))
            for topic in topics:
                if not query_matches(compile_topic(topic), index):
                    continue
                topic.weight = updated_weight(float(topic.weight or 0), delta)
                if delta > 0:
                    topic.positive_count = (topic.positive_count or 0) + 1
                else:
                    topic.negative_count = (topic.negative_count or 0) + 1
                if (topic.source or SOURCE_MANUAL) == SOURCE_AUTO:
                    topic.auto_hits = (topic.auto_hits or 0) + 1
                    if float(topic.weight or 0) <= AUTO_TOPIC_RETIRE_WEIGHT:
                        topic.is_active = False  # 自动主题：被反复否定则自动退役
                        retired.append(topic)
                changed.append(topic)

            # L1.5：正向反馈且没有任何主题命中 → 从这条资讯里长出候选主题
            if not changed and new_signal > 0:
                created = self._auto_create_topics(news=news, user_id=user_id)

        self.db.commit()
        self.db.refresh(feedback)
        for topic in created:
            self.db.refresh(topic)
        return feedback, changed, created, retired

    # ------------------------------------------------------------------
    # 候选主题自动创建
    # ------------------------------------------------------------------

    def _auto_create_topics(self, *, news: IndustryNews, user_id) -> list[FocusTopic]:
        candidates = candidate_topics_from_news(
            title=news.title or "",
            entities=parse_json_list(news.entities),
            extracted_topics=parse_json_list(news.extracted_topics),
            keywords=news.keywords,
            limit=MAX_AUTO_TOPICS_PER_FEEDBACK,
        )
        if not candidates:
            return []

        existing = self.db.query(FocusTopic).filter(FocusTopic.user_id == user_id).all()
        by_name = {(topic.topic_type, topic.name): topic for topic in existing}
        existing_names = {topic.name for topic in existing}

        created: list[FocusTopic] = []
        for candidate in candidates:
            name = candidate["name"]
            if name in existing_names:
                continue  # 同名主题（哪怕类型不同）不重复创建
            topic_type = candidate["topic_type"]
            if (topic_type, name) in by_name:
                continue
            topic = FocusTopic(
                user_id=user_id,
                topic_type=topic_type,
                name=name,
                query_text=candidate["query_text"],
                weight=AUTO_TOPIC_INIT_WEIGHT,
                positive_count=1,
                negative_count=0,
                is_active=True,
                source=SOURCE_AUTO,
                origin_news_id=news.id,
                auto_hits=1,
            )
            self.db.add(topic)
            by_name[(topic_type, name)] = topic
            existing_names.add(name)
            created.append(topic)
        if created:
            self.db.flush()
        return created

    # ------------------------------------------------------------------
    # 排序
    # ------------------------------------------------------------------

    def enrich_and_rank(self, items: list[IndustryNews], user_id) -> list[dict]:
        topics = self.db.query(FocusTopic).filter(
            FocusTopic.user_id == user_id, FocusTopic.is_active.is_(True)).all()
        compiled = [(topic, compile_topic(topic)) for topic in topics]
        ids = [item.id for item in items]
        feedback_by_news = {item.news_id: item for item in self.db.query(NewsFeedback).filter(
            NewsFeedback.user_id == user_id, NewsFeedback.news_id.in_(ids)).all()} if ids else {}
        now = datetime.now(timezone.utc)
        now_naive = now.replace(tzinfo=None)
        ranked = []
        for news in items:
            index = TextIndex(news_text(news))
            matched = [topic for topic, query in compiled if query_matches(query, index)]
            topic_score = sum(float(topic.weight or 0) for topic in matched)
            published = news.publish_time or news.collected_at
            age_days = max(0.0, ((now if published and published.tzinfo else now_naive)
                                 - (published or now_naive)).total_seconds() / 86400)
            freshness = max(0.0, 1.0 - age_days / 30.0)
            data = news.to_dict()
            data["personalized_score"] = round(0.7 * topic_score + 0.3 * freshness, 4)
            data["matched_topics"] = [topic.name for topic in matched]
            existing = feedback_by_news.get(news.id)
            data["feedback"] = ({"interested": existing.interested, "relevant": existing.relevant,
                                 "note": existing.note} if existing else None)
            ranked.append(data)
        return sorted(ranked, key=lambda item: (item["personalized_score"], item.get("publish_time") or ""), reverse=True)
