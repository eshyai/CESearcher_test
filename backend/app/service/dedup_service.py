"""
资讯去重服务 - SimHash 内容指纹 + 近重复检测

设计说明：
- 每条资讯入库前计算 64 位 SimHash 指纹（字符 3-gram 特征 + MD5 特征哈希）
- 采用字符 n-gram 而非词级分词：对百字级新闻摘要，n-gram 特征更稠密，
  同事件的不同改写（转载/标题微调）距离显著小于不相关文本
- 精确重复：指纹完全相同
- 近重复：与近 N 天内资讯的汉明距离 <= 阈值
- 标题重复：归一化相同 / 主干相同且带站点后缀（门户转载）/ 整体 Jaccard 达标
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

# 标题归一化用：去掉空白、常见中英标点与站点分隔符。
# 只用于「是不是同一条」的判定，不用于展示（展示仍用原标题）。
_TITLE_NOISE_RE = re.compile(
    r"[\s\u3000\-—_|｜:：,，.。!！?？;；、()（）\[\]【】<>《》/\\\"'“”‘’®™·]+"
)


def normalize_title(title: str) -> str:
    """标题归一化：去掉空白与标点、ASCII 转小写。

    例：「巴斯夫上海一化工装置发生泄漏事故_公司频道_财新网」与
        「巴斯夫上海一化工装置发生泄漏事故- 财新网」归一化后前缀一致。
    """
    return _TITLE_NOISE_RE.sub("", (title or "")).lower()


def title_jaccard(title_a: str, title_b: str) -> float:
    """两个标题的字符 3-gram Jaccard 相似度。

    调用方自行决定是否先做 normalize_title（检索结果层先归一化更稳）。
    """
    a = (title_a or "").strip()
    b = (title_b or "").strip()
    if not a or not b:
        return 0.0
    grams_a = {a[i:i + 3] for i in range(len(a) - 2)}
    grams_b = {b[i:i + 3] for i in range(len(b) - 2)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


# ---- 站点转载识别 ----
# 门户转载同一篇文章时不动正文标题，只在尾部拼上自己的站点名，例如：
#   「德国化工整合升温:巴斯夫拟收购赢创,双方确认已初步接触_新浪财经_新浪网」
#   「…双方确认已初步接触_大风号_凤凰网」/「…双方确认已初步接触 _ 东方财富网」
# 这类标题整体 Jaccard 只有 0.6x，会被阈值挡住，所以单独比对「主干」。
_TITLE_STEM_SEP_RE = re.compile(r"[_|｜\-–—]+")
_SITE_TAIL_WORDS = (
    "网", "报", "头条", "财经", "新闻", "频道", "社", "传媒", "周刊",
    "日报", "观察", "资讯", "媒体", "门户", "客户端", "号",
)
_STEM_MIN_LEN = 16   # 主干长度下限：太短不足以证明是同一条（如「市场行情」「环氧丙烷周报」）
_TAIL_MAX_LEN = 20   # 站点后缀长度上限
_ASCII_DIGIT_RE = re.compile(r"[0-9]")


def split_title(title: str) -> tuple:
    """把标题拆成（主干, 尾部）。无分隔符时主干即整条标题、尾部为空串。"""
    parts = _TITLE_STEM_SEP_RE.split((title or "").strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return (parts[0] if parts else ""), ""


def looks_like_site_tail(tail: str) -> bool:
    """尾部是否像「站点名」：不含数字、不太长、且含常见站点字眼。

    排除「-2026年9月」「-华东」这类其实是内容一部分的尾部，
    避免把不同月份/不同地区的同题文章误判成同一条。
    """
    t = (tail or "").strip()
    if not t or len(t) > _TAIL_MAX_LEN or _ASCII_DIGIT_RE.search(t):
        return False
    return any(word in t for word in _SITE_TAIL_WORDS)


def titles_same_article(title_a: str, title_b: str, jaccard_threshold: float) -> bool:
    """两个标题是否指向同一篇资讯（展示层与入库层共用同一口径）。

    三级判定，任一命中即视为同一条：
    1. 归一化后完全相同；
    2. 主干完全相同（主干长度 >= 16）且至少一方带站点后缀 —— 抓门户转载；
    3. 归一化后整体 Jaccard >= 阈值 —— 抓同事件改写。
    """
    a = (title_a or "").strip()
    b = (title_b or "").strip()
    if not a or not b:
        return False

    norm_a = normalize_title(a)
    norm_b = normalize_title(b)
    if norm_a == norm_b:
        return True

    stem_a, tail_a = split_title(a)
    stem_b, tail_b = split_title(b)
    norm_stem_a = normalize_title(stem_a)
    norm_stem_b = normalize_title(stem_b)
    if (
        len(norm_stem_a) >= _STEM_MIN_LEN
        and norm_stem_a == norm_stem_b
        and (looks_like_site_tail(tail_a) or looks_like_site_tail(tail_b))
    ):
        return True

    return title_jaccard(norm_a, norm_b) >= jaccard_threshold


class NewsDedupService:
    """资讯内容指纹去重服务"""

    # 汉明距离阈值：<=4 视为近重复（字符 3-gram SimHash，抓"转载微调"）
    HAMMING_THRESHOLD = 4

    # 标题 3-gram Jaccard 阈值：视为同一条的兜底线。
    # 之所以定得高：实测「PA6巴斯夫…塑胶颗粒」与「PA66巴斯夫…尼龙颗粒」=0.725，
    # 「己内酰胺市场价格走势分析-2026年9月」与「…-2026年8月」=0.789 ——
    # 低阈值会把不同牌号、不同月份的同系列内容误判成重复。门户转载那种
    # 「同正文 + 不同站点后缀」的情形已由 titles_same_article 的主干规则覆盖，
    # 这里只需做「几乎逐字相同」的保守兜底。
    TITLE_JACCARD_THRESHOLD = 0.85

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
        """两个标题的字符 3-gram Jaccard 相似度（保留旧入口，内部委托模块函数）"""
        return title_jaccard(title_a, title_b)

    def load_recent_pool(self, db: Session) -> dict:
        """一次性载入近 N 天资讯的标题与指纹，供批量判重使用。

        逐条调用 find_duplicate 会反复扫描全表；沉淀一批全网检索结果时
        改为「载入一次、内存比对」，条数多时差别明显。

        Returns:
            {"titles": [(id, title), ...], "fingerprints": [(id, fp), ...]}
            查询失败（旧库未迁移）时返回空池，判重降级为「查不到重复」。
        """
        from models.news import IndustryNews

        since = datetime.utcnow() - timedelta(days=self.window_days)
        pool: dict = {"titles": [], "fingerprints": []}
        try:
            pool["titles"] = [
                (str(news_id), title)
                for news_id, title in db.query(IndustryNews.id, IndustryNews.title)
                .filter(
                    IndustryNews.collected_at >= since,
                    IndustryNews.title.isnot(None),
                )
                .order_by(IndustryNews.collected_at.desc())
                .limit(self.TITLE_COMPARE_LIMIT)
                .all()
            ]
            pool["fingerprints"] = [
                (str(news_id), fingerprint)
                for news_id, fingerprint in db.query(
                    IndustryNews.id, IndustryNews.content_fingerprint
                )
                .filter(
                    IndustryNews.content_fingerprint.isnot(None),
                    IndustryNews.collected_at >= since,
                )
                .all()
            ]
        except Exception as e:
            # 旧库未执行 03-news-dedup.sql 迁移时的降级处理
            if not self._migration_warned:
                logger.warning(
                    f"[dedup] 查询指纹列失败（可能未执行 docker/init-db/03-news-dedup.sql），"
                    f"降级为仅 URL 去重: {e}"
                )
                self._migration_warned = True
            return {"titles": [], "fingerprints": []}
        return pool

    def match_pool(
        self,
        pool: dict,
        fingerprint: str,
        title: str = "",
    ) -> Optional[str]:
        """在已载入的池子里判重，判定口径与 find_duplicate 完全一致。

        两级：标题 Jaccard >= 阈值（同事件改写）；SimHash 汉明距离 <= 阈值（转载微调）。
        """
        if not pool:
            return None

        normalized_title = (title or "").strip()
        if normalized_title:
            for news_id, existing_title in pool.get("titles") or []:
                if existing_title and titles_same_article(
                    normalized_title, existing_title, self.title_jaccard_threshold
                ):
                    return news_id

        if not fingerprint:
            return None
        try:
            fp_int = int(fingerprint, 16)
        except (ValueError, TypeError):
            return None
        for news_id, existing_fp in pool.get("fingerprints") or []:
            if not existing_fp:
                continue
            try:
                existing_int = int(existing_fp, 16)
            except (ValueError, TypeError):
                continue
            if bin(fp_int ^ existing_int).count("1") <= self.hamming_threshold:
                return news_id

        return None

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
        if not fingerprint:
            return None
        return self.match_pool(self.load_recent_pool(db), fingerprint, title)

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

    def backfill_fingerprints(self, db: Session, batch_size: int = 500) -> dict:
        """
        为历史数据补算指纹（迁移/升级后执行一次）。

        只处理 content_fingerprint 为空的记录；分批提交避免长事务。
        可重复执行（幂等）。
        """
        from models.news import IndustryNews

        filled = 0
        scanned = 0
        while True:
            rows = (
                db.query(IndustryNews)
                .filter(IndustryNews.content_fingerprint.is_(None))
                .limit(batch_size)
                .all()
            )
            if not rows:
                break
            for news in rows:
                news.content_fingerprint = self.compute_fingerprint(
                    news.title, news.content or ""
                )
                filled += 1
            db.commit()
            scanned += len(rows)
            logger.info(f"[backfill] 已补算 {scanned} 条指纹")
        return {"filled": filled}


# 模块级单例（避免重复实例化）
_dedup_service: Optional[NewsDedupService] = None


def get_dedup_service() -> NewsDedupService:
    global _dedup_service
    if _dedup_service is None:
        _dedup_service = NewsDedupService()
    return _dedup_service
