"""
行业资讯和招投标信息模型
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Boolean, Float, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from core.database import Base


class IndustryNews(Base):
    """行业资讯表"""
    __tablename__ = "industry_news"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    industry_id = Column(String(50), index=True, comment="行业ID")
    title = Column(String(500), nullable=False, comment="资讯标题")
    content = Column(Text, comment="资讯内容/摘要")
    source = Column(String(200), comment="来源")
    source_url = Column(Text, comment="来源链接")
    category = Column(String(50), default="新闻", index=True, comment="分类：政策/纪要/研报/新闻")
    department = Column(String(200), comment="发布部门/机构")
    publish_time = Column(DateTime, index=True, comment="发布时间")
    collected_at = Column(DateTime, default=datetime.utcnow, comment="采集时间")
    keywords = Column(String(500), comment="关键词")
    is_read = Column(Boolean, default=False, comment="是否已读")
    # ===== 去重指纹（docker/init-db/03-news-dedup.sql）=====
    content_fingerprint = Column(String(16), index=True, comment="SimHash 内容指纹(16位十六进制)")
    is_duplicate = Column(Boolean, default=False, comment="是否近重复(仍保留入库但标记)")
    duplicate_of_id = Column(UUID(as_uuid=True), comment="重复于哪条资讯的ID")
    # ===== LLM 结构化抽取（docker/init-db/04-news-extraction.sql）=====
    extracted_topics = Column(Text, comment="主题标签 JSON 数组 [{type,name}]")
    event_type = Column(String(50), index=True, comment="事件类型：投产扩产/停产检修/价格变动/政策发布/安全事故/环保处罚/并购融资/技术突破/招投标/其他")
    impact_direction = Column(String(20), comment="影响方向：positive/negative/neutral")
    key_numbers = Column(Text, comment="关键数字 JSON 数组 [{name,value,unit}]")
    entities = Column(Text, comment="涉及公司/产品/机构 JSON 数组")
    summary = Column(String(500), comment="一句话摘要")
    extraction_status = Column(String(20), default="pending", index=True, comment="抽取状态：pending/completed/failed")
    extracted_at = Column(DateTime, comment="抽取完成时间")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        import json as _json

        def _safe_json(text, default):
            if not text:
                return default
            try:
                return _json.loads(text)
            except (ValueError, TypeError):
                return default

        return {
            "id": str(self.id),
            "industry_id": self.industry_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "source_url": self.source_url,
            "category": self.category,
            "department": self.department,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
            "keywords": self.keywords,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            # 去重指纹
            "is_duplicate": self.is_duplicate,
            # LLM 抽取结果
            "extracted_topics": _safe_json(self.extracted_topics, []),
            "event_type": self.event_type,
            "impact_direction": self.impact_direction,
            "key_numbers": _safe_json(self.key_numbers, []),
            "entities": _safe_json(self.entities, []),
            "summary": self.summary,
            "extraction_status": self.extraction_status,
        }


class BiddingInfo(Base):
    """招投标信息表"""
    __tablename__ = "bidding_info"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    industry_id = Column(String(50), index=True, comment="行业ID")
    bid_id = Column(String(100), unique=True, index=True, comment="招投标项目ID")
    title = Column(String(500), nullable=False, comment="项目标题")
    notice_type = Column(String(50), index=True, comment="公告类型：招标/中标/采购等")
    province = Column(String(50), index=True, comment="省份")
    city = Column(String(50), comment="城市")
    content = Column(Text, comment="详细内容")
    publish_time = Column(DateTime, index=True, comment="发布时间")
    source = Column(String(200), default="81api", comment="数据来源")
    collected_at = Column(DateTime, default=datetime.utcnow, comment="采集时间")
    is_read = Column(Boolean, default=False, comment="是否已读")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": str(self.id),
            "industry_id": self.industry_id,
            "bid_id": self.bid_id,
            "title": self.title,
            "notice_type": self.notice_type,
            "province": self.province,
            "city": self.city,
            "content": self.content,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "source": self.source,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NewsCollectionTask(Base):
    """资讯采集任务记录表"""
    __tablename__ = "news_collection_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type = Column(String(50), nullable=False, comment="任务类型：news/bidding")
    status = Column(String(20), default="pending", comment="状态：pending/running/completed/failed")
    total_collected = Column(Integer, default=0, comment="采集数量")
    error_message = Column(Text, comment="错误信息")
    started_at = Column(DateTime, comment="开始时间")
    completed_at = Column(DateTime, comment="完成时间")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": str(self.id),
            "task_type": self.task_type,
            "status": self.status,
            "total_collected": self.total_collected,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class FocusTopic(Base):
    """用户关注主题：``source='manual'`` 为用户手动创建，``'auto'`` 为反馈自动派生。"""
    __tablename__ = "focus_topics"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_type = Column(String(30), nullable=False, default="industry", index=True)
    name = Column(String(200), nullable=False)
    query_text = Column(Text, nullable=False)
    weight = Column(Float, nullable=False, default=1.0)
    positive_count = Column(Integer, nullable=False, default=0)
    negative_count = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    # ===== L1.5：反馈自动派生的候选主题（docker/init-db/06-focus-topic-source.sql）=====
    source = Column(String(20), nullable=False, default="manual", index=True,
                    comment="来源：manual=用户手动创建 / auto=反馈自动派生")
    origin_news_id = Column(UUID(as_uuid=True),
                            ForeignKey("industry_news.id", ondelete="SET NULL"),
                            nullable=True, comment="自动主题的触发资讯（可解释性）")
    auto_hits = Column(Integer, nullable=False, default=0, comment="被反馈命中的次数（自动主题）")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "topic_type", "name", name="uq_focus_topic_user_type_name"),)


class NewsFeedback(Base):
    """资讯反馈事件，保留原始判断以支持审计和重算。"""
    __tablename__ = "news_feedback"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    news_id = Column(UUID(as_uuid=True), ForeignKey("industry_news.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    interested = Column(Boolean, nullable=True)
    relevant = Column(Boolean, nullable=True)
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("news_id", "user_id", name="uq_news_feedback_news_user"),)
