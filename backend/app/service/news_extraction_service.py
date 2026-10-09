"""
LLM 结构化抽取服务 - 把资讯从"文本"变"数据"

对每条新采集的资讯，用便宜模型（默认 qwen-turbo）抽取：
- topics: 主题标签列表（对齐 FocusTopic 五类：industry/company/process/technology/product）
- event_type: 事件类型（投产扩产/停产检修/价格变动/政策发布/安全事故/环保处罚/并购融资/技术突破/招投标/其他）
- impact_direction: 影响方向（positive/negative/neutral）
- key_numbers: 关键数字（市场规模/价格/产能等，含名称、数值、单位）
- entities: 涉及的公司/产品/机构
- summary: 一句话摘要

设计原则：
- 每条资讯约几百 token，成本可控
- 失败降级：单条抽取失败只标记 failed，绝不阻塞采集管道
- 幂等：只处理 extraction_status='pending' 的记录，可安全重跑

使用方式:
    from service.news_extraction_service import NewsExtractionService
    svc = NewsExtractionService()
    stats = await svc.process_pending(db, limit=50)
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy.orm import Session

load_dotenv()

logger = logging.getLogger(__name__)


# 化工行业事件类型（封闭枚举，便于后续告警与统计路由）
EVENT_TYPES = [
    "投产扩产", "停产检修", "价格变动", "政策发布", "安全事故",
    "环保处罚", "并购融资", "技术突破", "招投标", "其他",
]

IMPACT_DIRECTIONS = ["positive", "negative", "neutral"]

def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """读取整型环境变量，非法值回退默认"""
    try:
        value = int(os.getenv(name, str(default)))
        return value if value >= minimum else minimum
    except (TypeError, ValueError):
        return default


# LLM 并发数（qwen-turbo 单条约 8-11s，并发越高整批越快；可用环境变量覆盖）
MAX_CONCURRENCY = _env_int("NEWS_EXTRACT_CONCURRENCY", 6)
MAX_CONTENT_CHARS = 1500     # 送入 LLM 的正文截断长度
MAX_RETRIES = 2

EXTRACTION_PROMPT = """你是化工行业的资讯分析专家。请从下面的资讯中抽取结构化信息，严格只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。

JSON 字段定义：
- "topics": 数组，主题标签，每个元素为 {{"type": "industry|company|process|technology|product", "name": "标签名"}}，最多5个，type 只能取这五种之一
- "event_type": 字符串，事件类型，只能从这些里选：{event_types}
- "impact_direction": 字符串，影响方向，只能从这些里选：positive、negative、neutral
- "key_numbers": 数组，关键数字，每个元素为 {{"name": "指标名", "value": "数值", "unit": "单位"}}，最多5个，没有则为空数组
- "entities": 数组，涉及的公司/产品/机构名称，最多10个
- "summary": 字符串，一句话中文摘要（50字以内）

