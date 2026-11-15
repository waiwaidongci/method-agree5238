import csv
import io
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models import (
    Instrument, MergeChannel, MergeEvent, MergeSpecimen,
    SampleSet, SampleSetRow, SampleSetExclusion,
)
from src.constants.enums import (
    MergeSide, MergeEventKind, MergeEventState, MergeSpecimenStatus,
)

# ---------- 通道 ----------

def list_channels(db):
    channels = db.scalars(select(MergeChannel).order_by(MergeChannel.id)).all()
    out = []
    for ch in channels:
        out.append({
            "id": ch.id, "name": ch.name, "note": ch.note,
            "reference_id": ch.reference_id, "candidate_id": ch.candidate_id,
            "reference_code": db.get(Instrument, ch.reference_id).code,
            "candidate_code": db.get(Instrument, ch.candidate_id).code,
            **channel_counters(db, ch.id),
        })
    return out


def channel_counters(db, channel_id):
    rows = db.scalars(select(MergeSpecimen).where(MergeSpecimen.channel_id == channel_id)).all()
    counters = {"specimen_total": len(rows), "complete": 0, "partial": 0,
                "blocked": 0, "withdrawn": 0, "waiting": 0}
    for s in rows:
        if s.status == MergeSpecimenStatus.ACTIVE:
            counters["complete"] += 1
        elif s.status == MergeSpecimenStatus.BLOCKED:
            counters["blocked"] += 1
        elif s.status == MergeSpecimenStatus.WITHDRAWN:
            counters["withdrawn"] += 1
        elif s.status == MergeSpecimenStatus.PARTIAL:
            counters["partial"] += 1
        else:
            counters["waiting"] += 1
    counters["staged_events"] = db.scalar(select(func.count()).select_from(MergeEvent).where(
        MergeEvent.channel_id == channel_id, MergeEvent.state == MergeEventState.STAGED))
    return counters


def create_channel(db, data, user):
    if data.reference_id == data.candidate_id:
        raise HTTPException(422, "参考与候选必须为不同仪器")
    ref = db.get(Instrument, data.reference_id)
    cand = db.get(Instrument, data.candidate_id)
    if not ref or not cand:
        raise HTTPException(404, "仪器不存在")
    if ref.unit != cand.unit:
        raise HTTPException(422, "两台仪器必须使用相同量纲")
    if db.scalar(select(MergeChannel).where(MergeChannel.name == data.name)):
        raise HTTPException(409, "归并通道名称已存在")
    item = MergeChannel(**data.model_dump())
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "归并通道名称已存在")
    return get_channel(db, item.id, user)


def _require_channel(db, channel_id):
    ch = db.get(MergeChannel, channel_id)
    if not ch:
        raise HTTPException(404, "归并通道不存在")
    return ch


def get_channel(db, channel_id, user=None):
    ch = _require_channel(db, channel_id)
    ref = db.get(Instrument, ch.reference_id)
    cand = db.get(Instrument, ch.candidate_id)
    specimens = db.scalars(select(MergeSpecimen).where(
        MergeSpecimen.channel_id == channel_id).order_by(MergeSpecimen.sample_code)).all()
    rows = [_specimen_payload(db, s) for s in specimens]
    sets = db.scalars(select(SampleSet).where(
        SampleSet.channel_id == channel_id).order_by(SampleSet.id.desc())).all()
    return {
        "id": ch.id, "name": ch.name, "note": ch.note,
        "reference_id": ch.reference_id, "candidate_id": ch.candidate_id,
        "reference_code": ref.code, "candidate_code": cand.code,
        "unit": ref.unit,
        "counters": channel_counters(db, ch.id),
        "specimens": rows,
        "sample_sets": [{
            "id": x.id, "name": x.name, "frozen_at": x.frozen_at.isoformat(),
            "created_by": x.created_by, "complete_count": x.complete_count,
            "excluded_count": x.excluded_count, "summary": x.summary,
        } for x in sets],
        "can_freeze": any(s.status == MergeSpecimenStatus.ACTIVE for s in specimens),
    }


