"""回归测试：屏蔽的「弱负信号」（LMA）。

验证的核心主张：**屏蔽是弱负信号，不是强负样本**——
它只对本用户命中的关注主题施加小额降权（``weak_penalty``），
❌ 不改 ``weight``、❌ 不动正/负反馈计数、❌ 不写 ``news_feedback``，
✅ 取消屏蔽按记录量精确回滚、✅ 跨用户互不影响、✅ 有下限不会失控。

覆盖三层：
A. 常量语义（弱信号的方向与量级）。
B. 接口 + 服务集成：施加 → 幂等 → 排序生效 → 精确回滚 → 跨用户隔离 → 下限封顶。
C. L2a 归纳：弱信号可达 drop 建议，但仍受「窗口无正向 + 历史无正向」两道闸保护。

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_hide_weak_signal.py industry_backend:/tmp/tw.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/tw.py

不打真实外部接口、不消耗 token。自建临时用户/主题/资讯，结束时全部删除。
"""
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[1] / "app", Path("/app")):
    if (_candidate / "service").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from service import news_hidden_service as hidden  # noqa: E402
from service.feedback_learning_service import effective_weight  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# =====================================================================
# A. 常量语义
# =====================================================================
print("--- A. 常量语义 ---")
check("弱降权为负值", hidden.WEAK_HIDE_DELTA < 0, f"delta={hidden.WEAK_HIDE_DELTA}")
check("下限比单次降权更负（够降几次）",
      hidden.WEAK_PENALTY_FLOOR < hidden.WEAK_HIDE_DELTA,
      f"floor={hidden.WEAK_PENALTY_FLOOR}")
check("弱降权量级远小于一次强负样本(约-0.35)",
      abs(hidden.WEAK_HIDE_DELTA) <= 0.1, f"|delta|={abs(hidden.WEAK_HIDE_DELTA)}")


# =====================================================================
# B. 接口 + 服务集成
# =====================================================================
print("--- B. 接口集成（施加 → 幂等 → 排序 → 回滚 → 隔离 → 下限）---")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.news import FocusTopic, IndustryNews, NewsFeedback, NewsHidden  # noqa: E402
from models.user import User  # noqa: E402

TOKEN = uuid.uuid4().hex[:8]
TOPIC_NAME = f"弱测{TOKEN}"
TEST_EVENT = f"__weak_test_{TOKEN}__"

db = SessionLocal()
_client = TestClient(app)


def _make_user(tag):
    name = f"weak_{tag}_{TOKEN}"
    u = User(username=name, email=f"{name}@example.com",
             hashed_password="x", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u, {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'username': name})}"}


user_a, HEAD_A = _make_user("a")
user_b, HEAD_B = _make_user("b")


def _make_topic(user_id, weight=1.0):
    t = FocusTopic(user_id=user_id, topic_type="product", name=TOPIC_NAME,
                   query_text=TOPIC_NAME, weight=weight, source="manual", is_active=True)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


topic_a = _make_topic(user_a.id)
topic_b = _make_topic(user_b.id)

# 12 条都命中该主题（标题含 TOPIC_NAME），用于验证下限封顶
news = []
for i in range(12):
    row = IndustryNews(
        id=uuid.uuid4(),
        industry_id="chemical",
        title=f"弱信号自测 {TOPIC_NAME} 第 {i} 条",
        content=f"用于验证屏蔽弱负信号的临时数据 {TOKEN}",
        summary=f"临时摘要 {TOKEN}",
        source="弱信号自测",
        source_url=f"https://example.com/weak-test/{TOKEN}/{i}",
        category="新闻",
        event_type=TEST_EVENT,
        extraction_status="completed",
    )
    db.add(row)
    news.append(row)
db.commit()
IDS = [str(n.id) for n in news]


def _reload_topic(t):
    db.expire(t)
    return t


