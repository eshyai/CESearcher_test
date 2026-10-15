"""
行业资讯和招投标信息路由
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel

from core.database import get_db
from service import news_hidden_service
from service.news_collection_service import get_news_collection_service
from service.news_hidden_service import coerce_uuid
from service.scheduler_service import get_scheduler_service
from config.industry_config import get_all_industries, get_industry_config
from models.news import IndustryNews
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
    # 当前用户已屏蔽（个人过滤，非全局删除）的资讯条数，供前端展示「已屏蔽(n)」入口
    hidden_count: int = 0


class CollectionResponse(BaseModel):
    """采集响应"""
    success: bool
    message: str
    news_collected: int = 0
    bidding_collected: int = 0
    duplicates_filtered: int = 0
    low_quality_filtered: int = 0
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
        stats=stats,
        hidden_count=news_hidden_service.hidden_count(db, current_user.id) if current_user else 0
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
            low_quality_filtered=news_result.get("low_quality_filtered", 0),
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


class NewsSearchRequest(BaseModel):
    """碎片检索请求"""
    query: str
    industry_id: Optional[str] = None
    event_type: Optional[str] = None
    impact_direction: Optional[str] = None
    limit: int = 20
    # 是否同时检索全网（默认开）：库内 + 全网一起给结果，一次搜索即可完成
    include_online: bool = True
    # 是否过滤外文结果（默认关）：打开后丢弃标题为外文（中文占比低于阈值）的候选
    exclude_foreign: bool = False


class NewsSearchResponse(BaseModel):
    """碎片检索响应"""
    success: bool
    query: str
    mode: str
    vector_indexed: bool
    total: int
    data: List[dict]
    online_total: int = 0   # 全网检索命中（过滤后）条数
    online_kept: int = 0    # 进入最终结果的全网条数
    persisted: int = 0      # 本次沉淀新入库条数
    irrelevant_filtered: int = 0   # 被行业相关性闸剔除的候选数
    irrelevant: List[dict] = []    # 被剔除的样例（最多 10 条，便于排查）
    title_deduped: int = 0         # 因标题重复（库内/全网同一条）被合并掉的候选数
    foreign_filtered: int = 0      # 因「过滤外文」开关被丢弃的候选数


@router.post("/search", response_model=NewsSearchResponse)
def search_news(
    payload: NewsSearchRequest,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """碎片检索：输入公司/产品/工艺/政策片段等碎片信息，返回相关报道。

    库内混合检索（向量语义召回 ∪ 关键词命中）+ 全网实时检索（博查）
    → 一起 Rerank 重排 → 相关度阈值过滤；
    向量索引未建立时自动降级为纯关键词检索。
    通过阈值的全网结果会沉淀入库，下次可被库内命中。
    `exclude_foreign=true` 时丢弃标题为外文的候选（默认不过滤）。

    注意：定义为同步端点（def），FastAPI 会放入线程池执行；
    因为全网检索使用同步 httpx，若用 async def 会阻塞事件循环。
    """
    from service.news_search_service import get_news_search_service

    logger.info(
        f"[news_router] search_news 请求: query='{payload.query[:40]}', "
        f"industry_id={payload.industry_id}, event_type={payload.event_type}, "
        f"limit={payload.limit}, include_online={payload.include_online}, "
        f"exclude_foreign={payload.exclude_foreign}"
    )
    service = get_news_search_service(db)
    result = service.search(
        query=payload.query,
        industry_id=payload.industry_id,
        event_type=payload.event_type,
        impact_direction=payload.impact_direction,
        limit=payload.limit,
        include_online=payload.include_online,
        exclude_foreign=payload.exclude_foreign,
        user_id=current_user.id if current_user else None,
    )
    logger.info(
        f"[news_router] search_news 返回: mode={result['mode']}, total={result['total']}, "
        f"online_kept={result.get('online_kept')}, persisted={result.get('persisted')}, "
        f"irrelevant_filtered={result.get('irrelevant_filtered')}, "
        f"title_deduped={result.get('title_deduped')}, "
        f"foreign_filtered={result.get('foreign_filtered')}, "
        f"vector_indexed={result['vector_indexed']}"
    )
    return NewsSearchResponse(success=True, **result)


# ============================================================================
# 个人屏蔽（不是删除）
# ----------------------------------------------------------------------------
# `industry_news` 是**按行业共享的语料**（没有 user_id），所以「删除」是全局动作：
# 会同时让所有用户看不到，并且因为 `news_feedback.news_id` 的 ON DELETE CASCADE
# 连带删掉所有人对该条资讯的反馈——而反馈正是 L1 / L1.5 / L2a 的输入。
#
# 因此这里只提供**个人过滤**：屏蔽后仅当前用户在列表与检索结果里看不到，
# 共享库、其他用户视图、Milvus 向量索引都不动，随时可恢复。
# 与反馈互不影响：屏蔽**不写反馈**，避免污染偏好学习样本。
# ============================================================================


class HideRequest(BaseModel):
    """屏蔽请求体（可选，reason 仅留痕）"""
    reason: Optional[str] = None


class HideActionResponse(BaseModel):
    """屏蔽 / 取消屏蔽响应"""
    success: bool
    message: str
    status: str = ""          # created / exists / removed / absent
    hidden_count: int = 0
    weak_topics: List[dict] = []   # 本次真正生效的弱降权主题（LMA 弱负信号）


class HiddenNewsResponse(BaseModel):
    """已屏蔽资讯列表响应"""
    success: bool
    data: List[dict]
    total: int


@router.post("/{news_id}/hide", response_model=HideActionResponse)
def hide_news(
    news_id: str,
    payload: Optional[HideRequest] = None,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """屏蔽一条资讯：只在**你自己**的资讯列表与检索结果里不再出现，随时可恢复。

    幂等：重复屏蔽不报错。不删除任何数据，也不写反馈。
    同时对命中的关注主题施加一点**弱降权**（弱负信号），随取消屏蔽精确回滚。
    """
    nid = coerce_uuid(news_id)
    if not nid:
        raise HTTPException(status_code=400, detail="news_id 非法")
    if not db.query(IndustryNews.id).filter(IndustryNews.id == nid).first():
        raise HTTPException(status_code=404, detail="资讯不存在")

    try:
        result = news_hidden_service.hide_news(
            db, current_user.id, nid, reason=payload.reason if payload else None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    created = result["status"] == "created"
    weak = result.get("weak_topics") or []
    logger.info(f"[news_router] hide_news: user={current_user.id}, news={nid}, "
                f"status={result['status']}, weak_topics={len(weak)}")
    if created and weak:
        message_text = "已屏蔽；同时降低了对 " + "、".join(f"「{w['name']}」" for w in weak) + " 的推荐"
    else:
        message_text = ("已屏蔽，这条资讯不再出现在你的列表与检索结果中"
                        if created else "这条资讯此前已被你屏蔽")
    return HideActionResponse(
        success=True,
        message=message_text,
        status=result["status"],
        hidden_count=news_hidden_service.hidden_count(db, current_user.id),
        weak_topics=weak,
    )


@router.delete("/{news_id}/hide", response_model=HideActionResponse)
def unhide_news(
    news_id: str,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """取消屏蔽，恢复显示。幂等：未屏蔽过也返回成功。"""
    nid = coerce_uuid(news_id)
    if not nid:
        raise HTTPException(status_code=400, detail="news_id 非法")

    removed = news_hidden_service.unhide_news(db, current_user.id, nid)
    logger.info(f"[news_router] unhide_news: user={current_user.id}, news={nid}, removed={removed}")
    return HideActionResponse(
        success=True,
        message="已恢复显示" if removed else "这条资讯本来就不在你的屏蔽列表里",
        status="removed" if removed else "absent",
        hidden_count=news_hidden_service.hidden_count(db, current_user.id),
    )


@router.get("/hidden", response_model=HiddenNewsResponse)
def list_hidden_news(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """当前用户已屏蔽的资讯列表（按屏蔽时间倒序），用于查看与恢复。"""
    items, total = news_hidden_service.list_hidden_news(
        db, current_user.id, limit=limit, offset=offset
    )
    return HiddenNewsResponse(success=True, data=items, total=total)
