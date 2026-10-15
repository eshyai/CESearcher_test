"""L2a 回归测试：归纳引擎（建议箱 + 快照回滚）。

分两部分：
A. **纯函数**（不需要数据库）：JSON 抠取、同义归一、模型输出的清洗规则
   （悬空建议丢弃 / 泛词丢弃 / 无证据丢弃 / 合并需 ≥2 条记录 / drop 需无正向证据 / insight 转建议）；
B. **DB 集成**（走 TestClient 真实接口）：归纳产出建议进 pending 箱 → 逐条 accept / reject
   → 主题按 kind 落地（add 建主题、adjust 改权重、merge 合并停用、drop 停用）
   → 回滚精确撤销该次归纳的影响，且不误伤之后手动建的主题。

**不打真实 LLM**：B 部分用 monkeypatch 把 ``lma_induction_service._chat_json`` 换成固定输出，
所以跑测试不消耗 token、不依赖网络。

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_l2a_induction.py industry_backend:/tmp/t2.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/t2.py

B 部分会自建临时用户（用户名带 uuid 后缀），结束时整户删除（CASCADE 清干净），
不影响库中既有数据。
"""
import json
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[1] / "app", Path("/app")):
    if (_candidate / "service").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from service import lma_induction_service as lma  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# =====================================================================
# A. 纯函数
# =====================================================================
print("--- A. 纯函数（解析 / 归一 / 输出清洗）---")

# 1) JSON 抠取：容忍代码围栏与前后废话
check("解析：纯 JSON", lma._parse_json_object('{"a":1}') == {"a": 1})
check("解析：``` 围栏", lma._parse_json_object('```json\n{"a":1}\n```') == {"a": 1})
check("解析：前后有废话",
      lma._parse_json_object('好的，结果如下：{"a":1} 以上。') == {"a": 1})
check("解析：数组不算对象", lma._parse_json_object('[1,2]') is None)
check("解析：空串", lma._parse_json_object("") is None)
check("解析：非法内容", lma._parse_json_object("完全不是 json") is None)

# 2) 同义归一 = 主题身份键
check("身份键：PA6 == 尼龙6", lma._topic_key("PA6") == lma._topic_key("尼龙6"))
check("身份键：PA6 != PA66", lma._topic_key("PA6") != lma._topic_key("PA66"))

# 3) 模型把对象写成裸字符串也能收
check("裸字符串归一", lma._entry("己内酰胺").get("name") == "己内酰胺")
check("dict 原样通过", lma._entry({"name": "x"}).get("name") == "x")

# 4) 数值兜底
check("_to_float 非法回退", lma._to_float("abc", 0.5) == 0.5)
check("_clamp 上界", lma._clamp(9.0) == 2.0)
check("_clamp 下界", lma._clamp(-9.0) == -2.0)

# 5) 输出清洗
from models.news import FocusTopic  # noqa: E402


def _topic(name, ttype="product", weight=1.0, active=True):
    return FocusTopic(id=uuid.uuid4(), user_id=uuid.uuid4(), topic_type=ttype,
                      name=name, query_text=name, weight=weight,
                      positive_count=0, negative_count=0, is_active=active,
                      source="manual", auto_hits=0)


topics = [_topic("PA6"), _topic("尼龙6"), _topic("可降解塑料"), _topic("页岩气", "industry")]
samples = [
    {"id": "s1", "interested": True, "relevant": True, "title": "己内酰胺装置投产",
     "summary": "己内酰胺产能增加", "entities": ["己内酰胺"], "topics": [], "event_type": "投产扩产"},
    {"id": "s2", "interested": True, "relevant": True, "title": "己内酰胺价格上行",
     "summary": "", "entities": ["己内酰胺"], "topics": [], "event_type": "价格变动"},
    {"id": "s3", "interested": True, "relevant": True, "title": "可降解塑料产能扩张",
     "summary": "", "entities": ["可降解塑料"], "topics": [], "event_type": "投产扩产"},
    {"id": "s4", "interested": False, "relevant": False, "title": "某地发现页岩气",
     "summary": "", "entities": ["页岩气"], "topics": [], "event_type": "其他"},
]

