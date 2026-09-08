"""The same stateful agent-env contract is exercised through both client variants."""

import inspect
import json
from copy import deepcopy
from importlib.metadata import version
from typing import Any

import httpx
import pytest

from ai_security_school_sdk import (
    ActionValidationError,
    APIError,
    AsyncClient,
    AuthenticationError,
    Client,
    ConfigurationError,
    ConflictError,
    LimitExceededError,
    NotFoundError,
    PermissionDeniedError,
    ProtocolError,
    RuntimeResponse,
    TaskDocumentation,
    TransportError,
    __version__,
)
from ai_security_school_sdk._http import retry_delay


async def invoke(fn: Any, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class AgentEnvServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.messages: list[str] = []
        self.graded = False
        self.hook: Any = None
        self.docs = {
            "task_named": self.documentation("task_named", named=True),
            "task_chat": self.documentation("task_chat", named=False),
        }

    @staticmethod
    def documentation(task_id: str, *, named: bool) -> dict[str, Any]:
        arguments = {
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
            "additionalProperties": False,
        }
        payload = deepcopy(arguments)
        if named:
            payload["properties"]["action"] = {"const": "send_message"}
            payload["required"].append("action")
        return {
            "task_id": task_id,
            "instance_id": "env_one",
            "agent_env_ref": "existing_agent",
            "ctf_ref": task_id.removeprefix("task_"),
            "title": task_id,
            "description": "Existing CTF",
            "instructions": "Inspect the agent",
            "action_payload_schema": payload,
            "action_payload_examples": [
                {"action": "send_message", "message": "Hello"} if named else {"message": "Hello"}
            ],
            "actions": [
                {
                    "name": "send_message",
                    "description": "Send a message to the agent",
                    "input_schema": arguments,
                    "examples": [{"message": "Hello"}],
                }
            ]
            if named
            else [],
            "supports_grading": True,
            "supports_standalone_grading": True,
            "future_documentation": "preserved",
        }

    def instance(self) -> dict[str, Any]:
        return {
            "instance_id": "env_one",
            "agent_env_ref": "existing_agent",
            "title": "Existing agent",
            "description": "Tasks share the user's state",
            "tasks": [
                {key: doc[key] for key in ("task_id", "ctf_ref", "title")}
                for doc in self.docs.values()
            ],
        }

    def state(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "state": {"messages": list(self.messages)},
            "completed": self.graded,
            "usage": {"requests": len(self.messages)},
            "future_runtime_field": {"retained": True},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["User-Agent"] == f"ai-security-school-sdk/{__version__}"
        assert request.url.path.startswith("/api/agent-env/")
        assert "Idempotency-Key" not in request.headers
        assert "session_id" not in request.url.params
        if self.hook is not None:
            result = self.hook(request)
            if result is not None:
                return result
        route = request.url.path.removeprefix("/api/agent-env/")
        if route == "instances":
            assert request.method == "GET"
            return httpx.Response(200, json={"instances": [self.instance()]})
        if route == "instances/env_one":
            return httpx.Response(200, json=self.instance())
        if route.startswith("tasks/") and route.endswith("/documentation"):
            assert request.method == "GET"
            return httpx.Response(200, json=self.docs[route.split("/")[1]])
        if route == "state":
            assert request.method == "GET"
            assert request.url.params["task_id"] in self.docs
            return httpx.Response(200, json=self.state())
        assert request.method == "POST"
        body = json.loads(request.content)
        assert "session_id" not in body
        assert body["task_id"] in self.docs
        if route == "action":
            assert set(body) == {"task_id", "action_payload"}
            self.messages.append(body["action_payload"]["message"])
            return httpx.Response(200, json={**self.state(), "response": {"answer": "Received"}})
        if route == "grade":
            assert set(body) == {"task_id", "payload"}
            self.graded = bool(self.messages)
            return httpx.Response(
                200,
                json={
                    **self.state(),
                    "grader_passed": self.graded,
                    "grader_result": {"reason": "Checked shared state"},
                },
            )
        if route == "reset":
            assert set(body) == {"task_id"}
            self.messages.clear()
            return httpx.Response(200, json={**self.state(), "reset": True})
        raise AssertionError(f"Unexpected route: {route}")


@pytest.fixture(params=[Client, AsyncClient], ids=["sync", "async"])
async def setup(request: Any) -> Any:
    server = AgentEnvServer()
    client = request.param(
        "test-token", transport=httpx.MockTransport(server.handle), retry_backoff=0
    )
    yield client, server
    await invoke(client.close)


async def test_discovery_and_existing_tasks_share_runtime_state(setup: Any) -> None:
    client, server = setup
    envs = await invoke(client.envs.list)
    assert [(env.instance_id, env.title) for env in envs] == [("env_one", "Existing agent")]
    env = await invoke(client.envs.get, "env_one")
    before_list = len(server.requests)
    tasks = await invoke(env.tasks.list)
    assert [task.task_id for task in tasks] == ["task_named", "task_chat"]
    assert len(server.requests) == before_list  # Summaries do not load every task's schema.
    named = await invoke(env.tasks.get, "task_named")
    docs = await invoke(named.documentation)
    assert isinstance(docs, TaskDocumentation)
    assert docs.model_dump()["future_documentation"] == "preserved"
    assert (await invoke(named.actions.list))[0].examples == [{"message": "Hello"}]
    result = await invoke(named.actions.call, "send_message", {"message": "First"})
    assert isinstance(result, RuntimeResponse)
    assert result.response == {"answer": "Received"}
    action_request = server.requests[-1]
    assert json.loads(action_request.content) == {
        "task_id": "task_named",
        "action_payload": {"action": "send_message", "message": "First"},
    }
    chat = await invoke(client.tasks.get, "task_chat")
    assert await invoke(chat.actions.list) == []
    assert (await invoke(chat.state)).state == {"messages": ["First"]}
    await invoke(chat.act, {"message": "Second"})
    verdict = await invoke(chat.grade)
    assert verdict.grader_passed is True
    assert verdict.completed is True
    assert json.loads(server.requests[-1].content)["payload"] == {}
    result = await invoke(named.state)
    assert result.state == {"messages": ["First", "Second"]}
    assert result.model_dump()["future_runtime_field"] == {"retained": True}
    await invoke(named.reset)
    assert (await invoke(chat.state)).state == {"messages": []}
    assert (await invoke(chat.state)).completed is True  # Existing reset policy retains credit.


async def test_lazy_task_handles_load_current_documentation(setup: Any) -> None:
    client, server = setup
    env = await invoke(client.envs.get, "env_one")
    task = (await invoke(env.tasks.list))[0]
    assert not isinstance(task.info, TaskDocumentation)
    doc = await invoke(task.documentation)
    assert task.info is doc
    server.docs["task_named"]["instructions"] = "Updated by author"
    assert (await invoke(task.documentation)).instructions == "Updated by author"
    with pytest.raises(ConfigurationError):
        await invoke(env.tasks.get, "other_task")


async def test_call_uses_latest_documentation_and_never_guesses_hidden_actions(setup: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_named")
    server.docs["task_named"]["actions"] = []
    for name in ("send_message", "internal_agent_tool"):
        with pytest.raises(ActionValidationError, match="not documented"):
            await invoke(task.actions.call, name, {"message": "Hello"})
    assert all(req.method == "GET" for req in server.requests)
    # The native schema can still be used without inventing an action descriptor.
    await invoke(task.act, {"action": "send_message", "message": "Hello"})


@pytest.mark.parametrize(
    "arguments", [{}, {"message": ""}, {"message": 4}, {"action": "other", "message": "x"}]
)
async def test_invalid_named_arguments_never_reach_runtime(setup: Any, arguments: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_named")
    with pytest.raises(ActionValidationError):
        await invoke(task.actions.call, "send_message", arguments)
    assert all(req.method == "GET" for req in server.requests)


async def test_convenience_call_validates_full_native_schema_too(setup: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_named")
    server.docs["task_named"]["action_payload_schema"]["required"].append("another_required_field")
    with pytest.raises(ActionValidationError):
        await invoke(task.actions.call, "send_message", {"message": "Hello"})
    assert not server.messages


@pytest.mark.parametrize(
    "payload", [[], {"message": float("nan")}, {"message": object()}, {"message": ""}]
)
async def test_native_payload_validation_and_redacted_errors(setup: Any, payload: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_chat")
    with pytest.raises(ActionValidationError):
        await invoke(task.act, payload)
    with pytest.raises(ActionValidationError) as error:
        await invoke(task.act, {"message": "", "secret": "PRIVATE_PAYLOAD"})
    assert "PRIVATE_PAYLOAD" not in str(error.value)
    assert not server.messages


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.com/private-schema"},
        {"$dynamicRef": "file:///etc/passwd"},
        {"type": "object", "properties": {"message": {"$ref": "other.json"}}},
        {"$ref": 123},
        {"$ref": "#/missing"},
        {"type": "invalid-type"},
    ],
)
async def test_external_invalid_and_unresolvable_schema_references_are_blocked(
    setup: Any, schema: Any
) -> None:
    client, server = setup
    server.docs["task_chat"]["action_payload_schema"] = schema
    task = await invoke(client.tasks.get, "task_chat")
    with pytest.raises(ProtocolError):
        await invoke(task.act, {"message": "Hello"})
    assert not server.messages
    assert all(req.url.host == "plgn.aisecschool.ru" for req in server.requests)


async def test_local_schema_references_are_supported(setup: Any) -> None:
    client, server = setup
    server.docs["task_chat"]["action_payload_schema"] = {
        "$defs": {"text": {"type": "string", "minLength": 1}},
        "type": "object",
        "properties": {"message": {"$ref": "#/$defs/text"}},
        "required": ["message"],
    }
    task = await invoke(client.tasks.get, "task_chat")
    await invoke(task.act, {"message": "Hello"})
    assert server.messages == ["Hello"]


async def test_grade_payload_passed_and_validated_without_requiring_named_actions(
    setup: Any,
) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_chat")
    await invoke(task.grade, {"answer": "custom"})
    assert json.loads(server.requests[-1].content)["payload"] == {"answer": "custom"}
    before = len(server.requests)
    with pytest.raises(ActionValidationError):
        await invoke(task.grade, {"answer": float("inf")})
    assert len(server.requests) == before


async def test_inline_grading_is_rejected_before_mutation_using_fresh_documentation(
    setup: Any,
) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_chat")
    server.docs["task_chat"]["supports_standalone_grading"] = False
    with pytest.raises(ActionValidationError, match="Standalone grading is unavailable"):
        await invoke(task.grade)
    assert not any(request.method == "POST" for request in server.requests)
    assert task.info.supports_grading is True
    assert task.info.supports_standalone_grading is False


@pytest.mark.parametrize("mutation", ["act", "grade", "reset"])
@pytest.mark.parametrize("failure", ["timeout", "connection", 429, 502, 503, 504])
async def test_mutations_are_never_retried(setup: Any, mutation: str, failure: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_chat")

    def fail(request: httpx.Request) -> httpx.Response | None:
        if request.method != "POST":
            return None
        if failure == "timeout":
            raise httpx.ReadTimeout("No definitive reply", request=request)
        if failure == "connection":
            raise httpx.ConnectError("Connection closed", request=request)
        return httpx.Response(failure, json={"detail": "temporarily_unavailable"})

    server.hook = fail
    with pytest.raises((TransportError, APIError)) as error:
        await invoke(
            getattr(task, mutation), *([{"message": "Hello"}] if mutation == "act" else [])
        )
    if isinstance(error.value, TransportError):
        assert error.value.may_have_executed is True
        assert "inspect task.state() before retrying" in str(error.value)
    assert sum(req.method == "POST" for req in server.requests) == 1


@pytest.mark.parametrize("failure", ["timeout", 429, 502, 503, 504])
async def test_get_retries_are_bounded_and_can_recover(setup: Any, failure: Any) -> None:
    client, server = setup
    attempts = 0

    def fail_twice(request: httpx.Request) -> httpx.Response | None:
        nonlocal attempts
        attempts += 1
        if attempts > 2:
            return None
        if failure == "timeout":
            raise httpx.ReadTimeout("Transient", request=request)
        return httpx.Response(failure, json={"detail": "try_again"})

    server.hook = fail_twice
    assert len(await invoke(client.envs.list)) == 1
    assert attempts == 3
    attempts = 0

    def fail_always(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Still unavailable", request=request)

    server.hook = fail_always
    with pytest.raises(TransportError) as error:
        await invoke(client.envs.list)
    assert error.value.may_have_executed is False
    assert attempts == 3


@pytest.mark.parametrize(
    "status, error_type",
    [
        (400, APIError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (429, LimitExceededError),
    ],
)
async def test_agent_env_error_format(setup: Any, status: int, error_type: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_chat")
    server.hook = lambda request: httpx.Response(
        status,
        json={
            "error": {
                "message": "Quota exhausted",
                "code": "usage_limit_exceeded",
                "details": {"operation": "grade"},
            },
            "usage": {"cost": 3},
            "retry_after": 12,
        },
    )
    with pytest.raises(error_type) as error:
        await invoke(task.grade)
    assert error.value.status_code == status
    assert error.value.code == "usage_limit_exceeded"
    assert error.value.message == "Quota exhausted"
    assert error.value.usage == {"cost": 3}
    assert error.value.details["retry_after"] == 12
    assert error.value.details["operation"] == "grade"


async def test_nested_errors_and_unexpected_error_shapes(setup: Any) -> None:
    client, server = setup
    server.hook = lambda request: httpx.Response(
        401, json={"error": {"code": "session_not_found", "message": "Sign in required"}}
    )
    with pytest.raises(AuthenticationError) as error:
        await invoke(client.envs.list)
    assert error.value.code == "session_not_found"
    assert len(server.requests) == 1
    server.hook = lambda request: httpx.Response(
        422, json={"detail": [{"input": "PRIVATE_PAYLOAD"}]}
    )
    with pytest.raises(APIError) as error:
        await invoke(client.envs.list)
    assert "PRIVATE_PAYLOAD" not in str(error.value)
    assert error.value.code == "http_error"


@pytest.mark.parametrize(
    "content",
    [b"not JSON", b"[]", b'{"wrong": "shape"}'],
)
async def test_invalid_success_responses_are_protocol_errors(setup: Any, content: bytes) -> None:
    client, server = setup
    server.hook = lambda request: httpx.Response(200, content=content)
    with pytest.raises(ProtocolError):
        await invoke(client.envs.list)


async def test_locked_runtime_response_preserves_prerequisites(setup: Any) -> None:
    client, server = setup
    task = await invoke(client.tasks.get, "task_chat")
    server.hook = lambda request: httpx.Response(
        200, json={"status": "locked", "missing_prerequisites": [{"ctf_ref": "first"}]}
    )
    result = await invoke(task.state)
    assert result.status == "locked"
    assert result.missing_prerequisites == [{"ctf_ref": "first"}]


async def test_redirect_never_receives_learner_token(setup: Any) -> None:
    client, server = setup
    server.hook = lambda request: httpx.Response(
        307, headers={"Location": "https://other.example/"}
    )
    with pytest.raises(APIError) as error:
        await invoke(client.envs.list)
    assert error.value.status_code == 307
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "identifier", ["../other", "/absolute", "x/y", "x?secret=1", "%2Fescape", ""]
)
async def test_identifiers_cannot_escape_api_routes(setup: Any, identifier: str) -> None:
    client, server = setup
    for resource in (client.tasks, client.envs):
        with pytest.raises(ConfigurationError):
            await invoke(resource.get, identifier)
    assert server.requests == []


async def test_documentation_identity_mismatch_is_rejected(setup: Any) -> None:
    client, server = setup
    server.docs["task_chat"]["task_id"] = "unexpected_task"
    with pytest.raises(ProtocolError, match="different task"):
        await invoke(client.tasks.get, "task_chat")


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
@pytest.mark.parametrize(
    "options",
    [
        {"token": ""},
        {"token": " secret"},
        {"token": "secret\n"},
        {"base_url": "http://remote.example"},
        {"base_url": "https://user:pass@example.com"},
        {"base_url": "https://example.com/path"},
        {"base_url": "https://example.com?query=1"},
        {"base_url": "https://example.com#fragment"},
        {"base_url": "https://[invalid"},
        {"timeout": 0},
        {"timeout": float("nan")},
        {"retry_backoff": -1},
        {"max_retries": -1},
        {"max_retries": True},
    ],
)
def test_invalid_configuration(client_type: Any, options: Any) -> None:
    with pytest.raises(ConfigurationError):
        client_type(**{"token": "test-token", **options})


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000/api/agent-env/",
        "https://example.com/api/agent-env",
    ],
)
async def test_configuration_from_env_and_no_retry_mode(
    client_type: Any, base_url: str, monkeypatch: Any
) -> None:
    monkeypatch.setenv("AI_SECURITY_SCHOOL_TOKEN", "env-token")
    monkeypatch.setenv("AI_SECURITY_SCHOOL_BASE_URL", base_url)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer env-token"
        assert request.url.path == "/api/agent-env/instances"
        return httpx.Response(503, json={"detail": "temporarily_unavailable"})

    client = client_type.from_env(max_retries=0, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(APIError):
            await invoke(client.envs.list)
        assert len(requests) == 1
    finally:
        await invoke(client.close)


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_context_manager_only_closes_transport(client_type: Any) -> None:
    requests = []
    client = client_type(
        "test-token", transport=httpx.MockTransport(lambda req: requests.append(req))
    )
    if isinstance(client, Client):
        with client as entered:
            assert entered is client
    else:
        async with client as entered:
            assert entered is client
    assert client._http.is_closed
    assert requests == []


def test_backoff_caps_untrusted_retry_after() -> None:
    assert retry_delay(httpx.Response(429, headers={"Retry-After": "999999999"}), 0, 0.25) == 30
    assert retry_delay(httpx.Response(429, headers={"Retry-After": "0"}), 0, 0.25) == 0
    assert retry_delay(httpx.Response(429, headers={"Retry-After": "nan"}), 1, 0.25) == 0.5
    assert retry_delay(None, 10000, 0.25) == 30


def test_installed_package_version_matches_public_version() -> None:
    assert version("ai-security-school-sdk") == __version__ == "0.3.0"


async def test_async_cancellation_does_not_resend_a_mutation() -> None:
    import asyncio

    server = AgentEnvServer()
    mutation_started = asyncio.Event()
    never_finishes = asyncio.Event()
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "POST":
            post_count += 1
            # The server may apply a change before the caller stops waiting.
            server.messages.append("executed")
            mutation_started.set()
            await never_finishes.wait()
        return server.handle(request)

    async with AsyncClient("test-token", transport=httpx.MockTransport(handler)) as client:
        task = await client.tasks.get("task_chat")
        pending = asyncio.create_task(task.act({"message": "executed"}))
        await asyncio.wait_for(mutation_started.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert post_count == 1
        assert (await task.state()).state == {"messages": ["executed"]}