# ---------- 事件归并 ----------

def _get_specimen(db, channel_id, code, lock=False):
    q = select(MergeSpecimen).where(
        MergeSpecimen.channel_id == channel_id, MergeSpecimen.sample_code == code)
    if lock:
        q = q.with_for_update()
    return db.scalar(q)


def _applied_version(specimen, side):
    return specimen.ref_applied_version if side == MergeSide.REFERENCE else specimen.cand_applied_version


def _apply_to_specimen(specimen, side, event):
    if side == MergeSide.REFERENCE:
        specimen.ref_applied_version = event.version
        specimen.ref_kind = event.kind
        specimen.ref_value = event.value
    else:
        specimen.cand_applied_version = event.version
        specimen.cand_kind = event.kind
        specimen.cand_value = event.value


def _recompute_status(specimen):
    if specimen.status == MergeSpecimenStatus.BLOCKED:
        return
    kinds = (specimen.ref_kind, specimen.cand_kind)
    if MergeEventKind.WITHDRAWAL in kinds:
        specimen.status = MergeSpecimenStatus.WITHDRAWN
    elif specimen.ref_kind == MergeEventKind.READING and specimen.cand_kind == MergeEventKind.READING:
        specimen.status = MergeSpecimenStatus.ACTIVE
    elif specimen.ref_kind or specimen.cand_kind:
        specimen.status = MergeSpecimenStatus.PARTIAL
    else:
        specimen.status = MergeSpecimenStatus.WAITING


def _block(specimen, event, reason):
    event.state = MergeEventState.QUARANTINED
    event.reason = reason
    specimen.status = MergeSpecimenStatus.BLOCKED
    specimen.block_reason = reason if not specimen.block_reason else specimen.block_reason + "｜" + reason


def _promote(db, channel_id, specimen, side):
    """按版本连续推进：乱序暂存事件在补齐前版后依次生效；遇缺口停下。"""
    if specimen.status == MergeSpecimenStatus.BLOCKED:
        return
    expected = (_applied_version(specimen, side) or 0) + 1
    staged = db.scalars(select(MergeEvent).where(
        MergeEvent.channel_id == channel_id,
        MergeEvent.sample_code == specimen.sample_code,
        MergeEvent.side == side,
        MergeEvent.state == MergeEventState.STAGED,
    ).order_by(MergeEvent.version)).all()
    by_version = {e.version: e for e in staged}
    while expected in by_version:
        event = by_version[expected]
        event.state = MergeEventState.APPLIED
        event.reason = ""
        _apply_to_specimen(specimen, side, event)
        expected += 1
    _recompute_status(specimen)