service = lma.LmaInductionService(db=None)  # 纯函数路径不需要 session
raw = {
    "add_topics": [
        {"name": "己内酰胺", "type": "product", "reason": "近30天正向反馈多条", "confidence": 0.86},
        "裸字符串主题",                                   # 无证据 → 丢弃
        {"name": "价格", "type": "industry", "reason": "泛词", "confidence": 0.9},   # 泛词 → 丢弃
        {"name": "有限公司", "type": "industry", "reason": "机构名", "confidence": 0.9},  # → 丢弃
        {"name": "PA6", "type": "product", "reason": "已存在", "confidence": 0.9},    # 已存在 → 丢弃
    ],
    "adjust_topics": [
        {"name": "尼龙6", "delta": 0.4, "reason": "反馈转强", "confidence": 0.72},
        {"name": "不存在的主题", "delta": 0.5, "reason": "悬空", "confidence": 0.5},  # 悬空 → 丢弃
        {"name": "可降解塑料", "delta": 0.0, "reason": "无变化", "confidence": 0.5},  # delta=0 → 丢弃
    ],
    "merge_topics": [
        {"targets": ["PA6", "尼龙6"], "into": "聚酰胺6", "reason": "同一物质的两种写法", "confidence": 0.91},
        {"targets": ["PA6"], "into": "只一个", "reason": "不足两条", "confidence": 0.5},  # → 丢弃
    ],
    "drop_topics": [
        {"name": "页岩气", "reason": "长期无正向反馈", "confidence": 0.95},
        {"name": "可降解塑料", "reason": "样本里还有正向证据", "confidence": 0.9},     # → 丢弃
    ],
    "insights": ["你近期关注己内酰胺产业链", "  ", "第二条结论"],
}

prepared, insights = service._normalize(raw, topics, samples)
by_kind = {}
for item in prepared:
    by_kind.setdefault(item["kind"], []).append(item)

check("清洗：add 只留 1 条（己内酰胺）",
      len(by_kind.get("add", [])) == 1 and by_kind["add"][0]["name"] == "己内酰胺",
      str([i["name"] for i in by_kind.get("add", [])]))
check("清洗：add 带证据（2 条反馈）",
      by_kind["add"][0]["evidence"] == ["s1", "s2"], str(by_kind["add"][0]["evidence"]))
check("清洗：add 类型为 product", by_kind["add"][0]["payload"]["topic_type"] == "product")
check("清洗：裸字符串主题因无证据被丢弃",
      all(i["name"] != "裸字符串主题" for i in by_kind.get("add", [])))
check("清洗：泛词 / 机构名不建主题",
      all(i["name"] not in ("价格", "有限公司") for i in by_kind.get("add", [])))
check("清洗：adjust 只留尼龙6",
      len(by_kind.get("adjust", [])) == 1 and by_kind["adjust"][0]["name"] == "尼龙6",
      str([i["name"] for i in by_kind.get("adjust", [])]))
check("清洗：adjust 带 target_id", by_kind["adjust"][0]["target_id"] is not None)
check("清洗：delta=0 不产生建议", all(i["name"] != "可降解塑料" for i in by_kind.get("adjust", [])))
check("清洗：merge 只留 1 条（聚酰胺6）",
      len(by_kind.get("merge", [])) == 1 and by_kind["merge"][0]["name"] == "聚酰胺6",
      str([i["name"] for i in by_kind.get("merge", [])]))
check("清洗：merge 记录了两条被合并的主题",
      len(by_kind["merge"][0]["payload"]["topic_ids"]) == 2,
      str(by_kind["merge"][0]["payload"]))
check("清洗：drop 只留页岩气",
      len(by_kind.get("drop", [])) == 1 and by_kind["drop"][0]["name"] == "页岩气",
      str([i["name"] for i in by_kind.get("drop", [])]))
check("清洗：有正向证据的主题不被淘汰",
      all(i["name"] != "可降解塑料" for i in by_kind.get("drop", [])))
