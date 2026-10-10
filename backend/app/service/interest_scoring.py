"""无数据库依赖的兴趣学习规则，便于单元测试和离线重算。

本模块负责「一条资讯是否命中某个关注主题」的判定，是 L1/L2 偏好学习的地基。

### 相对旧实现的三处升级（L1.5）

| 旧行为 | 新行为 | 解决的问题 |
|---|---|---|
| 正则切词 + 子串包含，任一词命中即算命中 | 词表**最长匹配** + 同义归并 + **覆盖率阈值** | 「收购」这类泛词命中一堆无关主题；「PA6」误命中「PA66」 |
| 无同义词概念 | `chem_lexicon.SYNONYM_GROUPS` 把 PA6/尼龙6/聚酰胺6 归并为同一主题 | 用户写 PA6、新闻写尼龙6 时匹配不上 |
| 每个词都算数 | `GENERIC_TERMS` 里的泛词**单独出现不算命中** | 主题名带「价格/装置/产能」时权重被噪声带偏 |

匹配规则：
1. 用词表在文本上做**最长匹配优先 + 区间吞并**（解决 `聚酰胺6` vs `聚酰胺66`、
   `尼龙` vs `尼龙6` 这类包含关系）；
2. ASCII 别名（PA6、MDI、PET…）额外要求**词边界**，避免 `PA6` 命中 `PA66`；
3. 词表未覆盖的词退化为字面包含（与旧实现一致，保证兼容）；
4. 命中判定：非泛词需要满足 ``ceil(非泛词数 × 0.5)``（≤2 个词时只要求 1 个）。
"""
from __future__ import annotations

import math
import re

from service.chem_lexicon import (
    ALIAS_TO_CANONICAL,
    GENERIC_TERMS,
    SORTED_ALIASES,
)

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9+._-]{1,}")

# 纯 ASCII 别名（需要词边界判断）
_ASCII_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")


def _ascii_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")


_ASCII_ALIAS_PATTERNS: dict[str, re.Pattern[str]] = {
    alias: _ascii_pattern(alias) for alias in SORTED_ALIASES if _ASCII_ALIAS_RE.match(alias)
}


def extract_terms(text: str) -> set[str]:
    """粗切词（保留旧接口）：中文按 2 字以上连续块，英文数字按连续 token。"""
    return {token.lower() for token in TOKEN_RE.findall(text or "")}


# ---------------------------------------------------------------------------
# 别名扫描
# ---------------------------------------------------------------------------

def find_alias_hits(text_lower: str) -> list[tuple[int, int, str]]:
    """扫描文本中出现的全部词表别名，返回 ``(start, end, canonical)`` 列表。"""
    if not text_lower:
        return []
    hits: list[tuple[int, int, str]] = []
    for alias in SORTED_ALIASES:
        if alias not in text_lower:  # C 级快速预筛，绝大多数别名在这里被跳过
            continue
        canonical = ALIAS_TO_CANONICAL[alias]
        pattern = _ASCII_ALIAS_PATTERNS.get(alias)
        if pattern is not None:
            hits.extend((m.start(), m.end(), canonical) for m in pattern.finditer(text_lower))
            continue
        start = text_lower.find(alias)
        while start != -1:
            hits.append((start, start + len(alias), canonical))
            start = text_lower.find(alias, start + 1)
    return hits


def resolve_hits(hits: list[tuple[int, int, str]]) -> tuple[list[str], list[tuple[int, int]]]:
    """最长匹配优先 + 区间吞并。

    返回命中的规范名（按出现顺序去重）与已被占用的字符区间。
    这样 ``聚酰胺66`` 会吞掉其中的 ``聚酰胺6``，``尼龙6`` 会吞掉其中的 ``尼龙``。
    """
    accepted: list[tuple[int, int, str]] = []
    groups: list[str] = []
    for start, end, canonical in sorted(hits, key=lambda h: (-(h[1] - h[0]), h[0])):
        if any(not (end <= s or start >= e) for s, e, _ in accepted):
            continue
        accepted.append((start, end, canonical))
        if canonical not in groups:
            groups.append(canonical)
    return groups, [(s, e) for s, e, _ in accepted]


