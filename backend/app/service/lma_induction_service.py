"""L2a 归纳引擎 —— LMA 的「慢回路」（LLM 驱动偏好归纳）

背景
----
L1.5 解决了两个瓶颈（冷启动死锁、匹配太粗），但留下三个：**无归纳能力**、
**无衰减淘汰**、**跨用户为零**。本模块做前两个。

L1 是「调参」：点一下反馈就按固定公式调主题权重，秒级生效、规则透明、永远高频。
L2 是「学会」：攒够一批反馈后交给大模型，让它读样本、下结论，产出
「主题增 / 删 / 改 / 合并」的建议。两者不冲突——L1 供数据，L2 供结论。

三条硬约束（人工确认模式下的安全网）
-----------------------------------
1. **建议箱**：模型**只产出建议**，全部进 ``preference_suggestions`` 的 pending 箱，
   由用户逐条接受 / 拒绝。任何主题增删改都不由模型直接落库。
2. **可回滚**：每次归纳前先给 ``focus_topics`` 拍一张全量快照
   （``focus_topic_revisions.snapshot``）。回滚时精确撤销该次归纳的影响：
   删掉由它的建议创建的主题、其余主题按快照还原字段；**不碰**快照之后由用户手动建的主题。
3. **可解释**：每个建议都带 ``reason`` 与 ``evidence``（命中的反馈 id），
   前端能点开看「系统凭什么这么认为」。

``confidence`` 仍然输出，但**只用于给建议排序**，不作为自动生效的开关。

LLM 调用
--------
所有模型调用都收在模块级的 ``_chat_json()`` 一个薄函数里（含 base_url / key / 模型名）。
将来把 AI 接口换成公司提供的，**只改这一个函数**即可 —— 这也是「等统一 AI 入口就位
再写 L2a」这个顾虑的解法。

对外接口（``LmaInductionService``）
---------------------------------
    svc = LmaInductionService(db)
    svc.induction_status(user_id)            # 是否该归纳了
    svc.induct(user_id, trigger="manual")    # 跑一次归纳，产出建议（不落库）
    svc.list_suggestions(user_id, status)    # 建议箱
    svc.accept_suggestion(sid, user_id)      # 接受一条建议
    svc.reject_suggestion(sid, user_id)      # 拒绝一条建议
    svc.list_revisions(user_id)              # 归纳历史（可回滚）
    svc.rollback_revision(rid, user_id)      # 回滚到某次归纳之前
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from models.news import (
    FocusTopic,
    FocusTopicRevision,
    IndustryNews,
    NewsFeedback,
    PreferenceSuggestion,
)
from service.chem_lexicon import canonical_of, is_generic
from service.topic_suggestion import infer_topic_type, normalize_name, parse_json_list

load_dotenv()

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value >= minimum else minimum
    except (TypeError, ValueError):
        return default


# 攒批触发：累计多少条新反馈就值得归纳一次
INDUCTION_MIN_FEEDBACK = _env_int("LMA_INDUCTION_MIN_FEEDBACK", 20, 1)
# 兜底触发：距上次归纳超过多少天（且这期间有新反馈）
INDUCTION_MAX_DAYS = _env_int("LMA_INDUCTION_MAX_DAYS", 7, 1)
# 打包给模型的反馈样本条数
INDUCTION_SAMPLE_LIMIT = _env_int("LMA_INDUCTION_SAMPLE_LIMIT", 60, 5)
# 打包给模型的当前主题条数
INDUCTION_TOPIC_LIMIT = _env_int("LMA_INDUCTION_TOPIC_LIMIT", 60, 5)
# 弱负信号（资讯屏蔽）达到多少次，才算「用户反复不想看」的 drop 证据
WEAK_DROP_HITS = _env_int("LMA_WEAK_DROP_HITS", 3, 1)
# 归纳用模型：需要一定推理力，用 plus 档
LMA_INDUCTION_MODEL = os.getenv("LMA_INDUCTION_MODEL", "qwen-plus")
# 样本里正文摘要的截断长度
SAMPLE_TEXT_LIMIT = 160
# 单个建议最多带多少条证据
MAX_EVIDENCE = 12

TOPIC_TYPES = ("industry", "customer", "process", "technology", "product")
KINDS = ("add", "adjust", "merge", "drop", "insight")
WEIGHT_MIN, WEIGHT_MAX = -2.0, 2.0

INDUCTION_SYSTEM_PROMPT = "你是用户偏好分析师，只输出严格合法的 JSON 对象。"

INDUCTION_PROMPT = """你是化工行业资讯助手的用户偏好分析师。
下面是一位用户的**反馈样本**和**当前的关注主题**，请归纳出这位用户的真实关注点，
提出对关注主题的调整建议。**只依据给出的数据推断，不要臆测**。

=== 当前关注主题（名称｜类型｜权重｜正/负反馈次数｜来源｜是否生效｜弱屏蔽次数）===
{topics_block}

