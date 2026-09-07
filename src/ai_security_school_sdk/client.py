"""Synchronous learner API. Local context managers only close HTTP connections."""

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


class Client:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        options = client_options(token, base_url, timeout, max_retries, retry_backoff)
        self._http = httpx.Client(**options, transport=transport)
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self.labs = Labs(self)
        self.tasks = Tasks(self)
        self.runs = Runs(self)
        self.jobs = Jobs(self)

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
        params: dict[str, str | int] | None = None,
        idempotency_key: str | None = None,
        deadline: float | None = None,
    ) -> JsonObject:
        # Generate the key once for all transport attempts, never inside the retry loop.
        headers = request_headers(method, idempotency_key)
        for attempt in range(self._max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._http.request(
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
            time.sleep(delay)
        raise AssertionError("Unreachable retry state")


class Labs:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, lab_id: str) -> "LabResource":
        data = self._client._request("GET", f"labs/{identifier(lab_id)}")
        return LabResource(self._client, parse_model(Lab, data))

    def list(self) -> list["LabResource"]:
        data = parse_model(LabList, self._client._request("GET", "labs"))
        return [LabResource(self._client, lab) for lab in data.labs]


class Tasks:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, task_id: str) -> Task:
        return parse_model(Task, self._client._request("GET", f"tasks/{identifier(task_id)}"))


class LabResource(LabHandle):
    def __init__(self, client: Client, info: Lab) -> None:
        super().__init__(info)
        self.runs = LabRuns(client, info.lab_id)


class LabRuns:
    def __init__(self, client: Client, lab_id: str) -> None:
        self._client = client
        self._lab_id = identifier(lab_id)

    def create(self, *, idempotency_key: str | None = None) -> "RunResource":
        data = self._client._request(
            "POST", f"labs/{self._lab_id}/runs", body={}, idempotency_key=idempotency_key
        )
        return RunResource(self._client, parse_model(Run, data))

    def list(self) -> list["RunResource"]:
        return self._client.runs.list(lab_id=self._lab_id)


class Runs:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, run_id: str) -> "RunResource":
        data = self._client._request("GET", f"runs/{identifier(run_id)}")
        return RunResource(self._client, parse_model(Run, data))

    def list(self, *, lab_id: str) -> list["RunResource"]:
        data = parse_model(
            RunList, self._client._request("GET", "runs", params={"lab_id": identifier(lab_id)})
        )
        return [RunResource(self._client, run) for run in data.runs]


class RunResource(RunHandle):
    def __init__(self, client: Client, info: Run) -> None:
        super().__init__(info)
        self._client = client
        self._path = f"runs/{identifier(info.run_id)}"
        self.actions = Actions(client, self)

    def refresh(self) -> Self:
        self.info = parse_model(Run, self._client._request("GET", self._path))
        return self

    def observation(self) -> Observation:
        return parse_model(Observation, self._client._request("GET", f"{self._path}/observation"))

    def events(self, *, after: int = 0) -> EventPage:
        if not isinstance(after, int) or after < 0:
            raise ValueError("after must be a non-negative event cursor")
        return parse_model(
            EventPage, self._client._request("GET", f"{self._path}/events", params={"after": after})
        )

    def checkpoint(self, *, idempotency_key: str | None = None) -> "CheckpointResource":
        data = self._client._request(
            "POST", f"{self._path}/checkpoints", body={}, idempotency_key=idempotency_key
        )
        return CheckpointResource(self._client, parse_model(Checkpoint, data))

    def start_submission(self, *, idempotency_key: str | None = None) -> "JobResource":
        data = self._client._request(
            "POST", f"{self._path}/submissions", body={}, idempotency_key=idempotency_key
        )
        return JobResource(self._client, parse_model(Job, data))

    def submit(
        self,
        *,
        timeout: float = 300.0,
        poll_interval: float = 0.5,
        idempotency_key: str | None = None,
    ) -> SubmissionResult:
        positive_duration(timeout, "timeout", allow_zero=True)
        positive_duration(poll_interval, "poll_interval")
        result = self.start_submission(idempotency_key=idempotency_key).wait(
            timeout=timeout, poll_interval=poll_interval
        )
        if not isinstance(result, SubmissionResult):
            raise ProtocolError("Submission returned an action result")
        self.info = self.info.model_copy(update={"stage_passed": result.passed})
        return result

    def advance(self, *, idempotency_key: str | None = None) -> Self:
        self.info = parse_model(
            Run,
            self._client._request(
                "POST", f"{self._path}/advance", body={}, idempotency_key=idempotency_key
            ),
        )
        return self

    def close(self, *, idempotency_key: str | None = None) -> Self:
        self.info = parse_model(
            Run,
            self._client._request(
                "POST", f"{self._path}/close", body={}, idempotency_key=idempotency_key
            ),
        )
        return self


