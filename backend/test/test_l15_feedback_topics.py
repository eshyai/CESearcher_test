"""L1.5 回归测试：分词/同义词匹配升级 + 反馈自动派生候选主题。

分两部分：
A. **纯函数**（不需要数据库）：同义归并、ASCII 词边界、泛词过滤、覆盖率阈值、
   最长匹配、候选主题派生与噪声过滤；
B. **DB 集成**（走 TestClient 真实接口）：正向反馈在无匹配主题时自动创建
   ``source='auto'`` 候选主题 → 可在列表按来源筛选 → 可「确认为关注」升级为 manual
   → 自动主题被负反馈打到退役线后自动停用，而手动主题永不被自动停用。

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_l15_feedback_topics.py industry_backend:/tmp/t.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/t.py

B 部分需要 Postgres 里存在测试用户 ``test113``，不存在则跳过；测试结束清理
自己创建的资讯与关注主题，不留脏数据。
"""
import json
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[1] / "app", Path("/app")):
    if (_candidate / "service").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from service.interest_scoring import (  # noqa: E402
    TextIndex,
    canonical_groups,
    compile_query,
    query_matches,
    text_matches,
)
from service.topic_suggestion import (  # noqa: E402
    candidate_topics_from_news,
    infer_topic_type,
    normalize_name,
)

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# =====================================================================
# A. 匹配算法
# =====================================================================
print("--- A. 匹配算法（同义归并 / 词边界 / 泛词 / 覆盖率）---")

# 1) 同义归并
check("同义：PA6 命中尼龙6", text_matches("PA6", "尼龙6切片报价上涨"))
check("同义：尼龙6 命中聚酰胺6", text_matches("尼龙6", "聚酰胺6 产能投放"))
check("同义：MDI 命中中文全称", text_matches("MDI", "二苯基甲烷二异氰酸酯价格"))
check("同义：BASF 命中巴斯夫", text_matches("BASF", "巴斯夫拟收购赢创"))

# 2) ASCII 词边界（旧实现 PA6 会误命中 PA66）
check("词边界：PA6 不命中 PA66", not text_matches("PA6", "PA66 价格持稳"))
check("词边界：PA6 命中独立 PA6", text_matches("PA6", "PA6 切片挂牌价上调"))
check("词边界：PET 不命中 PETG", not text_matches("聚酯", "PETG 板材需求"))

# 3) 泛词不单独触发
check("泛词：收购 单独不触发", not text_matches("MDI 收购", "某公司收购某厂股权"))
check("泛词：装置 单独不触发", not text_matches("MDI 装置", "环氧丙烷装置例行检修"))
check("泛词：全泛词主题退化为出现即命中", text_matches("检修", "己内酰胺装置例行检修"))

# 4) 覆盖率阈值
check("覆盖率：MDI TDI 万华 命中 2/3", text_matches("MDI TDI 万华", "万华化学MDI装置扩产"))
check("覆盖率：MDI TDI 万华 仅 1/3 不命中", not text_matches("MDI TDI 万华", "TDI 价格下跌"))
check("覆盖率：两词主题命中其一即算", text_matches("PVC 烧碱", "烧碱价格上调"))

# 5) 最长匹配优先 + 区间吞并
check("最长匹配：聚酰胺66 吞掉聚酰胺6", canonical_groups("聚酰胺66产能扩张") == ["聚酰胺66"],
      str(canonical_groups("聚酰胺66产能扩张")))
check("最长匹配：尼龙6 吞掉尼龙", canonical_groups("尼龙6切片") == ["聚酰胺6"],
      str(canonical_groups("尼龙6切片")))
check("最长匹配：裸尼龙 仍可命中", "尼龙" in canonical_groups("尼龙行业景气度"))

# 6) 词表外的词退化为字面包含（与旧行为一致）
check("未知词退化：赢创", text_matches("赢创", "巴斯夫拟收购赢创"))
check("未知词退化：不命中", not text_matches("赢创", "巴斯夫扩建MDI装置"))

# 7) 主题编译：名称与匹配词合并
topic_like = type("T", (), {"name": "MDI", "query_text": "MDI 价格 装置"})()
check("编译主题：泛词被剔除后仍命中", query_matches(
    compile_query(f"{topic_like.name} {topic_like.query_text}"),
    TextIndex("万华化学MDI装置因故降负")))
check("编译主题：无实质词则不命中", not query_matches(
    compile_query(f"{topic_like.name} {topic_like.query_text}"),
    TextIndex("某地装置检修炼厂开工率下滑")))


# =====================================================================
# A2. 候选主题派生
# =====================================================================
print("--- A2. 候选主题派生 ---")

title = "巴斯夫宣布己内酰胺装置投产，PA66 需求走强"
entities = ["巴斯夫", "己内酰胺", "中国石油和化学工业联合会", "价格"]
extracted = [{"type": "product", "name": "己内酰胺"}, {"type": "technology", "name": "尼龙6"}]

