"""
资讯管道编排器 - 采集 → 去重(采集内置) → LLM 抽取

将原本散落在 API 进程里的采集逻辑串联为完整管道，供两个入口复用：
1. 独立 worker 进程（backend/app/worker_main.py，推荐生产用法）
2. API 手动触发（POST /news/collect）

管道各阶段失败互不阻塞：
- 采集失败：返回错误信息，跳过抽取
- 抽取失败：单条标记 failed，不影响已采集数据
"""
import logging
from typing import Optional

from core.database import SessionLocal
from service.news_collection_service import NewsCollectionService
from service.news_extraction_service import get_extraction_service

logger = logging.getLogger(__name__)


async def run_full_pipeline(
    max_news: int = 20,
    max_bidding: int = 20,
    industry_id: Optional[str] = None,
    focus_user_id=None,
    extraction_limit: int = 100,
) -> dict:
    """
    执行完整资讯管道：采集(含去重) -> LLM 结构化抽取

    Args:
        max_news: 单次最大资讯采集数
        max_bidding: 单次最大招投标采集数
        industry_id: 行业ID（None 用默认行业配置）
        focus_user_id: 用户ID（追加其关注主题为搜索词）
        extraction_limit: 单次最多抽取条数

    Returns:
        {"collection": {...}, "extraction": {...}}
    """
    db = SessionLocal()
    try:
        # ---- 阶段 1: 采集（内部已集成 URL + SimHash 指纹去重）----
        try:
            collection_service = NewsCollectionService(db)
            collection_result = await collection_service.collect_all(
                max_news=max_news,
                max_bidding=max_bidding,
                industry_id=industry_id,
                focus_user_id=focus_user_id,
            )
        except Exception as e:
            logger.error(f"[pipeline] 采集阶段失败: {e}")
            collection_result = {"success": False, "error": str(e)}

        # ---- 阶段 2: LLM 结构化抽取（只处理 pending 记录，幂等）----
        extraction_result = {"processed": 0, "succeeded": 0, "failed": 0}
        if collection_result.get("success"):
            try:
                extraction_service = get_extraction_service()
                extraction_result = await extraction_service.process_pending(
                    db, limit=extraction_limit
                )
            except Exception as e:
                logger.error(f"[pipeline] 抽取阶段失败: {e}")
                extraction_result = {"processed": 0, "succeeded": 0, "failed": 0, "error": str(e)}
        else:
            extraction_result["skipped"] = "采集失败，跳过抽取"

        result = {
            "collection": collection_result,
            "extraction": extraction_result,
        }
        logger.info(f"[pipeline] 管道执行完成: {result}")
        return result
    finally:
        db.close()
