"""共享资讯库路由：收录池 + 批注

鉴权：**全部 requirement 认证**（``get_current_user_required``）——批注要记作者、收录要记收录人，
不能匿名。判据：匿名请求应当是 **401**（不是 422）。
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from core.database import get_db
from models.user import User
from router.auth_router import get_current_user_required
from service import news_library_service as lib
from service.news_hidden_service import coerce_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["共享资讯库"])


class AddToLibraryRequest(BaseModel):
    news_id: str


class AnnotationBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=lib.ANNOTATION_MAX_LEN)


def _uuid_or_400(raw: str, label: str = "资讯") -> uuid.UUID:
    value = coerce_uuid(raw)
    if value is None:
        raise HTTPException(status_code=400, detail=f"无效的{label} ID")
    return value


def _to_http(exc: lib.NewsLibraryError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


# ---------------------------------------------------------------------------
# 收录池
# ---------------------------------------------------------------------------

@router.get("/library")
def list_library(
    status: str = Query("active", description="active=已收录 / archived=已下架"),
    keyword: Optional[str] = Query(None, description="匹配标题/摘要/正文"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    try:
        data, total = lib.list_items(
            db, status=status, keyword=keyword, limit=limit, offset=offset
        )
    except lib.NewsLibraryError as exc:
        raise _to_http(exc)
    return {"success": True, "data": data, "total": total}


@router.post("/library")
def add_to_library(
    payload: AddToLibraryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    news_id = _uuid_or_400(payload.news_id)
    try:
        data, created = lib.add_item(db, news_id, current_user.id)
    except lib.NewsLibraryError as exc:
        raise _to_http(exc)
    return {"success": True, "created": created, "data": data}


@router.delete("/library/{news_id}")
def archive_library_item(
    news_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    """下架（软删）。幂等：不在库里返回 absent。"""
    parsed = _uuid_or_400(news_id)
    status = lib.archive_item(db, parsed, current_user.id)
    return {"success": True, "status": status}


# ---------------------------------------------------------------------------
# 批注
# ---------------------------------------------------------------------------

@router.post("/library/{news_id}/annotations")
def create_annotation(
    news_id: str,
    payload: AnnotationBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    parsed = _uuid_or_400(news_id)
    try:
        data = lib.add_annotation(db, parsed, current_user.id, payload.content)
    except lib.NewsLibraryError as exc:
        raise _to_http(exc)
    return {"success": True, "data": data}


@router.put("/annotations/{annotation_id}")
def update_annotation(
    annotation_id: str,
    payload: AnnotationBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    parsed = _uuid_or_400(annotation_id, "批注")
    try:
        data = lib.update_annotation(db, parsed, current_user.id, payload.content)
    except lib.NewsLibraryError as exc:
        raise _to_http(exc)
    return {"success": True, "data": data}


@router.delete("/annotations/{annotation_id}")
def delete_annotation(
    annotation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    parsed = _uuid_or_400(annotation_id, "批注")
    try:
        lib.delete_annotation(db, parsed, current_user.id)
    except lib.NewsLibraryError as exc:
        raise _to_http(exc)
    return {"success": True}


# ---------------------------------------------------------------------------
# 详情（放在最后，避免 /library/{news_id} 抢在 /library 之前匹配）
# ---------------------------------------------------------------------------

@router.get("/library/{news_id}")
def library_detail(
    news_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    parsed = _uuid_or_400(news_id)
    try:
        data = lib.get_detail(db, parsed, current_user.id)
    except lib.NewsLibraryError as exc:
        raise _to_http(exc)
    return {"success": True, "data": data}
