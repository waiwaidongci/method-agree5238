from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from src.config.database import get_db
from src.utils.auth import current_user, editor
from src.schemas.merge import MergeChannelInput, MergeEventInput, MergeEventBatch, SampleSetInput
from src.controllers.merge_controller import (
    list_channels, create_channel, get_channel,
    ingest_event, ingest_batch, list_events,
    freeze_sample_set, list_sample_sets, get_sample_set, export_sample_set,
)

router = APIRouter(prefix="/merge")


@router.get("/channels")
def channels(db=Depends(get_db), user=Depends(current_user)):
    return list_channels(db)


@router.post("/channels", status_code=201)
def create(data: MergeChannelInput, db=Depends(get_db), user=Depends(editor)):
    return create_channel(db, data, user)


@router.get("/channels/{channel_id}")
def channel_detail(channel_id: int, db=Depends(get_db), user=Depends(current_user)):
    return get_channel(db, channel_id, user)


@router.post("/channels/{channel_id}/events", status_code=201)
def add_event(channel_id: int, data: MergeEventInput, db=Depends(get_db), user=Depends(editor)):
    return ingest_event(db, channel_id, data, user)


@router.post("/channels/{channel_id}/events/batch", status_code=201)
def add_batch(channel_id: int, data: MergeEventBatch, db=Depends(get_db), user=Depends(editor)):
    return ingest_batch(db, channel_id, data, user)


@router.get("/channels/{channel_id}/events")
def events(channel_id: int, search: str = Query(default="", max_length=80),
           state: str = Query(default="", pattern="^(staged|applied|quarantined|duplicate)?$"),
           offset: int = Query(default=0, ge=0),
           limit: int = Query(default=50, ge=1, le=200),
           db=Depends(get_db), user=Depends(current_user)):
    return list_events(db, channel_id, search, state, offset, limit)


@router.post("/channels/{channel_id}/freeze", status_code=201)
def freeze(channel_id: int, data: SampleSetInput, db=Depends(get_db), user=Depends(editor)):
    return freeze_sample_set(db, channel_id, data, user)


@router.get("/sample-sets")
def sample_sets(channel_id: int | None = Query(default=None),
                db=Depends(get_db), user=Depends(current_user)):
    return list_sample_sets(db, channel_id)


@router.get("/sample-sets/{set_id}")
def sample_set_detail(set_id: int, db=Depends(get_db), user=Depends(current_user)):
    return get_sample_set(db, set_id)


@router.get("/sample-sets/{set_id}/export", response_class=PlainTextResponse)
def sample_set_export(set_id: int, db=Depends(get_db), user=Depends(current_user)):
    return PlainTextResponse(
        export_sample_set(db, set_id), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=sample-set-{set_id}.csv"})
