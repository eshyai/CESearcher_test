"""容器镜像冒烟测试：在容器内验证镜像内容完整、依赖齐全、能连上中间件。

用法（项目根目录，需中间件已在 industry_network 上运行）:

    docker run --rm -i --network industry_network \
      --env-file backend/.env \
      -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e MILVUS_HOST=milvus \
      -v "$PWD/backend/test/smoke_container.py:/tmp/smoke.py:ro" \
      chemical-agent-backend:dev python /tmp/smoke.py

或不挂脚本，直接管道喂进去:

    docker run --rm -i --network industry_network --env-file backend/.env \
      -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e MILVUS_HOST=milvus \
      chemical-agent-backend:dev python - < backend/test/smoke_container.py

检查项:
    1. 关键第三方包 import（覆盖曾经缺失过的 redis/bcrypt/jose/trafilatura 等）
    2. app_main 导入（会触发 SQLAlchemy engine 建连 + create_all）
    3. 路由注册数量
    4. TestClient 打几个真实接口（/hello、/news/industries、/news/search）
"""
import sys
import traceback

FAILED = []


def step(name):
    print(f"\n--- {name} ---")


def check(name, fn):
    try:
        result = fn()
        print(f"  [OK] {name}: {result}")
        return result
    except Exception as exc:  # noqa: BLE001
        FAILED.append(name)
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


# ---------- 1. 依赖 import（历史缺失项全覆盖） ----------
def deps():
    import importlib

    mods = [
        "fastapi", "uvicorn", "sqlalchemy", "psycopg2", "redis",
        "bcrypt", "jose", "pydantic", "dotenv", "httpx", "requests",
        "pymilvus", "dashscope", "openai", "apscheduler",
        "pandas", "numpy", "jieba", "tiktoken", "lxml",
        "bs4", "trafilatura", "matplotlib", "seaborn", "langgraph",
        "llama_index", "alibabacloud_docmind_api20220711",
    ]
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{m}({type(exc).__name__})")
    if bad:
        raise RuntimeError("以下依赖导入失败: " + ", ".join(bad))
    return f"{len(mods)} 个依赖全部导入成功"


# ---------- 2. 应用导入 + 数据库建连 ----------
def app_import():
    import app_main

    n = len(app_main.app.routes)
    return f"app_main 导入成功，注册路由 {n} 条"


def db_ping():
    from sqlalchemy import text

    from core.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        cnt = db.execute(text("SELECT count(*) FROM industry_news")).scalar()
        return f"PostgreSQL 连接正常，industry_news 现有 {cnt} 条"
    finally:
        db.close()


def redis_ping():
    from core.redis_client import get_redis_client

    return f"Redis PING = {get_redis_client().ping()}"


def milvus_check():
    from service.milvus_service import MilvusService

    stats = MilvusService().get_collection_stats("news_chemical")
    return f"Milvus 连接正常，news_chemical 统计: {stats}"


# ---------- 3. 接口冒烟 ----------
def api_smoke():
    from fastapi.testclient import TestClient

    import app_main

    c = TestClient(app_main.app)
    out = []

    r = c.get("/hello")
    out.append(f"/hello={r.status_code}")

    r = c.get("/news/industries")
    out.append(f"/news/industries={r.status_code}")

    r = c.post("/news/search", json={"query": "环氧丙烷", "include_online": False})
    body = r.json() if r.status_code == 200 else {}
    out.append(
        f"/news/search={r.status_code} total={body.get('total')} "
        f"vector_indexed={body.get('vector_indexed')} mode={body.get('mode')}"
    )

    bad = [o for o in out if "=200" not in o]
    if bad:
        raise RuntimeError("非 200 响应: " + ", ".join(bad) + f" | body={r.text[:400]}")
    return " | ".join(out)


def main():
    step("1. 第三方依赖 import")
    check("deps", deps)

    step("2. 应用与中间件")
    check("app_main 导入", app_import)
    check("PostgreSQL", db_ping)
    check("Redis", redis_ping)
    check("Milvus", milvus_check)

    step("3. 接口冒烟（TestClient，不占端口）")
    check("api", api_smoke)

    print("\n" + "=" * 56)
    if FAILED:
        print(f"结果: 失败 {len(FAILED)} 项 -> {', '.join(FAILED)}")
        return 1
    print("结果: 镜像与中间件全部验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