# 每条非空结论各生成一条 insight 建议（空白条被丢弃），供用户逐条「知道了 / 忽略」
check("清洗：insight 转为建议（2 条非空结论）", len(by_kind.get("insight", [])) == 2)
check("清洗：insight 不落库（payload 空）", by_kind["insight"][0]["payload"] == {})
check("清洗：insights 去空白后 2 条", insights == ["你近期关注己内酰胺产业链", "第二条结论"], str(insights))
check("清洗：建议总数 = 6（add1+adjust1+merge1+drop1+insight2）",
      len(prepared) == 6, str(len(prepared)))


# =====================================================================
# B. DB 集成
# =====================================================================
print("--- B. DB 集成（归纳 → 建议箱 → 确认 → 回滚）---")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.news import (  # noqa: E402
    FocusTopic,
    FocusTopicRevision,
    IndustryNews,
    NewsFeedback,
    PreferenceSuggestion,
)
from models.user import User  # noqa: E402

USERNAME = f"l2a_test_{uuid.uuid4().hex[:8]}"

db = SessionLocal()
user = User(username=USERNAME, email=f"{USERNAME}@example.com",
            hashed_password="x", is_active=True)
db.add(user)
db.commit()
db.refresh(user)
USER_ID = user.id

_client = TestClient(app)
HEADERS = {"Authorization": f"Bearer {create_access_token({'sub': str(USER_ID), 'username': USERNAME})}"}


def _news(title, entities, summary=""):
    row = IndustryNews(id=uuid.uuid4(), industry_id="chemical", title=title,
                       content=summary or title, source="L2a 自测",
                       source_url=f"https://example.com/l2a/{uuid.uuid4()}",
                       entities=json.dumps(entities, ensure_ascii=False),
                       summary=summary or None)
    db.add(row)
    db.flush()
    return row


def _feedback(news, interested, relevant=True):
    db.add(NewsFeedback(news_id=news.id, user_id=USER_ID,
                        interested=interested, relevant=relevant))
    db.flush()


