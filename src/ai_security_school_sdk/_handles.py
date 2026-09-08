"""Shared metadata properties; resources do not represent separate server sessions."""

from .models import InstanceDocumentation, TaskDocumentation, TaskSummary


class EnvironmentHandle:
    def __init__(self, info: InstanceDocumentation) -> None:
        self.info = info

    @property
    def instance_id(self) -> str:
        return self.info.instance_id

    @property
    def title(self) -> str:
        return self.info.title


class TaskHandle:
    def __init__(self, info: TaskSummary | TaskDocumentation, instance_id: str) -> None:
        self.info = info
        self._instance_id = instance_id

    @property
    def task_id(self) -> str:
        return self.info.task_id

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def title(self) -> str:
        return self.info.title
