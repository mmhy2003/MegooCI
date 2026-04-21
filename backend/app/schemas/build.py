import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BuildTriggerRequest(BaseModel):
    branch: str | None = None
    commit_sha: str | None = None
    params: dict | None = None


class BuildResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pipeline_id: uuid.UUID
    number: int
    branch: str | None
    commit_sha: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    triggered_by: uuid.UUID | None
    trigger_type: str
    params_json: dict | None
    created_at: datetime
    updated_at: datetime | None = None


class StageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    build_id: uuid.UUID
    name: str
    status: str
    sort_order: int
    started_at: datetime | None
    finished_at: datetime | None


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stage_id: uuid.UUID
    name: str
    step_type: str = "run"
    command: str | None = None
    config_json: dict | None = None
    status: str
    exit_code: int | None
    sort_order: int
    started_at: datetime | None
    finished_at: datetime | None


class StageDetailResponse(StageResponse):
    steps: list[StepResponse] = []


class BuildDetailResponse(BuildResponse):
    stages: list[StageDetailResponse] = []