def _ingest_one(db, channel_id, data):
    """单条归并，不提交；由单条/批量入口统一控制事务边界。"""
    ch = _require_channel(db, channel_id)
    instrument = db.get(Instrument, ch.reference_id if data.side == MergeSide.REFERENCE else ch.candidate_id)
    # 输入校验：撤回不带值；读数带值且在仪器量程内
    if data.kind == MergeEventKind.WITHDRAWAL and data.value is not None:
        raise HTTPException(422, f"试片{data.sample_code} v{data.version} 撤回事件不得携带读数")
    if data.kind == MergeEventKind.READING:
        if data.value is None:
            raise HTTPException(422, f"试片{data.sample_code} v{data.version} 读数事件缺少数值")
        if not instrument.lower <= data.value <= instrument.upper:
            raise HTTPException(422, f"试片{data.sample_code} v{data.version} 读数超出仪器{instrument.code}量程")

    specimen = _get_specimen(db, channel_id, data.sample_code, lock=True)
    if specimen is None:
        specimen = MergeSpecimen(channel_id=channel_id, sample_code=data.sample_code)
        db.add(specimen)
        db.flush()
    applied_version = _applied_version(specimen, data.side)

    existing = db.scalar(select(MergeEvent).where(
        MergeEvent.channel_id == channel_id, MergeEvent.side == data.side,
        MergeEvent.sample_code == data.sample_code, MergeEvent.version == data.version))

    verdict = "applied"
    if existing is not None:
        same = existing.kind == data.kind and (
            (existing.value is None and data.value is None)
            or (existing.value is not None and data.value is not None and abs(existing.value - data.value) < 1e-12))
        if same:
            # 重复回传：不重复计入，状态保持原样
            verdict = "duplicate"
        elif applied_version is not None and data.version < applied_version:
            # 严格旧版的异值重传：旧版不得复活撤回值或任何历史读数，自然键保留首条，状态不变
            verdict = "stale"
        else:
            # 当前版或暂存版同版异值：隔离首条事件并永久阻断该试片
            reason = (f"试片{data.sample_code} {data.side}侧 v{data.version} 同版异值冲突："
                      f"已收 {existing.kind}"
                      + (f"={existing.value}" if existing.value is not None else "")
                      + f"，又收 {data.kind}"
                      + (f"={data.value}" if data.value is not None else ""))
            if existing.state != MergeEventState.QUARANTINED:
                existing.state = MergeEventState.QUARANTINED
                existing.reason = reason
            _block(specimen, existing, reason)
            verdict = "conflict"
    elif specimen.status == MergeSpecimenStatus.BLOCKED:
        reason = f"试片{data.sample_code}已因冲突阻断，{data.side}侧 v{data.version} 隔离待人工处置"
        event = MergeEvent(channel_id=channel_id, side=data.side, sample_code=data.sample_code,
                           version=data.version, kind=data.kind, value=data.value,
                           state=MergeEventState.QUARANTINED, reason=reason)
        db.add(event)
        verdict = "quarantined"
    elif applied_version is not None and data.version <= applied_version:
        # 旧版回传（自然键中无此载荷时）：一律隔离，不改变当前生效版本，撤回值不被复活
        reason = (f"试片{data.sample_code} {data.side}侧 v{data.version} 为旧版"
                  f"（当前已到 v{applied_version}），旧版回传不复活任何读数或撤回")
        event = MergeEvent(channel_id=channel_id, side=data.side, sample_code=data.sample_code,
                           version=data.version, kind=data.kind, value=data.value,
                           state=MergeEventState.QUARANTINED, reason=reason)
        db.add(event)
        verdict = "stale"
    else:
        # 新版回传：先暂存，再尝试从缺口之后连续推进；乱序数据在补齐前版前一直保留
        event = MergeEvent(channel_id=channel_id, side=data.side, sample_code=data.sample_code,
                           version=data.version, kind=data.kind, value=data.value,
                           state=MergeEventState.STAGED)
        db.add(event)
        db.flush()
        _promote(db, channel_id, specimen, data.side)
        verdict = db.get(MergeEvent, event.id).state

    db.flush()
    return {"sample_code": data.sample_code, "side": data.side, "version": data.version,
            "kind": data.kind, "verdict": verdict,
            "specimen": _specimen_payload(db, _get_specimen(db, channel_id, data.sample_code))}


def ingest_event(db, channel_id, data, user):
    try:
        result = _ingest_one(db, channel_id, data)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise


def ingest_batch(db, channel_id, batch, user):
    # 整批在同一事务内顺序归并，保证批内乱序也能连续推进；任一条非法则整批不入库
    try:
        results = []
        for item in batch.events:
            results.append(_ingest_one(db, channel_id, item))
        db.commit()
        return {"accepted": results, "errors": []}
    except HTTPException:
        db.rollback()
        raise


# ---------- 只读视图 ----------

