"""资讯向量索引服务

把已入库/已抽取的化工资讯向量化写入 Milvus，供「碎片输入 -> 相关报道」的语义检索使用。

为什么单独一个服务：
- milvus_service 的集合 schema 是为知识库文档写死的（doc_id/kb_id/filename/chunk_index），
  资讯的字段完全不同，直接复用会把两套数据混在一个 schema 里。这里另起一个集合前缀
  news_{industry_id}，互不干扰，不动现有知识库链路。

对外用法：
    from service.news_index_service import get_news_index_service
    svc = get_news_index_service()
    svc.index_news(news_list, industry_id="chemical")         # 增量写入（按 news.id upsert，幂等）
    svc.reindex_industry(db, industry_id="chemical")          # 全量重建
    hits = svc.search("chemical", "环氧丙烷 检修", top_k=30)   # 返回 [(news_id, score), ...]
"""
import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)
from sqlalchemy.orm import Session

from config.industry_config import DEFAULT_INDUSTRY_ID
from models.news import IndustryNews

# Milvus 是「增强」而非「必需」：它不可用时检索应退化为关键词召回，而不是把主入口拖死。
# 超时保护统一收口在 milvus_guard，这里重新导出以保持既有 import 路径可用。
from service.milvus_guard import (  # noqa: F401  (MILVUS_OP_TIMEOUT / MilvusUnavailableError 对外兼容)
    MILVUS_OP_TIMEOUT,
    MilvusUnavailableError,
    safe_load,
)

logger = logging.getLogger(__name__)

VECTOR_DIM = 1024                              # 与 text-embedding-v4 默认维度一致
EMBED_MODEL = os.getenv("NEWS_EMBED_MODEL", "text-embedding-v4")
EMBED_MAX_CHARS = int(os.getenv("NEWS_EMBED_MAX_CHARS", "1200"))  # 送 embedding 的正文截断
EMBED_BATCH = 10                               # 阿里 embedding 单次批量上限
COLLECTION_PREFIX = "news_"


def collection_name(industry_id: Optional[str]) -> str:
    """资讯集合名：news_{industry_id}。"""
    ind = (industry_id or DEFAULT_INDUSTRY_ID or "all").strip().lower().replace(" ", "_")
    return f"{COLLECTION_PREFIX}{ind}"


def build_news_text(news: IndustryNews) -> str:
    """拼出用于 embedding 的文本：标题 + 摘要 + 实体 + 正文截断。

    标题与摘要权重最高（放前面），实体（公司/产品/机构）能显著提升碎片命中的召回。
    """
    parts: List[str] = []
    if news.title:
        parts.append(news.title.strip())
    if news.summary:
        parts.append(news.summary.strip())
    if news.entities:
        try:
            import json

            ents = json.loads(news.entities)
            if isinstance(ents, list):
                names = [
                    e if isinstance(e, str) else str(e.get("name") or e.get("text") or "")
                    for e in ents
                ]
                names = [n for n in names if n]
                if names:
                    parts.append("相关主体：" + "、".join(names))
        except (ValueError, TypeError, AttributeError):
            pass
    if news.content:
        parts.append(news.content.strip()[:EMBED_MAX_CHARS])
    text = "\n".join(p for p in parts if p)
    # 兜底：至少要有标题（title 非空约束，但防脏数据）
    return text or (news.title or "")