try:
    # ---- 1) 基线 ----
    check("基线：weak_penalty=0", float(topic_a.weak_penalty or 0) == 0.0,
          f"={topic_a.weak_penalty}")
    check("基线：weak_hits=0", int(topic_a.weak_hits or 0) == 0)
    check("基线：weight=1.0", float(topic_a.weight) == 1.0)

    # ---- 2) 屏蔽施加弱降权 ----
    r = _client.post(f"/news/{IDS[0]}/hide", json={"reason": "弱信号自测"}, headers=HEAD_A)
    b = r.json()
    check("屏蔽成功", r.status_code == 200 and b["status"] == "created")
    weak = b.get("weak_topics") or []
    check("响应回传被降权的主题", [w["name"] for w in weak] == [TOPIC_NAME], f"{weak}")
    _reload_topic(topic_a)
    check("weak_penalty 变为 -0.05", float(topic_a.weak_penalty) == -0.05,
          f"={topic_a.weak_penalty}")
    check("weak_hits=1", int(topic_a.weak_hits) == 1, f"={topic_a.weak_hits}")

    # ---- 3) 弱信号的三条硬边界：不动权重、不动计数、不写反馈 ----
    check("★ 不改 weight（仍是 1.0）", float(topic_a.weight) == 1.0, f"={topic_a.weight}")
    check("★ 不改 positive_count", int(topic_a.positive_count or 0) == 0)
    check("★ 不改 negative_count", int(topic_a.negative_count or 0) == 0)
    check("★ 不写 news_feedback（屏蔽不是反馈）",
          db.query(NewsFeedback).filter(NewsFeedback.user_id == user_a.id,
                                        NewsFeedback.news_id == news[0].id).first() is None)

    # ---- 4) 再屏蔽一条：累计 ----
    _client.post(f"/news/{IDS[1]}/hide", headers=HEAD_A)
    _reload_topic(topic_a)
    check("累计到 -0.10", float(topic_a.weak_penalty) == -0.10, f"={topic_a.weak_penalty}")
    check("weak_hits=2", int(topic_a.weak_hits) == 2, f"={topic_a.weak_hits}")

    # ---- 5) 幂等：重复屏蔽不二次降权 ----
    r = _client.post(f"/news/{IDS[1]}/hide", headers=HEAD_A)
    b = r.json()
    _reload_topic(topic_a)
    check("重复屏蔽 status=exists", b["status"] == "exists")
    check("重复屏蔽 weak_topics 为空", (b.get("weak_topics") or []) == [])
    check("重复屏蔽不再降权（仍 -0.10）", float(topic_a.weak_penalty) == -0.10,
          f"={topic_a.weak_penalty}")

    # ---- 6) 排序生效：有效权重 = weight + weak_penalty ----
    check("effective_weight = 0.90", abs(effective_weight(topic_a) - 0.90) < 1e-9,
          f"={effective_weight(topic_a):.4f}")

    from service.feedback_learning_service import FeedbackLearningService  # noqa: E402
    ranker = FeedbackLearningService(db)
    ranked_with = ranker.enrich_and_rank([news[2]], user_a.id)[0]
    check("排序结果仍列出命中主题（可解释）",
          ranked_with["matched_topics"] == [TOPIC_NAME], f"{ranked_with['matched_topics']}")
    # 把弱降权临时置 0 再排一次，验证它确实拉低了分数，然后回滚内存改动
    _reload_topic(topic_a)
    topic_a.weak_penalty = 0.0
    ranked_without = ranker.enrich_and_rank([news[2]], user_a.id)[0]
    db.rollback()  # 丢弃内存改动，DB 保持 -0.10
    check("★ 弱降权确实降低了该资讯的个性化分",
          ranked_with["personalized_score"] < ranked_without["personalized_score"],
          f"with={ranked_with['personalized_score']} < "
          f"without={ranked_without['personalized_score']}")

    # ---- 7) 跨用户隔离 ----
    _reload_topic(topic_b)
    check("★ 另一个用户的同名主题不受影响（weak_penalty=0）",
          float(topic_b.weak_penalty or 0) == 0.0 and int(topic_b.weak_hits or 0) == 0)
    r = _client.post(f"/news/{IDS[0]}/hide", headers=HEAD_B)
    _reload_topic(topic_a)
    _reload_topic(topic_b)
    check("B 屏蔽后，A 的主题仍不受影响（不串账）",
          float(topic_a.weak_penalty) == -0.10, f"A={topic_a.weak_penalty}")
    check("B 自己的主题被降权", float(topic_b.weak_penalty) == -0.05,
          f"B={topic_b.weak_penalty}")

    # ---- 8) 精确回滚：取消屏蔽按记录量加回 ----
    _client.delete(f"/news/{IDS[1]}/hide", headers=HEAD_A)
    _reload_topic(topic_a)
    check("取消屏蔽回滚一条 → -0.05", float(topic_a.weak_penalty) == -0.05,
          f"={topic_a.weak_penalty}")
    check("weak_hits 减 1 → 1", int(topic_a.weak_hits) == 1, f"={topic_a.weak_hits}")
    _client.delete(f"/news/{IDS[0]}/hide", headers=HEAD_A)
    _reload_topic(topic_a)
    check("全部取消 → 回到 0.00（精确回滚，无残差）",
          float(topic_a.weak_penalty) == 0.0 and int(topic_a.weak_hits) == 0,
          f"penalty={topic_a.weak_penalty}, hits={topic_a.weak_hits}")
    check("回滚后 weight 依旧 1.0", float(topic_a.weight) == 1.0)

    # ---- 9) 已屏蔽列表带弱降权信息 ----
    _client.post(f"/news/{IDS[3]}/hide", headers=HEAD_A)
    r = _client.get("/news/hidden", headers=HEAD_A)
    rows = r.json()["data"]
    one = next((x for x in rows if str(x["id"]) == IDS[3]), None)
    check("已屏蔽列表回传 weak_topics",
          bool(one) and [w["name"] for w in (one.get("weak_topics") or [])] == [TOPIC_NAME],
          f"{one.get('weak_topics') if one else None}")
    _client.delete(f"/news/{IDS[3]}/hide", headers=HEAD_A)

    # ---- 10) 下限封顶：屏蔽再多也不会失控 ----
    # 先屏蔽 10 条（够降满 -0.50），再多屏蔽 2 条验证「封顶后不再计数」。
    for nid in IDS[2:] + IDS[:2]:
        _client.post(f"/news/{nid}/hide", headers=HEAD_A)
    _reload_topic(topic_a)
    check("★ 弱降权被封顶在下限 -0.50", float(topic_a.weak_penalty) == hidden.WEAK_PENALTY_FLOOR,
          f"={topic_a.weak_penalty}")
    check("★ weak_hits 只统计真正生效的降权次数（10，封顶后 2 次不再计）",
          int(topic_a.weak_hits) == 10, f"={topic_a.weak_hits}")
    check("★ 封顶后仍不改 weight", float(topic_a.weight) == 1.0)
    check("★ 封顶后仍不写反馈",
          db.query(NewsFeedback).filter(NewsFeedback.user_id == user_a.id).count() == 0)

    # ---- 11) 主题接口暴露弱信号字段 ----
    r = _client.get("/intelligence/topics", headers=HEAD_A)
    rows = r.json()
    mine = next((t for t in rows if t["name"] == TOPIC_NAME), None)
    check("主题接口回传 weak_penalty / weak_hits",
          bool(mine) and "weak_penalty" in mine and "weak_hits" in mine,
          f"{mine and (mine.get('weak_penalty'), mine.get('weak_hits'))}")
    check("接口里的 weak_penalty 与库一致",
          mine and float(mine["weak_penalty"]) == hidden.WEAK_PENALTY_FLOOR)

