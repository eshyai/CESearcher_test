"""知识库管理路由"""
import os
import shutil
from typing import List, Literal
import json
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel

from core.database import get_db
from models.knowledge import KnowledgeBase, Document, KnowledgeBaseMember, KnowledgeAuditLog
from models.user import User
from router.auth_router import get_current_user_required
from schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseResponse,
    KnowledgeBaseWithDocuments,
    DocumentResponse,
    DocumentUploadResponse,
)

router = APIRouter(prefix="/knowledge-bases", tags=["知识库管理"])

# 文件上传目录
UPLOAD_DIR = "/tmp/knowledge_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 支持的文件类型
ALLOWED_EXTENSIONS = {
    # 文档类型
    '.pdf', '.docx', '.doc', '.txt', '.md', '.html', '.xlsx', '.xls', '.pptx', '.ppt',
    # 图片类型
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
    # 代码/数据类型
    '.py', '.js', '.ts', '.json', '.yaml', '.yml', '.xml', '.csv',
}


def get_file_extension(filename: str) -> str:
    """获取文件扩展名"""
    return os.path.splitext(filename)[1].lower()


ROLE_RANK = {"viewer": 1, "editor": 2, "manager": 3}


def get_kb_access(db: Session, kb_id, user: User, required_role: str = "viewer", include_deleted: bool = False):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb or (kb.deleted_at and not include_deleted):
        raise HTTPException(status_code=404, detail="知识库不存在")
    if kb.user_id == user.id:
        role = "manager"
    else:
        member = db.query(KnowledgeBaseMember).filter(
            KnowledgeBaseMember.knowledge_base_id == kb.id,
            KnowledgeBaseMember.user_id == user.id).first()
        role = member.role if member and kb.is_shared else None
    if not role or ROLE_RANK[role] < ROLE_RANK[required_role]:
        raise HTTPException(status_code=403, detail="没有该知识库的操作权限")
    return kb, role


def add_audit(db: Session, kb, actor, action: str, detail: dict | None = None):
    db.add(KnowledgeAuditLog(knowledge_base_id=kb.id, actor_user_id=actor.id,
                             action=action, detail=json.dumps(detail or {}, ensure_ascii=False)))


