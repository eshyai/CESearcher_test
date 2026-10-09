"""
行业资讯和招投标信息路由
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel

from core.database import get_db
from service.news_collection_service import get_news_collection_service
from service.scheduler_service import get_scheduler_service
from config.industry_config import get_all_industries, get_industry_config
from models.user import User
from router.auth_router import get_current_user, get_current_user_required

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["行业资讯"])


class NewsListResponse(BaseModel):
    """资讯列表响应"""
    success: bool
    data: List[dict]
    total: int
    stats: Optional[dict] = None


class CollectionResponse(BaseModel):
    """采集响应"""
    success: bool
    message: str
    news_collected: int = 0
    bidding_collected: int = 0
    duplicates_filtered: int = 0
    extraction: Optional[dict] = None
    errors: List[str] = []


@router.get("/list", response_model=NewsListResponse)
async def get_news_list(
    category: Optional[str] = Query(None, description="分类：政策/研报/新闻"),
    industry_id: Optional[str] = Query(None, description="行业ID"),
    event_type: Optional[str] = Query(None, description="事件类型：投产扩产/停产检修/价格变动/政策发布/安全事故/环保处罚/并购融资/技术突破/招投标/其他"),
    impact_direction: Optional[str] = Query(None, description="影响方向：positive/negative/neutral"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取行业资讯列表
    """
    logger.info(f"[news_router] get_news_list 请求: category={category}, industry_id={industry_id}, event_type={event_type}, impact={impact_direction}, limit={limit}, offset={offset}")
    service = get_news_collection_service(db)
    items, filtered_total = service.get_news_list(
        category=category, industry_id=industry_id,
        event_type=event_type, impact_direction=impact_direction,
        user_id=current_user.id if current_user else None,
        limit=limit, offset=offset)
    stats = service.get_news_stats(industry_id=industry_id)
    logger.info(f"[news_router] get_news_list 返回: items数量={len(items)}, filtered_total={filtered_total}")

    return NewsListResponse(
        success=True,
        data=items,
        total=filtered_total,
        stats=stats
    )


@router.get("/bidding/list", response_model=NewsListResponse)
async def get_bidding_list(
    notice_type: Optional[str] = Query(None, description="公告类型：招标/中标"),
    province: Optional[str] = Query(None, description="省份"),
    industry_id: Optional[str] = Query(None, description="行业ID"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    获取招投标信息列表
    """
    logger.info(f"[news_router] get_bidding_list 请求: notice_type={notice_type}, province={province}, industry_id={industry_id}, limit={limit}, offset={offset}")
    service = get_news_collection_service(db)
    items, filtered_total = service.get_bidding_list(
        notice_type=notice_type,
        province=province,
        industry_id=industry_id,
        limit=limit,
        offset=offset
    )
    stats = service.get_bidding_stats(industry_id=industry_id)
    logger.info(f"[news_router] get_bidding_list 返回: items数量={len(items)}, filtered_total={filtered_total}")

    return NewsListResponse(
        success=True,
        data=items,
        total=filtered_total,
        stats=stats
    )


@router.get("/stats")
async def get_all_stats(db: Session = Depends(get_db)):
    """
    获取所有统计信息
    """
    service = get_news_collection_service(db)
    news_stats = service.get_news_stats()
    bidding_stats = service.get_bidding_stats()

    return {
        "success": True,
        "news": news_stats,
        "bidding": bidding_stats
    }


@router.post("/collect", response_model=CollectionResponse)
async def trigger_collection(
    background_tasks: BackgroundTasks,
    max_news: int = Query(50, ge=1, le=200),
    max_bidding: int = Query(50, ge=1, le=200),
    industry_id: Optional[str] = Query(None, description="行业ID"),
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    """
    手动触发完整资讯管道：采集(含去重) -> LLM 结构化抽取
    """
    logger.info(f"[news_router] trigger_collection 请求: industry_id={industry_id}, max_news={max_news}, max_bidding={max_bidding}")

    # 直接执行（不使用后台任务，以便返回结果）
    try:
        from service.news_pipeline import run_full_pipeline
        logger.info(f"[news_router] 开始执行完整管道, industry_id={industry_id}")
        result = await run_full_pipeline(
            max_news=max_news,
            max_bidding=max_bidding,
            industry_id=industry_id,
            focus_user_id=current_user.id,
        )
        logger.info(f"[news_router] 管道返回: {result}")

        news_result = result.get("collection", {}).get("news", {})
        bidding_result = result.get("collection", {}).get("bidding", {})
        extraction_result = result.get("extraction", {})

        errors = []
        if news_result.get("errors"):
            errors.extend(news_result["errors"])
        if bidding_result.get("errors"):
            errors.extend(bidding_result["errors"])

        response = CollectionResponse(
            success=result.get("collection", {}).get("success", False),
            message="管道执行完成（采集 + 去重 + 抽取）",
            news_collected=news_result.get("collected", 0),
            bidding_collected=bidding_result.get("collected", 0),
            duplicates_filtered=news_result.get("duplicates_filtered", 0),
            extraction=extraction_result,
            errors=errors[:10]  # 只返回前10个错误
        )
        logger.info(f"[news_router] trigger_collection 返回: success={response.success}, news={response.news_collected}, bidding={response.bidding_collected}, dedup={response.duplicates_filtered}")
        return response
    except Exception as e:
        logger.error(f"[news_router] trigger_collection 异常: {e}", exc_info=True)
        return CollectionResponse(
            success=False,
            message=f"采集失败: {str(e)}",
            errors=[str(e)]
        )


@router.get("/scheduler/status")
async def get_scheduler_status():
    """
    获取定时任务状态（采集调度默认已迁移到独立 Worker 进程）
    """
    scheduler = get_scheduler_service()
    jobs = scheduler.get_jobs_info()

    return {
        "success": True,
        "mode": "worker" if not jobs else "embedded",
        "message": "采集调度默认运行在独立 Worker 进程（python app/worker_main.py）；设置 ENABLE_EMBEDDED_SCHEDULER=true 可恢复 API 内嵌模式",
        "jobs": jobs
    }


@router.get("/check")
async def check_data_status(db: Session = Depends(get_db)):
    """
    检查数据状态
    """
    service = get_news_collection_service(db)
    has_data = service.has_data()
    news_stats = service.get_news_stats()
    bidding_stats = service.get_bidding_stats()

    return {
        "success": True,
        "has_data": has_data,
        "news_count": news_stats["total"],
        "bidding_count": bidding_stats["total"],
        "news_recent_24h": news_stats.get("recent_24h", 0)
    }


@router.get("/industries")
async def list_industries():
    """
    获取所有行业列表
    """
    logger.info("[news_router] list_industries 请求")
    industries = get_all_industries()
    logger.info(f"[news_router] list_industries 返回: {len(industries)} 个行业")
    return {
        "success": True,
        "industries": industries
    }


@router.get("/industries/{industry_id}")
async def get_industry(industry_id: str):
    """
    获取单个行业配置
    """
    logger.info(f"[news_router] get_industry 请求: industry_id={industry_id}")
    config = get_industry_config(industry_id)
    return {
        "success": True,
        "industry": {
            "id": config.id,
            "name": config.name,
            "description": config.description,
            "news_keywords": config.news_keywords,
            "bidding_keywords": config.bidding_keywords,
        }
    }
