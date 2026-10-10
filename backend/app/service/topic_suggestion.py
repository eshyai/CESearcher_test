"""从一条资讯派生「候选关注主题」——L1.5 打破冷启动死锁的核心。

**纯函数，不依赖数据库**，便于单测。

### 为什么需要它

旧逻辑下，用户点「感兴趣」只会去**修改已存在的**关注主题；如果用户从没手动建过
主题，点再多反馈也毫无效果（冷启动死锁）。这里补上「从资讯里自己长出主题」的
一环：反馈正向且没有任何已有主题命中时，从该资讯的
**LLM 抽取主题 → 实体 → 标题词表词 → 关键词** 逐级派生候选主题。

### 派生优先级

| 优先级 | 来源 | 判定 |
|---|---|---|
| 100 | LLM 抽取主题，类型为产品/工艺/技术 | 最贴近从业者关注粒度 |
| 90 | 实体命中化工词表 | 归一后即为规范主题（PA6→聚酰胺6） |
| 80 | 标题命中化工词表 | 标题里出现的产品名是很强的信号 |
| 70 | LLM 抽取主题，类型为行业/客户 | 粒度偏粗，降级 |
| 60 | 其余实体 | 公司/机构名，兜底 |
| 50 | 关键词里命中词表的词 | 最弱 |

产出的候选主题一律带上 ``reason``（为什么系统认为你关注它），落到
``focus_topics`` 时标记 ``source='auto'``，用户可在「我的关注」页确认为正式主题
或直接删除。
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from service.chem_lexicon import (
    ALIAS_TO_CANONICAL,
    GENERIC_TERMS,
    INSTITUTION_SUFFIXES,
    NON_TOPIC_EVENT_WORDS,
    PROCESS_CANONICALS,
    PRODUCT_CANONICALS,
    SYNONYM_GROUPS,
    TOPIC_STOPWORDS,
    aliases_of,
    canonical_of,
)
from service.interest_scoring import TOKEN_RE, canonical_groups

TOPIC_TYPES: tuple[str, ...] = ("industry", "customer", "process", "technology", "product")

MIN_TOPIC_NAME_LEN = 2
MAX_TOPIC_NAME_LEN = 30
DEFAULT_CANDIDATE_LIMIT = 2

# 派生优先级
P_LLM_TECH = 100
P_LEXICON_ENTITY = 90
P_LEXICON_TITLE = 80
P_LLM_BROAD = 70
P_ENTITY = 60
P_KEYWORD = 50

_STRIP_CHARS = "。，,、；;：:！!？?）)]}】》\"'“”‘’《【[{(（"
_TAIL_PAREN_RE = re.compile(r"\s*[（(][^（()）]{0,20}[)）]\s*$")
_KEYWORD_SPLIT_RE = re.compile(r"[,，;；/|、\s]+")
_GENERIC_BY_LEN: tuple[str, ...] = tuple(sorted(GENERIC_TERMS, key=len, reverse=True))


# ---------------------------------------------------------------------------
# 输入清洗
# ---------------------------------------------------------------------------

def parse_json_list(value: Any) -> list:
    """把模型抽取结果里的 JSON 字符串/列表统一成列表；解析失败返回空列表。"""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def normalize_name(raw: Any) -> str | None:
    """清洗并校验一个候选主题名；不合格返回 ``None``。"""
    if raw is None:
        return None
    name = re.sub(r"\s+", " ", str(raw)).strip()
    if not name:
        return None
    name = _TAIL_PAREN_RE.sub("", name).strip()  # 去掉尾部括号补充说明
    name = name.strip(_STRIP_CHARS).strip()
    if not (MIN_TOPIC_NAME_LEN <= len(name) <= MAX_TOPIC_NAME_LEN):
        return None

    canon = canonical_of(name)
    if canon in GENERIC_TERMS or canon in TOPIC_STOPWORDS:
        return None
    if name in NON_TOPIC_EVENT_WORDS or name in TOPIC_STOPWORDS:
        return None
    if any(name.endswith(suffix) for suffix in INSTITUTION_SUFFIXES):
        return None
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", name):
        return None
    if re.fullmatch(r"[\W\d_]+", name):
        return None
    # 整词都由泛词拼出来的（如「装置检修」「价格行情」）不是有效主题
    stripped = name
    for term in _GENERIC_BY_LEN:
        stripped = stripped.replace(term, "")
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", stripped):
        return None
    return name


def infer_topic_type(name: str, hint: Any = None) -> str:
    """推断主题类型；模型给了合法类型就沿用，否则按词表/后缀启发式判断。"""
    if isinstance(hint, str) and hint in TOPIC_TYPES:
        return hint
    canon = canonical_of(name)
    if canon in PROCESS_CANONICALS:
        return "process"
    if canon in PRODUCT_CANONICALS:
        return "product"
    if canon.endswith("工艺") or canon.endswith("法"):
        return "process"
    if canon.endswith("技术"):
        return "technology"
    return "industry"


# ---------------------------------------------------------------------------
# 派生
# ---------------------------------------------------------------------------

def candidate_topics_from_news(
    *,
    title: str = "",
    entities: Iterable[Any] | None = None,
    extracted_topics: Iterable[Any] | None = None,
    keywords: Any = None,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[dict]:
    """从一条资讯派生候选关注主题（已按优先级排序、归一化去重）。

    返回 ``[{"name", "topic_type", "query_text", "source", "reason", "priority"}]``。
    """
    pool: dict[str, dict] = {}

    def offer(raw_name: Any, hint_type: Any, priority: int, reason: str) -> None:
        name = normalize_name(raw_name)
        if not name:
            return
        key = canonical_of(name)
        if key in GENERIC_TERMS or key in TOPIC_STOPWORDS:
            return
        existing = pool.get(key)
        if existing and existing["_priority"] >= priority:
            return
        group = SYNONYM_GROUPS.get(key)
        display = key if group else name
        pool[key] = {
            "name": display,
            # 词表已知的词以词表结论为准（模型常把「尼龙6」标成 technology，
            # 但它其实是产品）；词表外的词才沿用模型给的类型
            "topic_type": infer_topic_type(display, None if group else hint_type),
            "query_text": " ".join(aliases_of(key)[:4]) if group else display,
            "source": "auto",
            "reason": reason,
            "priority": priority,
            "_priority": priority,
        }

    # 1) LLM 抽取的主题
    for item in extracted_topics or []:
        if isinstance(item, dict):
            raw_name, hint = item.get("name"), item.get("type")
        else:
            raw_name, hint = item, None
        if not raw_name:
            continue
        broad = isinstance(hint, str) and hint in ("industry", "customer")
        offer(
            raw_name,
            hint if not broad else None,
            P_LLM_BROAD if broad else P_LLM_TECH,
            "这条资讯的抽取主题指向该领域",
        )

    # 2) 实体
    for entity in entities or []:
        raw = entity.get("name") if isinstance(entity, dict) else entity
        name = normalize_name(raw)
        if not name:
            continue
        canon = canonical_of(name)
        if canon in SYNONYM_GROUPS:
            offer(name, None, P_LEXICON_ENTITY, "该实体命中化工词表")
        else:
            offer(name, "industry", P_ENTITY, "该资讯涉及此主体")

    # 3) 标题里命中词表的词（标题信号强）
    for canonical in canonical_groups(title or ""):
        if canonical in SYNONYM_GROUPS:
            offer(canonical, None, P_LEXICON_TITLE, "标题中出现该关键词")

    # 4) 关键词兜底（只接受词表内的词，避免噪声）
    if isinstance(keywords, str):
        keyword_terms = _KEYWORD_SPLIT_RE.split(keywords)
    elif isinstance(keywords, (list, tuple)):
        keyword_terms = list(keywords)
    else:
        keyword_terms = []
    for term in keyword_terms:
        canon = canonical_of(str(term))
        if canon in SYNONYM_GROUPS:
            offer(canon, None, P_KEYWORD, "出现在资讯关键词中")

    ranked = sorted(pool.values(), key=lambda item: (-item["_priority"], len(item["name"])))
    result = []
    for item in ranked[: max(0, limit)]:
        item = dict(item)
        item.pop("_priority", None)
        result.append(item)
    return result


# 别名反向索引供外部（如调试脚本）使用
ALIAS_INDEX = ALIAS_TO_CANONICAL
_TITLE_TOKEN_RE = TOKEN_RE