def _side_payload(db, channel_id, code, side, applied_version, kind, value):
    staged = db.scalars(select(MergeEvent.version).where(
        MergeEvent.channel_id == channel_id, MergeEvent.sample_code == code,
        MergeEvent.side == side, MergeEvent.state == MergeEventState.STAGED)).all()
    quarantined = db.scalars(select(MergeEvent).where(
        MergeEvent.channel_id == channel_id, MergeEvent.sample_code == code,
        MergeEvent.side == side, MergeEvent.state == MergeEventState.QUARANTINED
    ).order_by(MergeEvent.version)).all()
    return {
        "applied_version": applied_version, "kind": kind, "value": value,
        "staged_versions": sorted(staged),
        "quarantined": [{"version": e.version, "kind": e.kind, "value": e.value,
                         "reason": e.reason} for e in quarantined],
    }


def _specimen_payload(db, s):
    return {
        "id": s.id, "sample_code": s.sample_code, "status": s.status,
        "block_reason": s.block_reason,
        "reference": _side_payload(db, s.channel_id, s.sample_code, MergeSide.REFERENCE,
                                   s.ref_applied_version, s.ref_kind, s.ref_value),
        "candidate": _side_payload(db, s.channel_id, s.sample_code, MergeSide.CANDIDATE,
                                   s.cand_applied_version, s.cand_kind, s.cand_value),
        "updated_at": s.updated_at.isoformat(),
    }


def list_events(db, channel_id, search="", state="", offset=0, limit=50):
    _require_channel(db, channel_id)
    q = select(MergeEvent).where(MergeEvent.channel_id == channel_id)
    if search:
        q = q.where(MergeEvent.sample_code.contains(search, autoescape=True))
    if state:
        q = q.where(MergeEvent.state == state)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(MergeEvent.id.desc()).offset(offset).limit(limit)).all()
    return {"total": total, "rows": [{
        "id": e.id, "side": e.side, "sample_code": e.sample_code, "version": e.version,
        "kind": e.kind, "value": e.value, "state": e.state, "reason": e.reason,
        "received_at": e.received_at.isoformat(),
    } for e in rows]}


# ---------- 冻结样本集 ----------

def _exclusion_for(db, s):
    """按 冲突 > 缺版 > 缺侧 的优先级列明未入选原因与依据。"""
    detail = {
        "reference": _side_payload(db, s.channel_id, s.sample_code, MergeSide.REFERENCE,
                                   s.ref_applied_version, s.ref_kind, s.ref_value),
        "candidate": _side_payload(db, s.channel_id, s.sample_code, MergeSide.CANDIDATE,
                                   s.cand_applied_version, s.cand_kind, s.cand_value),
    }
    if s.status == MergeSpecimenStatus.BLOCKED:
        return "conflict", f"同版异值冲突已阻断：{s.block_reason}", detail
    if detail["reference"]["staged_versions"] or detail["candidate"]["staged_versions"]:
        return "missing_version", f"试片{s.sample_code}存在乱序暂存、前版未补齐，暂不推进", detail
    missing = []
    for side_name, payload in (("reference", detail["reference"]), ("candidate", detail["candidate"])):
        if payload["kind"] is None:
            missing.append(f"{side_name}侧从未回传")
        elif payload["kind"] == MergeEventKind.WITHDRAWAL:
            missing.append(f"{side_name}侧 v{payload['applied_version']} 已撤回，当前无生效读数")
    return "missing_side", "；".join(missing), detail


