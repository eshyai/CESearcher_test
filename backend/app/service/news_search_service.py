"""资讯检索服务 - 「碎片输入 -> 相关报道」

检索策略（混合）：
1. 向量召回：把碎片文本 embedding 后在 Milvus 里按 COSINE 召回（主管「语义相近」，
   能处理"描述性片段"和同义改写，比如 "装置停车" 命中 "停产检修"）。
2. 关键词召回：Postgres ILIKE 命中 title/summary/content/entities（主管"精确名字"，
   比如公司名、产品名、政策文号；向量对专有名词不够敏感）。
3. 合并去重后，用 DashScope Rerank 对候选做精细重排（rerank 失败则退回融合分）。

向量索引不存在时自动降级为纯关键词检索，接口不会报错。
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from config.industry_config import DEFAULT_INDUSTRY_ID
from models.news import IndustryNews
from service.news_index_service import get_news_index_service

logger = logging.getLogger(__name__)

# 关键词召回时对候选的上限（再大 rerank 也没收益）
KEYWORD_CANDIDATES = 40
VECTOR_CANDIDATES = 40
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
    """rerank 用的文档文本：标题 + 摘要（够了，正文太长反而稀释信号）。"""
    return "\n".join(p for p in [news.title or "", news.summary or ""] if p)


class NewsSearchService:
    def __init__(self, db: Session):
        self.db = db
        self.index = get_news_index_service()
        self._rerank_model: Optional[str] = None  # 探测到的可用 rerank 模型，进程内复用

    # ---------- 对外 ----------

    def search(
        self,
        query: str,
        industry_id: Optional[str] = None,
        event_type: Optional[str] = None,
        impact_direction: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        industry_id = industry_id or DEFAULT_INDUSTRY_ID
        if not query:
            return {"query": query, "mode": "empty", "vector_indexed": False, "total": 0, "data": []}

        candidates: Dict[str, Dict[str, Any]] = {}

        # ---- 1) 向量召回 ----
        vector_indexed = True
        try:
            vec_hits = self.index.search(
                industry_id, query, top_k=VECTOR_CANDIDATES, event_type=event_type
            )
        except Exception as e:
            logger.warning(f"[news_search] 向量召回失败，降级为关键词: {e}")
            vec_hits = []
            vector_indexed = False
        for nid, score in vec_hits:
            candidates.setdefault(nid, {})["vector"] = score

        # ---- 2) 关键词召回 ----
        for nid in self._keyword_search(
            query, industry_id, event_type, impact_direction, KEYWORD_CANDIDATES
        ):
            candidates.setdefault(nid, {})["keyword"] = True
            vector_indexed = vector_indexed and True  # 保持标记

        if not candidates:
            return {
                "query": query,
                "mode": "hybrid" if vector_indexed else "keyword",
                "vector_indexed": vector_indexed,
                "total": 0,
                "data": [],
            }

        # ---- 3) 取回完整记录并做后置过滤 ----
        ids = list(candidates.keys())
        rows = self.db.query(IndustryNews).filter(IndustryNews.id.in_(ids)).all()
        rows = [
            r for r in rows
            if (not industry_id or r.industry_id == industry_id)
            and (not event_type or r.event_type == event_type)
            and (not impact_direction or r.impact_direction == impact_direction)
        ]
        by_id = {str(r.id): r for r in rows}

        # ---- 4) 重排 ----
        ranked = self._rerank(query, rows)
        used_rerank = ranked is not None
        if not used_rerank:
            # 退回融合分：向量分 + 关键词加成
            ranked = []
            for nid, meta in candidates.items():
                row = by_id.get(nid)
                if not row:
                    continue
                base = float(meta.get("vector", 0.0))
                if meta.get("keyword"):
                    base += 0.25
                ranked.append((row, base))
            ranked.sort(key=lambda x: x[1], reverse=True)
            mode = "hybrid" if vec_hits else ("keyword" if not vector_indexed else "hybrid")
        else:
            mode = "rerank"

        # ---- 5) 组装结果（按相关度阈值过滤）----
        # 仅 rerank 成功时启用阈值（量纲问题见 MIN_SCORE 注释）；
        # 关键词精确命中的结果无论分数高低一律保留（精确匹配是强信号）。
        min_score = MIN_SCORE if used_rerank else 0.0
        data: List[Dict[str, Any]] = []
        for row, score in ranked:
            nid = str(row.id)
            meta = candidates.get(nid, {})
            s = float(score)
            if s < min_score and not meta.get("keyword"):
                continue
            item = row.to_dict()
            item["score"] = round(s, 4)
            item["match_reason"] = self._reason(meta)
            data.append(item)
            if len(data) >= max(1, limit):
                break

        return {
            "query": query,
            "mode": mode,
            "vector_indexed": vector_indexed,
            "total": len(data),
            "data": data,
        }

    # ---------- 内部 ----------

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

    def _rerank(
        self, query: str, rows: List[IndustryNews]
    ) -> Optional[List[Tuple[IndustryNews, float]]]:
        """DashScope 重排。任何异常都返回 None，由调用方退回融合分。

        按 RERANK_MODELS 顺序尝试；一旦某个模型成功，记入 self._rerank_model，
        后续请求直接复用，避免每次都去撞已失效的模型。
        """
        if not rows:
            return []
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return None

        models = [self._rerank_model] if self._rerank_model else RERANK_MODELS
        try:
            import dashscope
            from http import HTTPStatus

            dashscope.api_key = api_key
            texts = [_doc_text(r) or (r.title or "") for r in rows]

            for model in models:
                if not model:
                    continue
                try:
                    resp = dashscope.TextReRank.call(
                        model=model,
                        query=query,
                        documents=texts,
                        top_n=len(texts),
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
                out: List[Tuple[IndustryNews, float]] = []
                for it in resp.output.results:
                    idx = it.get("index") if isinstance(it, dict) else it.index
                    score = it.get("relevance_score") if isinstance(it, dict) else it.relevance_score
                    out.append((rows[idx], float(score)))
                return out
            logger.warning("[news_search] 所有 rerank 模型均不可用，退回融合分")
            return None
        except Exception as e:
            logger.warning(f"[news_search] rerank 异常，退回融合分: {e}")
            return None

    @staticmethod
    def _reason(meta: Dict[str, Any]) -> str:
        has_kw = bool(meta.get("keyword"))
        has_vec = "vector" in meta
        if has_kw and has_vec:
            return "关键词 + 语义相关"
        if has_kw:
            return "关键词命中"
        return "语义相关"


_news_search_service = None


def get_news_search_service(db: Session) -> NewsSearchService:
    return NewsSearchService(db)
