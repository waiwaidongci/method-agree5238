"""归并路由的 HTTP 集成测试：鉴权、校验、并发冻结取同一时点。

运行：PYTHONPATH=backend python3 tests/test_merge_api.py
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/merge_api_unused.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.config.database import Base, get_db  # noqa: E402
from src.models import User, Instrument  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
from src.middlewares import rate_limit as rl  # noqa: E402
from src.main import app  # noqa: E402


class _FakeRedis:
    store: dict = {}

    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, *args):
        return True

    def ping(self):
        return True


rl.Redis = _FakeRedis

# 文件型 SQLite + WAL：每会话独立连接，写事务经 busy_timeout 串行，
# 模拟生产 MySQL 下回传与冻结在同一事实源上排队加锁的并发语义。
_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_file.close()
engine = create_engine(
    f"sqlite:///{_db_file.name}",
    connect_args={"timeout": 30, "check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


Session = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)

# 审计中间件默认使用全局 engine，测试中指向同一 SQLite 库以避免噪声异常
from src.middlewares import audit as audit_mw  # noqa: E402
audit_mw.Session = Session


def override_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_db

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {extra}")


with Session() as db:
    db.add_all([
        User(name="分析师", email="analyst@ciguang.demo", role="analyst",
             password_hash=hash_password("pw")),
        User(name="观察员", email="viewer@ciguang.demo", role="viewer",
             password_hash=hash_password("pw")),
        Instrument(code="IR-A", name="参考仪", serial="A", unit="%", lower=0, upper=100),
        Instrument(code="IR-B", name="候选仪", serial="B", unit="%", lower=0, upper=100),
    ])
    db.commit()
    ref_id, cand_id = db.query(Instrument).order_by(Instrument.id).all()[0].id, db.query(Instrument).order_by(Instrument.id).all()[1].id

client = TestClient(app)
token = client.post("/auth/login", json={"email": "analyst@ciguang.demo", "password": "pw"}).json()["token"]
viewer_token = client.post("/auth/login", json={"email": "viewer@ciguang.demo", "password": "pw"}).json()["token"]
H = {"Authorization": f"Bearer {token}"}
HV = {"Authorization": f"Bearer {viewer_token}"}

print("1) 鉴权")
r = client.get("/merge/channels")
check("未登录 401", r.status_code == 401, r.text)
r = client.post("/merge/channels", headers=HV,
                json={"name": "观察员通道", "reference_id": ref_id, "candidate_id": cand_id})
check("观察员不能建通道 403", r.status_code == 403, r.text)

print("2) 通道与回传全链路")
r = client.post("/merge/channels", headers=H,
                json={"name": "API 通道", "reference_id": ref_id, "candidate_id": cand_id, "note": "n"})
check("建通道 201", r.status_code == 201, r.text)
cid = r.json()["id"]
r = client.post(f"/merge/channels/{cid}/events", headers=H,
                json={"side": "reference", "sample_code": "P1", "version": 2,
                      "kind": "reading", "value": 51})
check("乱序 v2 -> staged", r.status_code == 201 and r.json()["verdict"] == "staged", r.text)
r = client.post(f"/merge/channels/{cid}/events", headers=H,
                json={"side": "reference", "sample_code": "P1", "version": 1,
                      "kind": "reading", "value": 50})
check("补齐 v1 后推进到 v2", r.json()["specimen"]["reference"]["applied_version"] == 2, r.text)
r = client.post(f"/merge/channels/{cid}/events", headers=H,
                json={"side": "candidate", "sample_code": "P1", "version": 1,
                      "kind": "reading", "value": 60})
check("双侧完整 active", r.json()["specimen"]["status"] == "active", r.text)
r = client.post(f"/merge/channels/{cid}/events", headers=H,
                json={"side": "reference", "sample_code": "P1", "version": 1,
                      "kind": "reading", "value": 50})
check("重复回传 duplicate", r.json()["verdict"] == "duplicate", r.text)
r = client.post(f"/merge/channels/{cid}/events", headers=H,
                json={"side": "reference", "sample_code": "P1", "version": 2,
                      "kind": "reading", "value": 99})
check("同版异值 conflict 阻断", r.json()["verdict"] == "conflict"
      and r.json()["specimen"]["status"] == "blocked", r.text)
r = client.post(f"/merge/channels/{cid}/events", headers=H,
                json={"side": "reference", "sample_code": "P2", "version": 1,
                      "kind": "withdrawal", "value": 1})
check("撤回带值 422", r.status_code == 422, r.text)
r = client.post(f"/merge/channels/{cid}/events/batch", headers=H, json={"events": [
    {"side": "reference", "sample_code": "P3", "version": 1, "kind": "reading", "value": 10},
    {"side": "candidate", "sample_code": "P3", "version": 1, "kind": "reading", "value": 11},
]})
check("批量归并 201", r.status_code == 201 and len(r.json()["accepted"]) == 2, r.text)
r = client.post(f"/merge/channels/{cid}/events/batch", headers=H, json={"events": [
    {"side": "candidate", "sample_code": "P4", "version": 1, "kind": "reading", "value": 12},
    {"side": "reference", "sample_code": "P4", "version": 1, "kind": "reading", "value": 999},
]})
check("批量非法整批 422", r.status_code == 422, r.text)
r = client.get(f"/merge/channels/{cid}", headers=H)
check("P4 整批回滚不存在", all(s["sample_code"] != "P4" for s in r.json()["specimens"]), r.text)

print("3) 冻结样本集与排除原因")
r = client.post(f"/merge/channels/{cid}/freeze", headers=H, json={"name": "冻结 A", "note": ""})
check("冻结 201", r.status_code == 201, r.text)
set_a = r.json()
check("入选仅 P3（P1 阻断）", {x["sample_code"] for x in set_a["rows"]} == {"P3"}, r.text)
ex = {x["sample_code"]: x for x in set_a["exclusions"]}
check("P1 冲突排除", ex["P1"]["reason_code"] == "conflict", r.text)
r = client.post(f"/merge/channels/{cid}/freeze", headers=H, json={"name": "冻结 A"})
check("样本集重名 409", r.status_code == 409, r.text)

print("4) 并发：回传与冻结同时进行，每份快照取同一时点且不撕裂")
errors = []
barrier = threading.Barrier(2)
freeze_holder = {}


def writer():
    local = TestClient(app)
    barrier.wait()
    try:
        for i in range(15):
            rr = local.post(f"/merge/channels/{cid}/events", headers=H, json={
                "side": "reference", "sample_code": f"C{i}", "version": 1,
                "kind": "reading", "value": 30 + i})
            if rr.status_code != 201:
                errors.append(f"writer {i} {rr.status_code}")
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))


def freezer():
    local = TestClient(app)
    barrier.wait()
    try:
        rr = local.post(f"/merge/channels/{cid}/freeze", headers=H, json={"name": "冻结并发", "note": ""})
        freeze_holder["status"] = rr.status_code
        freeze_holder["body"] = rr.json() if rr.status_code == 201 else rr.text
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))


t1, t2 = threading.Thread(target=writer), threading.Thread(target=freezer)
t1.start(); t2.start(); t1.join(); t2.join()
check("并发请求无异常", not errors, str(errors))
check("并发冻结成功", freeze_holder.get("status") == 201, str(freeze_holder)[:200])
body = freeze_holder.get("body", {})
# 快照内部一致性：complete + excluded 等于冻结时点试片总数；行与排除不重叠
codes = {x["sample_code"] for x in body.get("rows", [])}
excodes = {x["sample_code"] for x in body.get("exclusions", [])}
check("快照无重叠且覆盖全部试片", not codes & excodes
      and len(codes) + len(excodes) == body.get("complete_count", -1) + body.get("excluded_count", -2),
      str((codes, excodes)))
# 冻结后再补传，旧集不变
before = (body["complete_count"], body["excluded_count"])
client.post(f"/merge/channels/{cid}/events", headers=H, json={
    "side": "candidate", "sample_code": "C0", "version": 1, "kind": "reading", "value": 31})
r = client.get(f"/merge/sample-sets/{body['id']}", headers=H)
check("补传不改旧研究", (r.json()["complete_count"], r.json()["excluded_count"]) == before, r.text[:200])
r = client.get(f"/merge/sample-sets/{body['id']}/export", headers=H)
check("CSV 导出可下载", r.status_code == 200 and "sample_code" in r.text, r.text[:120])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
