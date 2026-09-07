"""Typed learner API envelopes; action-specific inputs and outputs remain JSON."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class PublicModel(BaseModel):
    """Accept additive server fields without exposing them as executable behavior."""

    model_config = ConfigDict(extra="ignore")


class ActionDescriptor(PublicModel):
    name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    examples: list[JsonObject] = Field(default_factory=list)
    version: str


class Task(PublicModel):
    task_id: str
    lab_id: str
    title: str
    instructions: str
    actions: list[ActionDescriptor]


class Lab(PublicModel):
    lab_id: str
    title: str
    description: str
    stages: list[Task]
    limits: JsonObject


class Run(PublicModel):
    run_id: str
    lab_id: str
    task_id: str
    stage_index: int
    status: Literal["idle", "busy", "closed"]
    revision: int
    stage_passed: bool
    created_at: datetime
    parent_checkpoint_id: str | None = None


class APIErrorDetail(PublicModel):
    code: str
    message: str
    details: JsonObject = Field(default_factory=dict)


class Job(PublicModel):
    job_id: str
    run_id: str
    kind: Literal["call", "submission"]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    result: JsonObject | None = None
    error: APIErrorDetail | None = None
    created_at: datetime


class CallResult(PublicModel):
    data: JsonObject
    state_revision: int
    usage: JsonObject


class SubmissionResult(PublicModel):
    passed: bool
    success_count: int
    case_count: int
    success_rate: float
    training_passed: bool
    task_id: str


class Checkpoint(PublicModel):
    checkpoint_id: str
    run_id: str
    lab_id: str
    task_id: str
    revision: int
    created_at: datetime


class Event(PublicModel):
    sequence: int
    kind: str
    task_id: str
    data: JsonObject
    created_at: datetime


class EventPage(PublicModel):
    events: list[Event]
    next_cursor: int


class Observation(PublicModel):
    state: JsonObject
    task_id: str
    usage: JsonObject


class ActionManifest(PublicModel):
    actions: list[ActionDescriptor]
    task_id: str


class LabList(PublicModel):
    labs: list[Lab]


class RunList(PublicModel):
    runs: list[Run]