finally:
    try:
        db.rollback()
        db.query(NewsHidden).filter(NewsHidden.news_id.in_([n.id for n in news])).delete(
            synchronize_session=False)
        db.query(IndustryNews).filter(IndustryNews.id.in_([n.id for n in news])).delete(
            synchronize_session=False)
        db.query(FocusTopic).filter(FocusTopic.user_id.in_([user_a.id, user_b.id])).delete(
            synchronize_session=False)
        db.query(NewsFeedback).filter(NewsFeedback.user_id.in_([user_a.id, user_b.id])).delete(
            synchronize_session=False)
        db.query(User).filter(User.id.in_([user_a.id, user_b.id])).delete(
            synchronize_session=False)
        db.commit()
        print("清理完成：临时用户/主题/资讯已删除")
    except Exception as e:  # pragma: no cover
        db.rollback()
        print(f"清理失败（需手工处理）: {e}")
    finally:
        db.close()


# =====================================================================
# C. L2a 归纳：弱信号可达 drop 建议，但两道闸仍在
# =====================================================================
print("--- C. L2a 归纳的 drop 闸门 ---")

from service.lma_induction_service import WEAK_DROP_HITS, LmaInductionService  # noqa: E402


class _StubTopic:
    """只带 `_normalize` 会读到的字段，避免为纯规则测试建库。"""

    def __init__(self, name, weak_hits, positive_count, negative_count=0,
                 weight=1.0, is_active=True):
        self.id = uuid.uuid4()
        self.name = name
        self.topic_type = "product"
        self.query_text = name
        self.weight = weight
        self.positive_count = positive_count
        self.negative_count = negative_count
        self.weak_hits = weak_hits
        self.is_active = is_active


db2 = SessionLocal()
try:
    svc = LmaInductionService(db2)
    raw = {"drop_topics": [{"name": TOPIC_NAME, "reason": "反复屏蔽", "confidence": 0.8}]}

    def kinds(topic):
        prepared, _ = svc._normalize(raw, [topic], [])
        return [x["kind"] for x in prepared]

    k = kinds(_StubTopic(TOPIC_NAME, WEAK_DROP_HITS, 0))
    check(f"弱屏蔽 {WEAK_DROP_HITS} 次 + 无正向 → 可产出 drop 建议",
          "drop" in k, f"{k}")

    k = kinds(_StubTopic(TOPIC_NAME, WEAK_DROP_HITS - 1, 0))
    check(f"弱屏蔽不足 {WEAK_DROP_HITS} 次 → 不产出 drop", "drop" not in k, f"{k}")

    k = kinds(_StubTopic(TOPIC_NAME, 99, 3))
    check("★ 有历史正向反馈 → 弱信号再多也不 drop（第二道闸仍在）",
          "drop" not in k, f"{k}")

    k = kinds(_StubTopic(TOPIC_NAME, 99, 0, is_active=False))
    check("已停用主题不重复 drop", "drop" not in k, f"{k}")
finally:
    db2.close()

passed, total = sum(results), len(results)
print(f"\n===== {passed}/{total} 通过 =====")
sys.exit(0 if passed == total else 1)
