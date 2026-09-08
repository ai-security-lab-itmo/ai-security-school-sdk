"""Documentation and responses from the shared agent-env API."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class PublicModel(BaseModel):
    """Preserve additive server fields as data, never executable behavior."""

    model_config = ConfigDict(extra="allow")


class ActionDescriptor(PublicModel):
    name: str
    description: str
    input_schema: JsonObject
    examples: list[JsonObject] = Field(default_factory=list)


class TaskSummary(PublicModel):
    task_id: str
    ctf_ref: str
    title: str


class InstanceDocumentation(PublicModel):
    instance_id: str
    agent_env_ref: str
    title: str
    description: str
    tasks: list[TaskSummary]


class InstanceList(PublicModel):
    instances: list[InstanceDocumentation]


class TaskDocumentation(TaskSummary):
    instance_id: str
    agent_env_ref: str
    description: str
    instructions: str
    action_payload_schema: JsonObject
    action_payload_examples: list[JsonObject] = Field(default_factory=list)
    actions: list[ActionDescriptor] = Field(default_factory=list)
    supports_grading: bool
    supports_standalone_grading: bool


class RuntimeResponse(PublicModel):
    status: str
    agent_env_ref: str | None = None
    ctf_ref: str | None = None
    state: JsonObject | None = None
    response: JsonObject | None = None
    completed: bool | None = None
    grader_passed: bool | None = None
    grader_result: JsonObject | None = None
    missing_prerequisites: list[JsonObject] | None = None
    usage: JsonObject | None = None
    message: str | None = None