def kb_to_response(kb: KnowledgeBase, role: str = "manager") -> KnowledgeBaseResponse:
    """将知识库模型转换为响应"""
    return KnowledgeBaseResponse(
        id=str(kb.id),
        name=kb.name,
        description=kb.description,
        document_count=kb.document_count or 0,
        is_shared=kb.is_shared,
        revision=kb.revision,
        role=role,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


def doc_to_response(doc: Document) -> DocumentResponse:
    """将文档模型转换为响应"""
    return DocumentResponse(
        id=str(doc.id),
        knowledge_base_id=str(doc.knowledge_base_id),
        filename=doc.filename,
        file_type=doc.file_type,
        file_size=doc.file_size,
        status=doc.status,
        chunk_count=doc.chunk_count or 0,
        error_message=doc.error_message,
        revision=doc.revision,
        deleted_at=doc.deleted_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


async def process_document(document_id: str, file_path: str, kb_name: str, db_session_factory):
    """后台处理文档（使用 DocMind 解析、向量化、存储到ES）"""
    from service.docmind_service import process_document_with_docmind

    # 创建新的数据库会话
    db = db_session_factory()
    try:
        # 获取文档记录
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return

        # 更新状态为处理中
        doc.status = "processing"
        db.commit()

        try:
            # 使用知识库名称作为ES索引名
            index_name = f"kb_{kb_name}".lower().replace(" ", "_")

            # 使用 DocMind 处理文档
            result = process_document_with_docmind(
                file_path=file_path,
                file_name=doc.filename,
                index_name=index_name,
            )

            if result["success"]:
                doc.status = "completed"
                doc.chunk_count = result["document_count"]
                doc.error_message = None
            else:
                doc.status = "failed"
                doc.error_message = result["message"]

        except Exception as e:
            doc.status = "failed"
            doc.error_message = str(e)

        db.commit()

    finally:
        db.close()
        # 清理临时文件
        if os.path.exists(file_path):
            os.remove(file_path)


@router.get("", response_model=List[KnowledgeBaseResponse])
async def get_knowledge_bases(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """获取用户的知识库列表"""
    memberships = {m.knowledge_base_id: m.role for m in db.query(KnowledgeBaseMember).filter(
        KnowledgeBaseMember.user_id == current_user.id).all()}
    kbs = db.query(KnowledgeBase).filter(
        KnowledgeBase.deleted_at.is_(None),
        or_(KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.id.in_(list(memberships.keys())) if memberships else False)
    ).order_by(KnowledgeBase.updated_at.desc()).all()
    return [kb_to_response(kb, "manager" if kb.user_id == current_user.id else memberships[kb.id]) for kb in kbs]


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    kb_data: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """创建知识库"""
    # 检查是否已存在同名知识库
    existing = db.query(KnowledgeBase).filter(
        KnowledgeBase.user_id == current_user.id,
        KnowledgeBase.name == kb_data.name
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已存在同名知识库"
        )

    kb = KnowledgeBase(
        user_id=current_user.id,
        name=kb_data.name,
        description=kb_data.description,
        is_shared=kb_data.is_shared,
    )
    db.add(kb)
    db.flush()
    add_audit(db, kb, current_user, "create", {"is_shared": kb.is_shared})
    db.commit()
    db.refresh(kb)

    return kb_to_response(kb)


@router.get("/{kb_id}", response_model=KnowledgeBaseWithDocuments)
async def get_knowledge_base(
    kb_id: str,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """获取知识库详情（包含文档列表）"""
    try:
        kb_uuid = UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的知识库ID格式"
        )

    kb, role = get_kb_access(db, kb_uuid, current_user)

    documents = db.query(Document).filter(
        Document.knowledge_base_id == kb.id,
        Document.deleted_at.is_(None)
    ).order_by(Document.created_at.desc()).all()

    return KnowledgeBaseWithDocuments(
        id=str(kb.id),
        name=kb.name,
        description=kb.description,
        document_count=kb.document_count or 0,
        is_shared=kb.is_shared,
        revision=kb.revision,
        role=role,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        documents=[doc_to_response(doc) for doc in documents],
    )


@router.put("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: str,
    kb_data: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """更新知识库"""
    try:
        kb_uuid = UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的知识库ID格式"
        )

    kb, role = get_kb_access(db, kb_uuid, current_user, "editor")

    if kb_data.name is not None:
        # 检查是否与其他知识库重名
        existing = db.query(KnowledgeBase).filter(
            KnowledgeBase.user_id == current_user.id,
            KnowledgeBase.name == kb_data.name,
            KnowledgeBase.id != kb_uuid
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="已存在同名知识库"
            )
        kb.name = kb_data.name

    if kb_data.description is not None:
        kb.description = kb_data.description
    if kb_data.is_shared is not None:
        if role != "manager":
            raise HTTPException(status_code=403, detail="只有管理员可以修改共享设置")
        kb.is_shared = kb_data.is_shared

    kb.revision += 1
    add_audit(db, kb, current_user, "update", kb_data.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(kb)

    return kb_to_response(kb, role)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: str,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """删除知识库"""
    try:
        kb_uuid = UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的知识库ID格式"
        )

    kb, _ = get_kb_access(db, kb_uuid, current_user, "manager")
    kb.deleted_at = datetime.utcnow()
    kb.revision += 1
    add_audit(db, kb, current_user, "soft_delete")
    db.commit()
    return None


@router.post("/{kb_id}/documents", response_model=DocumentUploadResponse)
async def upload_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """上传文档到知识库"""
    try:
        kb_uuid = UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的知识库ID格式"
        )

    # 验证知识库存在
    kb, _ = get_kb_access(db, kb_uuid, current_user, "editor")

    # 验证文件类型
    ext = get_file_extension(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型: {ext}，支持的类型: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # 保存文件到临时目录
    file_path = os.path.join(UPLOAD_DIR, f"{kb_uuid}_{file.filename}")
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件保存失败: {str(e)}"
        )

    # 获取文件大小
    file_size = os.path.getsize(file_path)

    # 创建文档记录
    doc = Document(
        knowledge_base_id=kb_uuid,
        user_id=current_user.id,
        filename=file.filename,
        file_type=ext[1:] if ext else None,  # 去掉点
        file_size=file_size,
        file_path=file_path,
        status="pending",
    )
    db.add(doc)

    # 更新知识库文档计数
    kb.document_count = (kb.document_count or 0) + 1
    kb.revision += 1
    add_audit(db, kb, current_user, "document_upload", {"filename": file.filename})

    db.commit()
    db.refresh(doc)

    # 获取数据库会话工厂
    from core.database import SessionLocal

    # 在后台处理文档
    background_tasks.add_task(
        process_document,
        str(doc.id),
        file_path,
        kb.name,
        SessionLocal
    )

    return DocumentUploadResponse(
        id=str(doc.id),
        filename=doc.filename,
        process_status="pending",
        message="文档已上传，正在后台处理中"
    )


@router.get("/{kb_id}/documents", response_model=List[DocumentResponse])
async def get_documents(
    kb_id: str,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """获取知识库的文档列表"""
    try:
        kb_uuid = UUID(kb_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的知识库ID格式"
        )

    # 验证知识库存在
    kb, _ = get_kb_access(db, kb_uuid, current_user)

    documents = db.query(Document).filter(
        Document.knowledge_base_id == kb_uuid,
        Document.deleted_at.is_(None)
    ).order_by(Document.created_at.desc()).all()

    return [doc_to_response(doc) for doc in documents]


@router.get("/{kb_id}/documents/{doc_id}/chunks")
async def get_document_chunks(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """获取文档的所有切片"""
    from service.milvus_service import get_milvus_service

    try:
        kb_uuid = UUID(kb_id)
        doc_uuid = UUID(doc_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的ID格式"
        )

    # 验证知识库存在
    kb, _ = get_kb_access(db, kb_uuid, current_user)

    # 获取文档
    doc = db.query(Document).filter(
        Document.id == doc_uuid,
        Document.knowledge_base_id == kb_uuid,
        Document.deleted_at.is_(None)
    ).first()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在"
        )

    if doc.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文档尚未处理完成"
        )

    # 从 Milvus 获取切片
    collection_name = f"kb_{kb.name}".lower().replace(" ", "_")
    print(f"[get_document_chunks] 查询切片: collection={collection_name}, filename={doc.filename}")

    try:
        milvus = get_milvus_service()
        chunks = milvus.get_chunks_by_filename(collection_name, doc.filename)
        print(f"[get_document_chunks] 找到 {len(chunks)} 个切片")
    except Exception as e:
        print(f"[get_document_chunks] Milvus 查询失败: {e}")
        # 返回空结果而不是报错
        chunks = []

    return {
        "document_id": str(doc.id),
        "filename": doc.filename,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "index": chunk.get("chunk_index", i),
                "content": chunk.get("content", ""),
            }
            for i, chunk in enumerate(chunks)
        ]
    }


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """删除文档"""
    try:
        kb_uuid = UUID(kb_id)
        doc_uuid = UUID(doc_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的ID格式"
        )

    # 验证知识库存在
    kb, _ = get_kb_access(db, kb_uuid, current_user, "editor")

    # 获取文档
    doc = db.query(Document).filter(
        Document.id == doc_uuid,
        Document.knowledge_base_id == kb_uuid,
        Document.deleted_at.is_(None)
    ).first()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在"
        )

    # 更新知识库文档计数
    kb.document_count = max((kb.document_count or 0) - 1, 0)
    kb.revision += 1
    doc.deleted_at = datetime.utcnow()
    doc.revision += 1
    add_audit(db, kb, current_user, "document_delete", {"document_id": str(doc.id), "filename": doc.filename})
    db.commit()
    return None


