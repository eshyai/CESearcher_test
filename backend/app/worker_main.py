"""
资讯采集 Worker - 独立进程入口

把采集管道（采集 → 去重 → LLM 抽取）从 API 进程中拆分出来：
- API 进程（app_main.py）不再内嵌定时采集，专注服务请求
- 本进程独立运行定时采集，长任务不占 API 事件循环，API 重启不丢采集状态

启动方式（在 backend 目录下）:
    python app/worker_main.py            # 常驻模式：每天定时执行完整管道
    python app/worker_main.py --once     # 单次执行完整管道后退出（调试用）
    python app/worker_main.py --once --max-news 10 --industry chemical
    python app/worker_main.py --scan-low-quality                # 扫描低质来源（只读）
    python app/worker_main.py --clean-low-quality               # 试算将删除多少条（不删）
    python app/worker_main.py --clean-low-quality --yes         # 真正删除低质历史数据

环境变量:
    NEWS_WORKER_CRON_HOUR     定时执行的小时（24h 制，默认 12）
    NEWS_EXTRACT_MODEL        抽取模型（默认 qwen-turbo）
    NEWS_EXTRACT_CONCURRENCY  LLM 抽取并发数（默认 6）
    BOCHA_FRESHNESS           博查时间范围（默认 oneMonth；可选 oneDay/oneWeek/oneMonth/oneYear/noLimit）
"""
import argparse
import asyncio
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NewsWorker")


async def has_any_data() -> bool:
    """检查库中是否已有资讯数据"""
    from core.database import SessionLocal
    from service.news_collection_service import NewsCollectionService

    db = SessionLocal()
    try:
        return NewsCollectionService(db).has_data()
    finally:
        db.close()


async def run_pipeline_once(args) -> dict:
    """单次执行完整管道"""
    from service.news_pipeline import run_full_pipeline

    logger.info(
        f"开始执行资讯管道: industry={args.industry}, "
        f"max_news={args.max_news}, max_bidding={args.max_bidding}"
    )
    result = await run_full_pipeline(
        max_news=args.max_news,
        max_bidding=args.max_bidding,
        industry_id=args.industry,
    )
    logger.info(f"管道执行完成: {result}")
    return result


async def run_daemon(args):
    """常驻模式：定时调度 + 空库初始采集"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    cron_hour = int(os.getenv("NEWS_WORKER_CRON_HOUR", "12"))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_pipeline_once_wrapper := _make_scheduled_job(args),
        CronTrigger(hour=cron_hour, minute=0),
        id="daily_news_pipeline",
        name="每日资讯管道（采集+去重+抽取）",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"资讯 Worker 已启动，定时任务: 每天 {cron_hour}:00")

    # 空库时立即执行一次初始采集
    try:
        if not await has_any_data():
            logger.info("数据库中没有资讯数据，立即执行初始采集...")
            await run_pipeline_once(args)
        else:
            logger.info("数据库中已有资讯数据，跳过初始采集")
    except Exception as e:
        logger.error(f"初始数据检查失败: {e}")

    # 保持进程运行
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，正在停止 Worker...")
        scheduler.shutdown()


def _make_scheduled_job(args):
    """构造定时任务回调（统一异常捕获，避免调度器吞异常后静默停止）"""
    async def job():
        logger.info(f"定时管道任务触发 - {datetime.now()}")
        try:
            await run_pipeline_once(args)
        except Exception as e:
            logger.error(f"定时管道任务失败: {e}")
    return job


def main():
    parser = argparse.ArgumentParser(description="化工资讯采集 Worker")
    parser.add_argument("--once", action="store_true", help="单次执行完整管道后退出")
    parser.add_argument("--backfill", action="store_true",
                        help="仅为历史数据补算去重指纹（迁移后执行一次），不触发采集")
    parser.add_argument("--scan-low-quality", action="store_true",
                        help="扫描已入库资讯中命中来源质量过滤规则的记录（只读，不删除）")
    parser.add_argument("--clean-low-quality", action="store_true",
                        help="删除已入库的低质来源资讯（需配合 --yes 才会真正删除）")
    parser.add_argument("--yes", action="store_true",
                        help="确认执行删除操作（不加则 clean-low-quality 为 dry-run）")
    parser.add_argument("--max-news", type=int, default=20, help="单次最大资讯采集数")
    parser.add_argument("--max-bidding", type=int, default=20, help="单次最大招投标采集数")
    parser.add_argument("--industry", type=str, default=None, help="行业ID（默认用配置）")
    args = parser.parse_args()

    if args.scan_low_quality or args.clean_low_quality:
        from core.database import SessionLocal
        from service.news_collection_service import NewsCollectionService

        db = SessionLocal()
        try:
            service = NewsCollectionService(db)
            dry_run = not (args.clean_low_quality and args.yes)
            if args.clean_low_quality:
                result = service.clean_low_quality_news(
                    industry_id=args.industry, dry_run=dry_run
                )
                mode = "DRY-RUN（未删除，加 --yes 才真正删除）" if dry_run else "已执行删除"
                logger.info(f"低质资讯清理 {mode}: {result}")
            else:
                result = service.scan_low_quality_news(industry_id=args.industry)
                logger.info(f"低质资讯扫描结果: {result}")
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        finally:
            db.close()
    elif args.backfill:
        from core.database import SessionLocal
        from service.dedup_service import get_dedup_service

        db = SessionLocal()
        try:
            result = get_dedup_service().backfill_fingerprints(db)
            logger.info(f"指纹回填完成: {result}")
        finally:
            db.close()
    elif args.once:
        asyncio.run(run_pipeline_once(args))
    else:
        try:
            asyncio.run(run_daemon(args))
        except KeyboardInterrupt:
            logger.info("Worker 已退出")


if __name__ == "__main__":
    main()