=== 反馈样本（编号｜判断｜事件类型｜标题 / 实体 摘要）===
（判断含义：✓=感兴趣或相关，✗=不感兴趣或不相关）
{samples_block}

=== 输出要求 ===
严格只输出一个 JSON 对象，不要输出任何解释、不要加 ``` 标记。字段如下：

{{
  "add_topics":    [{{"name": "己内酰胺", "type": "product", "reason": "近30天正向反馈12条", "confidence": 0.86}}],
  "adjust_topics": [{{"name": "可降解塑料", "delta": 0.4, "reason": "近期反馈转向负面", "confidence": 0.72}}],
  "merge_topics":  [{{"targets": ["PA6", "尼龙6"], "into": "聚酰胺6", "reason": "同一物质的两种写法", "confidence": 0.91}}],
  "drop_topics":   [{{"name": "页岩气", "reason": "长期无任何正向反馈", "confidence": 0.95}}],
  "insights":      ["你近期关注集中在己内酰胺-聚酰胺产业链的产能与价格"]
}}

规则：
1. `name` / `targets` / `into` 必须来自上面出现过的名称或样本里真实出现过的词，
   不要编造新概念；名称长度 2~20 字，不要带机构后缀（如「有限公司」「股份有限公司」）。
2. `type` 只能取：industry / customer / process / technology / product。
3. `delta` 取值范围 -1 ~ 1，正数=比现在更关注，负数=兴趣下降。
4. `merge_topics` 只用于**确实指同一事物**的写法（同义词、全称简称、中英文），
   `into` 是合并后的规范名；不要合并上下游关系或同类不同物（如 PA6 与 PA66 不能合并）。
5. `drop_topics` 只用于**长期没有任何正向证据**的主题（样本中从未出现、或只出现负面）。
   不要因为样本里没出现就判它该删——样本有限。
   「弱屏蔽次数」= 用户主动屏蔽了命中该主题的资讯的次数（弱负信号：不改权重、不计入正/负反馈）。
   只有在该主题**没有任何正向证据**、且弱屏蔽次数 ≥ {weak_drop_hits} 时，才可以把它列入 `drop_topics`。
6. `insights` 最多 3 条，每条不超过 40 字，是给用户看的画像结论。
7. 没有把握的类别就给空数组，不要为了凑数硬编。"""


# ---------------------------------------------------------------------------
# LLM 薄封装（供应商可替换的唯一入口）
# ---------------------------------------------------------------------------

_LLM_CLIENT = None


def _chat_json(system_prompt: str, user_prompt: str, model: Optional[str] = None) -> Optional[dict]:
    """调用对话模型并解析出 JSON 对象。失败返回 None（调用方降级）。

    这是 LMA 里**唯一**直接接触 AI 供应商的地方：换供应商时只改本函数
    （base_url / key / 模型名），上层业务代码零改动。
    """
    global _LLM_CLIENT
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        logger.warning("[lma_induction] DASHSCOPE_API_KEY 未配置，跳过归纳")
        return None
    base_url = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    if _LLM_CLIENT is None:
        from openai import OpenAI

        _LLM_CLIENT = OpenAI(api_key=api_key, base_url=base_url)
    try:
        resp = _LLM_CLIENT.chat.completions.create(
            model=model or LMA_INDUCTION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - 归纳失败不应打断调用方
        logger.warning(f"[lma_induction] LLM 调用失败：{exc}")
        return None
    return _parse_json_object(text)


def _parse_json_object(text: str) -> Optional[dict]:
    """从模型回复里抠出 JSON 对象（容忍 ``` 围栏与前后废话）。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float = WEIGHT_MIN, high: float = WEIGHT_MAX) -> float:
    return max(low, min(high, value))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _topic_key(name: str) -> str:
    """主题的身份键：同义归一后的小写名（PA6 与 尼龙6 视为同一主题）。"""
    return canonical_of(name or "").strip().lower()


def _topic_snapshot(topic: FocusTopic) -> dict:
    """把主题序列化成可回滚的快照条目。

    注意：**故意不包含** ``weak_penalty`` / ``weak_hits``。它们是「资讯屏蔽」造成的，
    与归纳无关；若纳入快照，回滚会把**归纳之后**用户新产生的屏蔽一并抹掉，属于越权。
    回滚只还原归纳真正会改的字段。
    """
    return {
        "id": str(topic.id),
        "topic_type": topic.topic_type,
        "name": topic.name,
        "query_text": topic.query_text,
        "weight": float(topic.weight or 0.0),
        "positive_count": int(topic.positive_count or 0),
        "negative_count": int(topic.negative_count or 0),
        "is_active": bool(topic.is_active),
        "source": topic.source or "manual",
        "auto_hits": int(topic.auto_hits or 0),
        "suggestion_id": str(topic.suggestion_id) if topic.suggestion_id else None,
    }


