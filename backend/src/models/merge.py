from datetime import datetime
from sqlalchemy import String, ForeignKey, Float, Integer, JSON, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MergeChannel(Base):
    """双侧增量归并通道：绑定两台仪器，承接各自独立回传的读数/撤回。"""
    __tablename__ = "merge_channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    reference_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    candidate_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    note: Mapped[str] = mapped_column(String(600), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MergeEvent(Base):
    """单侧单次回传。自然键 (channel, side, sample_code, version) 保证重复回传不重复计入。"""
    __tablename__ = "merge_events"
    __table_args__ = (
        UniqueConstraint("channel_id", "side", "sample_code", "version", name="uq_merge_event_natural"),
        Index("ix_merge_event_lookup", "channel_id", "sample_code", "side", "version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("merge_channels.id"), index=True)
    side: Mapped[str] = mapped_column(String(16))  # reference / candidate
    sample_code: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)  # 从 1 开始连续递增
    kind: Mapped[str] = mapped_column(String(16))  # reading / withdrawal
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="staged")  # staged/applied/quarantined/duplicate
    reason: Mapped[str] = mapped_column(String(300), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MergeSpecimen(Base):
    """试片级归并状态：两侧当前生效版本、待补版本与阻断依据，中断恢复后仍是事实源。"""
    __tablename__ = "merge_specimens"
    __table_args__ = (
        UniqueConstraint("channel_id", "sample_code", name="uq_merge_specimen"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("merge_channels.id"), index=True)
    sample_code: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="waiting")  # waiting/partial/active/withdrawn/blocked
    ref_applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ref_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ref_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cand_applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cand_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cand_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    block_reason: Mapped[str] = mapped_column(String(600), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SampleSet(Base):
    """冻结的研究样本集：记录冻结时点，后续补传不影响其内容。"""
    __tablename__ = "sample_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("merge_channels.id"))
    frozen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(160), default="")
    complete_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # 缺侧/缺版/冲突计数与说明


class SampleSetRow(Base):
    """冻结时点的双侧读数快照，数值直接落库，不随后续归并变化。"""
    __tablename__ = "sample_set_rows"
    __table_args__ = (
        UniqueConstraint("sample_set_id", "sample_code", name="uq_sample_set_row"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    sample_set_id: Mapped[int] = mapped_column(ForeignKey("sample_sets.id"), index=True)
    sample_code: Mapped[str] = mapped_column(String(80))
    reference_value: Mapped[float] = mapped_column(Float)
    candidate_value: Mapped[float] = mapped_column(Float)
    ref_version: Mapped[int] = mapped_column(Integer)
    cand_version: Mapped[int] = mapped_column(Integer)


class SampleSetExclusion(Base):
    """未进入样本集的试片及其原因：缺侧、缺版（含撤回）、同版异值冲突阻断。"""
    __tablename__ = "sample_set_exclusions"
    id: Mapped[int] = mapped_column(primary_key=True)
    sample_set_id: Mapped[int] = mapped_column(ForeignKey("sample_sets.id"), index=True)
    sample_code: Mapped[str] = mapped_column(String(80))
    reason_code: Mapped[str] = mapped_column(String(24))  # conflict/missing_version/missing_side
    reason: Mapped[str] = mapped_column(String(600))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
