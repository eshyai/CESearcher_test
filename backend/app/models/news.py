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
    # ===== LMA 弱负信号：资讯「屏蔽」的降权（docker/init-db/09-news-hidden-weak-signal.sql）=====
    # 刻意与 weight / 正负计数分家：屏蔽只做**弱降权**（≤0，仅作用于本用户的个性化排序），
    # 不写 news_feedback、不改 positive_count / negative_count，取消屏蔽即可精确回滚。
    weak_penalty = Column(Float, nullable=False, default=0.0,
                          comment="屏蔽累积的弱降权（≤0，只影响个性化排序，不影响 weight/计数）")
    weak_hits = Column(Integer, nullable=False, default=0, comment="被屏蔽资讯命中的次数（弱负信号）")
    # ===== L2a：由「归纳建议」落地的主题（docker/init-db/07-lma-induction.sql）=====
    suggestion_id = Column(UUID(as_uuid=True), nullable=True, index=True,
                           comment="产生该主题的偏好建议 id（归纳建议 add 落地时写入，用于解释与回滚）")
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


class FocusTopicRevision(Base):
    """L2a 归纳快照：每次归纳前拍一张关注主题的全量快照，用于一键回滚。

    模型永远不直接改数据，只产出「建议」；用户接受建议后才会写 ``focus_topics``。
    回滚时按本表的 ``snapshot`` 把该用户的主题集合恢复成归纳当刻的状态。
    """
    __tablename__ = "focus_topic_revisions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger = Column(String(30), nullable=False, default="manual",
                     comment="触发方式：manual=手动 / batch=攒批(反馈数达标) / schedule=超过间隔天数")
    sample_count = Column(Integer, nullable=False, default=0, comment="本次归纳使用的反馈样本数")
    topic_count = Column(Integer, nullable=False, default=0, comment="快照时的主题数")
    current = Column(Boolean, nullable=False, default=True, index=True,
                     comment="是否为该用户最新一次归纳（回滚时以此判断）")
    status = Column(String(20), nullable=False, default="applied", index=True,
                    comment="applied=生效中 / rolled_back=已回滚")
    snapshot = Column(Text, comment="归纳前的主题全量快照 JSON 数组（回滚依据）")
    diff = Column(Text, comment="本次归纳产出的变更摘要 JSON 数组（建议级）")
    summary = Column(Text, comment="模型给出的一句话画像总结（insights 拼接）")
    error = Column(Text, comment="失败原因（LLM 不可用 / 解析失败时为非空）")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PreferenceSuggestion(Base):
    """L2a 待确认建议箱：模型产出的「主题增/删/改/合并」建议，等人逐条确认。

    任何建议都**不会自动落到 focus_topics**；``accept`` 时才按 ``kind`` 执行。
    """
    __tablename__ = "preference_suggestions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("focus_topic_revisions.id", ondelete="SET NULL"),
                         nullable=True, index=True, comment="来源归纳快照")
    kind = Column(String(20), nullable=False, index=True,
                  comment="add=新增主题 / adjust=调权重 / merge=合并 / drop=停用 / insight=结论（不落库）")
    name = Column(String(200), nullable=False, comment="建议主体名（合并建议里是合并后的目标名）")
    target_id = Column(UUID(as_uuid=True), ForeignKey("focus_topics.id", ondelete="SET NULL"),
                       nullable=True, comment="指向已有主题（adjust/drop 用）")
    payload = Column(Text, comment="建议完整载荷 JSON（targets/into/delta/aliases 等）")
    reason = Column(Text, comment="模型给出的理由（可解释性）")
    confidence = Column(Float, nullable=False, default=0.0, comment="置信度，仅用于排序，不作为自动生效开关")
    evidence = Column(Text, comment="支撑证据 JSON（反馈 id / 命中统计）")
    status = Column(String(20), nullable=False, default="pending", index=True,
                    comment="pending=待确认 / accepted=已接受 / rejected=已拒绝")
    resolved_at = Column(DateTime, comment="确认时间")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "revision_id", "kind", "name", name="uq_pref_suggestion_user_rev_kind_name"),
    )


class NewsHidden(Base):
    """用户对资讯的**个人屏蔽**：记录「这个用户不想再看到这条资讯」。

    设计边界（重要，别和 NewsFeedback 混）：
    - ``industry_news`` 是**按行业共享的语料**（无 user_id），任何「删除」都是全局动作。
      因此这里只做**个人过滤**：命中本表的资讯只在**该用户的检出链路**里被排除，
      共享库、其他用户的列表、向量索引**都不受影响**，且随时可逆。
    - 与 ``NewsFeedback`` 互不影响：反馈是**信号**（喂给 L1 / L1.5 / L2a 学习），
      屏蔽是**过滤**（只在查询层排除）。屏蔽**不写反馈**，避免污染偏好学习样本。
    - FK 用 CASCADE 是安全的：只有 ``industry_news`` 行真的被删除时，
      屏蔽记录才跟着走；本功能本身从不删 ``industry_news``。

    弱负信号（LMA）：
    - 屏蔽除了「过滤」，还对**本用户**命中的关注主题施加一点**弱降权**
      （``focus_topics.weak_penalty``，写在这里而不是 news_feedback）。
      它是「弱」的——只影响个性化排序，不改 ``weight``、不动正/负计数、不进 L2a 的反馈样本。
    - ``weak_signals`` 记录本次屏蔽**对哪些主题、各施加了多少**降权，
      取消屏蔽时按它**精确反向**回滚（而不是按当前命中的主题重算——主题集合可能已经变了）。
    """
    __tablename__ = "news_hidden"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    news_id = Column(UUID(as_uuid=True), ForeignKey("industry_news.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    reason = Column(String(200), comment="可选：屏蔽原因（留痕，不参与计算）")
    weak_signals = Column(Text, comment="本次屏蔽施加的弱降权 JSON（取消屏蔽时据此精确回滚）")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "news_id", name="uq_news_hidden_user_news"),)

    def to_dict(self):
        return {
            "id": str(self.id),
            "news_id": str(self.news_id),
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
