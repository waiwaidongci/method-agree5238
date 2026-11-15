from pydantic import BaseModel, Field


class MergeChannelInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    reference_id: int
    candidate_id: int
    note: str = Field(default="", max_length=600)


class MergeEventInput(BaseModel):
    side: str = Field(pattern="^(reference|candidate)$")
    sample_code: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    kind: str = Field(pattern="^(reading|withdrawal)$")
    value: float | None = Field(default=None, allow_inf_nan=False)


class MergeEventBatch(BaseModel):
    events: list[MergeEventInput] = Field(min_length=1, max_length=2000)


class SampleSetInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    note: str = Field(default="", max_length=600)
