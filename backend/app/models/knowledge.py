"""知识库相关模型"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, BigInteger, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from core.database import Base


class KnowledgeBase(Base):
    """知识库模型"""
    __tablename__ = "knowledge_bases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    name = Column(String(255), nullable=False)
    description = Column(Text)
    document_count = Column(Integer, default=0)
    is_shared = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    deleted_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    user = relationship("User", back_populates="knowledge_bases")
    documents = relationship("Document", back_populates="knowledge_base", cascade="all, delete-orphan")


class Document(Base):
    """文档模型"""
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50))
    file_size = Column(BigInteger)
    file_path = Column(String(500))
    status = Column(String(50), default="pending")  # pending, processing, completed, failed
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text)
    revision = Column(Integer, nullable=False, default=1)
    deleted_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    user = relationship("User", back_populates="documents")


class KnowledgeBaseMember(Base):
    """共享知识库成员。角色：viewer/editor/manager。"""
    __tablename__ = "knowledge_base_members"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False, default="viewer")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("knowledge_base_id", "user_id", name="uq_kb_member"),)


class KnowledgeAuditLog(Base):
    """知识库管理审计日志。"""
    __tablename__ = "knowledge_audit_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(50), nullable=False)
    target_type = Column(String(30), nullable=False, default="knowledge_base")
    target_id = Column(String(100))
    detail = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