资讯标题：{title}
资讯内容：{content}"""


class NewsExtractionService:
    """资讯 LLM 结构化抽取服务"""

    def __init__(self, model: str = None):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model or os.getenv("NEWS_EXTRACT_MODEL", "qwen-turbo")
        if not self.api_key:
            logger.warning("[extract] DASHSCOPE_API_KEY 未配置，抽取功能不可用")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # ---------- 单条抽取 ----------

    async def extract_one(self, title: str, content: str) -> Optional[dict]:
        """
        对单条资讯执行 LLM 抽取

        Returns:
            结构化字典；失败返回 None
        """
        if not self.available:
            return None

        from openai import AsyncOpenAI

        prompt = EXTRACTION_PROMPT.format(
            event_types="、".join(EVENT_TYPES),
            title=(title or "")[:200],
            content=(content or "")[:MAX_CONTENT_CHARS] or "（无正文）",
        )

        try:
            client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        except Exception as e:
            logger.error(f"[extract] 初始化 LLM 客户端失败: {e}")
            return None

        for attempt in range(MAX_RETRIES):
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=800,
                )
                raw = response.choices[0].message.content or ""
                return self._parse_llm_json(raw)
            except Exception as e:
                logger.warning(
                    f"[extract] LLM 调用失败（第{attempt + 1}次）: {e}"
                )
                await asyncio.sleep(1.5 * (attempt + 1))

        return None

    @staticmethod
    def _parse_llm_json(raw: str) -> Optional[dict]:
        """健壮解析 LLM 输出（容忍代码块标记/前后杂文字）"""
        if not raw:
            return None
        # 剥离 markdown 代码块
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        # 截取第一个 { 到最后一个 }
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        # 字段规整 + 枚举校验，非法值降级
        topics = []
        for t in (data.get("topics") or [])[:5]:
            if isinstance(t, dict) and t.get("name"):
                ttype = t.get("type")
                topics.append({
                    "type": ttype if ttype in
                    ("industry", "company", "process", "technology", "product") else "industry",
                    "name": str(t["name"])[:100],
                })

        event_type = data.get("event_type")
        if event_type not in EVENT_TYPES:
            event_type = "其他"

        impact = data.get("impact_direction")
        if impact not in IMPACT_DIRECTIONS:
            impact = "neutral"

        key_numbers = []
        for k in (data.get("key_numbers") or [])[:5]:
            if isinstance(k, dict) and k.get("value") is not None:
                key_numbers.append({
                    "name": str(k.get("name", ""))[:100],
                    "value": str(k.get("value"))[:50],
                    "unit": str(k.get("unit", ""))[:30],
                })

        entities = [str(e)[:100] for e in (data.get("entities") or [])[:10] if e]

        return {
            "topics": topics,
            "event_type": event_type,
            "impact_direction": impact,
            "key_numbers": key_numbers,
            "entities": entities,
            "summary": str(data.get("summary", ""))[:500],
        }

    # ---------- 批处理 ----------

    async def _extract_and_save(self, db: Session, news) -> bool:
        """抽取单条并落库（独立事务提交，失败标记 failed）"""
        from models.news import IndustryNews

        try:
            result = await self.extract_one(news.title, news.content)
            if result is None:
                news.extraction_status = "failed"
                return False

            news.extracted_topics = json.dumps(result["topics"], ensure_ascii=False)
            news.event_type = result["event_type"]
            news.impact_direction = result["impact_direction"]
            news.key_numbers = json.dumps(result["key_numbers"], ensure_ascii=False)
            news.entities = json.dumps(result["entities"], ensure_ascii=False)
            news.summary = result["summary"]
            news.extraction_status = "completed"
            news.extracted_at = datetime.utcnow()
            return True
        except Exception as e:
            logger.error(f"[extract] 保存抽取结果失败 news_id={news.id}: {e}")
            try:
                news.extraction_status = "failed"
            except Exception:
                pass
            return False
        finally:
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"[extract] 提交事务失败 news_id={getattr(news, 'id', '?')}: {e}")

    async def process_pending(self, db: Session, limit: int = 50) -> dict:
        """
        处理所有待抽取的资讯（extraction_status='pending'）

        Returns:
            {"processed": n, "succeeded": m, "failed": k, "skipped": 原因}
        """
        from models.news import IndustryNews

        if not self.available:
            logger.warning("[extract] API Key 未配置，跳过抽取")
            return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": "DASHSCOPE_API_KEY 未配置"}

        try:
            pending = (
                db.query(IndustryNews)
                .filter(IndustryNews.extraction_status == "pending")
                .order_by(IndustryNews.collected_at.desc())
                .limit(limit)
                .all()
            )
        except Exception as e:
            logger.warning(
                f"[extract] 查询待抽取记录失败（可能未执行 docker/init-db/04-news-extraction.sql）: {e}"
            )
            return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": f"表结构未迁移: {e}"}

        if not pending:
            return {"processed": 0, "succeeded": 0, "failed": 0}

        logger.info(f"[extract] 开始抽取 {len(pending)} 条资讯（模型: {self.model}）")

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        succeeded = 0
        failed = 0

        async def worker(news_item):
            nonlocal succeeded, failed
            async with semaphore:
                if await self._extract_and_save(db, news_item):
                    succeeded += 1
                else:
                    failed += 1

        await asyncio.gather(*(worker(n) for n in pending))

        stats = {
            "processed": len(pending),
            "succeeded": succeeded,
            "failed": failed,
        }
        logger.info(f"[extract] 抽取完成: {stats}")
        return stats


# 模块级单例
_extraction_service: Optional[NewsExtractionService] = None


def get_extraction_service() -> NewsExtractionService:
    global _extraction_service
    if _extraction_service is None:
        _extraction_service = NewsExtractionService()
    return _extraction_service
