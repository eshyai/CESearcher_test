from contextlib import asynccontextmanager
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from router import document_router, search_router, chat_router, research_router
from router.auth_router import router as auth_router
from router.session_router import router as session_router
from router.knowledge_router import router as knowledge_router
from router.attachment_router import router as attachment_router
from router.memory_router import router as memory_router
from router.database_router import router as database_router
from router.news_router import router as news_router
from router.news_library_router import router as news_library_router
from router.feedback_router import router as feedback_router
from core.database import engine, Base
# 导入所有模型以确保它们被注册
from models import (
    User, ChatSession, ChatMessage, ChatAttachment, LongTermMemory,
    KnowledgeBase, Document, KnowledgeBaseMember, KnowledgeAuditLog,
    IndustryStats, CompanyData, PolicyData,
    ResearchCheckpoint, IndustryNews, BiddingInfo, NewsCollectionTask,
    FocusTopic, NewsFeedback
)

# 创建所有数据表（如果不存在）
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("应用启动中...")

    # 采集管道已拆分到独立 Worker 进程（python app/worker_main.py）
    # 如需恢复旧行为（API 进程内嵌定时采集），设置 ENABLE_EMBEDDED_SCHEDULER=true
    if os.getenv("ENABLE_EMBEDDED_SCHEDULER", "").lower() == "true":
        try:
            from service.scheduler_service import init_scheduler_and_check_data
            await init_scheduler_and_check_data()
            logger.info("内嵌定时任务调度器启动成功（ENABLE_EMBEDDED_SCHEDULER=true）")
        except Exception as e:
            logger.error(f"内嵌定时任务调度器启动失败: {e}")
    else:
        logger.info("采集调度已由独立 Worker 进程承担（python app/worker_main.py），API 进程不启动内嵌调度器")

    yield

    # 关闭时执行
    logger.info("应用关闭中...")
    try:
        from service.scheduler_service import get_scheduler_service
        scheduler = get_scheduler_service()
        scheduler.stop()
    except Exception as e:
        logger.error(f"定时任务调度器关闭失败: {e}")


# 交互式文档（/docs、/redoc、/openapi.json）会完整暴露 API 地图，含内部调试端点。
# 用 env 控制而非硬编码：本地开发照常可用，生产关掉。
# ⚠️ 与注册准入同理，此值在模块导入时读取，改 env 后必须重建容器才生效。
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "true").strip().lower() in ("1", "true", "yes", "on")

app = FastAPI(
    title="行业信息助手 API",
    description="基于 AI Agent 的行业信息助手系统",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境中应该设置具体的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

# 注册路由
app.include_router(auth_router)
app.include_router(session_router)
app.include_router(knowledge_router)
app.include_router(attachment_router)
app.include_router(memory_router)
app.include_router(database_router)
app.include_router(document_router)
app.include_router(search_router)
app.include_router(chat_router)
app.include_router(research_router)
# 共享资讯库须在 news_router 之前注册：/news/library 不能被 /news/{news_id} 之类模式先吃掉
app.include_router(news_library_router)
app.include_router(news_router)
app.include_router(feedback_router)

@app.get("/hello")
async def hello_world():
    """
    Simple hello world endpoint for network verification
    """
    return {
        "status": "success",
        "message": "Hello World! The API is working correctly."
    }

if __name__ == '__main__':
    import uvicorn
    # API_PORT 可覆盖（Windows 上 8000 可能被 Hyper-V/WSL 端口保留范围占用，报 winerror 10013）
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("API_PORT", "8000")))
