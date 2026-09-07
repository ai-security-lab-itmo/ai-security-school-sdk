"""Asynchronous learner API with the same contracts as AsyncClient."""

import asyncio
import time
from types import TracebackType
from typing import Any, Self

import httpx

from ._handles import CheckpointHandle, JobHandle, LabHandle, RunHandle
from ._http import (
    DEFAULT_BASE_URL,
    RETRYABLE_STATUS,
    client_options,
    decode_response,
    environment_config,
    identifier,
    parse_model,
    positive_duration,
    remaining_timeout,
    request_headers,
    retry_delay,
)
from ._schema import validate_arguments
from .errors import (
    ActionValidationError,
    ConflictError,
    JobTimeoutError,
    ProtocolError,
    TransportError,
)
from .models import (
    ActionDescriptor,
    ActionManifest,
    CallResult,
    Checkpoint,
    EventPage,
    Job,
    JsonObject,
    Lab,
    LabList,
    Observation,
    Run,
    RunList,
    SubmissionResult,
    Task,
)


class AsyncClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        options = client_options(token, base_url, timeout, max_retries, retry_backoff)
        self._http = httpx.AsyncClient(**options, transport=transport)
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self.labs = AsyncLabs(self)
        self.tasks = AsyncTasks(self)
        self.runs = AsyncRuns(self)
        self.jobs = AsyncJobs(self)

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
        params: dict[str, str | int] | None = None,
        idempotency_key: str | None = None,
        deadline: float | None = None,
    ) -> JsonObject:
        # Generate the key once for all transport attempts, never inside the retry loop.
        headers = request_headers(method, idempotency_key)
        for attempt in range(self._max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = await self._http.request(
                    method,
                    path,
                    json=body,
                    params=params,
                    headers=headers,
                    timeout=remaining_timeout(self._timeout, deadline),
                )
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise TransportError(
                        "Unable to obtain a definitive server response",
                        idempotency_key=headers.get("Idempotency-Key"),
                    ) from exc
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt == self._max_retries:
                    return decode_response(response)
            delay = retry_delay(response, attempt, self._retry_backoff)
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.monotonic()))
            await asyncio.sleep(delay)
        raise AssertionError("Unreachable retry state")


class AsyncLabs:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, lab_id: str) -> "AsyncLabResource":
        data = await self._client._request("GET", f"labs/{identifier(lab_id)}")
        return AsyncLabResource(self._client, parse_model(Lab, data))

    async def list(self) -> list["AsyncLabResource"]:
        data = parse_model(LabList, await self._client._request("GET", "labs"))
        return [AsyncLabResource(self._client, lab) for lab in data.labs]


class AsyncTasks:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, task_id: str) -> Task:
        return parse_model(Task, await self._client._request("GET", f"tasks/{identifier(task_id)}"))


class AsyncLabResource(LabHandle):
    def __init__(self, client: AsyncClient, info: Lab) -> None:
        super().__init__(info)
        self.runs = AsyncLabRuns(client, info.lab_id)


class AsyncLabRuns:
    def __init__(self, client: AsyncClient, lab_id: str) -> None:
        self._client = client
        self._lab_id = identifier(lab_id)

    async def create(self, *, idempotency_key: str | None = None) -> "AsyncRunResource":
        data = await self._client._request(
            "POST", f"labs/{self._lab_id}/runs", body={}, idempotency_key=idempotency_key
        )
        return AsyncRunResource(self._client, parse_model(Run, data))

    async def list(self) -> list["AsyncRunResource"]:
        return await self._client.runs.list(lab_id=self._lab_id)


class AsyncRuns:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, run_id: str) -> "AsyncRunResource":
        data = await self._client._request("GET", f"runs/{identifier(run_id)}")
        return AsyncRunResource(self._client, parse_model(Run, data))

    async def list(self, *, lab_id: str) -> list["AsyncRunResource"]:
        data = parse_model(
            RunList,
            await self._client._request("GET", "runs", params={"lab_id": identifier(lab_id)}),
        )
        return [AsyncRunResource(self._client, run) for run in data.runs]


class AsyncRunResource(RunHandle):
    def __init__(self, client: AsyncClient, info: Run) -> None:
        super().__init__(info)
        self._client = client
        self._path = f"runs/{identifier(info.run_id)}"
        self.actions = AsyncActions(client, self)

    async def refresh(self) -> Self:
        self.info = parse_model(Run, await self._client._request("GET", self._path))
        return self

    async def observation(self) -> Observation:
        return parse_model(
            Observation, await self._client._request("GET", f"{self._path}/observation")
        )

    async def events(self, *, after: int = 0) -> EventPage:
        if not isinstance(after, int) or after < 0:
            raise ValueError("after must be a non-negative event cursor")
        return parse_model(
            EventPage,
            await self._client._request("GET", f"{self._path}/events", params={"after": after}),
        )

    async def checkpoint(self, *, idempotency_key: str | None = None) -> "AsyncCheckpointResource":
        data = await self._client._request(
            "POST", f"{self._path}/checkpoints", body={}, idempotency_key=idempotency_key
        )
        return AsyncCheckpointResource(self._client, parse_model(Checkpoint, data))

    async def start_submission(self, *, idempotency_key: str | None = None) -> "AsyncJobResource":
        data = await self._client._request(
            "POST", f"{self._path}/submissions", body={}, idempotency_key=idempotency_key
        )
        return AsyncJobResource(self._client, parse_model(Job, data))

    async def submit(
        self,
        *,
        timeout: float = 300.0,
        poll_interval: float = 0.5,
        idempotency_key: str | None = None,
    ) -> SubmissionResult:
        positive_duration(timeout, "timeout", allow_zero=True)
        positive_duration(poll_interval, "poll_interval")
        job = await self.start_submission(idempotency_key=idempotency_key)
        result = await job.wait(timeout=timeout, poll_interval=poll_interval)
        if not isinstance(result, SubmissionResult):
            raise ProtocolError("Submission returned an action result")
        self.info = self.info.model_copy(update={"stage_passed": result.passed})
        return result

    async def advance(self, *, idempotency_key: str | None = None) -> Self:
        self.info = parse_model(
            Run,
            await self._client._request(
                "POST", f"{self._path}/advance", body={}, idempotency_key=idempotency_key
            ),
        )
        return self

    async def close(self, *, idempotency_key: str | None = None) -> Self:
        self.info = parse_model(
            Run,
            await self._client._request(
                "POST", f"{self._path}/close", body={}, idempotency_key=idempotency_key
            ),
        )
        return self