cands = candidate_topics_from_news(title=title, entities=entities,
                                   extracted_topics=extracted, keywords="巴斯夫,己内酰胺")
names = [c["name"] for c in cands]
check("派生：默认取 2 条且产品优先", names == ["己内酰胺", "聚酰胺6"], str(names))
check("派生：全部为 product 类型", all(c["topic_type"] == "product" for c in cands),
      str([c["topic_type"] for c in cands]))
check("派生：标记 source=auto", all(c["source"] == "auto" for c in cands))
check("派生：带 reason 可解释", all(c.get("reason") for c in cands))

check("派生：机构全称被过滤",
      normalize_name("中国石油和化学工业联合会") is None)
check("派生：泛词被过滤", normalize_name("价格") is None and normalize_name("装置检修") is None)
check("派生：纯数字/符号被过滤", normalize_name("2026") is None and normalize_name("--") is None)
check("派生：尾部括号说明被剥掉",
      normalize_name("万华化学（烟台）") == "万华化学",
      str(normalize_name("万华化学（烟台）")))
check("派生：单字被过滤", normalize_name("苯") is None)

single = candidate_topics_from_news(entities=["陶氏"], extracted_topics=[], keywords=None)
check("派生：实体命中词表归一为企业主题",
      [c["name"] for c in single] == ["陶氏"] and single[0]["topic_type"] == "industry",
      str(single))

none_cands = candidate_topics_from_news(title="今日要闻", entities=["中国", "行业"],
                                        extracted_topics=[{"type": "industry", "name": "化工"}],
                                        keywords="行业")
check("派生：无有效信号时不产生候选", none_cands == [], str(none_cands))

limit_cands = candidate_topics_from_news(title=title, entities=entities,
                                         extracted_topics=extracted, limit=1)
check("派生：limit 生效", len(limit_cands) == 1, str([c["name"] for c in limit_cands]))

check("类型推断：工艺词", infer_topic_type("HPPO工艺") == "process")
check("类型推断：词表外的词沿用模型类型", infer_topic_type("赢创工业", "customer") == "customer")


# =====================================================================
# B. DB 集成
# =====================================================================
print("--- B. DB 集成（反馈自动建主题）---")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.news import FocusTopic, IndustryNews, NewsFeedback  # noqa: E402
from models.user import User  # noqa: E402

USERNAME = "test113"
# 这些名字会被测试用到：先临时备份并摘掉，避免与既有数据互相干扰
COLLIDE_NAMES = ("己内酰胺", "聚酰胺6", "聚酰胺66", "巴斯夫", "己二酸")

db = SessionLocal()
user = db.query(User).filter(User.username == USERNAME).first()

if not user:
    print(f"SKIP: 需要测试用户 {USERNAME}，当前库中不存在")
    db.close()
