"""
资讯去重服务 - SimHash 内容指纹 + 近重复检测

设计说明：
- 每条资讯入库前计算 64 位 SimHash 指纹（字符 3-gram 特征 + MD5 特征哈希）
- 采用字符 n-gram 而非词级分词：对百字级新闻摘要，n-gram 特征更稠密，
  同事件的不同改写（转载/标题微调）距离显著小于不相关文本
- 精确重复：指纹完全相同
- 近重复：与近 N 天内资讯的汉明距离 <= 阈值
- 对旧库兼容：若 industry_news 表尚未迁移指纹列，自动降级为仅 URL 去重并告警

使用方式:
    from service.dedup_service import NewsDedupService
    dedup = NewsDedupService()
    fingerprint = dedup.compute_fingerprint(title, content)
    duplicate_of = dedup.find_duplicate(db, fingerprint)
"""
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class NewsDedupService:
    """资讯内容指纹去重服务"""

    # 汉明距离阈值：<=4 视为近重复（字符 3-gram SimHash，抓"转载微调"）
    HAMMING_THRESHOLD = 4

    # 标题 3-gram Jaccard 阈值：>=0.6 视为同事件改写（抓"同事件不同措辞"）
    TITLE_JACCARD_THRESHOLD = 0.6

    # 只与近 N 天的资讯比对（控制查询量，资讯时效性强）
    COMPARE_WINDOW_DAYS = 30

    # 标题比对的最近条数上限（控制内存/耗时）
    TITLE_COMPARE_LIMIT = 2000

    # 字符 n-gram 长度
    NGRAM_SIZE = 3

    def __init__(self, hamming_threshold: int = None, window_days: int = None,
                 title_jaccard_threshold: float = None):
        self.hamming_threshold = hamming_threshold or self.HAMMING_THRESHOLD
        self.window_days = window_days or self.COMPARE_WINDOW_DAYS
        self.title_jaccard_threshold = title_jaccard_threshold or self.TITLE_JACCARD_THRESHOLD
        self._migration_warned = False

    # ---------- 指纹计算 ----------

    def _extract_features(self, text: str) -> list:
        """
        提取字符 n-gram 特征（去空白后滑窗）
        """
        cleaned = re.sub(r"\s+", "", text)
        n = self.NGRAM_SIZE
        if len(cleaned) < n:
            return [cleaned] if cleaned else []
        return [cleaned[i:i + n] for i in range(len(cleaned) - n + 1)]

    def compute_fingerprint(self, title: str, content: str) -> str:
        """
        计算 title + content 的 64 位 SimHash 指纹
        标题权重加倍（转载常改写正文而保留标题主干）

        Returns:
            16 位十六进制字符串
        """
        title = (title or "").strip()
        content = (content or "").strip()
        # 标题重复计入一次，等效权重 x2
        text = f"{title} {title} {content}".strip()
        if not text:
            return "0" * 16

        tokens = self._extract_features(text)
        if not tokens:
            return "0" * 16

        # 逐 token 投票到 64 个 bit 桶
        bits = [0] * 64
        for token in tokens:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            for i in range(64):
                if (h >> i) & 1:
                    bits[i] += 1
                else:
                    bits[i] -= 1

        fingerprint = 0
        for i in range(64):
            if bits[i] > 0:
                fingerprint |= (1 << i)

        return format(fingerprint, "016x")

    @staticmethod
    def hamming_distance(fp_hex_a: str, fp_hex_b: str) -> int:
        """两个十六进制指纹的汉明距离"""
        try:
            return bin(int(fp_hex_a, 16) ^ int(fp_hex_b, 16)).count("1")
        except (ValueError, TypeError):
            return 64

    # ---------- 去重查询 ----------

    @staticmethod
    def _title_jaccard(title_a: str, title_b: str) -> float:
        """两个标题的字符 3-gram Jaccard 相似度"""
        if not title_a or not title_b:
            return 0.0
        grams_a = set(title_a[i:i + 3] for i in range(len(title_a) - 2))
        grams_b = set(title_b[i:i + 3] for i in range(len(title_b) - 2))
        if not grams_a or not grams_b:
            return 0.0
        return len(grams_a & grams_b) / len(grams_a | grams_b)

    def find_duplicate(
        self,
        db: Session,
        fingerprint: str,
        title: str = "",
        content: str = "",
    ) -> Optional[str]:
        """
        查找近 N 天内与给定资讯重复/近重复的记录

        两级判定（任一命中即视为重复）：
        1. 标题 Jaccard >= 阈值：同事件改写（不同来源报道同一事件）
        2. SimHash 汉明距离 <= 阈值：转载微调（正文高度雷同）

        Args:
            db: 数据库会话
            fingerprint: 16 位十六进制 SimHash
            title/content: 原文

        Returns:
            重复资讯的 id（字符串），无重复返回 None
        """
        from models.news import IndustryNews

        if not fingerprint:
            return None

        since = datetime.utcnow() - timedelta(days=self.window_days)
        normalized_title = (title or "").strip()

        try:
            # ---- 级别 1: 标题相似（同事件改写）----
            if normalized_title:
                recent_titles = (
                    db.query(IndustryNews.id, IndustryNews.title)
                    .filter(
                        IndustryNews.collected_at >= since,
                        IndustryNews.title.isnot(None),
                    )
                    .order_by(IndustryNews.collected_at.desc())
                    .limit(self.TITLE_COMPARE_LIMIT)
                    .all()
                )
                for news_id, existing_title in recent_titles:
                    if existing_title and self._title_jaccard(
                        normalized_title, existing_title
                    ) >= self.title_jaccard_threshold:
                        return str(news_id)

            # ---- 级别 2: SimHash 近重复（转载微调）----
            rows = (
                db.query(IndustryNews.id, IndustryNews.content_fingerprint)
                .filter(
                    IndustryNews.content_fingerprint.isnot(None),
                    IndustryNews.collected_at >= since,
                )
                .all()
            )
        except Exception as e:
            # 旧库未执行 03-news-dedup.sql 迁移时的降级处理
            if not self._migration_warned:
                logger.warning(
                    f"[dedup] 查询指纹列失败（可能未执行 docker/init-db/03-news-dedup.sql），"
                    f"降级为仅 URL 去重: {e}"
                )
                self._migration_warned = True
            return None

        fp_int = int(fingerprint, 16)

        for news_id, existing_fp in rows:
            if not existing_fp:
                continue
            try:
                existing_int = int(existing_fp, 16)
            except (ValueError, TypeError):
                continue
            if bin(fp_int ^ existing_int).count("1") <= self.hamming_threshold:
                return str(news_id)

        return None

    def stats(self, db: Session) -> dict:
        """指纹覆盖情况统计（用于评估迁移完成度）"""
        from models.news import IndustryNews
        from sqlalchemy import func

        try:
            total = db.query(func.count(IndustryNews.id)).scalar()
            with_fp = (
                db.query(func.count(IndustryNews.id))
                .filter(IndustryNews.content_fingerprint.isnot(None))
                .scalar()
            )
            return {"total": total, "with_fingerprint": with_fp}
        except Exception as e:
            return {"total": -1, "with_fingerprint": -1, "error": str(e)}


# 模块级单例（jieba 分词器初始化较重，避免重复加载）
_dedup_service: Optional[NewsDedupService] = None


def get_dedup_service() -> NewsDedupService:
    global _dedup_service
    if _dedup_service is None:
        _dedup_service = NewsDedupService()
    return _dedup_service