class AsyncActions:
    def __init__(self, client: AsyncClient, run: AsyncRunResource) -> None:
        self._client = client
        self._run = run

    async def _manifest(self) -> ActionManifest:
        manifest = parse_model(
            ActionManifest,
            await self._client._request("GET", f"runs/{identifier(self._run.run_id)}/actions"),
        )
        if manifest.task_id != self._run.task_id:
            raise ConflictError("task_changed", "Current stage changed; refresh the run first")
        return manifest

    async def list(self) -> list[ActionDescriptor]:
        return (await self._manifest()).actions

    async def start_call(
        self, name: str, arguments: JsonObject, *, idempotency_key: str | None = None
    ) -> "AsyncJobResource":
        manifest = await self._manifest()
        descriptor = next((action for action in manifest.actions if action.name == name), None)
        if descriptor is None:
            raise ActionValidationError("This action is not available in the current stage")
        body = {
            "action": name,
            "arguments": validate_arguments(descriptor, arguments),
            "expected_task_id": manifest.task_id,
        }
        data = await self._client._request(
            "POST",
            f"runs/{identifier(self._run.run_id)}/calls",
            body=body,
            idempotency_key=idempotency_key,
        )
        return AsyncJobResource(self._client, parse_model(Job, data))

    async def call(
        self,
        name: str,
        arguments: JsonObject,
        *,
        timeout: float = 300.0,
        poll_interval: float = 0.5,
        idempotency_key: str | None = None,
    ) -> CallResult:
        positive_duration(timeout, "timeout", allow_zero=True)
        positive_duration(poll_interval, "poll_interval")
        job = await self.start_call(name, arguments, idempotency_key=idempotency_key)
        result = await job.wait(timeout=timeout, poll_interval=poll_interval)
        if not isinstance(result, CallResult):
            raise ProtocolError("Action returned a submission result")
        self._run.info = self._run.info.model_copy(update={"revision": result.state_revision})
        return result


class AsyncCheckpointResource(CheckpointHandle):
    def __init__(self, client: AsyncClient, info: Checkpoint) -> None:
        super().__init__(info)
        self._client = client

    async def fork(self, *, idempotency_key: str | None = None) -> AsyncRunResource:
        data = await self._client._request(
            "POST",
            f"checkpoints/{identifier(self.checkpoint_id)}/fork",
            body={},
            idempotency_key=idempotency_key,
        )
        return AsyncRunResource(self._client, parse_model(Run, data))


class AsyncJobs:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get(self, job_id: str) -> "AsyncJobResource":
        return AsyncJobResource(
            self._client,
            parse_model(Job, await self._client._request("GET", f"jobs/{identifier(job_id)}")),
        )


class AsyncJobResource(JobHandle):
    def __init__(self, client: AsyncClient, info: Job) -> None:
        super().__init__(info)
        self._client = client

    async def refresh(self) -> Self:
        self.info = parse_model(
            Job, await self._client._request("GET", f"jobs/{identifier(self.job_id)}")
        )
        return self

    async def cancel(self, *, idempotency_key: str | None = None) -> Self:
        self.info = parse_model(
            Job,
            await self._client._request(
                "POST",
                f"jobs/{identifier(self.job_id)}/cancel",
                body={},
                idempotency_key=idempotency_key,
            ),
        )
        return self

    async def wait(
        self, *, timeout: float = 300.0, poll_interval: float = 0.5
    ) -> CallResult | SubmissionResult:
        positive_duration(timeout, "timeout", allow_zero=True)
        positive_duration(poll_interval, "poll_interval")
        deadline = time.monotonic() + timeout
        while not self.done:
            if time.monotonic() >= deadline:
                raise JobTimeoutError(self.job_id, timeout)
            try:
                self.info = parse_model(
                    Job,
                    await self._client._request(
                        "GET", f"jobs/{identifier(self.job_id)}", deadline=deadline
                    ),
                )
            except (TimeoutError, TransportError) as exc:
                if time.monotonic() >= deadline or isinstance(exc, TimeoutError):
                    raise JobTimeoutError(self.job_id, timeout) from exc
                raise
            if not self.done:
                await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        return self.result()
