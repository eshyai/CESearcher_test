"""K2 知识库可见范围隔离 + 集合命名唯一性回归测试

覆盖三件事：
1. **集合命名唯一**：不同用户建同名库必须落到不同 Milvus 集合
   （历史实现 kb_{名称} 会串库）；
2. **可见范围**：private 库对非成员不可见；切 team 后可见；
3. **检索闸门**：scout 只检索当前用户可见库的集合，缺用户上下文时直接跳过。

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_kb_isolation.py industry_backend:/tmp/t.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/t.py

说明：需要 Postgres 里存在测试用户 test113(owner) / diag_test_01(other)；
若不存在则 SKIP。测试结束硬删除测试知识库，不留脏数据。
"""
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.knowledge import KnowledgeBase  # noqa: E402
from models.user import User  # noqa: E402
from service import kb_access_service as kba  # noqa: E402

KB_NAME = "__kb_isolation_selftest__"
OWNER, OTHER = "test113", "diag_test_01"

db = SessionLocal()
owner = db.query(User).filter(User.username == OWNER).first()
other = db.query(User).filter(User.username == OTHER).first()
if not owner or not other:
    print(f"SKIP: 需要测试用户 {OWNER} / {OTHER}，当前库中不存在")
    db.close()
    sys.exit(0)

# 提前取出主键，后续 db 关闭后这些对象会 detach，不能再访问属性
OWNER_ID, OTHER_ID = owner.id, other.id

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# ---------------------------------------------------------------- 1) 命名唯一
fake_a = type("KB", (), {"id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "name": "报价库"})()
fake_b = type("KB", (), {"id": uuid.UUID("22222222-2222-2222-2222-222222222222"), "name": "报价库"})()

check("同名库新集合名不同", kba.kb_collection_name(fake_a) != kba.kb_collection_name(fake_b),
      f"{kba.kb_collection_name(fake_a)} vs {kba.kb_collection_name(fake_b)}")
check("新集合名含 uuid hex", fake_a.id.hex in kba.kb_collection_name(fake_a))
check("候选名含历史 kb_{名称}",
      kba.legacy_kb_collection_name(fake_a) in kba.kb_collection_candidates(fake_a),
      str(kba.kb_collection_candidates(fake_a)))

# resolve_collection_for_read 的三段回退（打桩 pymilvus.utility.has_collection）
from pymilvus import utility as _milvus_utility  # noqa: E402

_orig_has = _milvus_utility.has_collection


class _FakeMilvusForResolve:
    pass


try:
    _milvus_utility.has_collection = lambda name: name == kba.legacy_kb_collection_name(fake_a)
    check("resolve 回退到历史集合",
          kba.resolve_collection_for_read(_FakeMilvusForResolve(), fake_a) == kba.legacy_kb_collection_name(fake_a))

    _milvus_utility.has_collection = lambda name: name == kba.kb_collection_name(fake_a)
    check("resolve 优先新集合",
          kba.resolve_collection_for_read(_FakeMilvusForResolve(), fake_a) == kba.kb_collection_name(fake_a))

    _milvus_utility.has_collection = lambda name: False
    check("resolve 全不存在返回 None",
          kba.resolve_collection_for_read(_FakeMilvusForResolve(), fake_a) is None)
finally:
    _milvus_utility.has_collection = _orig_has


# ------------------------------------------------------------- 2) 可见范围（DB）
def auth(u):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'username': u.username})}"}


H1, H2 = auth(owner), auth(other)
client = TestClient(app)
fake_milvus = None

# 清理可能残留
db.query(KnowledgeBase).filter(KnowledgeBase.name == KB_NAME).delete(synchronize_session=False)
db.commit()
db.close()

kb_id = None
try:
    r = client.post("/knowledge-bases",
                    json={"name": KB_NAME, "description": "isolation", "visibility": "private"},
                    headers=H1)
    check("owner 建 private 库 -> 201", r.status_code == 201, r.text[:120])
    kb_id = r.json()["id"]
    kb_uuid = uuid.UUID(kb_id)

    d = SessionLocal()
    check("owner 可见自己的库", kb_uuid in kba.accessible_kb_ids(d, OWNER_ID))
    check("他人不可见 private 库", kb_uuid not in kba.accessible_kb_ids(d, OTHER_ID))
    allowed_other = kba.allowed_kb_collections(d, OTHER_ID)
    check("他人集合白名单不含该库",
          all(kb_uuid.hex not in c for c in allowed_other),
          str(sorted(c for c in allowed_other if "isolation" in c or kb_uuid.hex[:8] in c)))
    d.close()

    client.put(f"/knowledge-bases/{kb_id}", json={"visibility": "team"}, headers=H1)
    d = SessionLocal()
    check("切 team 后他人可见", kb_uuid in kba.accessible_kb_ids(d, OTHER_ID))
    check("team 库进入他人集合白名单",
          any(kb_uuid.hex in c for c in kba.allowed_kb_collections(d, OTHER_ID)))
    check("无用户上下文 -> 可见为空", kba.accessible_knowledge_bases(d, None) == [])
    d.close()
