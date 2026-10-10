"""资讯检索服务 - 「碎片输入 -> 相关报道」

检索策略（库内 + 全网混合）：
1. 向量召回：把碎片文本 embedding 后在 Milvus 里按 COSINE 召回（主管「语义相近」，
   能处理"描述性片段"和同义改写，比如 "装置停车" 命中 "停产检修"）。
2. 关键词召回：Postgres ILIKE 命中 title/summary/content/entities（主管"精确名字"，
   比如公司名、产品名、政策文号；向量对专有名词不够敏感）。
3. 全网实时：调用 news_live_search_service 走博查 web-search 补充"库里没有"的资讯，
   过滤低质来源后与库内候选一起重排——这样"一个搜索"即可完成，不依赖预先采集。
4. 合并去重后，用 DashScope Rerank 对全部候选做精细重排（rerank 失败则退回融合分）。
5. 通过阈值的全网结果会**沉淀入库 + 建向量索引**，下次搜索即可被库内命中（库随使用自增长）。

向量索引不存在时自动降级为纯关键词检索，接口不会报错。
"""
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from config.industry_config import DEFAULT_INDUSTRY_ID
from models.news import IndustryNews
from service.dedup_service import normalize_title, titles_same_article
from service.industry_relevance_service import get_industry_relevance_service
from service.news_index_service import get_news_index_service
from service.news_live_search_service import get_news_live_search_service

logger = logging.getLogger(__name__)

# 关键词召回时对候选的上限（再大 rerank 也没收益）
KEYWORD_CANDIDATES = 40
VECTOR_CANDIDATES = 40
# rerank 单次可提交的文档上限（库内 + 全网合计，超出则截断）
RERANK_MAX_DOCS = int(os.getenv("NEWS_RERANK_MAX_DOCS", "50"))
# rerank 模型候选链：按序尝试，前一个不可用（403/未开通）时自动换下一个。
# 注意 gte-rerank 是旧名，在当前网关已 403，故排在后面仅作兜底。
RERANK_MODELS = [
    m.strip()
    for m in os.getenv("NEWS_RERANK_MODEL", "gte-rerank-v2,qwen3-rerank").split(",")
    if m.strip()
]
# 相关度下限：rerank 分数量纲稳定（实测无关查询≈0.006，相关查询≥0.13），
# 0.05 能干净地滤掉"完全无关"的结果，避免"输入任何内容都返回一堆报道"。
# 仅 rerank 成功时启用（向量/cosine 分数量纲不同，不适用）；关键词精确命中不受此限。
MIN_SCORE = float(os.getenv("NEWS_SEARCH_MIN_SCORE", "0.05"))

# 标题去重开关：同一条资讯常同时出现在库内与全网（不同站点、不同 URL），
# 不处理的话同一条会重复占多个结果位。默认开启。
TITLE_DEDUP_ENABLED = os.getenv("NEWS_SEARCH_TITLE_DEDUP", "1").lower() not in (
    "0", "false", "no",
)
# 整体相似度阈值，仅作保守兜底（门户转载已由「主干 + 站点后缀」规则覆盖）。
# 实测不能定低：「PA6巴斯夫…塑胶颗粒」vs「PA66巴斯夫…尼龙颗粒」=0.725、
# 「己内酰胺市场价格走势分析-2026年9月」vs「…-2026年8月」=0.789，
# 定低会误杀不同牌号/不同月份的同系列内容。想更激进可调低此环境变量。
TITLE_DEDUP_JACCARD = float(os.getenv("NEWS_TITLE_DEDUP_JACCARD", "0.85"))
_SPLIT_RE = re.compile(r"[\s,，、;；/|]+")