try:
    # ---- 造数据：4 条资讯 + 4 条反馈 + 4 个既有主题 ----
    n1 = _news("己内酰胺装置投产，产能增加", ["己内酰胺"])
    n2 = _news("己内酰胺价格上行", ["己内酰胺"])
    n3 = _news("可降解塑料产能扩张", ["可降解塑料"])
    n4 = _news("某地发现页岩气", ["页岩气"])
    _feedback(n1, True)
    _feedback(n2, True)
    _feedback(n3, True)
    _feedback(n4, False)

    for name, ttype in (("PA6", "product"), ("尼龙6", "product"),
                        ("可降解塑料", "product"), ("页岩气", "industry")):
        db.add(FocusTopic(user_id=USER_ID, topic_type=ttype, name=name,
                          query_text=name, weight=1.0, source="manual"))
    db.commit()

    # ---- 归纳状态 ----
    r = _client.get("/intelligence/induction/status", headers=HEADERS)
    st = r.json() if r.status_code == 200 else {}
    check("B0 状态接口 200", r.status_code == 200, str(r.status_code))
    check("B0 反馈数达标（≥20？不，4 条 → due=False）",
          st.get("due") is False and st.get("pending_feedback") == 4, str(st))

    # ---- 打桩 LLM，跑归纳 ----
    llm_payload = {
        "add_topics": [{"name": "己内酰胺", "type": "product",
                        "reason": "近30天正向反馈多条", "confidence": 0.86}],
        "adjust_topics": [{"name": "尼龙6", "delta": 0.4,
                           "reason": "反馈转强", "confidence": 0.72}],
        "merge_topics": [{"targets": ["PA6", "尼龙6"], "into": "聚酰胺6",
                          "reason": "同一物质的两种写法", "confidence": 0.91}],
        "drop_topics": [{"name": "页岩气", "reason": "长期无正向反馈", "confidence": 0.95}],
        "insights": ["你近期关注己内酰胺产业链"],
    }
    lma._chat_json = lambda *a, **k: llm_payload

    r = _client.post("/intelligence/induction", json={"force": True}, headers=HEADERS)
    body = r.json() if r.status_code == 200 else {}
    check("B1 归纳接口 200", r.status_code == 200, str(r.status_code))
    check("B1 产出 5 条建议", body.get("created") == 5, str(body.get("created")))
    check("B1 返回 revision", bool(body.get("revision", {}).get("id")))
    check("B1 revision 记录了样本数", body.get("revision", {}).get("sample_count") == 4,
          str(body.get("revision", {}).get("sample_count")))
    revision_id = body.get("revision", {}).get("id")

    r = _client.get("/intelligence/suggestions", headers=HEADERS)
    items = r.json() if r.status_code == 200 else []
    kinds = sorted(i["kind"] for i in items)
    check("B2 建议箱默认只返回 pending", r.status_code == 200 and len(items) == 5, str(len(items)))
    check("B2 五种 kind 齐全",
          kinds == ["add", "adjust", "drop", "insight", "merge"], str(kinds))
    _confs = [i["confidence"] for i in items]
    check("B2 高置信排前面（置信度非递增）",
          _confs == sorted(_confs, reverse=True), str(_confs))
    check("B2 排序首位 = 最高置信（drop 0.95）",
          items[0]["kind"] == "drop", str(items[0]["kind"]))
    check("B2 建议带理由与证据", bool(items[0]["reason"]) and isinstance(items[0]["evidence"], list))

    by_kind = {i["kind"]: i for i in items}

    # ---- 重复归纳不堆重复建议 ----
    r = _client.post("/intelligence/induction", json={"force": True}, headers=HEADERS)
    check("B3 重复归纳不再产生重复建议", r.json().get("created") == 0,
          str(r.json().get("created")))
    check("B3 建议箱仍是 5 条",
          len(_client.get("/intelligence/suggestions", headers=HEADERS).json()) == 5)

    # ---- 接受：adjust ----
    r = _client.post(f"/intelligence/suggestions/{by_kind['adjust']['id']}/accept",
                     headers=HEADERS)
    check("B4 接受 adjust 成功", r.status_code == 200, str(r.status_code))
    touched = (r.json().get("touched_topics") or [{}])[0]
    check("B4 权重 1.0 → 1.4", abs(touched.get("weight", 0) - 1.4) < 1e-6,
          str(touched.get("weight")))

    # ---- 接受：add ----
    r = _client.post(f"/intelligence/suggestions/{by_kind['add']['id']}/accept",
                     headers=HEADERS)
    check("B5 接受 add 成功", r.status_code == 200, str(r.status_code))
    touched = (r.json().get("touched_topics") or [{}])[0]
    check("B5 新主题名 = 己内酰胺", touched.get("name") == "己内酰胺", str(touched.get("name")))
    check("B5 新主题 source=manual（用户确认过，受保护）",
          touched.get("source") == "manual", str(touched.get("source")))
    check("B5 新主题带 suggestion_id", bool(touched.get("suggestion_id")))
    add_topic_id = touched.get("id")

    # ---- 接受：merge ----
    r = _client.post(f"/intelligence/suggestions/{by_kind['merge']['id']}/accept",
                     headers=HEADERS)
    check("B6 接受 merge 成功", r.status_code == 200, str(r.status_code))
    touched = r.json().get("touched_topics") or []
    names = {t["name"]: t for t in touched}
    check("B6 生成规范名主题「聚酰胺6」",
          "聚酰胺6" in names and names["聚酰胺6"]["is_active"] is True, str(list(names)))
    check("B6 PA6 被停用而不删除",
          "PA6" in names and names["PA6"]["is_active"] is False, str(list(names)))
    check("B6 尼龙6 被停用而不删除",
          "尼龙6" in names and names["尼龙6"]["is_active"] is False, str(list(names)))
    check("B6 query_text 并入别名",
          "PA6" in (names["聚酰胺6"]["query_text"] or ""),
          str(names["聚酰胺6"]["query_text"]))

    # ---- 拒绝：drop ----
    r = _client.post(f"/intelligence/suggestions/{by_kind['drop']['id']}/reject",
                     headers=HEADERS)
    check("B7 拒绝 drop 成功", r.status_code == 200, str(r.status_code))
    check("B7 drop 未生效（页岩气仍生效）",
          _client.get("/intelligence/topics", headers=HEADERS).json() and
          any(t["name"] == "页岩气" and t["is_active"] for t in
              _client.get("/intelligence/topics", headers=HEADERS).json()))

    # ---- 接受：insight（不落库） ----
    r = _client.post(f"/intelligence/suggestions/{by_kind['insight']['id']}/accept",
                     headers=HEADERS)
    check("B8 接受 insight 成功且不改主题",
          r.status_code == 200 and not (r.json().get("touched_topics") or []))

    # ---- 重复接受应 409 ----
    r = _client.post(f"/intelligence/suggestions/{by_kind['add']['id']}/accept",
                     headers=HEADERS)
    check("B9 已处理的建议再接受返回 409", r.status_code == 409, str(r.status_code))

    # ---- 按状态筛选 ----
    r = _client.get("/intelligence/suggestions?status=accepted", headers=HEADERS)
    check("B10 accepted 筛选 4 条", len(r.json()) == 4, str(len(r.json())))
    r = _client.get("/intelligence/suggestions?status=rejected", headers=HEADERS)
    check("B10 rejected 筛选 1 条", len(r.json()) == 1)

    # ---- 归纳历史 ----
    r = _client.get("/intelligence/revisions", headers=HEADERS)
    revs = r.json() if r.status_code == 200 else []
    check("B11 归纳历史 2 条（含重复那次）", len(revs) == 2, str(len(revs)))
    check("B11 current 只有一条", sum(1 for x in revs if x["current"]) == 1)

    # ---- 回滚 ----
    r = _client.post(f"/intelligence/revisions/{revision_id}/rollback", headers=HEADERS)
    rb = r.json() if r.status_code == 200 else {}
    check("B12 回滚成功", r.status_code == 200, str(r.status_code))
    check("B12 移除了归纳新增的主题（己内酰胺）", "己内酰胺" in (rb.get("removed") or []),
          str(rb.get("removed")))

    topics_now = {t["name"]: t for t in _client.get("/intelligence/topics", headers=HEADERS).json()}
    check("B12 己内酰胺主题已删除", "己内酰胺" not in topics_now)
    check("B12 尼龙6 权重已还原为 1.0",
          abs(topics_now.get("尼龙6", {}).get("weight", 0) - 1.0) < 1e-6,
          str(topics_now.get("尼龙6", {}).get("weight")))
    check("B12 尼龙6 重新生效", topics_now.get("尼龙6", {}).get("is_active") is True)
    check("B12 PA6 重新生效", topics_now.get("PA6", {}).get("is_active") is True)
    check("B12 合并出的「聚酰胺6」已被移除", "聚酰胺6" not in topics_now,
          str(list(topics_now)))

    r = _client.post(f"/intelligence/revisions/{revision_id}/rollback", headers=HEADERS)
    check("B13 重复回滚返回 409", r.status_code == 409, str(r.status_code))

    # ---- 清空建议箱：未确认的建议在回滚后不再挂 pending ----
    r = _client.get("/intelligence/suggestions", headers=HEADERS)
    check("B14 回滚后该次归纳的 pending 建议已清",
          all(s["revision_id"] != revision_id for s in r.json()),
          str([(s["kind"], s["status"]) for s in r.json()]))

finally:
    try:
        db.close()
    except Exception:
        pass
    db = SessionLocal()
    try:
        db.query(PreferenceSuggestion).filter(PreferenceSuggestion.user_id == USER_ID).delete(
            synchronize_session=False)
        db.query(FocusTopicRevision).filter(FocusTopicRevision.user_id == USER_ID).delete(
            synchronize_session=False)
        db.query(FocusTopic).filter(FocusTopic.user_id == USER_ID).delete(synchronize_session=False)
        news_ids = [nid for (nid,) in db.query(IndustryNews.id).filter(
            IndustryNews.source == "L2a 自测").all()]
        if news_ids:
            db.query(NewsFeedback).filter(NewsFeedback.news_id.in_(news_ids)).delete(
                synchronize_session=False)
            db.query(IndustryNews).filter(IndustryNews.id.in_(news_ids)).delete(
                synchronize_session=False)
        db.query(User).filter(User.id == USER_ID).delete(synchronize_session=False)
        db.commit()
        print(f"清理完成：临时用户 {USERNAME} 及其资讯/反馈/主题/建议/快照已删除")
    finally:
        db.close()

# =====================================================================
print(f"\n结果：{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