finally:
    db2 = SessionLocal()
    db2.query(KnowledgeBase).filter(KnowledgeBase.name == KB_NAME).delete(synchronize_session=False)
    db2.commit()
    db2.close()


# ------------------------------------------------------------------ 3) 检索闸门
from service.deep_research_v2.agents import scout as scout_mod  # noqa: E402


class FakeMilvus:
    def __init__(self):
        self.calls = []

    def search(self, collection_name, query_vector, top_k=5):
        self.calls.append(collection_name)
        return [{"doc_id": "d1", "kb_id": collection_name, "filename": "f.txt",
                 "content": "化工内容", "chunk_index": 0, "score": 0.9}]


def make_scout():
    s = object.__new__(scout_mod.DeepScout)
    import logging
    s.logger = logging.getLogger("scout-test")
    s.milvus_service = FakeMilvus()
    s.search_cache = {}
    s.fact_fingerprints = {}
    s._kb_collection_cache = {}
    return s


allowed_kb = type("KB", (), {"id": uuid.uuid4(), "name": "可见库"})()
secret_kb = type("KB", (), {"id": uuid.uuid4(), "name": "别人的私有库"})()
# 数据库里存在的假集合（含一个不属于可见范围的，验证不会被检索）
EXISTING = {
    kba.kb_collection_name(allowed_kb),
    kba.kb_collection_name(secret_kb),
    kba.legacy_kb_collection_name(secret_kb),
}
_orig_has2 = _milvus_utility.has_collection
_orig_gen = scout_mod.generate_embedding
_orig_acc = kba.accessible_knowledge_bases

try:
    _milvus_utility.has_collection = lambda name: name in EXISTING
    scout_mod.generate_embedding = lambda q: [0.1] * 8
    kba.accessible_knowledge_bases = lambda db, user_id: [allowed_kb] if user_id == "u1" else []

    s = make_scout()
    out = asyncio.run(s._execute_local_search("环氧丙烷", user_id="u1"))
    calls = s.milvus_service.calls
    check("只检索可见库集合", calls == [kba.kb_collection_name(allowed_kb)], str(calls))
    check("不会碰他人私有库集合",
          kba.legacy_kb_collection_name(secret_kb) not in calls
          and kba.kb_collection_name(secret_kb) not in calls)
    check("结果带知识库名", out and out[0].get("kb_name") == "可见库", str(out[:1]))

    s2 = make_scout()
    out2 = asyncio.run(s2._execute_local_search("环氧丙烷", user_id=None))
    check("缺用户上下文 -> 不检索且返回空", out2 == [] and s2.milvus_service.calls == [])

    s3 = make_scout()
    out3 = asyncio.run(s3._execute_local_search("环氧丙烷", user_id="u-unknown"))
    check("无可见库用户 -> 空结果", out3 == [] and s3.milvus_service.calls == [])
finally:
    _milvus_utility.has_collection = _orig_has2
    scout_mod.generate_embedding = _orig_gen
    kba.accessible_knowledge_bases = _orig_acc

# ------------------------------------------------------- 4) 写入路径使用新命名
# 打桩 docmind（真实调用需要有效的阿里云 DocMind AccessKey），
# 只断言传给向量库的集合名 = kb_{uuid hex}。
from service import docmind_service as docmind_mod  # noqa: E402
from router import knowledge_router as kr  # noqa: E402

_records = []
_orig_docmind = docmind_mod.process_document_with_docmind
_write_kb = None
_write_doc = None
try:
    d = SessionLocal()
    owner_row = d.query(User).filter(User.id == OWNER_ID).first()
    _write_kb = KnowledgeBase(user_id=owner_row.id, name=KB_NAME + "_w", visibility="private")
    d.add(_write_kb)
    d.flush()
    import tempfile, os as _os  # noqa: E402
    fd, tmp_path = tempfile.mkstemp(suffix=".txt", text=True)
    with _os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("x" * 200)
    from models.knowledge import Document as _Doc  # noqa: E402
    _write_doc = _Doc(knowledge_base_id=_write_kb.id, user_id=owner_row.id,
                      filename="t.txt", file_path=tmp_path, status="pending")
    d.add(_write_doc)
    d.commit()
    doc_id = str(_write_doc.id)
    expect_name = kba.kb_collection_name(_write_kb)
    d.close()

    def _fake_docmind(file_path, file_name, index_name):
        _records.append(index_name)
        return {"success": True, "document_count": 3, "message": "ok"}

    docmind_mod.process_document_with_docmind = _fake_docmind
    asyncio.run(kr.process_document(doc_id, tmp_path, SessionLocal))

    check("写入路径使用 kb_{uuid hex} 集合", _records == [expect_name],
          f"got {_records}, expect {[expect_name]}")
finally:
    docmind_mod.process_document_with_docmind = _orig_docmind
    d3 = SessionLocal()
    d3.query(KnowledgeBase).filter(KnowledgeBase.name == KB_NAME + "_w").delete(synchronize_session=False)
    d3.commit()
    d3.close()


print(f"\n结果: {sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) and results else 1)