else:
    USER_ID = user.id
    news_id = None
    backup_rows = []
    cols = [c.name for c in FocusTopic.__table__.columns]
    # 测试前已存在的主题 id：清理时只删「测试期间新出现」的主题，绝不误伤既有数据
    pre_existing_ids = {row.id for row in db.query(FocusTopic).filter(
        FocusTopic.user_id == USER_ID).all()}

    def _snapshot_and_remove_collisions(session):
        rows = session.query(FocusTopic).filter(
            FocusTopic.user_id == USER_ID, FocusTopic.name.in_(COLLIDE_NAMES)).all()
        for row in rows:
            backup_rows.append({c: getattr(row, c) for c in cols})
            session.delete(row)
        session.commit()
        return len(rows)

    try:
        removed = _snapshot_and_remove_collisions(db)
        if removed:
            print(f"（临时摘掉 {removed} 条同名既有主题，测试后恢复）")

        news = IndustryNews(
            id=uuid.uuid4(), industry_id="chemical",
            title="巴斯夫宣布己内酰胺装置投产，PA66 需求走强",
            content="巴斯夫在亚太新建己内酰胺装置，配套聚酰胺6与尼龙66产能。",
            source="L1.5 自测", source_url=f"https://example.com/l15/{uuid.uuid4()}",
            keywords="巴斯夫,己内酰胺", entities=json.dumps(entities, ensure_ascii=False),
            extracted_topics=json.dumps(extracted, ensure_ascii=False),
        )
        db.add(news)
        db.commit()
        news_id = news.id
        db.close()
        db = None

        headers = {"Authorization": f"Bearer {create_access_token({'sub': str(USER_ID), 'username': USERNAME})}"}
        client = TestClient(app)

        # --- B1 正向反馈：无任何主题命中 → 自动派生候选主题
        r = client.post(f"/intelligence/news/{news_id}/feedback",
                        json={"interested": True, "relevant": True}, headers=headers)
        check("B1 反馈接口 200", r.status_code == 200, str(r.status_code))
        payload = r.json() if r.status_code == 200 else {}
        created = payload.get("created_topics") or []
        check("B1 自动创建了候选主题", len(created) >= 1, str([c.get("name") for c in created]))
        check("B1 候选主题 source=auto", all(c.get("source") == "auto" for c in created))
        check("B1 候选主题初始权重 0.5", all(abs(c.get("weight", 0) - 0.5) < 1e-6 for c in created))
        check("B1 候选主题带来源资讯",
              all(str(c.get("origin_news_id")) == str(news_id) for c in created))
        check("B1 候选主题类型为 product",
              all(c.get("topic_type") == "product" for c in created),
              str([c.get("topic_type") for c in created]))
        created_names = [c["name"] for c in created]
        first_id = created[0]["id"] if created else None

        # --- B2 重复正向反馈：不应重复创建
        r2 = client.post(f"/intelligence/news/{news_id}/feedback",
                         json={"interested": True, "relevant": True}, headers=headers)
        check("B2 重复反馈不再创建",
              r2.status_code == 200 and not (r2.json().get("created_topics") or []))

        # --- B3 按来源筛选
        auto_names = [t["name"] for t in client.get(
            "/intelligence/topics?source=auto", headers=headers).json()]
        manual_names = [t["name"] for t in client.get(
            "/intelligence/topics?source=manual", headers=headers).json()]
        check("B3 auto 筛选能看到自动主题", set(created_names) <= set(auto_names), str(auto_names))
        check("B3 manual 筛选看不到自动主题", not (set(created_names) & set(manual_names)))

        # --- B4「确认为关注」升级为 manual
        if first_id:
            r4 = client.patch(f"/intelligence/topics/{first_id}",
                              json={"source": "manual"}, headers=headers)
            check("B4 可升级为手动主题",
                  r4.status_code == 200 and r4.json().get("source") == "manual", r4.text[:120])
            client.patch(f"/intelligence/topics/{first_id}",
                         json={"source": "auto"}, headers=headers)

        # --- B5 自动主题被负反馈打到退役线 → 自动停用
        if first_id:
            client.patch(f"/intelligence/topics/{first_id}", json={"weight": -1.0}, headers=headers)
            r5 = client.post(f"/intelligence/news/{news_id}/feedback",
                             json={"interested": False, "relevant": False}, headers=headers)
            retired = (r5.json().get("retired_topics") or []) if r5.status_code == 200 else []
            check("B5 自动主题被自动停用", any(t["id"] == first_id for t in retired),
                  str([t.get("name") for t in retired]))
            check("B5 停用后不出现在默认列表",
                  all(t["id"] != first_id for t in client.get(
                      "/intelligence/topics", headers=headers).json()))

        # --- B6 手动主题不会被自动停用（用标题里出现过的 PA66）
        manual = client.post("/intelligence/topics", json={
            "topic_type": "product", "name": "聚酰胺66", "query_text": "聚酰胺66", "weight": -1.0,
        }, headers=headers)
        check("B6 手动创建主题成功", manual.status_code in (200, 201), manual.text[:120])
        manual_id = manual.json().get("id") if manual.status_code in (200, 201) else None
        if manual_id:
            client.post(f"/intelligence/news/{news_id}/feedback",
                        json={"interested": True, "relevant": True}, headers=headers)
            r6 = client.post(f"/intelligence/news/{news_id}/feedback",
                             json={"interested": False, "relevant": False}, headers=headers)
            body6 = r6.json() if r6.status_code == 200 else {}
            check("B6 手动主题不被自动停用",
                  manual_id not in [t["id"] for t in (body6.get("retired_topics") or [])])
            check("B6 手动主题被正常更新权重",
                  manual_id in [t["id"] for t in (body6.get("updated_topics") or [])])
            check("B6 手动主题仍启用",
                  any(t["id"] == manual_id for t in client.get(
                      "/intelligence/topics", headers=headers).json()))
    finally:
        db = SessionLocal()
        try:
            removed = 0
            for row in db.query(FocusTopic).filter(FocusTopic.user_id == USER_ID).all():
                if row.id not in pre_existing_ids:
                    db.delete(row)
                    removed += 1
            for row in backup_rows:
                db.add(FocusTopic(**row))
            if news_id:
                db.query(NewsFeedback).filter(NewsFeedback.news_id == news_id).delete(
                    synchronize_session=False)
                db.query(IndustryNews).filter(IndustryNews.id == news_id).delete(
                    synchronize_session=False)
            db.commit()
            print(f"清理完成：删除测试产生的 {removed} 条主题 + 测试资讯/反馈，"
                  f"恢复 {len(backup_rows)} 条既有主题")
        finally:
            db.close()

print(f"\n===== 结果：{sum(results)}/{len(results)} 通过 =====")
sys.exit(0 if all(results) else 1)
