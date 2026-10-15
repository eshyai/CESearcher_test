"""回归测试：资讯个人屏蔽（只看不到，不是删除）。

覆盖两层：
A. **纯函数**：`coerce_uuid` 的容错、未登录时不过滤。
B. **接口集成**（TestClient 真实接口）：
   未登录 401 / 非法 id 400 / 不存在 404 → 屏蔽幂等 → 列表与检索都不再出现
   → **其他用户仍能看到（共享库不被动）** → 取消屏蔽恢复 → 取消幂等。

**不打真实外部接口**：检索那一段把 rerank 与行业相关性闸都 monkeypatch 掉，
只验证「个人屏蔽过滤」这一条链路，不消耗 token。

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_news_hidden.py industry_backend:/tmp/th.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/th.py

自建临时用户与临时资讯（标题带 uuid 后缀 + 专用 event_type 便于隔离），
结束时全部删除，不影响库中既有数据。
"""
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[1] / "app", Path("/app")):
    if (_candidate / "service").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from service import news_hidden_service as hidden  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# =====================================================================
# A. 纯函数
# =====================================================================
print("--- A. 纯函数 ---")

_u = uuid.uuid4()
check("UUID：合法字符串", hidden.coerce_uuid(str(_u)) == _u)
check("UUID：UUID 对象原样返回", hidden.coerce_uuid(_u) == _u)
check("UUID：None → None", hidden.coerce_uuid(None) is None)
check("UUID：空串 → None", hidden.coerce_uuid("") is None)
check("UUID：乱码 → None", hidden.coerce_uuid("not-a-uuid") is None)

from core.database import SessionLocal  # noqa: E402

db = SessionLocal()
check("未登录（user_id=None）不过滤", hidden.hidden_news_ids(db, None) == set())
check("非法 user_id 不过滤", hidden.hidden_news_ids(db, "oops") == set())


# =====================================================================
# B. 接口集成
# =====================================================================
print("--- B. 接口集成（屏蔽 → 过滤 → 取消）---")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.news import IndustryNews, NewsHidden  # noqa: E402
from models.user import User  # noqa: E402

TOKEN = uuid.uuid4().hex[:8]
TEST_EVENT = f"__hidden_test_{TOKEN}__"

_client = TestClient(app)


def _make_user(tag):
    name = f"hidden_{tag}_{TOKEN}"
    u = User(username=name, email=f"{name}@example.com",
             hashed_password="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u, {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'username': name})}"}


user_a, HEAD_A = _make_user("a")
user_b, HEAD_B = _make_user("b")

news = []
for i in range(3):
    row = IndustryNews(
        id=uuid.uuid4(),
        industry_id="chemical",
        title=f"屏蔽自测资讯 {TOKEN} 第 {i} 条",
        content=f"用于验证个人屏蔽的临时数据 {TOKEN}",
        summary=f"临时摘要 {TOKEN}",
        source="屏蔽自测",
        source_url=f"https://example.com/hidden-test/{TOKEN}/{i}",
        category="新闻",
        event_type=TEST_EVENT,
        extraction_status="completed",
    )
    db.add(row)
    news.append(row)
db.commit()
IDS = [str(n.id) for n in news]
LIST_Q = {"industry_id": "chemical", "event_type": TEST_EVENT, "limit": 50}

