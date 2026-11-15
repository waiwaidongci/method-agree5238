"""双侧增量归并服务的行为测试。

用 SQLite 内存库验证：乱序暂存与补齐推进、重复回传幂等、同版异值隔离阻断、
撤回/旧版不复活、冻结时点快照与缺侧/缺版/冲突原因、冻结后补传不改旧研究。
运行：PYTHONPATH=backend python3 tests/test_merge_service.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/merge_test_unused.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine, expire_on_commit=False)

from src.config.database import Base  # noqa: E402
from src.models import Instrument, MergeChannel  # noqa: E402
from src.schemas.merge import MergeChannelInput, MergeEventInput, MergeEventBatch, SampleSetInput  # noqa: E402
from src.services import merge_service as svc  # noqa: E402
from fastapi import HTTPException  # noqa: E402

Base.metadata.create_all(engine)


class U:
    email = "analyst@ciguang.demo"


PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {extra}")


def ev(side, code, version, kind="reading", value=None):
    return MergeEventInput(side=side, sample_code=code, version=version, kind=kind, value=value)


db = Session()
ref = Instrument(code="CG-REF", name="参考仪", serial="R1", unit="%", lower=0, upper=100)
cand = Instrument(code="CG-CAND", name="候选仪", serial="C1", unit="%", lower=0, upper=100)
db.add_all([ref, cand])
db.commit()

ch = svc.create_channel(db, MergeChannelInput(name="N03 增量通道", reference_id=ref.id, candidate_id=cand.id), U)
cid = ch["id"]

print("1) 乱序暂存，补齐前版后连续推进")
r = svc.ingest_event(db, cid, ev("reference", "S1", 2, value=51), U)
check("v2 先到 -> 试片 waiting，事件 staged",
      r["verdict"] == "staged" and r["specimen"]["status"] == "waiting"
      and r["specimen"]["reference"]["staged_versions"] == [2])
r = svc.ingest_event(db, cid, ev("reference", "S1", 1, value=50), U)
check("v1 补齐后 v2 自动推进到 applied v2",
      r["specimen"]["reference"]["applied_version"] == 2
      and r["specimen"]["reference"]["value"] == 51
      and r["specimen"]["reference"]["staged_versions"] == []
      and r["specimen"]["status"] == "partial")
r = svc.ingest_event(db, cid, ev("candidate", "S1", 1, value=60), U)
check("候选侧补齐 -> active 完整配对", r["specimen"]["status"] == "active")

print("2) 重复回传不重复计入")
before = svc.list_events(db, cid)["total"]
r = svc.ingest_event(db, cid, ev("reference", "S1", 1, value=50), U)
after = svc.list_events(db, cid)["total"]
check("同内容重复回传 verdict=duplicate 且不新增事件", r["verdict"] == "duplicate" and before == after)
s1 = next(x for x in svc.get_channel(db, cid)["specimens"] if x["sample_code"] == "S1")
check("重复回传不改变当前生效版本 v2/值51",
      s1["reference"]["applied_version"] == 2 and s1["reference"]["value"] == 51)

print("3) 同版异值隔离并永久阻断该试片")
r = svc.ingest_event(db, cid, ev("reference", "S1", 2, value=52), U)
check("同版异值 -> conflict", r["verdict"] == "conflict" and r["specimen"]["status"] == "blocked")
r = svc.ingest_event(db, cid, ev("candidate", "S1", 2, value=61), U)
check("阻断后的后续回传一律隔离", r["verdict"] == "quarantined")
s1 = next(x for x in svc.get_channel(db, cid)["specimens"] if x["sample_code"] == "S1")
check("阻断原因保留同版异值依据", "同版异值" in s1["block_reason"])

print("4) 撤回生效与旧版不得复活撤回值")
svc.ingest_event(db, cid, ev("reference", "S2", 1, value=40), U)
svc.ingest_event(db, cid, ev("candidate", "S2", 1, value=42), U)
r = svc.ingest_event(db, cid, ev("reference", "S2", 2, kind="withdrawal"), U)
check("撤回事件 applied，试片 withdrawn",
      r["verdict"] == "applied" and r["specimen"]["status"] == "withdrawn"
      and r["specimen"]["reference"]["kind"] == "withdrawal")
r = svc.ingest_event(db, cid, ev("reference", "S2", 1, value=99), U)
check("旧版 v1 回传 -> stale 隔离，撤回不复活", r["verdict"] == "stale")
s2 = next(x for x in svc.get_channel(db, cid)["specimens"] if x["sample_code"] == "S2")
check("当前仍为撤回状态，值不被旧版覆盖",
      s2["reference"]["kind"] == "withdrawal" and s2["reference"]["applied_version"] == 2)
r = svc.ingest_event(db, cid, ev("reference", "S2", 1, value=40), U)
check("旧版即使内容相同也不复活（duplicate 判定优先于状态变更）", r["verdict"] == "duplicate")
s2 = next(x for x in svc.get_channel(db, cid)["specimens"] if x["sample_code"] == "S2")
check("状态保持 withdrawn", s2["status"] == "withdrawn")
svc.ingest_event(db, cid, ev("reference", "S2", 3, value=41), U)
s2 = next(x for x in svc.get_channel(db, cid)["specimens"] if x["sample_code"] == "S2")
check("撤回后新版 v3 读数可恢复 active", s2["status"] == "active" and s2["reference"]["value"] == 41)

print("5) 批量接口：批内乱序一次归并、非法整批回滚")
batch = MergeEventBatch(events=[
    ev("reference", "S3", 2, value=71), ev("reference", "S3", 1, value=70),
    ev("candidate", "S3", 1, value=72),
])
out = svc.ingest_batch(db, cid, batch, U)
check("批内乱序全部受理", len(out["accepted"]) == 3 and out["errors"] == [])
s3 = next(x for x in svc.get_channel(db, cid)["specimens"] if x["sample_code"] == "S3")
check("批量归并后 S3 active 且参考侧到 v2", s3["status"] == "active" and s3["reference"]["applied_version"] == 2)
bad = MergeEventBatch(events=[ev("candidate", "S4", 1, value=80),
                              ev("reference", "S4", 1, value=999)])  # 超量程
try:
    svc.ingest_batch(db, cid, bad, U)
    check("非法批量应抛 422", False)
except HTTPException as e:
    check("非法批量应抛 422", e.status_code == 422)
check("整批回滚：S4 不存在", not any(x["sample_code"] == "S4" for x in svc.get_channel(db, cid)["specimens"]))

print("6) 输入校验：撤回不得带值、读数必须在量程内")
try:
    svc.ingest_event(db, cid, ev("reference", "S5", 1, kind="withdrawal", value=10), U)
    check("撤回带值应 422", False)
except HTTPException as e:
    check("撤回带值应 422", e.status_code == 422)
try:
    svc.ingest_event(db, cid, ev("candidate", "S5", 1, value=101), U)
    check("超量程应 422", False)
except HTTPException as e:
    check("超量程应 422", e.status_code == 422)

print("7) 冻结：同一时点双侧快照，列明缺侧/缺版/冲突")
# 再造：缺版（暂存缺口）、缺侧、撤回（缺侧原因）、完整
svc.ingest_event(db, cid, ev("reference", "S6", 2, value=30), U)  # S6 缺 v1 -> 暂存
svc.ingest_event(db, cid, ev("reference", "S7", 1, value=31), U)  # S7 只有参考侧
svc.ingest_event(db, cid, ev("reference", "S8", 1, value=32), U)
svc.ingest_event(db, cid, ev("candidate", "S8", 1, kind="withdrawal"), U)  # S8 候选撤回
frozen = svc.freeze_sample_set(db, cid, SampleSetInput(name="研究冻结 R1"), U)
codes_complete = {r["sample_code"] for r in frozen["rows"]}
check("完整配对入选（S2/S3），阻断与缺侧排除",
      codes_complete == {"S2", "S3"}, str(codes_complete))
ex = {e["sample_code"]: e for e in frozen["exclusions"]}
check("S1 冲突原因 conflict", ex["S1"]["reason_code"] == "conflict" and "同版异值" in ex["S1"]["reason"])
check("S6 缺版原因 missing_version", ex["S6"]["reason_code"] == "missing_version")
check("S7 缺侧原因 missing_side", ex["S7"]["reason_code"] == "missing_side" and "从未回传" in ex["S7"]["reason"])
check("S8 撤回按缺侧列明依据",
      ex["S8"]["reason_code"] == "missing_side" and "撤回" in ex["S8"]["reason"])
check("汇总计数正确",
      frozen["summary"]["counts"] == {"conflict": 1, "missing_version": 1, "missing_side": 2},
      str(frozen["summary"]["counts"]))
check("快照行带双侧版本号",
      frozen["rows"][0]["ref_version"] >= 1 and frozen["rows"][0]["cand_version"] == 1)

print("8) 冻结后补传不改旧研究；中断恢复保留待补版本与撤回依据")
# 补齐 S6 缺口、S7 缺侧、S8 撤回，再产生新完整配对（S6 仅单侧，仍属缺侧）
svc.ingest_event(db, cid, ev("reference", "S6", 1, value=29), U)
svc.ingest_event(db, cid, ev("candidate", "S7", 1, value=33), U)
svc.ingest_event(db, cid, ev("candidate", "S8", 2, value=34), U)
old = svc.get_sample_set(db, frozen["id"])
check("旧样本集完整配对数不变", old["complete_count"] == frozen["complete_count"] == 2)
check("旧样本集排除清单不变", len(old["exclusions"]) == frozen["excluded_count"] == 4)
check("旧快照数值不随后续归并变化",
      {(r["sample_code"], r["reference_value"]) for r in old["rows"]}
      == {(r["sample_code"], r["reference_value"]) for r in frozen["rows"]})
# 模拟进程重启：丢弃会话、重建会话（表与数据在同一 engine 内持久）
db2 = Session()
detail = svc.get_channel(db2, cid)
s6 = next(x for x in detail["specimens"] if x["sample_code"] == "S6")
check("恢复后 S6 已推进到 v2（仍缺候选侧）",
      s6["reference"]["applied_version"] == 2 and s6["status"] == "partial")
s1b = next(x for x in detail["specimens"] if x["sample_code"] == "S1")
check("恢复后阻断依据仍在", s1b["status"] == "blocked" and "同版异值" in s1b["block_reason"])
# 制造新的缺口验证待补版本持久
svc.ingest_event(db2, cid, ev("candidate", "S9", 3, value=90), U)
db3 = Session()
s9 = next(x for x in svc.get_channel(db3, cid)["specimens"] if x["sample_code"] == "S9")
check("恢复后待补版本仍暂存（v3 等 v1/v2）", s9["candidate"]["staged_versions"] == [3])
s2b = next(x for x in svc.get_channel(db3, cid)["specimens"] if x["sample_code"] == "S2")
check("撤回历史在事件流中可追溯",
      any(e["kind"] == "withdrawal" and e["state"] == "applied"
          for e in svc.list_events(db3, cid, search="S2")["rows"]))

print("9) 再次冻结取新时点，与旧集并存")
frozen2 = svc.freeze_sample_set(db, cid, SampleSetInput(name="研究冻结 R2"), U)
check("新冻结包含补传后的完整配对（S6 单侧仍排除）",
      {r["sample_code"] for r in frozen2["rows"]} == {"S2", "S3", "S7", "S8"},
      str({r["sample_code"] for r in frozen2["rows"]}))
ex2 = {e["sample_code"]: e for e in frozen2["exclusions"]}
check("S6 在新冻结中转为缺侧", ex2["S6"]["reason_code"] == "missing_side")
check("阻断试片 S1 仍不入选", all(r["sample_code"] != "S1" for r in frozen2["rows"]))
check("两次冻结为不同不可变样本集", frozen["id"] != frozen2["id"] and frozen["complete_count"] != frozen2["complete_count"])

print("10) 导出 CSV 含冻结时点与双侧版本")
csv_text = svc.export_sample_set(db, frozen2["id"])
check("CSV 表头正确", csv_text.splitlines()[0].lstrip("﻿").startswith(
    "sample_code,reference_pct,candidate_pct,reference_version,candidate_version,frozen_at,sample_set"))
check("CSV 行数与完整配对一致", len(csv_text.strip().splitlines()) - 1 == frozen2["complete_count"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
