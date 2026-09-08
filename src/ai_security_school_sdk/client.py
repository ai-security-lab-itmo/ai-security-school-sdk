"""Synchronous client for the platform's shared agent-env runtime."""

import time
from types import TracebackType
from typing import Any, Self

import httpx

from ._handles import EnvironmentHandle, TaskHandle
from ._http import (
    DEFAULT_BASE_URL,
    RETRYABLE_STATUS,
    client_options,
    decode_response,
    environment_config,
    identifier,
    parse_model,
    retry_delay,
)
from ._schema import action_payload, json_object, validate_payload
from .errors import ConfigurationError, ProtocolError, TransportError
from .models import (
    ActionDescriptor,
    InstanceDocumentation,
    InstanceList,
    JsonObject,
    RuntimeResponse,
    TaskDocumentation,
    TaskSummary,
)


class Client:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        options = client_options(token, base_url, timeout, max_retries, retry_backoff)
        self._http = httpx.Client(**options, transport=transport)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self.envs = Environments(self)
        self.tasks = Tasks(self)

    @classmethod
    def from_env(cls, **overrides: Any) -> Self:
        return cls(**environment_config(overrides))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: JsonObject | None = None,
        params: dict[str, str] | None = None,
    ) -> JsonObject:
        # The shared runtime does not deduplicate mutations. Never retry a POST.
        retries = self._max_retries if method == "GET" else 0
        for attempt in range(retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._http.request(method, path, json=body, params=params)
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise TransportError(may_have_executed=method != "GET") from exc
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt == retries:
                    return decode_response(response)
            time.sleep(retry_delay(response, attempt, self._retry_backoff))
        raise AssertionError("Unreachable retry state")


class Environments:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, instance_id: str) -> "EnvironmentResource":
        data = self._client._request("GET", f"instances/{identifier(instance_id)}")
        info = parse_model(InstanceDocumentation, data)
        if info.instance_id != instance_id:
            raise ProtocolError("Server returned documentation for a different environment")
        return EnvironmentResource(self._client, info)

    def list(self) -> list["EnvironmentResource"]:
        data = parse_model(InstanceList, self._client._request("GET", "instances"))
        return [EnvironmentResource(self._client, info) for info in data.instances]


class Tasks:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, task_id: str) -> "TaskResource":
        data = self._client._request("GET", f"tasks/{identifier(task_id)}/documentation")
        info = parse_model(TaskDocumentation, data)
        if info.task_id != task_id:
            raise ProtocolError("Server returned documentation for a different task")
        return TaskResource(self._client, info, info.instance_id)


class EnvironmentResource(EnvironmentHandle):
    def __init__(self, client: Client, info: InstanceDocumentation) -> None:
        super().__init__(info)
        self.tasks = EnvironmentTasks(client, info)


class EnvironmentTasks:
    def __init__(self, client: Client, info: InstanceDocumentation) -> None:
        self._client = client
        self._info = info

    def list(self) -> list["TaskResource"]:
        """Return handles from the environment snapshot; load task schemas on use."""
        return [
            TaskResource(self._client, task, self._info.instance_id) for task in self._info.tasks
        ]

    def get(self, task_id: str) -> "TaskResource":
        if not any(task.task_id == task_id for task in self._info.tasks):
            raise ConfigurationError("The task is not in this environment's documentation")
        task = self._client.tasks.get(task_id)
        if task.instance_id != self._info.instance_id:
            raise ProtocolError("Server returned documentation for a different environment")
        return task


class TaskResource(TaskHandle):
    def __init__(
        self, client: Client, info: TaskSummary | TaskDocumentation, instance_id: str
    ) -> None:
        super().__init__(info, instance_id)
        self._client = client
        self.actions = Actions(self)

    def documentation(self) -> TaskDocumentation:
        """Fetch current learner documentation and refresh this resource's metadata."""
        data = self._client._request("GET", f"tasks/{identifier(self.task_id)}/documentation")
        info = parse_model(TaskDocumentation, data)
        if info.task_id != self.task_id or info.instance_id != self.instance_id:
            raise ProtocolError("Server returned documentation for a different task or environment")
        self.info = info
        return info

    def state(self) -> RuntimeResponse:
        return parse_model(
            RuntimeResponse,
            self._client._request("GET", "state", params={"task_id": identifier(self.task_id)}),
        )

    def act(self, payload: JsonObject) -> RuntimeResponse:
        """Send the task's native payload, including chat schemas without an action name."""
        documentation = self.documentation()
        return self._act(validate_payload(documentation.action_payload_schema, payload))

    def _act(self, payload: JsonObject) -> RuntimeResponse:
        return parse_model(
            RuntimeResponse,
            self._client._request(
                "POST",
                "action",
                body={"task_id": identifier(self.task_id), "action_payload": payload},
            ),
        )

    def grade(self, payload: JsonObject | None = None) -> RuntimeResponse:
        return parse_model(
            RuntimeResponse,
            self._client._request(
                "POST",
                "grade",
                body={
                    "task_id": identifier(self.task_id),
                    "payload": json_object(payload if payload is not None else {}),
                },
            ),
        )

    def reset(self) -> RuntimeResponse:
        """Reset the user's shared environment through the task's existing reset behavior."""
        return parse_model(
            RuntimeResponse,
            self._client._request("POST", "reset", body={"task_id": identifier(self.task_id)}),
        )


class Actions:
    def __init__(self, task: TaskResource) -> None:
        self._task = task

    def list(self) -> list[ActionDescriptor]:
        return self._task.documentation().actions

    def call(self, name: str, arguments: JsonObject) -> RuntimeResponse:
        """Validate documented arguments and insert the native action discriminator."""
        documentation = self._task.documentation()
        return self._task._act(action_payload(documentation, name, arguments))