def topic_dict(topic: FocusTopic) -> dict:
    """与 feedback_router.topic_dict 同构的对外结构（避免循环 import 而本地复制）。"""
    return {
        "id": str(topic.id), "topic_type": topic.topic_type, "name": topic.name,
        "query_text": topic.query_text, "weight": topic.weight,
        "positive_count": topic.positive_count, "negative_count": topic.negative_count,
        "is_active": topic.is_active, "source": topic.source or "manual",
        "origin_news_id": str(topic.origin_news_id) if topic.origin_news_id else None,
        "auto_hits": topic.auto_hits or 0,
        "weak_penalty": float(topic.weak_penalty or 0.0),
        "weak_hits": int(topic.weak_hits or 0),
        "suggestion_id": str(topic.suggestion_id) if topic.suggestion_id else None,
        "created_at": topic.created_at.isoformat() if topic.created_at else None,
        "updated_at": topic.updated_at.isoformat() if topic.updated_at else None,
    }


def suggestion_dict(item: PreferenceSuggestion) -> dict:
    return {
        "id": str(item.id),
        "kind": item.kind,
        "name": item.name,
        "target_id": str(item.target_id) if item.target_id else None,
        "payload": _safe_json(item.payload, {}),
        "reason": item.reason,
        "confidence": round(float(item.confidence or 0.0), 3),
        "evidence": _safe_json(item.evidence, []),
        "status": item.status,
        "revision_id": str(item.revision_id) if item.revision_id else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def revision_dict(item: FocusTopicRevision) -> dict:
    return {
        "id": str(item.id),
        "trigger": item.trigger,
        "sample_count": item.sample_count,
        "topic_count": item.topic_count,
        "status": item.status,
        "current": item.current,
        "summary": item.summary,
        "error": item.error,
        "diff": _safe_json(item.diff, []),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _safe_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

class LmaInductionService:
    """L2a 归纳引擎：攒批 → 打包 → LLM 归纳 → 建议箱 → 人工确认 → 可回滚。"""

    def __init__(self, db: Session):
        self.db = db

    # ---------------- 触发判定 ----------------

    def last_revision(self, user_id: UUID) -> Optional[FocusTopicRevision]:
        return (
            self.db.query(FocusTopicRevision)
            .filter(FocusTopicRevision.user_id == user_id,
                    FocusTopicRevision.status == "applied")
            .order_by(FocusTopicRevision.created_at.desc())
            .first()
        )

    def _pending_feedback_count(self, user_id: UUID,
                                since: Optional[datetime]) -> int:
        query = self.db.query(NewsFeedback).filter(NewsFeedback.user_id == user_id)
        if since is not None:
            query = query.filter(NewsFeedback.updated_at > since)
        return query.count()

    def induction_status(self, user_id: UUID) -> dict:
        """返回「是否该归纳了」以及依据（前端按钮旁展示）。"""
        last = self.last_revision(user_id)
        since = last.created_at if last else None
        pending = self._pending_feedback_count(user_id, since)
        days = None
        if last and last.created_at:
            days = (datetime.utcnow() - last.created_at).days
        due_batch = pending >= INDUCTION_MIN_FEEDBACK
        due_schedule = bool(last) and days is not None and days >= INDUCTION_MAX_DAYS and pending > 0
        return {
            "due": bool(due_batch or due_schedule),
            "reason": "batch" if due_batch else ("schedule" if due_schedule else "insufficient"),
            "pending_feedback": pending,
            "threshold": INDUCTION_MIN_FEEDBACK,
            "max_days": INDUCTION_MAX_DAYS,
            "days_since_last": days,
            "last_inducted_at": last.created_at.isoformat() if last and last.created_at else None,
            "pending_suggestions": self.db.query(PreferenceSuggestion).filter(
                PreferenceSuggestion.user_id == user_id,
                PreferenceSuggestion.status == "pending").count(),
        }

    # ---------------- 打包样本 ----------------

    def collect_samples(self, user_id: UUID, limit: Optional[int] = None) -> List[dict]:
        """近 N 条反馈样本：标题 + 摘要 + 事件类型 + 实体 + 用户的判断 + 反馈 id。"""
        rows = (
            self.db.query(NewsFeedback, IndustryNews)
            .join(IndustryNews, NewsFeedback.news_id == IndustryNews.id)
            .filter(NewsFeedback.user_id == user_id)
            .order_by(NewsFeedback.updated_at.desc())
            .limit(limit or INDUCTION_SAMPLE_LIMIT)
            .all()
        )
        samples: List[dict] = []
        for fb, news in rows:
            entities = parse_json_list(news.entities)
            topics = parse_json_list(news.extracted_topics)
            samples.append({
                "id": str(fb.id),
                "news_id": str(news.id),
                "interested": fb.interested,
                "relevant": fb.relevant,
                "note": (fb.note or "").strip()[:80] or None,
                "title": (news.title or "").strip(),
                "summary": (news.summary or news.content or "").strip()[:SAMPLE_TEXT_LIMIT],
                "event_type": news.event_type,
                "entities": [str(e) for e in entities][:8],
                "topics": [str(t.get("name") if isinstance(t, dict) else t) for t in topics][:8],
                "updated_at": fb.updated_at.isoformat() if fb.updated_at else None,
            })
        return samples

    def _sample_text(self, sample: dict) -> str:
        parts = [sample.get("title") or "", sample.get("summary") or ""]
        parts.extend(sample.get("entities") or [])
        parts.extend(sample.get("topics") or [])
        return " ".join(p for p in parts if p)

    def _evidence_for(self, name: str, samples: List[dict]) -> Tuple[List[str], dict]:
        """挑出支撑某个主题名的反馈证据（命中判定复用 L1.5 的匹配算法）。"""
        from service.interest_scoring import text_matches

        hit_ids: List[str] = []
        positive = negative = 0
        for sample in samples:
            if not text_matches(name, self._sample_text(sample)):
                continue
            if sample.get("interested") is False or sample.get("relevant") is False:
                negative += 1
            elif sample.get("interested") or sample.get("relevant"):
                positive += 1
            if len(hit_ids) < MAX_EVIDENCE:
                hit_ids.append(sample["id"])
        return hit_ids, {"positive": positive, "negative": negative,
                         "hits": positive + negative}

    # ---------------- 归纳 ----------------

    def _prompt_blocks(self, topics: List[FocusTopic], samples: List[dict]) -> Tuple[str, str]:
        if topics:
            topics_block = "\n".join(
                f"{i + 1}. {t.name}｜{t.topic_type}｜权重{t.weight:.2f}｜"
                f"正{t.positive_count or 0}/负{t.negative_count or 0}｜"
                f"{t.source or 'manual'}｜{'生效' if t.is_active else '已停用'}｜"
                f"弱屏蔽{int(t.weak_hits or 0)}"
                for i, t in enumerate(topics)
            )
        else:
            topics_block = "（暂无关注主题，用户完全从零开始）"

        lines = []
        for i, s in enumerate(samples):
            if s.get("interested") is False or s.get("relevant") is False:
                mark = "✗"
            elif s.get("interested") or s.get("relevant"):
                mark = "✓"
            else:
                mark = "–"
            detail = " / ".join(x for x in [
                s.get("event_type"), "、".join(s.get("entities") or []),
                s.get("summary") or "",
            ] if x)
            lines.append(f"{i + 1}. [{mark}] {s.get('title') or '(无标题)'} — {detail[:110]}")
        samples_block = "\n".join(lines) if lines else "（无反馈样本）"
        return topics_block, samples_block

    def induct(self, user_id: UUID, trigger: str = "manual",
               force: bool = False) -> dict:
        """跑一次归纳：产出建议进 pending 箱，同时留一张可回滚快照。

        永不直接改 ``focus_topics``。``force=True`` 时跳过攒批判定（手动触发用）。
        """
        if trigger not in ("manual", "batch", "schedule"):
            trigger = "manual"

        topics = (
            self.db.query(FocusTopic)
            .filter(FocusTopic.user_id == user_id)
            .order_by(FocusTopic.weight.desc())
            .limit(INDUCTION_TOPIC_LIMIT)
            .all()
        )
        samples = self.collect_samples(user_id)
        if not samples:
            return {"ok": False, "reason": "no_samples",
                    "message": "还没有任何资讯反馈，先对几条资讯点「感兴趣 / 不相关」再来归纳"}

        status = self.induction_status(user_id)
        if trigger != "manual" and not force and not status["due"]:
            return {"ok": False, "reason": "not_due", "status": status,
                    "message": "反馈量还没到归纳阈值"}

        topics_block, samples_block = self._prompt_blocks(topics, samples)
        prompt = INDUCTION_PROMPT.format(topics_block=topics_block,
                                         samples_block=samples_block,
                                         weak_drop_hits=WEAK_DROP_HITS)
        raw = _chat_json(INDUCTION_SYSTEM_PROMPT, prompt)

        # 快照必须先拍（回滚依据），无论这次有没有产出建议
        snapshot = [_topic_snapshot(t) for t in topics]

        if raw is None:
            revision = self._new_revision(user_id, trigger, len(samples), snapshot,
                                          diff=[], summary=None,
                                          error="LLM 不可用或输出无法解析")
            return {"ok": False, "reason": "llm_unavailable",
                    "revision": revision_dict(revision),
                    "message": "归纳失败：模型不可用或输出无法解析，稍后可重试"}

        prepared, insights = self._normalize(raw, topics, samples)
        summary = "；".join(insights) if insights else None
        diff = [
            {"kind": p["kind"], "name": p["name"], "reason": p["reason"],
             "confidence": p["confidence"]}
            for p in prepared
        ]
        revision = self._new_revision(user_id, trigger, len(samples), snapshot,
                                      diff=diff, summary=summary, error=None)

        created: List[PreferenceSuggestion] = []
        for item in prepared:
            if self._duplicate_pending(user_id, item):
                continue
            row = PreferenceSuggestion(
                user_id=user_id, revision_id=revision.id, kind=item["kind"],
                name=item["name"], target_id=item["target_id"],
                payload=json.dumps(item["payload"], ensure_ascii=False),
                reason=item["reason"], confidence=item["confidence"],
                evidence=json.dumps(item["evidence"], ensure_ascii=False),
            )
            self.db.add(row)
            created.append(row)
        self.db.commit()
        self.db.refresh(revision)
        for row in created:
            self.db.refresh(row)

        return {
            "ok": True,
            "revision": revision_dict(revision),
            "suggestions": [suggestion_dict(s) for s in created],
            "created": len(created),
            "insights": insights,
        }

    def _new_revision(self, user_id: UUID, trigger: str, sample_count: int,
                      snapshot: List[dict], diff: List[dict],
                      summary: Optional[str], error: Optional[str]) -> FocusTopicRevision:
        # 同一用户只保留一条 current
        self.db.query(FocusTopicRevision).filter(
            FocusTopicRevision.user_id == user_id,
            FocusTopicRevision.current.is_(True),
        ).update({"current": False}, synchronize_session=False)
        revision = FocusTopicRevision(
            user_id=user_id, trigger=trigger, sample_count=sample_count,
            topic_count=len(snapshot),
            current=True, status="applied",
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            diff=json.dumps(diff, ensure_ascii=False),
            summary=summary, error=error,
        )
        self.db.add(revision)
        self.db.commit()
        self.db.refresh(revision)
        return revision

    def _duplicate_pending(self, user_id: UUID, item: dict) -> bool:
        """已经有一条待确认的同类型同名建议，就不再堆一条。"""
        rows = self.db.query(PreferenceSuggestion).filter(
            PreferenceSuggestion.user_id == user_id,
            PreferenceSuggestion.status == "pending",
            PreferenceSuggestion.kind == item["kind"],
        ).all()
        key = _topic_key(item["name"])
        return any(_topic_key(r.name) == key for r in rows)

    def _normalize(self, raw: dict, topics: List[FocusTopic],
                   samples: List[dict]) -> Tuple[List[dict], List[str]]:
        """把模型输出洗成可安全落地的建议。非法/悬空的建议一律丢弃。"""
        # 同一 canonical 可能对应多条真实主题记录（用户既有 PA6 又有 尼龙6），
        # 所以按 canonical 分组、解析时先精确同名、再按权重取同义里最强的那条。
        grouped: Dict[str, List[FocusTopic]] = {}
        for topic in topics:
            grouped.setdefault(_topic_key(topic.name), []).append(topic)

        def resolve(name: Optional[str]) -> Optional[FocusTopic]:
            if not name:
                return None
            for topic in topics:
                if topic.name == name:
                    return topic
            candidates = grouped.get(_topic_key(name)) or []
            if not candidates:
                return None
            return max(candidates, key=lambda t: abs(float(t.weight or 0.0)))

        prepared: List[dict] = []
        seen: set = set()

        def _take(kind: str, name: str) -> bool:
            key = (kind, _topic_key(name))
            if key in seen:
                return False
            seen.add(key)
            return True

        # add：样本里真实出现过、且当前没有的新主题
        for raw_item in _as_list(raw.get("add_topics")):
            item = _entry(raw_item)
            name = normalize_name(item.get("name"))
            if not name or is_generic(name) or not _take("add", name):
                continue
            if resolve(name) is not None:
                continue  # 已存在，交给 adjust
            evidence, stats = self._evidence_for(name, samples)
            if stats["hits"] == 0:
                continue  # 没有一条反馈能对上，不要凭空加主题
            prepared.append({
                "kind": "add", "name": name, "target_id": None,
                "payload": {"topic_type": infer_topic_type(name, item.get("type")),
                            "query_text": name, "weight": 1.0,
                            "alias_count": stats["hits"]},
                "reason": (item.get("reason") or "").strip()[:300],
                "confidence": _clamp(_to_float(item.get("confidence"), 0.5), 0.0, 1.0),
                "evidence": evidence,
            })

        # adjust：只针对已存在的主题
        for raw_item in _as_list(raw.get("adjust_topics")):
            item = _entry(raw_item)
            name = normalize_name(item.get("name"))
            if not name or not _take("adjust", name):
                continue
            topic = resolve(name)
            if topic is None:
                continue
            delta = _clamp(_to_float(item.get("delta"), 0.0), -1.0, 1.0)
            if abs(delta) < 1e-6:
                continue
            evidence, stats = self._evidence_for(topic.name, samples)
            prepared.append({
                "kind": "adjust", "name": topic.name, "target_id": topic.id,
                "payload": {"delta": round(delta, 3),
                            "from_weight": float(topic.weight or 0.0)},
                "reason": (item.get("reason") or "").strip()[:300],
                "confidence": _clamp(_to_float(item.get("confidence"), 0.5), 0.0, 1.0),
                "evidence": evidence,
            })

        # merge：合并「确实指同一事物」的写法
        #
        # 注意不能用「同义归一后的 key 数」判有效性：PA6 / 尼龙6 / 聚酰胺6 在词表里
        # 本来就归一到同一个 canonical，键数会退化成 1，反而把最该合并的那种情况丢掉。
        # 所以按**被合并的主题记录条数**判定（≥2 条记录才叫合并）。
        for raw_item in _as_list(raw.get("merge_topics")):
            item = _entry(raw_item)
            targets_raw = item.get("targets") or []
            into = normalize_name(item.get("into"))
            if not into or not isinstance(targets_raw, list):
                continue
            members: List[FocusTopic] = []
            for raw_target in targets_raw:
                topic = resolve(normalize_name(raw_target))
                if topic is not None and all(t.id != topic.id for t in members):
                    members.append(topic)
            into_topic = resolve(into)
            if into_topic is not None and all(t.id != into_topic.id for t in members):
                members.append(into_topic)
            if len(members) < 2:
                continue  # 凑不出两条真实主题记录，不是合并
            if not _take("merge", into):
                continue
            names = sorted({t.name for t in members} | {into})
            prepared.append({
                "kind": "merge", "name": into,
                "target_id": members[0].id,
                "payload": {"targets": names,
                            "into": into,
                            "topic_ids": [str(t.id) for t in members],
                            "topic_type": infer_topic_type(into, members[0].topic_type)},
                "reason": (item.get("reason") or "").strip()[:300],
                "confidence": _clamp(_to_float(item.get("confidence"), 0.5), 0.0, 1.0),
                "evidence": [s["id"] for s in samples[:MAX_EVIDENCE]],
            })

        # drop：只停用（不删除），且必须真处于长期无正向证据
        for raw_item in _as_list(raw.get("drop_topics")):
            item = _entry(raw_item)
            name = normalize_name(item.get("name"))
            if not name or not _take("drop", name):
                continue
            topic = resolve(name)
            if topic is None or not topic.is_active:
                continue
            evidence, stats = self._evidence_for(topic.name, samples)
            if stats["positive"] > 0:
                continue  # 样本里还有正向证据，不能淘汰
            # 第二道闸：样本窗口外的历史正向反馈同样算「用户在意过」。
            # 少了这道闸，一个词若没被 _evidence_for 匹配上（窗口内恰好没提到），
            # positive 会退化成 0，导致有正向历史的主题被误判为可淘汰。
            if int(topic.positive_count or 0) > 0:
                continue  # 历史上拿到过正向反馈，只允许用户手动停用
            # 第三道闸：至少要有某种负向证据——样本内命中（evidence）、历史负反馈，
            # 或**反复屏蔽**（弱负信号，需达到 WEAK_DROP_HITS 次）。
            # 弱信号单独不足以降权到停用，但足以支撑一条「可被用户拒绝的」建议。
            weak_hits = int(topic.weak_hits or 0)
            if (not evidence and not (int(topic.negative_count or 0) > 0)
                    and weak_hits < WEAK_DROP_HITS):
                continue  # 既无样本证据、也无历史负反馈、弱信号也不足，不值得淘汰
            prepared.append({
                "kind": "drop", "name": topic.name, "target_id": topic.id,
                "payload": {"from_weight": float(topic.weight or 0.0),
                            "hits": stats["hits"], "weak_hits": weak_hits},
                "reason": (item.get("reason") or "").strip()[:300],
                "confidence": _clamp(_to_float(item.get("confidence"), 0.5), 0.0, 1.0),
                "evidence": evidence,
            })

        # insight：画像结论，不落库，作为建议进箱供用户「知道了 / 忽略」
        for text in _as_list(raw.get("insights")):
            text = str(text).strip()
            if not text:
                continue
            name = text[:60]
            if not _take("insight", name):
                continue
            prepared.append({
                "kind": "insight", "name": name, "target_id": None,
                "payload": {}, "reason": "", "confidence": 0.0, "evidence": [],
            })

        insights = [str(x).strip()[:80] for x in _as_list(raw.get("insights")) if str(x).strip()][:3]
        return prepared, insights

    # ---------------- 建议箱 ----------------

    def list_suggestions(self, user_id: UUID, status: str = "pending",
                         kind: Optional[str] = None) -> List[dict]:
        query = self.db.query(PreferenceSuggestion).filter(
            PreferenceSuggestion.user_id == user_id)
        if status and status != "all":
            query = query.filter(PreferenceSuggestion.status == status)
        if kind:
            query = query.filter(PreferenceSuggestion.kind == kind)
        rows = query.order_by(PreferenceSuggestion.confidence.desc(),
                              PreferenceSuggestion.created_at.desc()).all()
        return [suggestion_dict(r) for r in rows]

    def accept_suggestion(self, suggestion_id: UUID, user_id: UUID) -> dict:
        row = self.db.query(PreferenceSuggestion).filter(
            PreferenceSuggestion.id == suggestion_id,
            PreferenceSuggestion.user_id == user_id).first()
        if not row:
            raise LookupError("建议不存在")
        if row.status != "pending":
            raise ValueError(f"建议已处理（{row.status}）")

        payload = _safe_json(row.payload, {}) or {}
        touched: List[dict] = []
        detail = ""

        if row.kind == "add":
            topic = self._accept_add(row, payload)
            touched.append(topic)
            detail = f"已新增关注主题「{topic.name}」"
        elif row.kind == "adjust":
            topic = self._require_topic(row.target_id, user_id)
            topic.weight = _clamp(float(topic.weight or 0.0) + _to_float(payload.get("delta"), 0.0))
            topic.is_active = True
            touched.append(topic)
            detail = f"「{topic.name}」权重已调整为 {topic.weight:.2f}"
        elif row.kind == "merge":
            touched, detail = self._accept_merge(row, payload)
        elif row.kind == "drop":
            topic = self._require_topic(row.target_id, user_id)
            topic.is_active = False
            touched.append(topic)
            detail = f"已停用关注主题「{topic.name}」"
        elif row.kind == "insight":
            detail = "已标记该结论为已读"
        else:
            raise ValueError(f"不支持的建议类型：{row.kind}")

        row.status = "accepted"
        row.resolved_at = datetime.utcnow()
        self.db.commit()
        for topic in touched:
            self.db.refresh(topic)
        self.db.refresh(row)
        return {"suggestion": suggestion_dict(row), "touched_topics": [topic_dict(t) for t in touched],
                "detail": detail}

    def reject_suggestion(self, suggestion_id: UUID, user_id: UUID) -> dict:
        row = self.db.query(PreferenceSuggestion).filter(
            PreferenceSuggestion.id == suggestion_id,
            PreferenceSuggestion.user_id == user_id).first()
        if not row:
            raise LookupError("建议不存在")
        if row.status != "pending":
            raise ValueError(f"建议已处理（{row.status}）")
        row.status = "rejected"
        row.resolved_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return {"suggestion": suggestion_dict(row),
                "detail": f"已忽略建议「{row.name}」"}

    def _require_topic(self, topic_id: Optional[UUID], user_id: UUID) -> FocusTopic:
        topic = None
        if topic_id:
            topic = self.db.query(FocusTopic).filter(
                FocusTopic.id == topic_id, FocusTopic.user_id == user_id).first()
        if not topic:
            raise LookupError("建议指向的主题已不存在（可能已被删除）")
        return topic

    def _accept_add(self, row: PreferenceSuggestion, payload: dict) -> FocusTopic:
        name = row.name
        # 同名或同义（PA6 vs 尼龙6）已存在时不重复建，只重新激活
        exists = None
        for topic in self.db.query(FocusTopic).filter(FocusTopic.user_id == row.user_id).all():
            if topic.name == name or _topic_key(topic.name) == _topic_key(name):
                exists = topic
                break
        if exists:
            exists.is_active = True
            exists.suggestion_id = row.id
            return exists
        topic = FocusTopic(
            user_id=row.user_id,
            topic_type=payload.get("topic_type") or infer_topic_type(name),
            name=name, query_text=payload.get("query_text") or name,
            weight=_to_float(payload.get("weight"), 1.0),
            # 用户确认过的主题按「手动」对待：受 L1.5「手动主题永不自动停用」保护
            source="manual", suggestion_id=row.id,
        )
        self.db.add(topic)
        self.db.flush()
        return topic

    def _accept_merge(self, row: PreferenceSuggestion,
                      payload: dict) -> Tuple[List[FocusTopic], str]:
        into = payload.get("into") or row.name
        topic_type = payload.get("topic_type")
        target_ids = [UUID(str(x)) for x in (payload.get("topic_ids") or []) if x]

        candidates = self.db.query(FocusTopic).filter(
            FocusTopic.user_id == row.user_id,
            FocusTopic.id.in_(target_ids)).all() if target_ids else []
        # 名称兜底：模型给的 targets 可能没带 id
        keys = {_topic_key(k) for k in (payload.get("targets") or [])}
        for topic in self.db.query(FocusTopic).filter(FocusTopic.user_id == row.user_id).all():
            if _topic_key(topic.name) in keys and topic not in candidates:
                candidates.append(topic)

        into_topic = next((t for t in candidates if t.name == into), None)
        if into_topic is None:
            # 候选里没有同名主题：看用户是否已单独建过这个名字
            into_topic = self.db.query(FocusTopic).filter(
                FocusTopic.user_id == row.user_id, FocusTopic.name == into).first()
        if into_topic is None:
            into_topic = FocusTopic(
                user_id=row.user_id,
                topic_type=topic_type or infer_topic_type(into),
                name=into, query_text=into, weight=1.0,
                source="manual", suggestion_id=row.id,
            )
            self.db.add(into_topic)
            self.db.flush()

        # 别名并进 query_text：合并后仍能命中原来的写法
        aliases: List[str] = []
        for topic in candidates:
            for alias in (topic.query_text or topic.name).split(","):
                alias = alias.strip()
                if alias and alias not in aliases:
                    aliases.append(alias)
        if into not in aliases:
            aliases.insert(0, into)
        into_topic.query_text = ",".join(aliases)
        into_topic.is_active = True
        into_topic.source = "manual"

        merged_away: List[str] = []
        for topic in candidates:
            if topic.id == into_topic.id:
                continue
            topic.is_active = False
            merged_away.append(topic.name)

        all_touched = [into_topic] + [t for t in candidates if t.id != into_topic.id]
        if merged_away:
            detail = f"已合并为「{into_topic.name}」，并停用：{'、'.join(merged_away)}"
        else:
            detail = f"已合并为「{into_topic.name}」"
        return all_touched, detail

    # ---------------- 归纳历史与回滚 ----------------

    def list_revisions(self, user_id: UUID, limit: int = 20) -> List[dict]:
        rows = (
            self.db.query(FocusTopicRevision)
            .filter(FocusTopicRevision.user_id == user_id)
            .order_by(FocusTopicRevision.created_at.desc())
            .limit(limit).all()
        )
        return [revision_dict(r) for r in rows]

    def rollback_revision(self, revision_id: UUID, user_id: UUID) -> dict:
        """回滚到某次归纳**之前**：精确撤销该次归纳的影响。

        只做两件事：① 删除由本次归纳的建议创建的主题；② 其余主题按快照还原字段。
        快照之后由用户手动新建/修改的主题**不受影响**（不做全量覆盖，避免误伤）。
        """
        revision = self.db.query(FocusTopicRevision).filter(
            FocusTopicRevision.id == revision_id,
            FocusTopicRevision.user_id == user_id).first()
        if not revision:
            raise LookupError("归纳记录不存在")
        if revision.status == "rolled_back":
            raise ValueError("该次归纳已经回滚过")

        snapshot = _safe_json(revision.snapshot, []) or []
        snapshot_by_id = {str(item.get("id")): item for item in snapshot if item.get("id")}

        suggestion_ids = {
            str(sid) for (sid,) in self.db.query(PreferenceSuggestion.id).filter(
                PreferenceSuggestion.revision_id == revision.id).all()
        }

        topics = self.db.query(FocusTopic).filter(FocusTopic.user_id == user_id).all()
        removed: List[str] = []
        restored: List[str] = []

        for topic in topics:
            tid = str(topic.id)
            created_here = (topic.suggestion_id is not None
                            and str(topic.suggestion_id) in suggestion_ids)
            if tid not in snapshot_by_id:
                if created_here:
                    removed.append(topic.name)
                    self.db.delete(topic)
                continue
            snap = snapshot_by_id[tid]
            topic.topic_type = snap.get("topic_type") or topic.topic_type
            topic.name = snap.get("name") or topic.name
            topic.query_text = snap.get("query_text") or topic.query_text
            topic.weight = _clamp(_to_float(snap.get("weight"), topic.weight or 0.0))
            topic.positive_count = int(snap.get("positive_count") or 0)
            topic.negative_count = int(snap.get("negative_count") or 0)
            topic.is_active = bool(snap.get("is_active", True))
            topic.source = snap.get("source") or "manual"
            topic.auto_hits = int(snap.get("auto_hits") or 0)
            topic.suggestion_id = UUID(str(snap["suggestion_id"])) if snap.get("suggestion_id") else None
            restored.append(topic.name)

        # 本次归纳之后产生的建议一并置为已处理，避免回滚后又冒出来
        if suggestion_ids:
            self.db.query(PreferenceSuggestion).filter(
                PreferenceSuggestion.revision_id == revision.id,
                PreferenceSuggestion.status == "pending",
            ).update({"status": "rejected", "resolved_at": datetime.utcnow()},
                     synchronize_session=False)

        revision.status = "rolled_back"
        revision.current = False
        self.db.commit()

        later = self.last_revision(user_id)
        if later:
            later.current = True
            self.db.commit()

        remaining = self.db.query(FocusTopic).filter(
            FocusTopic.user_id == user_id).order_by(FocusTopic.weight.desc()).all()
        return {
            "revision": revision_dict(revision),
            "restored": restored,
            "removed": removed,
            "topics": [topic_dict(t) for t in remaining],
            "detail": f"已回滚：还原 {len(restored)} 个主题、移除 {len(removed)} 个由该次归纳新增的主题",
        }


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        return [value]
    return []


def _entry(item: Any) -> dict:
    """模型有时把对象写成裸字符串，这里统一成 dict。"""
    return item if isinstance(item, dict) else {"name": item}


def get_lma_induction_service(db: Session) -> LmaInductionService:
    return LmaInductionService(db)
