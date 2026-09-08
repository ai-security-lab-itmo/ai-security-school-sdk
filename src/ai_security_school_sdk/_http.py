"""Transport policy and response parsing shared by synchronous and async clients."""

import math
import os
import re
from importlib.metadata import version
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .errors import ConfigurationError, ProtocolError, api_error
from .models import JsonObject, PublicModel

DEFAULT_BASE_URL = "https://plgn.aisecschool.ru"
API_PREFIX = "/api/agent-env/"
RETRYABLE_STATUS = {429, 502, 503, 504}


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ConfigurationError("Resource identifiers must contain only letters, digits, _ . : -")
    return value


def positive_duration(value: float, name: str, *, allow_zero: bool = False) -> float:
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ConfigurationError(
            f"{name} must be finite and {'non-negative' if allow_zero else 'positive'}"
        )
    return value


def environment_config(overrides: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "token": os.environ.get("AI_SECURITY_SCHOOL_TOKEN", ""),
        "base_url": os.environ.get("AI_SECURITY_SCHOOL_BASE_URL") or DEFAULT_BASE_URL,
    }
    options.update(overrides)
    return options


def client_options(
    token: str, base_url: str, timeout: float, max_retries: int, retry_backoff: float
) -> dict[str, Any]:
    if not token or token != token.strip() or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ConfigurationError("Set AI_SECURITY_SCHOOL_TOKEN to a valid learner token")
    try:
        url = urlsplit(base_url)
    except ValueError as exc:
        raise ConfigurationError("base_url must be a valid server URL") from exc
    if (
        url.scheme not in {"https", "http"}
        or not url.netloc
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path.rstrip("/") not in {"", API_PREFIX.rstrip("/")}
    ):
        raise ConfigurationError("base_url must be a server origin or agent-env API base URL")
    if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
        raise ConfigurationError(
            "Remote servers require HTTPS; HTTP is supported only for local use"
        )
    positive_duration(timeout, "timeout")
    positive_duration(retry_backoff, "retry_backoff", allow_zero=True)
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ConfigurationError("max_retries must be a non-negative integer")
    return {
        "base_url": f"{url.scheme}://{url.netloc}{API_PREFIX}",
        "headers": {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"ai-security-school-sdk/{version('ai-security-school-sdk')}",
        },
        "timeout": timeout,
        "follow_redirects": False,
    }


def retry_delay(response: httpx.Response | None, attempt: int, backoff: float) -> float:
    if response is not None:
        try:
            delay = float(response.headers.get("Retry-After", ""))
            if math.isfinite(delay) and delay >= 0:
                return min(delay, 30.0)
        except ValueError:
            pass
    return min(math.ldexp(backoff, min(attempt, 10)), 30.0)


def decode_response(response: httpx.Response) -> JsonObject:
    if not 200 <= response.status_code < 300:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        error = body.get("error")
        error = error if isinstance(error, dict) else {}
        code = error.get("code")
        message = error.get("message")
        details = error.get("details")
        details = dict(details) if isinstance(details, dict) else {}
        for key in ("usage", "retry_after"):
            if key in body:
                details[key] = body[key]
        raise api_error(
            code if isinstance(code, str) else "http_error",
            message if isinstance(message, str) else f"Server returned HTTP {response.status_code}",
            status_code=response.status_code,
            details=details,
        )
    try:
        value = response.json()
    except ValueError as exc:
        raise ProtocolError("Server returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Expected a JSON object response")
    return value


def parse_model[ModelT: PublicModel](model: type[ModelT], value: JsonObject) -> ModelT:
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise ProtocolError(f"Server returned an invalid {model.__name__} response") from exc
