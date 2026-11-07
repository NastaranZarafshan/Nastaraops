from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    kind: Literal["email", "document", "request", "report"]
    title: str = Field(min_length=3, max_length=180)
    content: str = Field(min_length=1, max_length=30000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionCreate(BaseModel):
    approved: bool
    reviewer: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=1000)


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    title: str
    content: str
    metadata_json: dict
    draft_json: dict | None
    result_json: dict | None
    risk_reasons: list
    trace_json: list
    decision_json: dict | None
    agent: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None


class MemoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    kind: str
    summary: str
    created_at: datetime


class AuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str | None
    actor: str
    action: str
    detail_json: dict
    created_at: datetime
    prev_hash: str
    event_hash: str


class KnowledgeDocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    filename: str
    file_type: str
    sha256: str
    status: str
    chunk_count: int
    uploaded_by: str
    error: str | None
    created_at: datetime
    indexed_at: datetime | None


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=3000)
    limit: int = Field(default=5, ge=1, le=12)


class EvaluationCreate(BaseModel):
    use_llm_judge: bool = False
    gold_source_ids: list[str] = Field(default_factory=list, max_length=30)


class EvaluationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    task_id: str
    mode: str
    evaluator_version: str
    status: str
    metrics_json: dict
    details_json: dict
    judge_model: str | None
    error: str | None
    created_at: datetime


class BenchmarkRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dataset_id: str
    dataset_sha256: str
    judge_model: str
    runner_version: str
    status: str
    total: int
    progress: int
    predictions_json: list
    metrics_json: dict
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