class NewsIndexService:
    """资讯向量索引服务（Milvus）。"""

    def __init__(self):
        self.host = os.getenv("MILVUS_HOST", "localhost")
        self.port = int(os.getenv("MILVUS_PORT", "19530"))
        self._connect()

    # ---------- 连接与集合 ----------

    def _connect(self) -> None:
        try:
            connections.connect(alias="default", host=self.host, port=self.port)
            logger.info(f"[news_index] 已连接 Milvus {self.host}:{self.port}")
        except Exception as e:  # pragma: no cover - 环境相关
            logger.error(f"[news_index] 连接 Milvus 失败: {e}")
            raise

    @staticmethod
    def _load(collection: Collection, name: str) -> None:
        """带超时地加载集合（转发到 milvus_guard.safe_load）。

        必须显式给 timeout：pymilvus 的 ``load()`` 默认**无限等待**，Milvus 侧异常时
        整条链路会一直挂着而不是抛错，上层已有的降级分支就永远等不到。
        """
        safe_load(collection, name)

    def ensure_collection(self, name: str) -> Collection:
        """取得（必要时创建）资讯集合。"""
        if utility.has_collection(name):
            collection = Collection(name)
            self._load(collection, name)
            return collection

        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="industry_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="source_url", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="event_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="impact_direction", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="publish_time", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
        ]
        schema = CollectionSchema(fields=fields, description=f"Industry news index: {name}")
        collection = Collection(name=name, schema=schema)
        # AUTOINDEX：小数据量也能建索引，不需要 IVF 训练，避免初期 nlist 训练告警
        collection.create_index(
            field_name="vector",
            index_params={"metric_type": "COSINE", "index_type": "AUTOINDEX"},
        )
        self._load(collection, name)
        logger.info(f"[news_index] 集合 {name} 创建成功")
        return collection

    # ---------- 写入 ----------

    def _embed(self, texts: Sequence[str]) -> List[Optional[List[float]]]:
        from service.embedding_service import generate_embedding

        if len(texts) == 1:
            vec = generate_embedding(texts[0], model_name=EMBED_MODEL, dimensions=VECTOR_DIM)
            return [vec]
        return generate_embedding(
            list(texts), model_name=EMBED_MODEL, dimensions=VECTOR_DIM, max_batch_size=EMBED_BATCH
        )

    def _row(self, news: IndustryNews, industry_id: str, vector: List[float]) -> Dict[str, Any]:
        return {
            "id": str(news.id),
            "industry_id": industry_id or "",
            "title": (news.title or "")[:1024],
            "summary": (news.summary or "")[:1024],
            "source": (news.source or "")[:256],
            "source_url": (news.source_url or "")[:2048],
            "event_type": (news.event_type or "")[:64],
            "impact_direction": (news.impact_direction or "")[:32],
            "publish_time": news.publish_time.isoformat() if news.publish_time else "",
            "vector": vector,
        }

    def index_news(
        self,
        news_list: Iterable[IndustryNews],
        industry_id: Optional[str] = None,
        skip_embedded: bool = True,
    ) -> int:
        """把资讯写入向量索引（按 news.id upsert，可重复调用）。

        Args:
            news_list: IndustryNews 可迭代
            industry_id: 覆盖行业（默认取每条自身的 industry_id）
            skip_embedded: 跳过没有标题的脏数据

        Returns:
            成功写入的条数
        """
        items = [n for n in news_list if (n.title or "").strip() or not skip_embedded]
        if not items:
            return 0

        # 按行业分组（不同行业进不同集合）
        grouped: Dict[str, List[IndustryNews]] = {}
        for n in items:
            key = industry_id or n.industry_id or DEFAULT_INDUSTRY_ID
            grouped.setdefault(key, []).append(n)

        total = 0
        for ind, group in grouped.items():
            collection = self.ensure_collection(collection_name(ind))
            texts = [build_news_text(n) for n in group]

            vectors: List[Optional[List[float]]] = []
            for i in range(0, len(texts), EMBED_BATCH):
                vectors.extend(self._embed(texts[i:i + EMBED_BATCH]))

            rows = [
                self._row(n, ind, v)
                for n, v in zip(group, vectors)
                if v
            ]
            if not rows:
                logger.warning(f"[news_index] {ind} 本次无有效向量，跳过写入")
                continue

            collection.upsert(rows)
            collection.flush()
            total += len(rows)
            logger.info(f"[news_index] {ind} 写入/更新 {len(rows)} 条（集合 {collection_name(ind)}）")

        return total

    def reindex_industry(
        self, db: Session, industry_id: Optional[str] = None, batch: int = 50
    ) -> Dict[str, Any]:
        """全量重建某行业的资讯索引（先 drop 再建，避免残留脏向量）。"""
        query = db.query(IndustryNews)
        if industry_id:
            query = query.filter(IndustryNews.industry_id == industry_id)
        rows = query.all()
        if not rows:
            return {"industry_id": industry_id, "indexed": 0, "collections": []}

        collections = sorted({(n.industry_id or DEFAULT_INDUSTRY_ID) for n in rows})
        for ind in collections:
            name = collection_name(ind)
            if utility.has_collection(name):
                utility.drop_collection(name)
                logger.info(f"[news_index] 已清空旧集合 {name}")

        indexed = 0
        for i in range(0, len(rows), batch):
            indexed += self.index_news(rows[i:i + batch])
        return {"industry_id": industry_id, "indexed": indexed, "collections": collections}

    # ---------- 增量补齐 ----------

    def existing_ids(self, industry_id: str) -> set:
        """集合里已索引的 news id 集合。"""
        name = collection_name(industry_id)
        if not utility.has_collection(name):
            return set()
        collection = Collection(name)
        self._load(collection, name)
        try:
            rows = collection.query(expr='id != ""', output_fields=["id"], limit=16384)
        except Exception as e:
            raise MilvusUnavailableError(
                f"集合 {name} 查询已索引 id 失败: {type(e).__name__}: {str(e)[:200]}"
            ) from e
        return {str(r["id"]) for r in rows}

    def index_missing(
        self, db: Session, industry_id: Optional[str] = None, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """只索引"已完成抽取但尚未入向量库"的资讯（幂等，可每轮调用）。

        用 Milvus 里已有的 id 集合做差集，不需要额外的数据库列（免迁移）。
        """
        done = (
            db.query(IndustryNews)
            .filter(IndustryNews.extraction_status == "completed")
            .all()
        )
        if industry_id:
            done = [n for n in done if n.industry_id == industry_id]

        by_industry: Dict[str, List[IndustryNews]] = {}
        for n in done:
            by_industry.setdefault(n.industry_id or DEFAULT_INDUSTRY_ID, []).append(n)

        total = 0
        detail: Dict[str, int] = {}
        for ind, group in by_industry.items():
            have = self.existing_ids(ind)
            todo = [n for n in group if str(n.id) not in have]
            if limit:
                todo = todo[:limit]
            if not todo:
                detail[ind] = 0
                continue
            detail[ind] = self.index_news(todo, industry_id=ind)
            total += detail[ind]

        logger.info(f"[news_index] index_missing 完成: 新增 {total} 条, 明细 {detail}")
        return {"indexed": total, "detail": detail}

    # ---------- 检索 ----------

    def search(
        self,
        industry_id: Optional[str],
        query_text: str,
        top_k: int = 30,
        event_type: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """向量召回，返回 [(news_id, cosine_score), ...]。集合不存在时返回空列表。"""
        name = collection_name(industry_id)
        if not utility.has_collection(name):
            logger.warning(f"[news_index] 集合 {name} 不存在，向量检索跳过（请先建索引）")
            return []

        vec = self._embed([query_text])[0]
        if not vec:
            logger.warning("[news_index] 查询向量生成失败")
            return []

        collection = Collection(name)
        self._load(collection, name)

        expr = f'event_type == "{event_type}"' if event_type else None
        try:
            results = collection.search(
                data=[vec],
                anns_field="vector",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=max(1, top_k),
                expr=expr,
                output_fields=["id", "title"],
            )
        except Exception as e:
            raise MilvusUnavailableError(
                f"集合 {name} 向量检索失败: {type(e).__name__}: {str(e)[:200]}"
            ) from e
        hits: List[Tuple[str, float]] = []
        for group in results:
            for hit in group:
                hits.append((str(hit.entity.get("id")), float(hit.score)))
        return hits

    def stats(self, industry_id: Optional[str] = None) -> Dict[str, Any]:
        """索引统计。industry_id 为空时汇总所有 news_ 集合。"""
        if industry_id:
            names = [collection_name(industry_id)]
        else:
            names = [c for c in utility.list_collections() if c.startswith(COLLECTION_PREFIX)]

        out: Dict[str, Any] = {}
        for name in names:
            if not utility.has_collection(name):
                out[name] = 0
                continue
            out[name] = Collection(name).num_entities
        return out


_news_index_service: Optional[NewsIndexService] = None


def get_news_index_service() -> NewsIndexService:
    global _news_index_service
    if _news_index_service is None:
        _news_index_service = NewsIndexService()
    return _news_index_service
