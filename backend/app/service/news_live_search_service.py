"""资讯全网实时检索服务

配合 news_search_service 使用：用户输入碎片时，除了查已入库资讯，
同时调用博查 web-search 实时搜全网，过滤低质来源后返回统一卡片，
并把通过的优质结果沉淀入库（下次即可被库内检索命中）。

为什么要它：
- 库内检索只能搜到「已采集入库」的资讯（数据量有限、且受固定采集关键词限制）；
- 用户要的是「输入碎片 -> 立刻得到相关报道」，不应依赖预先采集；
- 博查 web-search 本身就是全网资讯搜索，接上即得实时结果。

设计要点：
- 查询构造：短碎片直接作 query；较长碎片切出 token 再拼一个 query，最多 MAX_QUERIES 个；
- 并发抓取：线程池并发（同步 httpx，供同步调用链使用，避免在事件循环里再起 asyncio.run）；
- 质量过滤：复用 source_quality_service，入库之前就拦掉文库站 / 题库站 / 低质聚合；
- URL 去重：规范化 URL（去 fragment、去尾斜杠、host 小写）后去重。

对外用法：
    from service.news_live_search_service import get_news_live_search_service
    items = get_news_live_search_service().search("万华化学 MDI 检修", count=10)
"""
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from service.source_quality_service import get_source_quality_service

logger = logging.getLogger(__name__)

BOCHA_URL = "https://api.bochaai.com/v1/web-search"
# 单次查询条数（配合质量过滤做「多取少留」）
ONLINE_COUNT = int(os.getenv("NEWS_ONLINE_COUNT", "10"))
# 并发查询上限（每个查询一次博查请求；太多会拖长响应）
MAX_QUERIES = int(os.getenv("NEWS_ONLINE_MAX_QUERIES", "2"))
# 时间范围：默认近一个月，显著降低陈旧 SEO 文档占比
ONLINE_FRESHNESS = os.getenv("NEWS_ONLINE_FRESHNESS", "oneMonth")
# 单次请求超时（秒）
ONLINE_TIMEOUT = float(os.getenv("NEWS_ONLINE_TIMEOUT", "12"))
# 全网结果最多保留条数（避免淹没库内结果）
ONLINE_MAX_KEEP = int(os.getenv("NEWS_ONLINE_MAX_KEEP", "15"))

_SPLIT_RE = re.compile(r"[\s,，、;；/|]+")


def _normalize_url(url: str) -> str:
    """规范化 URL 用于去重：去 fragment、去尾斜杠、host 小写。"""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        host = (p.netloc or "").lower()
        path = p.path.rstrip("/") or "/"
        return urlunparse((p.scheme.lower(), host, path, p.params, p.query, ""))
    except Exception:
        return url.strip()


