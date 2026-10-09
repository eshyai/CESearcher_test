"""验证「库内 + 全网」合并检索与沉淀。

用法（backend 目录）:
    ../.venv/Scripts/python.exe test/verify_news_online_search.py
"""
import logging
import sys

sys.path.insert(0, "app")

from dotenv import load_dotenv

load_dotenv(".env")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from core.database import SessionLocal
from models.news import IndustryNews
from service.news_search_service import get_news_search_service

CASES = [
    ("产品名", "环氧丙烷"),
    ("公司名", "万华化学"),
    ("政策片段", "危化品 监管"),
    ("描述性片段", "装置停车检修影响供应"),
]


def main() -> int:
    db = SessionLocal()
    svc = get_news_search_service(db)

    before = db.query(IndustryNews).count()
    print(f"检索前 industry_news 总数: {before}")
    print()

    persisted_total = 0
    ok = True

    for label, q in CASES:
        try:
            r = svc.search(q, industry_id="chemical", limit=8, include_online=True)
        except Exception as e:
            print(f"[FAIL] {label} {q!r} 异常: {e}")
            ok = False
            continue
        print(
            f"--- [{label}] {q!r}  mode={r['mode']}  total={r['total']}  "
            f"online_total={r['online_total']}  online_kept={r['online_kept']}  "
            f"persisted={r['persisted']}"
        )
        for it in r["data"]:
            tag = "库内" if it.get("source_type") == "library" else "全网"
            print(
                f"    [{tag}] {it['score']:.3f} ({it.get('match_reason')}) "
                f"[{it.get('source') or '-'}] {(it.get('title') or '')[:44]}"
            )
        print()
        persisted_total += r.get("persisted", 0)

    # 只查库内，验证 include_online 关闭时行为
    r_lib = svc.search("危化品 监管", industry_id="chemical", limit=5, include_online=False)
    print(
        f"--- [仅库内] include_online=False  total={r_lib['total']}  "
        f"online_kept={r_lib['online_kept']}  persisted={r_lib['persisted']}"
    )
    if r_lib["online_kept"] != 0 or r_lib["persisted"] != 0:
        print("[FAIL] 关闭全网后仍有全网结果")
        ok = False
    else:
        print("[PASS] include_online=False 只返回库内结果")

    db.expire_all()
    after = db.query(IndustryNews).count()
    print()
    print(f"检索后 industry_news 总数: {after}  (本次沉淀 {persisted_total} 条)")
    print(f"结果: {'通过' if ok else '存在失败项'}")
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
