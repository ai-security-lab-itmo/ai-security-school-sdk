"""Errors never include authentication headers or request bodies."""

from typing import Any


class SDKError(Exception):
    """Base class for SDK failures."""


class ConfigurationError(SDKError, ValueError):
    pass


class ProtocolError(SDKError):
    """The server returned an invalid v1 envelope or unsupported JSON schema."""


class ActionValidationError(SDKError, ValueError):
    """Arguments do not satisfy the current stage's public action schema."""

    def __init__(self, message: str, *, path: list[str | int] | None = None) -> None:
        super().__init__(message)
        self.path = path or []


class APIError(SDKError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.job_id = job_id


class AuthenticationError(APIError):
    pass


class PermissionDeniedError(APIError):
    pass


class NotFoundError(APIError):
    pass


class ConflictError(APIError):
    pass


class StageLockedError(ConflictError):
    pass


class LimitExceededError(APIError):
    pass


class JobCancelledError(APIError):
    pass


class JobInterruptedError(APIError):
    pass


class TransportError(SDKError):
    """No definitive response; retry a mutation with this same idempotency key."""

    def __init__(self, message: str, *, idempotency_key: str | None = None) -> None:
        super().__init__(message)
        self.idempotency_key = idempotency_key


class JobTimeoutError(SDKError, TimeoutError):
    """Polling stopped locally; the durable job remains available on the server."""

    def __init__(self, job_id: str, timeout: float) -> None:
        super().__init__(
            f"Job {job_id} did not finish within {timeout:g}s; "
            "resume with client.jobs.get(job_id).wait()."
        )
        self.job_id = job_id
        self.timeout = timeout


def api_error(
    code: str,
    message: str,
    *,
    status_code: int | None = None,
    details: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> APIError:
    cls: type[APIError] = APIError
    if code in {"stage_locked", "stage_not_passed", "prerequisite_not_met"}:
        cls = StageLockedError
    elif code in {"job_cancelled", "cancelled"}:
        cls = JobCancelledError
    elif code in {"job_interrupted", "interrupted", "configuration_changed"}:
        cls = JobInterruptedError
    elif status_code == 401 or code in {"unauthorized", "invalid_token", "token_expired"}:
        cls = AuthenticationError
    elif status_code == 403 or code in {"forbidden", "action_not_allowed"}:
        cls = PermissionDeniedError
    elif status_code == 404:
        cls = NotFoundError
    elif status_code == 409 or code in {"run_busy", "task_changed", "idempotency_conflict"}:
        cls = ConflictError
    elif status_code == 429 or code in {"budget_exceeded", "limit_exceeded", "rate_limited"}:
        cls = LimitExceededError
    return cls(code, message, status_code=status_code, details=details, job_id=job_id)
