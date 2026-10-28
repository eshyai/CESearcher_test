"""回归测试：共享资讯库（收录池 + 批注）

覆盖：
A. **纯函数**：批注内容校验（空 / 超长 / 首尾空白）。
B. **接口集成**（TestClient 真实接口）：
   匿名 401 / 非法 id 400 / 不存在的资讯 404 → 收录幂等 → 列表与关键词 →
   批注共享可见、**改别人的 403**、改自己的 200、删除是**软删**（行还在）→
   下架后 active 列表消失、archived 列表可见、**再次收录能恢复** → 清理。

**不打外部接口**：不触碰检索/大模型，只验证收录池与批注这层。

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_news_library.py industry_backend:/tmp/tl.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/tl.py

自建两个临时用户 + 两条临时资讯，结束时全部删除（CASCADE 会带走收录与批注）。
"""
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[1] / "app", Path("/app")):
    if (_candidate / "service").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from service import news_library_service as lib  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# =====================================================================
# A. 纯函数
# =====================================================================
print("--- A. 纯函数（批注校验）---")

try:
    lib._validate_content("   ")
    check("空批注被拒", False)
except lib.NewsLibraryError as exc:
    check("空批注被拒", exc.status_code == 400, f"msg={exc.message}")

try:
    lib._validate_content("x" * (lib.ANNOTATION_MAX_LEN + 1))
    check("超长批注被拒", False)
except lib.NewsLibraryError as exc:
    check("超长批注被拒", exc.status_code == 400, f"msg={exc.message}")

check("首尾空白被裁掉", lib._validate_content("  hi  ") == "hi")
check("正好 2000 字放行", lib._validate_content("x" * lib.ANNOTATION_MAX_LEN) != "")

# =====================================================================
# B. 接口集成
# =====================================================================
print("--- B. 接口集成（收录 → 批注 → 下架 → 恢复）---")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.news import IndustryNews  # noqa: E402
from models.news_library import NewsAnnotation, NewsLibraryItem  # noqa: E402
from models.user import User  # noqa: E402

TOKEN = uuid.uuid4().hex[:8]
TEST_EVENT = f"__library_test_{TOKEN}__"

db = SessionLocal()
client = TestClient(app)


def _make_user(tag):
    name = f"lib_{tag}_{TOKEN}"
    u = User(username=name, email=f"{name}@example.com", hashed_password="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u, {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'username': name})}"}


user_a, HEAD_A = _make_user("a")
user_b, HEAD_B = _make_user("b")

news = []
for i in range(2):
    row = IndustryNews(
        id=uuid.uuid4(),
        industry_id="chemical",
        title=f"共享库自测资讯 {TOKEN} 第 {i} 条",
        content=f"用于验证共享资讯库的临时数据 {TOKEN}",
        summary=f"临时摘要 {TOKEN}",
        source="共享库自测",
        source_url=f"https://example.com/library-test/{TOKEN}/{i}",
        category="新闻",
        event_type=TEST_EVENT,
        extraction_status="completed",
    )
    db.add(row)
    news.append(row)
db.commit()
N0, N1 = str(news[0].id), str(news[1].id)
extra = None

