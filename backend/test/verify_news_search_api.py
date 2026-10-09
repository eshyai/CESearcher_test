"""验证 /news/search 接口（TestClient，不依赖已启动的后端进程）。

用法（backend 目录）:
    python test/verify_news_search_api.py
"""
import sys

sys.path.insert(0, "app")

from dotenv import load_dotenv

load_dotenv(".env")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402

client = TestClient(app)

CASES = [
    ("主题词", "危化品 监管"),
    ("语义片段", "装置停车检修影响供应"),
    ("公司名", "万华化学"),
    ("产品名", "环氧丙烷"),
]


def main() -> int:
    passed = 0
    for label, q in CASES:
        r = client.post(
            "/news/search",
            json={"query": q, "industry_id": "chemical", "limit": 5},
        )
        print(f'--- [{label}] "{q}" -> HTTP {r.status_code}')
        if r.status_code != 200:
            print("    FAIL:", r.text[:200])
            continue
        d = r.json()
        assert d["success"] is True
        assert isinstance(d["data"], list)
        print(
            f'    mode={d["mode"]} indexed={d["vector_indexed"]} '
            f'total={d["total"]} 查询词={d["query"]}'
        )
        for it in d["data"][:3]:
            print(
                f'      {it.get("score")} [{it.get("match_reason")}] '
                f'{(it.get("title") or "")[:42]}'
            )
        passed += 1

    # 边界：空查询应返回空结果而非报错
    r = client.post("/news/search", json={"query": "  ", "industry_id": "chemical"})
    print(f"空查询 -> HTTP {r.status_code} mode={r.json().get('mode')}")

    # 边界：不存在的关键词应返回空列表（相关度阈值生效）
    r = client.post(
        "/news/search",
        json={"query": "zzz不存在的关键词zzz", "industry_id": "chemical"},
    )
    n_none = r.json().get("total")
    print(f"无命中 -> HTTP {r.status_code} total={n_none}")
    assert n_none == 0, f"无关查询应返回 0 条，实际 {n_none}"

    print(f"\n{passed}/{len(CASES)} 接口用例通过")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
