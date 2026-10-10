"""知识库协作能力回归测试（权限矩阵 / 可见性 / 成员 / 审计）

依赖：
- 一个可用的 Postgres（容器 industry_postgres）
- 两个测试用户：test113（owner）、diag_test_01（other）

运行（在 backend 容器内，工作目录 /app）：
    MSYS_NO_PATHCONV=1 docker cp backend/test/test_kb_collab.py industry_backend:/tmp/t.py
    MSYS_NO_PATHCONV=1 docker exec -w /app industry_backend python /tmp/t.py

设计要点（易踩坑处）：
- 用 FastAPI TestClient 直连应用，避免手工造 JWT 与起 HTTP 服务；
- 每个断言独立打印 PASS/FAIL，末尾汇总，任一失败以非 0 退出；
- 测试结束**硬删除**测试知识库（成员/审计随 FK 级联清理），不留脏数据。
"""
import sys

sys.path.insert(0, "/app")

from fastapi.testclient import TestClient  # noqa: E402

from app_main import app  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from core.security import create_access_token  # noqa: E402
from models.knowledge import KnowledgeBase  # noqa: E402
from models.user import User  # noqa: E402

KB_NAME = "__kb_collab_selftest__"
OWNER, OTHER = "test113", "diag_test_01"

db = SessionLocal()
owner = db.query(User).filter(User.username == OWNER).first()
other = db.query(User).filter(User.username == OTHER).first()
if not owner or not other:
    print(f"SKIP: 需要测试用户 {OWNER} / {OTHER}，当前库中不存在")
    db.close()
    sys.exit(0)


def auth(u):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'username': u.username})}"}


H1, H2 = auth(owner), auth(other)
client = TestClient(app)
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")


# 清理可能残留的同名测试库
db.query(KnowledgeBase).filter(KnowledgeBase.name == KB_NAME).delete(synchronize_session=False)
db.commit()
db.close()

try:
    # 1) owner 建 private 库
    r = client.post("/knowledge-bases",
                    json={"name": KB_NAME, "description": "self-test", "visibility": "private"},
                    headers=H1)
    check("owner 建 private 库 -> 201", r.status_code == 201, r.text[:120])
    kb_id = r.json()["id"]
    check("新建库 visibility=private", r.json().get("visibility") == "private")

    # 2) 他人看不到私有库，直接访问 403
    r = client.get("/knowledge-bases", headers=H2)
    check("他人列表看不到 private 库", all(k["id"] != kb_id for k in r.json()))
    r = client.get(f"/knowledge-bases/{kb_id}", headers=H2)
    check("他人访问 private 详情 -> 403", r.status_code == 403, f"got {r.status_code}")

    # 3) 切 team 后他人可见且角色 viewer
    r = client.put(f"/knowledge-bases/{kb_id}", json={"visibility": "team"}, headers=H1)
    check("owner 改 visibility=team", r.status_code == 200 and r.json().get("visibility") == "team")
    r = client.get("/knowledge-bases", headers=H2)
    row = next((k for k in r.json() if k["id"] == kb_id), None)
    check("team 后他人列表可见", row is not None)
    check("team 后他人角色=viewer", row is not None and row.get("role") == "viewer", str(row))

    # 4) viewer 只读：上传 / 改名均 403
    r = client.post(f"/knowledge-bases/{kb_id}/documents",
                    files={"file": ("t.txt", b"hello", "text/plain")}, headers=H2)
    check("viewer 上传 -> 403", r.status_code == 403, f"got {r.status_code}")
    r = client.put(f"/knowledge-bases/{kb_id}", json={"name": "hacked"}, headers=H2)
    check("viewer 改名 -> 403", r.status_code == 403, f"got {r.status_code}")

    # 5) 成员名单：viewer 可读，且含 owner
    r = client.get(f"/knowledge-bases/{kb_id}/members", headers=H2)
    check("viewer 可读成员名单", r.status_code == 200, r.text[:120])
    names = {m["username"]: m for m in r.json()}
    check("名单含 owner 且 is_owner=True", names.get(OWNER, {}).get("is_owner") is True)

    # 6) 加成员为 editor 后角色生效（不再依赖 is_shared 门槛）
    r = client.put(f"/knowledge-bases/{kb_id}/members",
                   json={"username": OTHER, "role": "editor"}, headers=H1)
    check("owner 加成员 editor", r.status_code == 200, r.text[:120])
    r = client.get(f"/knowledge-bases/{kb_id}", headers=H2)
    check("成员角色生效=editor", r.json().get("role") == "editor", r.json().get("role"))
    r = client.put(f"/knowledge-bases/{kb_id}", json={"name": KB_NAME}, headers=H2)
    check("editor 可改名", r.status_code == 200, r.text[:120])

    # 7) 审计日志含关键动作
    r = client.get(f"/knowledge-bases/{kb_id}/audit", headers=H1)
    actions = [x["action"] for x in r.json()]
    check("审计含 create/member_upsert", "create" in actions and "member_upsert" in actions, str(actions))

    # 8) 非 manager 改 visibility -> 403
    r = client.put(f"/knowledge-bases/{kb_id}", json={"visibility": "private"}, headers=H2)
    check("editor 改 visibility -> 403", r.status_code == 403, f"got {r.status_code}")
finally:
    # 硬删除测试库（成员 / 审计 FK 级联清理）
    db2 = SessionLocal()
    db2.query(KnowledgeBase).filter(KnowledgeBase.name == KB_NAME).delete(synchronize_session=False)
    db2.commit()
    db2.close()

print(f"\n结果: {sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) and results else 1)