def _escape_like(token: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % _ 变成通配。"""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tokens(query: str) -> List[str]:
    """把碎片文本切成关键词 token。保留整句（短句）+ 分隔符切出的片段。"""
    query = query.strip()
    tokens: List[str] = []
    if query and len(query) <= 40:
        tokens.append(query)
    for t in _SPLIT_RE.split(query):
        t = t.strip()
        if len(t) >= 2 and t not in tokens:
            tokens.append(t)
    return tokens


def _doc_text(news: IndustryNews) -> str:
    """rerank 用的库内文档文本：标题 + 摘要（够了，正文太长反而稀释信号）。"""
    return "\n".join(p for p in [news.title or "", news.summary or ""] if p)


def _online_text(item: Dict[str, Any]) -> str:
    """rerank 用的全网文档文本：标题 + 摘要。"""
    return "\n".join(p for p in [item.get("title") or "", item.get("summary") or ""] if p)


def _token_hit(query: str, text: str) -> bool:
    """文本里是否出现 query 的任一 token（小写包含，够用）。"""
    if not text:
        return False
    low = text.lower()
    return any(t.lower() in low for t in _tokens(query))


def _parse_iso(value: Any) -> Optional[datetime]:
    """把 ISO 字符串解析为 datetime；已是 datetime 则原样返回。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        from dateutil import parser as dtparser

        return dtparser.parse(str(value))
    except Exception:
        return None


class NewsSearchService:
    def __init__(self, db: Session):
        self.db = db
        self.index = get_news_index_service()
        self.live = get_news_live_search_service()
        self.relevance = get_industry_relevance_service()
        self._rerank_model: Optional[str] = None  # 探测到的可用 rerank 模型，进程内复用

    # ---------- 对外 ----------

    def search(
        self,
        query: str,
        industry_id: Optional[str] = None,
        event_type: Optional[str] = None,
        impact_direction: Optional[str] = None,
        limit: int = 20,
        include_online: bool = True,
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        industry_id = industry_id or DEFAULT_INDUSTRY_ID
        if not query:
            return {
                "query": query, "mode": "empty", "vector_indexed": False, "total": 0,
                "online_total": 0, "online_kept": 0, "persisted": 0, "data": [],
            }

        candidates: Dict[str, Dict[str, Any]] = {}

        # ---- 1) 库内向量召回 ----
        vector_indexed = True
        vec_hits: List[Tuple[str, float]] = []
        try:
            vec_hits = self.index.search(
                industry_id, query, top_k=VECTOR_CANDIDATES, event_type=event_type
            )
        except Exception as e:
            logger.warning(f"[news_search] 向量召回失败，降级为关键词: {e}")
            vector_indexed = False
        for nid, score in vec_hits:
            candidates.setdefault(nid, {})["vector"] = score

        # ---- 2) 库内关键词召回 ----
        for nid in self._keyword_search(
            query, industry_id, event_type, impact_direction, KEYWORD_CANDIDATES
        ):
            candidates.setdefault(nid, {})["keyword"] = True

        rows: List[IndustryNews] = []
        if candidates:
            ids = list(candidates.keys())
            fetched = self.db.query(IndustryNews).filter(IndustryNews.id.in_(ids)).all()
            rows = [
                r for r in fetched
                if (not industry_id or r.industry_id == industry_id)
                and (not event_type or r.event_type == event_type)
                and (not impact_direction or r.impact_direction == impact_direction)
            ]
            # 让向量分高的排前面：标题去重时同标题的库内条目会保留靠前的那条。
            rows.sort(
                key=lambda r: float(candidates.get(str(r.id), {}).get("vector", 0.0)),
                reverse=True,
            )

        # ---- 3) 全网实时检索（补库里没有的）----
        online_items: List[Dict[str, Any]] = []
        if include_online:
            try:
                online_items = self.live.search(query)
            except Exception as e:
                logger.warning(f"[news_search] 全网实时检索失败，仅返回库内结果: {e}")
                online_items = []

        # ---- 4) 统一重排（库内 + 全网一起排）----
        meta: List[Tuple[str, Any, bool]] = []  # (kind, obj, keyword_hit)
        for r in rows:
            meta.append(("lib", r, bool(candidates.get(str(r.id), {}).get("keyword"))))
        for it in online_items:
            meta.append(("web", it, _token_hit(query, _online_text(it))))

        # ---- 4a) 标题去重 ----
        # 同一条资讯往往同时命中库内与全网（不同站点/不同 URL），标题归一化后
        # 相同即视为同一条。这一步放在 rerank 之前，既避免同一条占满结果位，
        # 也避免它浪费 RERANK_MAX_DOCS 的配额。
        total_before_dedup = len(meta)
        meta = self._dedupe_candidates(meta)
        title_deduped = total_before_dedup - len(meta)

        docs: List[str] = [
            ((_doc_text(obj) or (obj.title or "")) if kind == "lib" else _online_text(obj))
            for kind, obj, _kw in meta
        ]

        if len(docs) > RERANK_MAX_DOCS:
            docs = docs[:RERANK_MAX_DOCS]
            meta = meta[:RERANK_MAX_DOCS]

        ranked = self._rerank(query, docs) if docs else []
        used_rerank = ranked is not None

        scored: List[Tuple[str, Any, float, bool]] = []
        if used_rerank:
            for idx, score in ranked:
                if 0 <= idx < len(meta):
                    kind, obj, kw = meta[idx]
                    scored.append((kind, obj, float(score), kw))
            mode = "rerank"
        else:
            for kind, obj, kw in meta:
                if kind == "lib":
                    m = candidates.get(str(obj.id), {})
                    s = float(m.get("vector", 0.0)) + (0.25 if m.get("keyword") else 0.0)
                else:
                    s = 0.0  # 无 rerank 时全网结果仅靠关键词命中保留
                scored.append((kind, obj, s, kw))
            mode = "hybrid" if vec_hits else ("keyword" if not vector_indexed else "hybrid")

        scored.sort(key=lambda x: x[2], reverse=True)

        # ---- 5a) 阈值过滤 ----
        # 阈值只挡"低分的非精确命中"；关键词精确命中是强信号，一律保留
        min_score = MIN_SCORE if used_rerank else 0.0
        kept = [
            (kind, obj, s, kw)
            for kind, obj, s, kw in scored
            if not (s < min_score and not kw)
        ]

        # ---- 5b) 行业相关性闸 ----
        # 阈值与 rerank 只保证"与查询词相关"，不保证"属于本行业"。检索泛词
        # （实测如「收购」）时会把医美收购、B2B 买卖、股票行情页等高相关度但
        # 非本行业的内容放行，并被 _persist_online 永久沉淀进资讯库。
        # 这里在组装与沉淀之前统一过一道闸：库内与全网候选一起判定，
        # 不合格的直接剔除（既不展示、也不入库）。
        gate_items = [
            {
                "title": (obj.title or "") if kind == "lib" else (obj.get("title") or ""),
                "summary": (obj.summary or "") if kind == "lib" else (obj.get("summary") or ""),
            }
            for kind, obj, s, kw in kept
        ]
        try:
            keep_idx, irrelevant = self.relevance.filter_relevant(gate_items, industry_id)
        except Exception as e:
            logger.warning(f"[news_search] 行业相关性判定异常，跳过该闸: {e}")
            keep_idx, irrelevant = list(range(len(kept))), []
        keep_set = set(keep_idx)

        # ---- 6) 组装结果 ----
        data: List[Dict[str, Any]] = []
        web_kept: List[Dict[str, Any]] = []
        for pos, (kind, obj, s, kw) in enumerate(kept):
            if pos not in keep_set:
                continue
            if kind == "lib":
                item = obj.to_dict()
                item["source_type"] = "library"
                has_vec = "vector" in candidates.get(str(obj.id), {})
                item["match_reason"] = (
                    "关键词 + 语义相关" if (kw and has_vec) else ("关键词命中" if kw else "语义相关")
                )
            else:
                item = dict(obj)
                item["source_type"] = "web"
                item["match_reason"] = "全网实时"
            item["score"] = round(float(s), 4)
            data.append(item)
            if kind == "web":
                web_kept.append(item)
            if len(data) >= max(1, limit):
                break

        # ---- 7) 沉淀：把保留的全网结果入库 + 建索引（下次可被库内命中）----
        # 注意：web_kept 只包含通过行业相关性闸的结果，因此与本行业无关的内容
        # 不会写进资讯库。
        persisted = 0
        if web_kept and include_online:
            mapping, persisted = self._persist_online(web_kept, industry_id)
            for item in data:
                if item.get("source_type") == "web":
                    nid = mapping.get(item.get("source_url") or "")
                    if nid:
                        item["id"] = nid

        return {
            "query": query,
            "mode": mode,
            "vector_indexed": vector_indexed,
            "total": len(data),
            "online_total": len(online_items),
            "online_kept": len(web_kept),
            "persisted": persisted,
            "irrelevant_filtered": len(irrelevant),
            "irrelevant": irrelevant[:10],
            "title_deduped": title_deduped,
            "data": data,
        }

    # ---------- 内部 ----------

    @staticmethod
    def _candidate_title(kind: str, obj: Any) -> str:
        return (obj.title or "") if kind == "lib" else (obj.get("title") or "")

    def _dedupe_candidates(
        self, meta: List[Tuple[str, Any, bool]]
    ) -> List[Tuple[str, Any, bool]]:
        """把标题指向同一篇资讯的候选合并成一条。

        判定口径与入库判重共用（service.dedup_service.titles_same_article）：
        ① 归一化标题完全相同；② 主干相同且带站点后缀（门户转载）；③ 整体相似度达标。

        保留优先级：
        1. 库内 > 全网 —— 库内条目有 id 能承载反馈，且已完成结构化抽取；
        2. 同源之间保持原有先后（库内已按向量分降序，全网为博查原始排序）。
        """
        if not TITLE_DEDUP_ENABLED or len(meta) < 2:
            return meta

        kept: List[Tuple[str, Any, bool]] = []
        kept_titles: List[str] = []
        for item in meta:
            kind, obj, _kw = item
            raw_title = self._candidate_title(kind, obj)
            if not normalize_title(raw_title):
                kept.append(item)
                continue
            if any(
                titles_same_article(raw_title, seen, TITLE_DEDUP_JACCARD)
                for seen in kept_titles
            ):
                continue
            kept_titles.append(raw_title)
            kept.append(item)
        return kept

    def _keyword_search(
        self,
        query: str,
        industry_id: Optional[str],
        event_type: Optional[str],
        impact_direction: Optional[str],
        limit: int,
    ) -> List[str]:
        tokens = _tokens(query)
        if not tokens:
            return []
        conds = []
        for t in tokens:
            like = f"%{_escape_like(t)}%"
            conds.append(
                or_(
                    IndustryNews.title.ilike(like, escape="\\"),
                    IndustryNews.summary.ilike(like, escape="\\"),
                    IndustryNews.content.ilike(like, escape="\\"),
                    IndustryNews.entities.ilike(like, escape="\\"),
                )
            )
        q = self.db.query(IndustryNews.id).filter(or_(*conds))
        if industry_id:
            q = q.filter(IndustryNews.industry_id == industry_id)
        if event_type:
            q = q.filter(IndustryNews.event_type == event_type)
        if impact_direction:
            q = q.filter(IndustryNews.impact_direction == impact_direction)
        q = q.order_by(
            IndustryNews.publish_time.desc().nullslast(),
            IndustryNews.collected_at.desc(),
        ).limit(limit)
        return [str(r[0]) for r in q.all()]

    def _rerank(self, query: str, docs: List[str]) -> Optional[List[Tuple[int, float]]]:
        """DashScope 重排。返回 [(doc_index, score), ...]（按分数降序）；失败返回 None。

        按 RERANK_MODELS 顺序尝试；一旦某个模型成功，记入 self._rerank_model，
        后续请求直接复用，避免每次都去撞已失效的模型。
        """
        if not docs:
            return []
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return None

        models = [self._rerank_model] if self._rerank_model else RERANK_MODELS
        try:
            import dashscope
            from http import HTTPStatus

            dashscope.api_key = api_key
            for model in models:
                if not model:
                    continue
                try:
                    resp = dashscope.TextReRank.call(
                        model=model,
                        query=query,
                        documents=docs,
                        top_n=len(docs),
                        return_documents=False,
                    )
                except Exception as e:
                    logger.warning(f"[news_search] rerank 模型 {model} 异常: {e}")
                    continue
                if resp.status_code != HTTPStatus.OK:
                    logger.warning(
                        f"[news_search] rerank 模型 {model} 不可用: "
                        f"{resp.status_code} {getattr(resp, 'message', '')}"
                    )
                    continue
                self._rerank_model = model  # 记住可用模型
                out: List[Tuple[int, float]] = []
                for it in resp.output.results:
                    idx = it.get("index") if isinstance(it, dict) else it.index
                    score = it.get("relevance_score") if isinstance(it, dict) else it.relevance_score
                    out.append((int(idx), float(score)))
                out.sort(key=lambda x: x[1], reverse=True)
                return out
            logger.warning("[news_search] 所有 rerank 模型均不可用，退回融合分")
            return None
        except Exception as e:
            logger.warning(f"[news_search] rerank 异常，退回融合分: {e}")
            return None

    def _persist_online(
        self, items: List[Dict[str, Any]], industry_id: str
    ) -> Tuple[Dict[str, str], int]:
        """把全网检索保留的结果沉淀入库并建索引。

        Returns:
            (url -> news_id 映射, 新入库条数)。任何异常都不抛出，仅记录日志，
            保证沉淀失败不影响本次检索返回。
        """
        if not items:
            return {}, 0
        try:
            from service.dedup_service import get_dedup_service

            dedup = get_dedup_service()
        except Exception:
            dedup = None

        mapping: Dict[str, str] = {}
        created: List[IndustryNews] = []
        skipped_duplicate = 0
        # 判重池只载入一次：逐条调用 find_duplicate 会把近 2000 条标题反复扫一遍。
        pool = None
        if dedup is not None:
            try:
                pool = dedup.load_recent_pool(self.db)
            except Exception as e:
                logger.warning(f"[news_search] 载入去重池失败，本轮仅按 URL 判重: {e}")
                pool = None

        for it in items:
            url = it.get("source_url") or ""
            if not url:
                continue
            existing = (
                self.db.query(IndustryNews.id)
                .filter(IndustryNews.source_url == url)
                .first()
            )
            if existing:
                mapping[url] = str(existing[0])
                continue

            title = (it.get("title") or "").strip() or "无标题"
            content = it.get("content") or it.get("summary") or ""
            fp = None
            if dedup is not None:
                try:
                    fp = dedup.compute_fingerprint(title, content)
                except Exception:
                    fp = None
                # 库内已经存在同一条（换了个站点/URL 又搜到）：只建立关联，不重复入库，
                # 否则同一篇文章会以不同 URL 反复沉淀，检索时重复出现。
                if pool is not None:
                    try:
                        dup_id = dedup.match_pool(pool, fp, title)
                    except Exception:
                        dup_id = None
                    if dup_id:
                        mapping[url] = dup_id
                        skipped_duplicate += 1
                        continue
            row = IndustryNews(
                industry_id=industry_id,
                title=title[:500],
                content=content,
                source=(it.get("source") or "")[:200],
                source_url=url,
                category="新闻",
                publish_time=_parse_iso(it.get("publish_time")),
                content_fingerprint=fp,
                extraction_status="pending",
                collected_at=datetime.utcnow(),
            )
            self.db.add(row)
            created.append(row)

        if not created:
            if skipped_duplicate:
                logger.info(
                    f"[news_search] 全网结果 {skipped_duplicate} 条与库内已有资讯重复，未重复入库"
                )
            return mapping, 0

        try:
            self.db.commit()
            for r in created:
                self.db.refresh(r)
                mapping[r.source_url] = str(r.id)
            self.index.index_news(created, industry_id=industry_id)
            extra = f"（另有 {skipped_duplicate} 条与库内重复已跳过）" if skipped_duplicate else ""
            logger.info(
                f"[news_search] 已沉淀入库并建索引 {len(created)} 条全网资讯{extra}"
            )
        except Exception as e:
            self.db.rollback()
            logger.warning(f"[news_search] 沉淀入库失败（不影响本次返回）: {e}")
            return mapping, 0
        return mapping, len(created)


def get_news_search_service(db: Session) -> NewsSearchService:
    return NewsSearchService(db)
