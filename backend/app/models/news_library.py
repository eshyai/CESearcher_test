"""共享资讯库模型：收录池 + 批注

设计边界（和 news_hidden / news_feedback 区分开）：
- ``industry_news`` 是**按行业共享的语料**（无 user_id），本模块**从不修改原文**
  （标题/正文/标签都不改），所有协作只落在 ``news_annotations`` 上。
  因此不需要字段级版本表——改坏共享语料的风险被结构性排除。
- ``news_library_items`` = 「收录池」：只有被收录的资讯才出现在共享库板块。
  ``status='archived'`` 表示下架（软删），行保留以便恢复与追溯。
- ``news_annotations``：任何登录用户都可加；**只有作者能改/删**；删除是软删
  （``deleted_at``），保留审计线索。
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID

from core.database import Base


class NewsLibraryItem(Base):
    """共享资讯库的收录条目"""

    __tablename__ = "news_library_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    news_id = Column(
        UUID(as_uuid=True),
        ForeignKey("industry_news.id", ondelete="CASCADE"),
        nullable=False,
        comment="被收录的资讯",
    )
    added_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="收录人（用户注销后留空，条目仍在）",
    )
    status = Column(
        String(20), nullable=False, default="active", index=True,
        comment="active=在库里；archived=已下架（软删）",
    )
    archived_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        comment="下架操作人",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    archived_at = Column(DateTime)

    __table_args__ = (
        # 一条资讯只能收录一次；重复收录靠它做幂等
        UniqueConstraint("news_id", name="uq_news_library_news"),
        # status / created_at 的索引由列上的 index=True 生成，这里只补列表排序用的那条
        Index("ix_news_library_items_created_at", "created_at"),
    )


class NewsAnnotation(Base):
    """共享库批注：任何登录用户可加，只有作者能改/删"""

    __tablename__ = "news_annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    news_id = Column(
        UUID(as_uuid=True),
        ForeignKey("industry_news.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    content = Column(String(2000), nullable=False, comment="批注正文，上限 2000 字（与「备注」一致）")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, comment="软删时间；非空即视为已删除")