try:
    # ---- 1) 鉴权与参数校验 ----
    r = client.post("/news/library", json={"news_id": N0})
    check("匿名收录 → 401", r.status_code == 401, f"code={r.status_code}")

    r = client.get("/news/library")
    check("匿名看列表 → 401", r.status_code == 401, f"code={r.status_code}")

    r = client.post("/news/library", json={"news_id": "not-a-uuid"}, headers=HEAD_A)
    check("非法 news_id → 400", r.status_code == 400, f"code={r.status_code}")

    r = client.post("/news/library", json={"news_id": str(uuid.uuid4())}, headers=HEAD_A)
    check("不存在的资讯 → 404", r.status_code == 404, f"code={r.status_code}")

    # ---- 2) 基线（库里有真实数据，用相对值判定）----
    r = client.get("/news/library", params={"status": "active", "limit": 1}, headers=HEAD_A)
    baseline = r.json()["total"]
    check("基线可读", r.status_code == 200, f"total={baseline}")

    # ---- 3) 收录 + 幂等 ----
    r = client.post("/news/library", json={"news_id": N0}, headers=HEAD_A)
    body = r.json()
    check("收录成功 created=True", r.status_code == 200 and body["created"] is True)
    check("收录回传收录人", body["data"]["added_by_name"] == user_a.username,
          f"by={body['data'].get('added_by_name')}")

    r = client.post("/news/library", json={"news_id": N0}, headers=HEAD_B)
    body = r.json()
    check("重复收录 created=False（幂等）", r.status_code == 200 and body["created"] is False)

    r = client.get("/news/library", params={"status": "active", "limit": 1}, headers=HEAD_A)
    check("列表 total +1", r.json()["total"] == baseline + 1, f"total={r.json()['total']}")
    check("库里只有一行收录记录",
          db.query(NewsLibraryItem).filter(NewsLibraryItem.news_id == news[0].id).count() == 1)

    # ---- 4) 关键词过滤 ----
    client.post("/news/library", json={"news_id": N1}, headers=HEAD_A)
    r = client.get("/news/library", params={"keyword": TOKEN, "limit": 50}, headers=HEAD_A)
    got = {x["id"] for x in r.json()["data"]}
    check("关键词命中两条自测资讯", {N0, N1}.issubset(got), f"got={len(got)}")
    check("关键词过滤不会带出无关条目", r.json()["total"] == 2, f"total={r.json()['total']}")

    # ---- 5) 批注：共享可见、作者隔离 ----
    r = client.post(f"/news/library/{N0}/annotations",
                    json={"content": f"B 的批注 {TOKEN}"}, headers=HEAD_B)
    check("B 加批注成功", r.status_code == 200, f"code={r.status_code}")
    ann_b = r.json()["data"]["id"]
    check("B 的批注 mine=True", r.json()["data"]["mine"] is True)

    r = client.post(f"/news/library/{N0}/annotations",
                    json={"content": f"A 的批注 {TOKEN}"}, headers=HEAD_A)
    ann_a = r.json()["data"]["id"]

    r = client.get(f"/news/library/{N0}", headers=HEAD_A)
    detail = r.json()["data"]
    check("A 能看到 B 的批注（共享）", detail["annotation_count"] == 2, f"n={detail['annotation_count']}")
    mine_map = {x["id"]: x["mine"] for x in detail["annotations"]}
    check("A 视角：自己的 mine=True，B 的 False",
          mine_map.get(ann_a) is True and mine_map.get(ann_b) is False)
    check("批注带作者名", all(x.get("username") for x in detail["annotations"]))

    r = client.put(f"/news/annotations/{ann_a}", json={"content": "越权改"}, headers=HEAD_B)
    check("改别人的批注 → 403", r.status_code == 403, f"code={r.status_code}")

    r = client.put(f"/news/annotations/{ann_a}", json={"content": f"A 改过的批注 {TOKEN}"},
                   headers=HEAD_A)
    check("改自己的批注 → 200", r.status_code == 200)
    check("批注内容已更新", "改过" in r.json()["data"]["content"])

    r = client.delete(f"/news/annotations/{ann_b}", headers=HEAD_A)
    check("删别人的批注 → 403", r.status_code == 403, f"code={r.status_code}")

    r = client.delete(f"/news/annotations/{ann_a}", headers=HEAD_A)
    check("删自己的批注 → 200", r.status_code == 200)

    row = db.query(NewsAnnotation).filter(NewsAnnotation.id == uuid.UUID(ann_a)).first()
    check("删除是软删（行还在，deleted_at 非空）", row is not None and row.deleted_at is not None)
    r = client.get(f"/news/library/{N0}", headers=HEAD_A)
    check("列表不再展示已删批注", r.json()["data"]["annotation_count"] == 1)

    # ---- 6) 下架 / 恢复 ----
    r = client.delete(f"/news/library/{N0}", headers=HEAD_A)
    check("下架返回 archived", r.status_code == 200 and r.json()["status"] == "archived",
          f"body={r.json()}")

    r = client.get("/news/library", params={"status": "active", "limit": 1}, headers=HEAD_A)
    check("active 列表不再含它", r.json()["total"] == baseline + 1,
          f"total={r.json()['total']}（下架前是 baseline+2）")

    r = client.get("/news/library", params={"status": "archived", "keyword": TOKEN}, headers=HEAD_A)
    check("archived 列表能看到", {x["id"] for x in r.json()["data"]} == {N0})

    item = db.query(NewsLibraryItem).filter(NewsLibraryItem.news_id == news[0].id).first()
    check("下架是软删（行还在，archived_by 已记）",
          item is not None and item.status == "archived" and item.archived_by is not None)

    r = client.delete(f"/news/library/{N0}", headers=HEAD_A)
    check("重复下架幂等 → absent", r.json()["status"] == "absent", f"body={r.json()}")

    r = client.post("/news/library", json={"news_id": N0}, headers=HEAD_B)
    body = r.json()
    check("重新收录能恢复上架", body["data"]["status"] == "active", f"status={body['data']['status']}")
    check("恢复后收录人仍是原来那位", body["data"]["added_by_name"] == user_a.username,
          f"by={body['data'].get('added_by_name')}")

    # ---- 7) 未收录的资讯也能看详情（status=None）----
    extra = IndustryNews(
        id=uuid.uuid4(), industry_id="chemical", title=f"未收录 {TOKEN}",
        content="x", source="自测", source_url=f"https://example.com/library-test/{TOKEN}/x",
        category="新闻", extraction_status="completed",
    )
    db.add(extra)
    db.commit()
    r = client.get(f"/news/library/{extra.id}", headers=HEAD_A)
    check("未收录资讯详情 status=None", r.status_code == 200 and r.json()["data"]["status"] is None)

finally:
    # 清理：删资讯（CASCADE 带走收录与批注）+ 删用户
    for row in news + ([extra] if extra is not None else []):
        db.delete(row)
    db.commit()
    db.query(NewsLibraryItem).filter(NewsLibraryItem.added_by == user_a.id).delete()
    db.query(NewsAnnotation).filter(NewsAnnotation.user_id.in_([user_a.id, user_b.id])).delete()
    db.delete(user_b)
    db.delete(user_a)
    db.commit()
    db.close()
    print("清理完成：临时用户/资讯/收录/批注已删除")

print(f"\n===== {sum(results)}/{len(results)} 通过 =====")
sys.exit(0 if all(results) else 1)
