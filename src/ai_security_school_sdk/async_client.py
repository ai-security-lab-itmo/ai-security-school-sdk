"""Asynchronous client for the platform's shared agent-env runtime."""

import asyncio
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


class AsyncClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        options = client_options(token, base_url, timeout, max_retries, retry_backoff)
        self._http = httpx.AsyncClient(**options, transport=transport)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self.envs = AsyncEnvironments(self)
        self.tasks = AsyncTasks(self)

    @classmethod
    def from_env(cls, **overrides: Any) -> Self:
        return cls(**environment_config(overrides))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
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
                response = await self._http.request(method, path, json=body, params=params)
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise TransportError(may_have_executed=method != "GET") from exc
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt == retries:
                    return decode_response(response)
            await asyncio.sleep(retry_delay(response, attempt, self._retry_backoff))
        raise AssertionError("Unreachable retry state")


class AsyncEnvironments:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, instance_id: str) -> "AsyncEnvironmentResource":
        data = await self._client._request("GET", f"instances/{identifier(instance_id)}")
        info = parse_model(InstanceDocumentation, data)
        if info.instance_id != instance_id:
            raise ProtocolError("Server returned documentation for a different environment")
        return AsyncEnvironmentResource(self._client, info)

    async def list(self) -> list["AsyncEnvironmentResource"]:
        data = parse_model(InstanceList, await self._client._request("GET", "instances"))
        return [AsyncEnvironmentResource(self._client, info) for info in data.instances]


class AsyncTasks:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, task_id: str) -> "AsyncTaskResource":
        data = await self._client._request("GET", f"tasks/{identifier(task_id)}/documentation")
        info = parse_model(TaskDocumentation, data)
        if info.task_id != task_id:
            raise ProtocolError("Server returned documentation for a different task")
        return AsyncTaskResource(self._client, info, info.instance_id)


class AsyncEnvironmentResource(EnvironmentHandle):
    def __init__(self, client: AsyncClient, info: InstanceDocumentation) -> None:
        super().__init__(info)
        self.tasks = AsyncEnvironmentTasks(client, info)


class AsyncEnvironmentTasks:
    def __init__(self, client: AsyncClient, info: InstanceDocumentation) -> None:
        self._client = client
        self._info = info

    async def list(self) -> list["AsyncTaskResource"]:
        """Return handles from the environment snapshot; load task schemas on use."""
        return [
            AsyncTaskResource(self._client, task, self._info.instance_id)
            for task in self._info.tasks
        ]

    async def get(self, task_id: str) -> "AsyncTaskResource":
        if not any(task.task_id == task_id for task in self._info.tasks):
            raise ConfigurationError("The task is not in this environment's documentation")
        task = await self._client.tasks.get(task_id)
        if task.instance_id != self._info.instance_id:
            raise ProtocolError("Server returned documentation for a different environment")
        return task


class AsyncTaskResource(TaskHandle):
    def __init__(
        self, client: AsyncClient, info: TaskSummary | TaskDocumentation, instance_id: str
    ) -> None:
        super().__init__(info, instance_id)
        self._client = client
        self.actions = AsyncActions(self)

    async def documentation(self) -> TaskDocumentation:
        """Fetch current learner documentation and refresh this resource's metadata."""
        data = await self._client._request("GET", f"tasks/{identifier(self.task_id)}/documentation")
        info = parse_model(TaskDocumentation, data)
        if info.task_id != self.task_id or info.instance_id != self.instance_id:
            raise ProtocolError("Server returned documentation for a different task or environment")
        self.info = info
        return info

    async def state(self) -> RuntimeResponse:
        return parse_model(
            RuntimeResponse,
            await self._client._request(
                "GET", "state", params={"task_id": identifier(self.task_id)}
            ),
        )

    async def act(self, payload: JsonObject) -> RuntimeResponse:
        """Send the task's native payload, including chat schemas without an action name."""
        documentation = await self.documentation()
        return await self._act(validate_payload(documentation.action_payload_schema, payload))

    async def _act(self, payload: JsonObject) -> RuntimeResponse:
        return parse_model(
            RuntimeResponse,
            await self._client._request(
                "POST",
                "action",
                body={"task_id": identifier(self.task_id), "action_payload": payload},
            ),
        )

    async def grade(self, payload: JsonObject | None = None) -> RuntimeResponse:
        return parse_model(
            RuntimeResponse,
            await self._client._request(
                "POST",
                "grade",
                body={
                    "task_id": identifier(self.task_id),
                    "payload": json_object(payload if payload is not None else {}),
                },
            ),
        )

    async def reset(self) -> RuntimeResponse:
        """Reset the user's shared environment through the task's existing reset behavior."""
        return parse_model(
            RuntimeResponse,
            await self._client._request(
                "POST", "reset", body={"task_id": identifier(self.task_id)}
            ),
        )


class AsyncActions:
    def __init__(self, task: AsyncTaskResource) -> None:
        self._task = task

    async def list(self) -> list[ActionDescriptor]:
        return (await self._task.documentation()).actions

    async def call(self, name: str, arguments: JsonObject) -> RuntimeResponse:
        """Validate documented arguments and insert the native action discriminator."""
        documentation = await self._task.documentation()
        return await self._task._act(action_payload(documentation, name, arguments))
