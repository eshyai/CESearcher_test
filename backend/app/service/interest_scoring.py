"""无数据库依赖的兴趣学习规则，便于单元测试和离线重算。"""
import re


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9+._-]{1,}")


def extract_terms(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text or "")}


def text_matches(query: str, text: str) -> bool:
    terms = extract_terms(query)
    lowered = (text or "").lower()
    return bool(terms) and any(term in lowered for term in terms)


def feedback_signal(interested: bool | None, relevant: bool | None) -> float:
    values = []
    if interested is not None:
        values.append(1.0 if interested else -1.0)
    if relevant is not None:
        values.append(1.0 if relevant else -1.0)
    return sum(values) / len(values) if values else 0.0


def updated_weight(old_weight: float, signal: float) -> float:
    return round(max(-2.0, min(2.0, 0.85 * old_weight + 0.35 * signal)), 4)