try:
    # ---- 1) 鉴权与参数校验 ----
    r = _client.post(f"/news/{IDS[0]}/hide")
    check("未登录屏蔽 → 401", r.status_code == 401, f"code={r.status_code}")

    r = _client.post("/news/not-a-uuid/hide", headers=HEAD_A)
    check("非法 news_id → 400", r.status_code == 400, f"code={r.status_code}")

    r = _client.post(f"/news/{uuid.uuid4()}/hide", headers=HEAD_A)
    check("不存在的资讯 → 404", r.status_code == 404, f"code={r.status_code}")

    # ---- 2) 基线：三条都在 ----
    r = _client.get("/news/list", params=LIST_Q, headers=HEAD_A)
    body = r.json()
    check("基线：列表 3 条", body["total"] == 3, f"total={body['total']}")
    check("基线：hidden_count 为 0", body["hidden_count"] == 0)
    check("基线：三条都在 data 里",
          {str(x["id"]) for x in body["data"]} == set(IDS))

    # ---- 3) 屏蔽第 0 条 ----
    r = _client.post(f"/news/{IDS[0]}/hide", json={"reason": "自测：不感兴趣"}, headers=HEAD_A)
    b = r.json()
    check("屏蔽成功 status=created", r.status_code == 200 and b["status"] == "created")
    check("屏蔽后 hidden_count=1", b["hidden_count"] == 1, f"count={b['hidden_count']}")

    # ---- 4) 幂等：重复屏蔽 ----
    r = _client.post(f"/news/{IDS[0]}/hide", headers=HEAD_A)
    b = r.json()
    check("重复屏蔽 status=exists（幂等）", r.status_code == 200 and b["status"] == "exists")
    check("重复屏蔽不重复计数", b["hidden_count"] == 1, f"count={b['hidden_count']}")
    check("重复屏蔽真的只有一条记录",
          db.query(NewsHidden).filter(NewsHidden.user_id == user_a.id).count() == 1)

    # ---- 5) 列表：被屏蔽的不再出现 ----
    r = _client.get("/news/list", params=LIST_Q, headers=HEAD_A)
    body = r.json()
    got = {str(x["id"]) for x in body["data"]}
    check("列表 total 减 1", body["total"] == 2, f"total={body['total']}")
    check("列表不再含被屏蔽那条", IDS[0] not in got)
    check("其余两条仍在", {IDS[1], IDS[2]}.issubset(got))
    check("列表回传 hidden_count=1", body["hidden_count"] == 1)

    # ---- 6) 关键：共享库不被动（另一个用户仍看得到）----
    r = _client.get("/news/list", params=LIST_Q, headers=HEAD_B)
    body_b = r.json()
    check("其他用户仍能看到 3 条（共享库不受影响）", body_b["total"] == 3, f"total={body_b['total']}")
    check("其他用户 hidden_count 为 0", body_b["hidden_count"] == 0)
    check("其他用户 data 含被屏蔽那条",
          IDS[0] in {str(x["id"]) for x in body_b["data"]})

    check("共享库里那条资讯本身还在",
          db.query(IndustryNews).filter(IndustryNews.id == news[0].id).first() is not None)

    # ---- 7) 已屏蔽列表 ----
    r = _client.get("/news/hidden", headers=HEAD_A)
    b = r.json()
    check("已屏蔽列表 total=1", r.status_code == 200 and b["total"] == 1)
    check("已屏蔽列表内容正确", [str(x["id"]) for x in b["data"]] == [IDS[0]])
    check("已屏蔽列表带 hidden_at", bool(b["data"][0].get("hidden_at")))

    r = _client.get("/news/hidden", headers=HEAD_B)
    check("其他用户的已屏蔽列表为空", r.json()["total"] == 0)

    # ---- 8) 检索链路：屏蔽后不再被召回 ----
    # monkeypatch 掉 rerank 与行业相关性闸，避免真实外部调用；
    # 屏蔽过滤发生在第 2b 步（进入 rerank 之前），因此只需验证候选集里没有它。
    from service.news_search_service import NewsSearchService  # noqa: E402

    def _no_rerank(self, query, docs):
        return None

    _orig_rerank = NewsSearchService._rerank
    _orig_gate = NewsSearchService.search.__globals__["get_industry_relevance_service"]
    NewsSearchService._rerank = _no_rerank

    class _PassGate:
        def filter_relevant(self, items, industry_id):
            return list(range(len(items))), []

    NewsSearchService.search.__globals__["get_industry_relevance_service"] = lambda: _PassGate()
    try:
        svc = NewsSearchService(db)
        svc.relevance = _PassGate()
        # limit 取大：库内向量召回恒返回 top-K，未建索引的自测数据只能靠关键词命中、
        # 排序靠后，limit 太小会被挤出结果页（那是排序问题，不是屏蔽问题）。
        # 这里要验证的是「屏蔽在进入 rerank 之前就把候选剔掉了」，所以取全量比较集合。
        res_before = svc.search(query=TOKEN, industry_id="chemical",
                                limit=60, include_online=False, user_id=user_b.id)
        ids_before = {str(x["id"]) for x in res_before["data"]}
        check("检索（未屏蔽用户）能召回全部自测资讯",
              set(IDS).issubset(ids_before),
              f"命中自测 {len(set(IDS) & ids_before)}/3, 总结果 {len(ids_before)}")

        res_after = svc.search(query=TOKEN, industry_id="chemical",
                               limit=60, include_online=False, user_id=user_a.id)
        ids_after = {str(x["id"]) for x in res_after["data"]}
        check("检索（已屏蔽用户）不含被屏蔽那条", IDS[0] not in ids_after)
        check("检索：同一次查询只少了被屏蔽的那条",
              {IDS[1], IDS[2]}.issubset(ids_after) and len(ids_after) == len(ids_before) - 1,
              f"before={len(ids_before)}, after={len(ids_after)}")
    finally:
        NewsSearchService._rerank = _orig_rerank
        NewsSearchService.search.__globals__["get_industry_relevance_service"] = _orig_gate

    # ---- 9) 取消屏蔽 → 恢复 ----
    r = _client.delete(f"/news/{IDS[0]}/hide", headers=HEAD_A)
    b = r.json()
    check("取消屏蔽 status=removed", r.status_code == 200 and b["status"] == "removed")
    check("取消后 hidden_count=0", b["hidden_count"] == 0, f"count={b['hidden_count']}")

    r = _client.get("/news/list", params=LIST_Q, headers=HEAD_A)
    body = r.json()
    check("取消后列表恢复 3 条", body["total"] == 3, f"total={body['total']}")
    check("取消后又能看到那条", IDS[0] in {str(x["id"]) for x in body["data"]})

    r = _client.delete(f"/news/{IDS[0]}/hide", headers=HEAD_A)
    check("重复取消 status=absent（幂等）", r.status_code == 200 and r.json()["status"] == "absent")

    r = _client.get("/news/hidden", headers=HEAD_A)
    check("取消后已屏蔽列表为空", r.json()["total"] == 0)

    # ---- 10) 合法性：取消屏蔽不影响资讯本体 ----
    check("取消屏蔽后资讯本体仍在",
          db.query(IndustryNews).filter(IndustryNews.id == news[0].id).first() is not None)

finally:
    # ---- 清理：删临时用户（CASCADE 清 news_hidden）与临时资讯 ----
    try:
        db.rollback()
        db.query(NewsHidden).filter(NewsHidden.news_id.in_([n.id for n in news])).delete(
            synchronize_session=False)
        db.query(IndustryNews).filter(IndustryNews.id.in_([n.id for n in news])).delete(
            synchronize_session=False)
        db.query(User).filter(User.id.in_([user_a.id, user_b.id])).delete(
            synchronize_session=False)
        db.commit()
        print("清理完成：临时用户与临时资讯已删除")
    except Exception as e:  # pragma: no cover
        db.rollback()
        print(f"清理失败（需手工处理）: {e}")
    finally:
        db.close()

passed, total = sum(results), len(results)
print(f"\n===== {passed}/{total} 通过 =====")
sys.exit(0 if passed == total else 1)
