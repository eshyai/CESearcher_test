"""
资讯去重服务 - SimHash 内容指纹 + 近重复检测

设计说明：
- 每条资讯入库前计算 64 位 SimHash 指纹（基于 jieba 分词 + MD5 特征哈希）
- 精确重复：指纹完全相同
- 近重复：与近 N 天内资讯的汉明距离 <= 3（同一事件的不同转载/改写）
- 对旧库兼容：若 industry_news 表尚未迁移指纹列，自动降级为仅 URL 去重并告警

使用方式:
    from service.dedup_service import NewsDedupService
    dedup = NewsDedupService()
    fingerprint = dedup.compute_fingerprint(title, content)
    duplicate_of = dedup.find_duplicate(db, fingerprint)
"""
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class NewsDedupService:
    """资讯内容指纹去重服务"""

    # 汉明距离阈值：<=3 视为近重复（64 位 SimHash 的常用经验值）
    HAMMING_THRESHOLD = 3

    # 只与近 N 天的资讯比对（控制查询量，资讯时效性强）
    COMPARE_WINDOW_DAYS = 30

    def __init__(self, hamming_threshold: int = None, window_days: int = None):
        self.hamming_threshold = hamming_threshold or self.HAMMING_THRESHOLD
        self.window_days = window_days or self.COMPARE_WINDOW_DAYS
        self._migration_warned = False

    # ---------- 指纹计算 ----------

    @staticmethod
    def _tokenize(text: str) -> list:
        """jieba 搜索引擎模式分词，过滤空白与单字符标点"""
        import jieba
        tokens = [w for w in jieba.cut_for_search(text) if w.strip()]
        return tokens

    def compute_fingerprint(self, title: str, content: str) -> str:
        """
        计算 title + content 的 64 位 SimHash 指纹

        Returns:
            16 位十六进制字符串
        """
        text = f"{title or ''} {content or ''}".strip()
        if not text:
            return "0" * 16

        tokens = self._tokenize(text)
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

    def find_duplicate(
        self,
        db: Session,
        fingerprint: str,
        title: str = "",
        content: str = "",
    ) -> Optional[str]:
        """
        查找近 N 天内与给定指纹重复/近重复的资讯

        Args:
            db: 数据库会话
            fingerprint: 16 位十六进制 SimHash
            title/content: 原文（用于标题完全相同的快速判断）

        Returns:
            重复资讯的 id（字符串），无重复返回 None
        """
        from models.news import IndustryNews

        if not fingerprint:
            return None

        since = datetime.utcnow() - timedelta(days=self.window_days)

        try:
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

        # 快速路径：标题完全相同视为重复（不同来源转载同一事件）
        normalized_title = (title or "").strip()
        if normalized_title:
            try:
                title_match = (
                    db.query(IndustryNews.id)
                    .filter(
                        IndustryNews.title == normalized_title,
                        IndustryNews.collected_at >= since,
                    )
                    .first()
                )
                if title_match:
                    return str(title_match.id)
            except Exception:
                pass

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
