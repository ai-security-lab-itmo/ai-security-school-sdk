"""Pure shared behavior for resource handles."""

from datetime import datetime
from typing import Literal

from ._http import parse_model
from .errors import ProtocolError, api_error
from .models import CallResult, Checkpoint, Job, Lab, Run, SubmissionResult, Task


class LabHandle:
    def __init__(self, info: Lab) -> None:
        self.info = info

    @property
    def lab_id(self) -> str:
        return self.info.lab_id

    @property
    def title(self) -> str:
        return self.info.title

    @property
    def description(self) -> str:
        return self.info.description

    @property
    def stages(self) -> list[Task]:
        return self.info.stages


class RunHandle:
    def __init__(self, info: Run) -> None:
        self.info = info

    @property
    def run_id(self) -> str:
        return self.info.run_id

    @property
    def lab_id(self) -> str:
        return self.info.lab_id

    @property
    def task_id(self) -> str:
        return self.info.task_id

    @property
    def stage_index(self) -> int:
        return self.info.stage_index

    @property
    def status(self) -> Literal["idle", "busy", "closed"]:
        return self.info.status

    @property
    def revision(self) -> int:
        return self.info.revision

    @property
    def stage_passed(self) -> bool:
        return self.info.stage_passed

    @property
    def created_at(self) -> datetime:
        return self.info.created_at


class CheckpointHandle:
    def __init__(self, info: Checkpoint) -> None:
        self.info = info

    @property
    def checkpoint_id(self) -> str:
        return self.info.checkpoint_id


class JobHandle:
    def __init__(self, info: Job) -> None:
        self.info = info

    @property
    def job_id(self) -> str:
        return self.info.job_id

    @property
    def run_id(self) -> str:
        return self.info.run_id

    @property
    def status(self) -> str:
        return self.info.status

    @property
    def done(self) -> bool:
        return self.info.status not in {"queued", "running"}

    def result(self) -> CallResult | SubmissionResult:
        if not self.done:
            raise ProtocolError("Job is still running; call wait() before result()")
        if self.info.status != "succeeded":
            error = self.info.error
            raise api_error(
                error.code if error else f"job_{self.info.status}",
                error.message if error else f"Job {self.info.status}",
                details=error.details if error else {},
                job_id=self.job_id,
            )
        if self.info.result is None:
            raise ProtocolError("Succeeded job is missing its result")
        if self.info.kind == "submission":
            return parse_model(SubmissionResult, self.info.result)
        return parse_model(CallResult, self.info.result)