def freeze_sample_set(db, channel_id, data, user):
    _require_channel(db, channel_id)
    if db.scalar(select(SampleSet).where(SampleSet.name == data.name)):
        raise HTTPException(409, "研究样本集名称已存在")
    # 单事务加锁：冻结期间回传写入在同一事实源上排队，快照只取同一时点的双侧状态
    specimens = db.scalars(select(MergeSpecimen).where(
        MergeSpecimen.channel_id == channel_id).with_for_update()
        .order_by(MergeSpecimen.sample_code)).all()
    if not specimens:
        raise HTTPException(422, "通道内尚无任何试片回传，无法冻结")
    if not any(s.status == MergeSpecimenStatus.ACTIVE for s in specimens):
        raise HTTPException(422, "当前没有双侧完整配对，无法冻结研究样本集")
    frozen_at = datetime.utcnow()
    item = SampleSet(name=data.name, channel_id=channel_id, frozen_at=frozen_at,
                     created_by=user.email, summary={})
    db.add(item)
    db.flush()

    complete_rows, exclusions = [], []
    counters = {"conflict": 0, "missing_version": 0, "missing_side": 0}
    for s in specimens:
        if s.status == MergeSpecimenStatus.ACTIVE:
            complete_rows.append(SampleSetRow(
                sample_set_id=item.id, sample_code=s.sample_code,
                reference_value=s.ref_value, candidate_value=s.cand_value,
                ref_version=s.ref_applied_version, cand_version=s.cand_applied_version))
        else:
            code, reason, detail = _exclusion_for(db, s)
            counters[code] += 1
            exclusions.append(SampleSetExclusion(
                sample_set_id=item.id, sample_code=s.sample_code,
                reason_code=code, reason=reason, detail=detail))
    db.add_all(complete_rows)
    db.add_all(exclusions)
    item.complete_count = len(complete_rows)
    item.excluded_count = len(exclusions)
    item.summary = {
        "note": data.note,
        "frozen_at": frozen_at.isoformat(),
        "complete": len(complete_rows),
        "excluded": len(exclusions),
        "reasons": {
            "conflict": "同版异值冲突，试片已隔离阻断",
            "missing_version": "乱序暂存，前版未补齐",
            "missing_side": "缺侧回传或该侧读数已撤回",
        },
        "counts": counters,
    }
    db.commit()
    return get_sample_set(db, item.id)


def list_sample_sets(db, channel_id=None):
    q = select(SampleSet).order_by(SampleSet.id.desc())
    if channel_id is not None:
        q = q.where(SampleSet.channel_id == channel_id)
    return [{
        "id": x.id, "name": x.name, "channel_id": x.channel_id,
        "frozen_at": x.frozen_at.isoformat(), "created_by": x.created_by,
        "complete_count": x.complete_count, "excluded_count": x.excluded_count,
        "summary": x.summary,
    } for x in db.scalars(q).all()]


def get_sample_set(db, set_id):
    item = db.get(SampleSet, set_id)
    if not item:
        raise HTTPException(404, "研究样本集不存在")
    rows = db.scalars(select(SampleSetRow).where(
        SampleSetRow.sample_set_id == set_id).order_by(SampleSetRow.sample_code)).all()
    exclusions = db.scalars(select(SampleSetExclusion).where(
        SampleSetExclusion.sample_set_id == set_id).order_by(SampleSetExclusion.id)).all()
    return {
        "id": item.id, "name": item.name, "channel_id": item.channel_id,
        "frozen_at": item.frozen_at.isoformat(), "created_by": item.created_by,
        "complete_count": item.complete_count, "excluded_count": item.excluded_count,
        "summary": item.summary,
        "rows": [{
            "sample_code": r.sample_code, "reference_value": r.reference_value,
            "candidate_value": r.candidate_value, "ref_version": r.ref_version,
            "cand_version": r.cand_version,
        } for r in rows],
        "exclusions": [{
            "sample_code": e.sample_code, "reason_code": e.reason_code,
            "reason": e.reason, "detail": e.detail,
        } for e in exclusions],
    }


def export_sample_set(db, set_id):
    item = get_sample_set(db, set_id)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output)
    writer.writerow(["sample_code", "reference_pct", "candidate_pct",
                     "reference_version", "candidate_version", "frozen_at", "sample_set"])
    for r in item["rows"]:
        code = ("'" + r["sample_code"]) if r["sample_code"].startswith(
            ('=', '+', '-', '@', '\t', '\r')) else r["sample_code"]
        writer.writerow([code, r["reference_value"], r["candidate_value"],
                         r["ref_version"], r["cand_version"], item["frozen_at"], item["name"]])
    return output.getvalue()
