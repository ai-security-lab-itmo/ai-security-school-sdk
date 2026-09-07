"""Both clients are exercised against the same stateful HTTP contract fixture."""

import inspect
import json
from copy import deepcopy
from typing import Any

import httpx
import pytest

from ai_security_school_sdk import (
    ActionValidationError,
    APIError,
    AsyncClient,
    AuthenticationError,
    CallResult,
    Client,
    ConfigurationError,
    ConflictError,
    JobCancelledError,
    JobInterruptedError,
    JobTimeoutError,
    LimitExceededError,
    NotFoundError,
    PermissionDeniedError,
    ProtocolError,
    StageLockedError,
    SubmissionResult,
    TransportError,
)

NOW = "2026-09-07T12:00:00Z"


async def invoke(fn: Any, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class LearnerServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.checkpoints: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, tuple[bytes, dict[str, Any]]] = {}
        self.hold_jobs = False
        self.extra_schema: dict[str, Any] | None = None

    def action(self, stage: int) -> dict[str, Any]:
        return {
            "name": "add_document" if stage == 0 else "send_message",
            "description": "An explicit learner action",
            "input_schema": self.extra_schema
            or {
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "output_schema": {"type": "object"},
            "examples": [{"text": "hello"}],
            "version": "1",
            "future_field": True,
        }

    def task(self, stage: int) -> dict[str, Any]:
        return {
            "task_id": f"task_{stage}",
            "lab_id": "lab_a",
            "title": f"Stage {stage}",
            "instructions": "Investigate",
            "actions": [self.action(stage)],
        }

    def lab(self) -> dict[str, Any]:
        return {
            "lab_id": "lab_a",
            "title": "Documents",
            "description": "Training lab",
            "stages": [self.task(0), self.task(1)],
            "limits": {"concurrency": 2},
        }

    def create_run(self) -> dict[str, Any]:
        run = {
            "run_id": f"run_{len(self.runs)}",
            "lab_id": "lab_a",
            "task_id": "task_0",
            "stage_index": 0,
            "status": "idle",
            "revision": 0,
            "stage_passed": False,
            "created_at": NOW,
            "parent_checkpoint_id": None,
            "future_field": "ignored",
        }
        self.runs[run["run_id"]] = run
        return run

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.path.startswith("/api/learner/v1/")
        route = request.url.path.removeprefix("/api/learner/v1/")
        key = request.headers.get("Idempotency-Key")
        if request.method == "POST":
            assert key
            fingerprint = route.encode() + request.content
            if key in self.idempotency:
                old_fingerprint, result = self.idempotency[key]
                if fingerprint != old_fingerprint:
                    return httpx.Response(
                        409,
                        json={
                            "error": {
                                "code": "idempotency_conflict",
                                "message": "Different body",
                                "details": {},
                            }
                        },
                    )
                return httpx.Response(200, json=result)
        result = deepcopy(self.dispatch(request, route))
        if key:
            self.idempotency[key] = (route.encode() + request.content, result)
        return httpx.Response(200, json=result)

    def dispatch(self, request: httpx.Request, route: str) -> dict[str, Any]:
        if route == "labs":
            return {"labs": [self.lab()]}
        if route == "labs/lab_a":
            return self.lab()
        if route.startswith("tasks/"):
            return self.task(int(route[-1]))
        if route == "labs/lab_a/runs":
            return self.create_run()
        if route == "runs":
            assert request.url.params["lab_id"] == "lab_a"
            return {"runs": list(self.runs.values())}
        if route.startswith("checkpoints/"):
            checkpoint = self.checkpoints[route.split("/")[1]]
            run = self.create_run()
            run.update(
                {
                    "parent_checkpoint_id": checkpoint["checkpoint_id"],
                    "task_id": checkpoint["task_id"],
                    "revision": checkpoint["revision"],
                }
            )
            return run
        if route.startswith("jobs/"):
            job = self.jobs[route.split("/")[1]]
            if route.endswith("/cancel"):
                job["status"] = "cancelled"
            elif not self.hold_jobs and job["status"] == "queued":
                job["status"] = "succeeded"
            return job
        parts = route.split("/")
        run = self.runs[parts[1]]
        resource = parts[2] if len(parts) > 2 else ""
        if resource == "actions":
            return {"task_id": run["task_id"], "actions": [self.action(run["stage_index"])]}
        if resource == "observation":
            return {"task_id": run["task_id"], "state": {"document_count": 1}, "usage": {}}
        if resource == "events":
            assert int(request.url.params["after"]) >= 0
            return {
                "events": [
                    {
                        "sequence": 1,
                        "kind": "document_added",
                        "task_id": run["task_id"],
                        "data": {"document_id": "document_1"},
                        "created_at": NOW,
                    }
                ],
                "next_cursor": 1,
            }
        if resource == "checkpoints":
            checkpoint = {
                "checkpoint_id": f"checkpoint_{len(self.checkpoints)}",
                "run_id": run["run_id"],
                "lab_id": run["lab_id"],
                "task_id": run["task_id"],
                "revision": run["revision"],
                "created_at": NOW,
            }
            self.checkpoints[checkpoint["checkpoint_id"]] = checkpoint
            return checkpoint
        if resource in {"calls", "submissions"}:
            run["revision"] += 1
            kind = "call" if resource == "calls" else "submission"
            if kind == "call":
                body = json.loads(request.content)
                assert body["expected_task_id"] == run["task_id"]
                result = {
                    "data": {"document_id": "document_1"},
                    "state_revision": run["revision"],
                    "usage": {"requests": 1},
                }
            else:
                run["stage_passed"] = True
                result = {
                    "passed": True,
                    "success_count": 3,
                    "case_count": 3,
                    "success_rate": 1.0,
                    "training_passed": True,
                    "task_id": run["task_id"],
                }
            job = {
                "job_id": f"job_{len(self.jobs)}",
                "run_id": run["run_id"],
                "kind": kind,
                "status": "queued",
                "result": result,
                "error": None,
                "created_at": NOW,
            }
            self.jobs[job["job_id"]] = job
            return job
        if resource == "advance":
            run.update({"task_id": "task_1", "stage_index": 1, "stage_passed": False})
        if resource == "close":
            run["status"] = "closed"
        return run


@pytest.fixture(params=[Client, AsyncClient], ids=["sync", "async"])
async def connection(request: pytest.FixtureRequest) -> Any:
    server = LearnerServer()
    client = request.param(
        "test-token",
        base_url="https://school.example",
        transport=httpx.MockTransport(server.handle),
        retry_backoff=0,
    )
    yield client, server
    await invoke(client.close)


async def setup_run(client: Any) -> Any:
    lab = await invoke(client.labs.get, "lab_a")
    return await invoke(lab.runs.create)


async def test_complete_multistage_lifecycle(connection: Any) -> None:
    client, server = connection
    labs = await invoke(client.labs.list)
    assert labs[0].title == "Documents"
    assert (await invoke(client.tasks.get, "task_0")).actions[0].name == "add_document"
    run = await invoke(labs[0].runs.create)
    assert (await invoke(labs[0].runs.list))[0].run_id == run.run_id
    assert (await invoke(client.runs.get, run.run_id)).task_id == "task_0"
    assert (await invoke(run.actions.list))[0].name == "add_document"
    result = await invoke(run.actions.call, "add_document", {"text": "candidate"})
    assert isinstance(result, CallResult)
    assert result.data["document_id"] == "document_1"
    assert run.revision == 1
    assert (await invoke(run.observation)).state == {"document_count": 1}
    assert (await invoke(run.events, after=0)).next_cursor == 1
    checkpoint = await invoke(run.checkpoint)
    fork = await invoke(checkpoint.fork)
    assert fork.run_id != run.run_id
    assert fork.info.parent_checkpoint_id == checkpoint.checkpoint_id
    verdict = await invoke(run.submit)
    assert isinstance(verdict, SubmissionResult) and verdict.passed
    assert run.stage_passed
    assert await invoke(run.advance) is run
    assert run.task_id == "task_1"
    assert (await invoke(run.actions.list))[0].name == "send_message"
    await invoke(run.actions.call, "send_message", {"text": "continue"})
    assert await invoke(run.refresh) is run
    await invoke(fork.close)
    assert fork.status == "closed"
    assert server.runs[run.run_id]["status"] == "idle"
    assert all("Idempotency-Key" in r.headers for r in server.requests if r.method == "POST")


@pytest.mark.parametrize(
    "arguments", [{"text": ""}, {"text": 12}, {}, {"text": "ok", "owner": "x"}]
)
async def test_invalid_arguments_never_dispatch(connection: Any, arguments: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    with pytest.raises(ActionValidationError):
        await invoke(run.actions.call, "add_document", arguments)
    assert not server.jobs


async def test_internal_tools_not_callable(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    with pytest.raises(ActionValidationError):
        await invoke(run.actions.call, "refund.issue", {})
    assert not server.jobs


async def test_manifest_stage_change_requires_explicit_refresh(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    server.runs[run.run_id].update({"task_id": "task_1", "stage_index": 1})
    with pytest.raises(ConflictError, match="task_changed"):
        await invoke(run.actions.call, "add_document", {"text": "old request"})
    assert not server.jobs
    await invoke(run.refresh)
    await invoke(run.actions.call, "send_message", {"text": "new request"})


@pytest.mark.parametrize("reference", ["https://attacker.example/schema", "other.json"])
async def test_remote_schema_references_are_rejected(connection: Any, reference: str) -> None:
    client, server = connection
    run = await setup_run(client)
    server.extra_schema = {"$ref": reference}
    with pytest.raises(ProtocolError, match="local fragment"):
        await invoke(run.actions.call, "add_document", {})
    assert not server.jobs
    assert all(r.url.host == "school.example" for r in server.requests)


async def test_local_schema_references_work(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    server.extra_schema = {
        "$defs": {"text": {"type": "string"}},
        "type": "object",
        "properties": {"text": {"$ref": "#/$defs/text"}},
        "required": ["text"],
    }
    await invoke(run.actions.call, "add_document", {"text": "ok"})


async def test_timeout_retains_job_and_resume_never_resubmits(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    server.hold_jobs = True
    job = await invoke(run.actions.start_call, "add_document", {"text": "candidate"})
    with pytest.raises(JobTimeoutError) as error:
        await invoke(job.wait, timeout=0.003, poll_interval=0.001)
    assert error.value.job_id == job.job_id
    assert len(server.jobs) == 1
    server.hold_jobs = False
    resumed = await invoke(client.jobs.get, job.job_id)
    assert isinstance(await invoke(resumed.wait), CallResult)
    assert len(server.jobs) == 1


async def test_cancel_does_not_resubmit(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    server.hold_jobs = True
    job = await invoke(run.start_submission)
    await invoke(job.cancel)
    with pytest.raises(JobCancelledError) as error:
        await invoke(job.wait)
    assert error.value.job_id == job.job_id
    assert len(server.jobs) == 1


async def test_interrupted_job_is_distinct_from_failed_attack(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    job = await invoke(run.start_submission)
    server.jobs[job.job_id].update(
        {
            "status": "interrupted",
            "error": {
                "code": "job_interrupted",
                "message": "Worker lost",
                "details": {},
            },
        }
    )
    with pytest.raises(JobInterruptedError):
        await invoke(job.wait)


async def test_idempotent_calls_reuse_original_job_and_conflict_on_different_body(
    connection: Any,
) -> None:
    client, server = connection
    run = await setup_run(client)
    first = await invoke(
        run.actions.start_call, "add_document", {"text": "a"}, idempotency_key="fixed-key"
    )
    second = await invoke(
        run.actions.start_call, "add_document", {"text": "a"}, idempotency_key="fixed-key"
    )
    assert first.job_id == second.job_id and len(server.jobs) == 1
    with pytest.raises(ConflictError):
        await invoke(
            run.actions.start_call, "add_document", {"text": "b"}, idempotency_key="fixed-key"
        )


async def test_invalid_wait_options_do_not_start_job(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    with pytest.raises(ConfigurationError):
        await invoke(run.actions.call, "add_document", {"text": "a"}, poll_interval=-1)
    with pytest.raises(ConfigurationError):
        await invoke(run.submit, timeout=float("inf"))
    assert not server.jobs


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_transport_retry_after_accepted_post_uses_same_key(client_type: Any) -> None:
    server = LearnerServer()
    failed = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal failed
        response = server.handle(request)
        if request.url.path.endswith("/calls") and not failed:
            failed = True
            raise httpx.ReadError("response lost", request=request)
        return response

    client = client_type("test-token", transport=httpx.MockTransport(handler), retry_backoff=0)
    try:
        run = await setup_run(client)
        await invoke(run.actions.call, "add_document", {"text": "a"})
        calls = [r for r in server.requests if r.url.path.endswith("/calls")]
        assert len(calls) == 2
        assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]
        assert calls[0].content == calls[1].content
        assert len(server.jobs) == 1
    finally:
        await invoke(client.close)


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_exhausted_transport_error_exposes_reusable_mutation_key(client_type: Any) -> None:
    server = LearnerServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ConnectError("unreachable", request=request)
        return server.handle(request)

    client = client_type("test-token", transport=httpx.MockTransport(handler), retry_backoff=0)
    try:
        lab = await invoke(client.labs.get, "lab_a")
        with pytest.raises(TransportError) as error:
            await invoke(lab.runs.create)
        assert error.value.idempotency_key
        assert "test-token" not in str(error.value)
    finally:
        await invoke(client.close)


@pytest.mark.parametrize(
    "status,code,error_type",
    [
        (401, "invalid_token", AuthenticationError),
        (403, "forbidden", PermissionDeniedError),
        (404, "not_found", NotFoundError),
        (409, "stage_locked", StageLockedError),
        (409, "run_busy", ConflictError),
        (429, "budget_exceeded", LimitExceededError),
        (422, "invalid_arguments", APIError),
    ],
)
@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_typed_http_errors(client_type: Any, status: int, code: str, error_type: Any) -> None:
    client = client_type(
        "test-token",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                json={
                    "error": {
                        "code": code,
                        "message": "Example error",
                        "details": {"retry": False},
                    }
                },
            )
        ),
    )
    try:
        with pytest.raises(error_type) as error:
            await invoke(client.labs.list)
        assert error.value.code == code
        assert error.value.status_code == status
        assert error.value.details == {"retry": False}
    finally:
        await invoke(client.close)


@pytest.mark.parametrize("body", [[], {"labs": "not a list"}, {"labs": [{}]}])
@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_invalid_success_envelopes_are_protocol_errors(client_type: Any, body: Any) -> None:
    client = client_type(
        "test-token", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    try:
        with pytest.raises(ProtocolError):
            await invoke(client.labs.list)
    finally:
        await invoke(client.close)


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_no_redirect_token_forwarding(client_type: Any) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.example"})

    client = client_type("test-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(APIError):
            await invoke(client.labs.list)
        assert len(requests) == 1
    finally:
        await invoke(client.close)


def test_environment_and_url_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_SECURITY_SCHOOL_TOKEN", raising=False)
    with pytest.raises(ConfigurationError):
        Client.from_env()
    monkeypatch.setenv("AI_SECURITY_SCHOOL_TOKEN", "test-token")
    monkeypatch.setenv("AI_SECURITY_SCHOOL_BASE_URL", "http://localhost:8000")
    with Client.from_env() as client:
        assert str(client._http.base_url) == "http://localhost:8000/api/learner/v1/"
    for url in ["http://remote.example", "https://user:pass@example.com", "https://a.com/admin"]:
        with pytest.raises(ConfigurationError):
            Client("test-token", base_url=url)


async def test_resource_path_cannot_escape_api(connection: Any) -> None:
    client, server = connection
    with pytest.raises(ConfigurationError):
        await invoke(client.runs.get, "../../admin")
    assert not server.requests


async def test_client_close_does_not_close_server_run(connection: Any) -> None:
    client, server = connection
    run = await setup_run(client)
    await invoke(client.close)
    assert server.runs[run.run_id]["status"] == "idle"