class Actions:
    def __init__(self, client: Client, run: RunResource) -> None:
        self._client = client
        self._run = run

    def _manifest(self) -> ActionManifest:
        manifest = parse_model(
            ActionManifest,
            self._client._request("GET", f"runs/{identifier(self._run.run_id)}/actions"),
        )
        if manifest.task_id != self._run.task_id:
            raise ConflictError("task_changed", "Current stage changed; refresh the run first")
        return manifest

    def list(self) -> list[ActionDescriptor]:
        return self._manifest().actions

    def start_call(
        self, name: str, arguments: JsonObject, *, idempotency_key: str | None = None
    ) -> "JobResource":
        manifest = self._manifest()
        descriptor = next((action for action in manifest.actions if action.name == name), None)
        if descriptor is None:
            raise ActionValidationError("This action is not available in the current stage")
        body = {
            "action": name,
            "arguments": validate_arguments(descriptor, arguments),
            "expected_task_id": manifest.task_id,
        }
        data = self._client._request(
            "POST",
            f"runs/{identifier(self._run.run_id)}/calls",
            body=body,
            idempotency_key=idempotency_key,
        )
        return JobResource(self._client, parse_model(Job, data))

    def call(
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
        result = self.start_call(name, arguments, idempotency_key=idempotency_key).wait(
            timeout=timeout, poll_interval=poll_interval
        )
        if not isinstance(result, CallResult):
            raise ProtocolError("Action returned a submission result")
        self._run.info = self._run.info.model_copy(update={"revision": result.state_revision})
        return result


class CheckpointResource(CheckpointHandle):
    def __init__(self, client: Client, info: Checkpoint) -> None:
        super().__init__(info)
        self._client = client

    def fork(self, *, idempotency_key: str | None = None) -> RunResource:
        data = self._client._request(
            "POST",
            f"checkpoints/{identifier(self.checkpoint_id)}/fork",
            body={},
            idempotency_key=idempotency_key,
        )
        return RunResource(self._client, parse_model(Run, data))


class Jobs:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get(self, job_id: str) -> "JobResource":
        return JobResource(
            self._client,
            parse_model(Job, self._client._request("GET", f"jobs/{identifier(job_id)}")),
        )


class JobResource(JobHandle):
    def __init__(self, client: Client, info: Job) -> None:
        super().__init__(info)
        self._client = client

    def refresh(self) -> Self:
        self.info = parse_model(
            Job, self._client._request("GET", f"jobs/{identifier(self.job_id)}")
        )
        return self

    def cancel(self, *, idempotency_key: str | None = None) -> Self:
        self.info = parse_model(
            Job,
            self._client._request(
                "POST",
                f"jobs/{identifier(self.job_id)}/cancel",
                body={},
                idempotency_key=idempotency_key,
            ),
        )
        return self

    def wait(
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
                    self._client._request(
                        "GET", f"jobs/{identifier(self.job_id)}", deadline=deadline
                    ),
                )
            except (TimeoutError, TransportError) as exc:
                if time.monotonic() >= deadline or isinstance(exc, TimeoutError):
                    raise JobTimeoutError(self.job_id, timeout) from exc
                raise
            if not self.done:
                time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        return self.result()