def canonical_groups(text: str) -> list[str]:
    """返回文本命中的规范名列表（最长匹配优先 / 已去重）。"""
    return resolve_hits(find_alias_hits((text or "").lower()))[0]


class TextIndex:
    """一次分词、多次比对的文本索引（``enrich_and_rank`` 里按资讯建一次）。"""


    __slots__ = ("raw", "groups", "_spans")

    def __init__(self, text: str):
        self.raw = (text or "").lower()
        self.groups, self._spans = resolve_hits(find_alias_hits(self.raw))

    def has_group(self, canonical: str) -> bool:
        return canonical in self.groups

    def contains(self, term: str) -> bool:
        """字面包含；纯 ASCII 词要求词边界。"""
        term = (term or "").lower()
        if not term:
            return False
        if _ASCII_ALIAS_RE.match(term):
            return bool(_ascii_pattern(term).search(self.raw))
        return term in self.raw


class TopicQuery:
    """一个关注主题编译后的匹配条件。"""

    __slots__ = ("groups", "extras", "meaningful")

    def __init__(self, groups: list[str], extras: list[str]):
        self.groups = groups
        self.extras = extras
        self.meaningful = [t for t in (list(groups) + list(extras)) if t not in GENERIC_TERMS]


def compile_query(text: str) -> TopicQuery:
    """把一个主题的「名称 + 匹配词」编译成匹配条件。"""
    lowered = (text or "").lower()
    groups, spans = resolve_hits(find_alias_hits(lowered))
    extras: list[str] = []
    for match in TOKEN_RE.finditer(lowered):
        if any(not (match.end() <= s or match.start() >= e) for s, e in spans):
            continue  # 已被词表别名覆盖
        token = match.group(0)
        if token not in extras:
            extras.append(token)
    return TopicQuery(groups, extras)


def compile_topic(topic) -> TopicQuery:
    """由 ``FocusTopic`` 记录编译匹配条件（名称与匹配词合并权重大于等于 1）。"""
    name = getattr(topic, "name", "") or ""
    query_text = getattr(topic, "query_text", "") or ""
    return compile_query(f"{name} {query_text}")


def required_hits(meaningful_count: int) -> int:
    """多词主题的覆盖率阈值：≤2 个词要求 1 个，3~4 个要求 2 个，依次类推。"""
    if meaningful_count <= 2:
        return 1
    return math.ceil(meaningful_count * 0.5)


def query_hits(query: TopicQuery, index: TextIndex) -> tuple[int, int]:
    hits = 0
    for term in query.meaningful:
        if term in query.groups:
            if index.has_group(term):
                hits += 1
        elif index.contains(term):
            hits += 1
    return hits, len(query.meaningful)


def query_matches(query: TopicQuery, index: TextIndex) -> bool:
    if query.meaningful:
        hits, total = query_hits(query, index)
        return hits >= required_hits(total)
    # 主题全部由泛词构成（例如主题就叫「检修」）：退化为「出现即命中」
    return any(index.has_group(g) for g in query.groups) or any(
        index.contains(e) for e in query.extras
    )


def text_matches(query: str, text: str) -> bool:
    """便捷入口（保留旧签名）：判断 query 是否命中 text。"""
    return query_matches(compile_query(query), TextIndex(text))


# ---------------------------------------------------------------------------
# 权重更新
# ---------------------------------------------------------------------------

def feedback_signal(interested: bool | None, relevant: bool | None) -> float:
    values = []
    if interested is not None:
        values.append(1.0 if interested else -1.0)
    if relevant is not None:
        values.append(1.0 if relevant else -1.0)
    return sum(values) / len(values) if values else 0.0


def updated_weight(old_weight: float, signal: float) -> float:
    return round(max(-2.0, min(2.0, 0.85 * old_weight + 0.35 * signal)), 4)
