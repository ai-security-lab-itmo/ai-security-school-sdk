"""SDK failures omit authentication headers and locally supplied payloads."""

from typing import Any


class SDKError(Exception):
    """Base class for SDK failures."""


class ConfigurationError(SDKError, ValueError):
    pass


class ProtocolError(SDKError):
    """The server returned an invalid response or unsupported JSON schema."""


class ActionValidationError(SDKError, ValueError):
    """Arguments do not satisfy the task's documented action schema."""

    def __init__(self, message: str, *, path: list[str | int] | None = None) -> None:
        super().__init__(message)
        self.path = path or []


class APIError(SDKError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        usage = self.details.get("usage")
        self.usage: dict[str, Any] | None = usage if isinstance(usage, dict) else None


class AuthenticationError(APIError):
    pass


class PermissionDeniedError(APIError):
    pass


class NotFoundError(APIError):
    pass


class ConflictError(APIError):
    pass


class LimitExceededError(APIError):
    pass


class TransportError(SDKError):
    """No definitive response was received; a mutation may already have executed."""

    def __init__(self, *, may_have_executed: bool) -> None:
        message = "Unable to obtain a definitive server response."
        if may_have_executed:
            message += " The action may have executed; inspect task.state() before retrying."
        super().__init__(message)
        self.may_have_executed = may_have_executed


def api_error(
    code: str,
    message: str,
    *,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> APIError:
    cls: type[APIError] = {
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        409: ConflictError,
        429: LimitExceededError,
    }.get(status_code, APIError)
    return cls(code, message, status_code=status_code, details=details)
