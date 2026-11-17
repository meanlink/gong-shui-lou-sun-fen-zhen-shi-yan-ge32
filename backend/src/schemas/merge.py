from pydantic import BaseModel, Field


class MergeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    base_dataset_id: int
    left_label: str = Field(default='第一队', max_length=100)
    right_label: str = Field(default='第二队', max_length=100)


class SegmentUpdate(BaseModel):
    version: int
    values: dict


class SegmentAdd(BaseModel):
    version: int
    record: dict


class VersionInput(BaseModel):
    version: int


class DecisionInput(BaseModel):
    choice: str = Field(min_length=1, max_length=20)
    values: dict | None = None
    note: str = Field(default='', max_length=500)
    left_rev: int | None = None
    right_rev: int | None = None