@router.post("/{kb_id}/documents/{doc_id}/restore")
async def restore_document(kb_id: UUID, doc_id: UUID,
                           current_user: User = Depends(get_current_user_required),
                           db: Session = Depends(get_db)):
    kb, _ = get_kb_access(db, kb_id, current_user, "editor")
    doc = db.query(Document).filter(Document.id == doc_id,
                                    Document.knowledge_base_id == kb.id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.deleted_at:
        doc.deleted_at = None
        doc.revision += 1
        kb.document_count = (kb.document_count or 0) + 1
        kb.revision += 1
        add_audit(db, kb, current_user, "document_restore", {"document_id": str(doc.id)})
        db.commit()
    return doc_to_response(doc)


class MemberUpsert(BaseModel):
    username: str
    role: Literal["viewer", "editor", "manager"] = "viewer"


@router.get("/{kb_id}/members")
async def list_members(kb_id: UUID, current_user: User = Depends(get_current_user_required),
                       db: Session = Depends(get_db)):
    kb, _ = get_kb_access(db, kb_id, current_user, "manager")
    members = db.query(KnowledgeBaseMember, User).join(
        User, User.id == KnowledgeBaseMember.user_id).filter(
        KnowledgeBaseMember.knowledge_base_id == kb.id).all()
    return [{"user_id": str(user.id), "username": user.username, "role": member.role,
             "created_at": member.created_at} for member, user in members]


@router.put("/{kb_id}/members")
async def upsert_member(kb_id: UUID, payload: MemberUpsert,
                        current_user: User = Depends(get_current_user_required),
                        db: Session = Depends(get_db)):
    kb, _ = get_kb_access(db, kb_id, current_user, "manager")
    user = db.query(User).filter(User.username == payload.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == kb.user_id:
        raise HTTPException(status_code=400, detail="创建者固定具有管理员权限")
    member = db.query(KnowledgeBaseMember).filter(
        KnowledgeBaseMember.knowledge_base_id == kb.id,
        KnowledgeBaseMember.user_id == user.id).first()
    if member:
        member.role = payload.role
    else:
        member = KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role=payload.role)
        db.add(member)
    kb.is_shared = True
    kb.revision += 1
    add_audit(db, kb, current_user, "member_upsert", {"username": user.username, "role": payload.role})
    db.commit()
    return {"user_id": str(user.id), "username": user.username, "role": member.role}


@router.delete("/{kb_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(kb_id: UUID, user_id: UUID,
                        current_user: User = Depends(get_current_user_required),
                        db: Session = Depends(get_db)):
    kb, _ = get_kb_access(db, kb_id, current_user, "manager")
    member = db.query(KnowledgeBaseMember).filter(
        KnowledgeBaseMember.knowledge_base_id == kb.id,
        KnowledgeBaseMember.user_id == user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="成员不存在")
    db.delete(member)
    kb.revision += 1
    add_audit(db, kb, current_user, "member_remove", {"user_id": str(user_id)})
    db.commit()
    return None


@router.get("/{kb_id}/audit")
async def get_audit_log(kb_id: UUID, current_user: User = Depends(get_current_user_required),
                        db: Session = Depends(get_db)):
    kb, _ = get_kb_access(db, kb_id, current_user, "manager", include_deleted=True)
    logs = db.query(KnowledgeAuditLog).filter(
        KnowledgeAuditLog.knowledge_base_id == kb.id).order_by(
        KnowledgeAuditLog.created_at.desc()).limit(200).all()
    return [{"id": str(log.id), "actor_user_id": str(log.actor_user_id) if log.actor_user_id else None,
             "action": log.action, "target_type": log.target_type, "target_id": log.target_id,
             "detail": json.loads(log.detail or "{}"), "created_at": log.created_at} for log in logs]


@router.post("/{kb_id}/restore")
async def restore_knowledge_base(kb_id: UUID,
                                 current_user: User = Depends(get_current_user_required),
                                 db: Session = Depends(get_db)):
    kb, role = get_kb_access(db, kb_id, current_user, "manager", include_deleted=True)
    kb.deleted_at = None
    kb.revision += 1
    add_audit(db, kb, current_user, "restore")
    db.commit()
    db.refresh(kb)
    return kb_to_response(kb, role)