def _host_of(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def build_online_queries(fragment: str) -> List[str]:
    """把碎片文本转成 1..MAX_QUERIES 个博查查询。"""
    fragment = (fragment or "").strip()
    if not fragment:
        return []

    queries: List[str] = []
    # 短句（或短碎片）直接用原句，保留语义
    if len(fragment) <= 40:
        queries.append(fragment)
    # 长文本或带分隔符的碎片：切 token 拼一个关键词查询（关键词命中更准）
    tokens = [t for t in _SPLIT_RE.split(fragment) if len(t) >= 2]
    if tokens:
        joined = " ".join(tokens[:4])
        if joined and joined not in queries:
            queries.append(joined)

    # 去重且限流
    out: List[str] = []
    for q in queries:
        if q not in out:
            out.append(q)
    if not out:
        out = [fragment]
    return out[: max(1, MAX_QUERIES)]


class NewsLiveSearchService:
    """博查全网实时检索（同步实现）。"""

    def __init__(self):
        self.api_key = os.getenv("BOCHA_API_KEY", "")
        self.quality = get_source_quality_service()
        self._client: Optional[httpx.Client] = None

    def _client_or_new(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=ONLINE_TIMEOUT)
        return self._client

    # ---------- 博查调用 ----------

    def _bocha_once(self, query: str, count: int) -> List[Dict[str, Any]]:
        if not self.api_key:
            logger.warning("[live_search] BOCHA_API_KEY 未配置，跳过全网检索")
            return []

        payload: Dict[str, Any] = {
            "query": query,
            "summary": True,
            "count": max(1, count),
            "page": 1,
        }
        freshness = (ONLINE_FRESHNESS or "").strip()
        if freshness and freshness.lower() != "nolimit":
            payload["freshness"] = freshness
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._client_or_new().post(BOCHA_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[live_search] 博查请求失败 query='{query[:30]}': {e}")
            return []

        pages = (data.get("data") or {}).get("webPages") or {}
        values = pages.get("value")
        if not isinstance(values, list):
            return []

        out: List[Dict[str, Any]] = []
        for item in values:
            url = item.get("url") or ""
            if not url:
                continue
            summary = item.get("summary") or item.get("snippet") or ""
            if not (item.get("name") or summary):
                continue
            out.append(
                {
                    "url": url,
                    "title": (item.get("name") or "").strip(),
                    "summary": summary,
                    "site_name": item.get("siteName") or "",
                    "date_published": item.get("datePublished") or "",
                }
            )
        return out

    # ---------- 对外 ----------

    def search(self, fragment: str, count: int = ONLINE_COUNT) -> List[Dict[str, Any]]:
        """全网实时检索，返回过滤后的统一卡片列表（未入库）。

        每项字段：title / summary / source / source_url / publish_time /
                 host / tier / quality_score / match_reason
        """
        queries = build_online_queries(fragment)
        if not queries:
            return []

        # 多查询并发（线程池），避免串行等待
        raw: List[Dict[str, Any]] = []
        try:
            with ThreadPoolExecutor(max_workers=len(queries)) as pool:
                for part in pool.map(lambda q: self._bocha_once(q, count), queries):
                    raw.extend(part)
        except Exception as e:
            logger.warning(f"[live_search] 并发检索异常，退回串行: {e}")
            for q in queries:
                raw.extend(self._bocha_once(q, count))

        # 去重（URL）+ 质量过滤
        seen: set = set()
        items: List[Dict[str, Any]] = []
        for r in raw:
            key = _normalize_url(r["url"])
            if not key or key in seen:
                continue
            seen.add(key)

            verdict = self.quality.check(r["url"], title=r["title"])
            if not verdict.accepted:
                logger.debug(
                    f"[live_search] 丢弃低质来源({verdict.reason}): {r['title'][:40]}"
                )
                continue

            items.append(
                {
                    "title": r["title"],
                    "summary": r["summary"],
                    "content": r["summary"],
                    "source": r["site_name"] or _host_of(r["url"]),
                    "source_url": r["url"],
                    "publish_time": self._parse_time(r["date_published"]),
                    "host": _host_of(r["url"]),
                    "tier": verdict.tier,
                    "quality_score": verdict.score,
                }
            )
            if len(items) >= ONLINE_MAX_KEEP:
                break

        logger.info(
            f"[live_search] fragments={queries} 原始 {len(raw)} 条 -> 过滤保留 {len(items)} 条"
        )
        return items

    @staticmethod
    def _parse_time(value: str) -> Optional[str]:
        """博查的 datePublished 常见形如 '2026-10-08T10:00:00+08:00' 或 '2026-10-08'。"""
        if not value:
            return None
        v = value.strip()
        try:
            from dateutil import parser as dtparser

            return dtparser.parse(v).isoformat()
        except Exception:
            return None


_news_live_search_service: Optional[NewsLiveSearchService] = None


def get_news_live_search_service() -> NewsLiveSearchService:
    global _news_live_search_service
    if _news_live_search_service is None:
        _news_live_search_service = NewsLiveSearchService()
    return _news_live_search_service
