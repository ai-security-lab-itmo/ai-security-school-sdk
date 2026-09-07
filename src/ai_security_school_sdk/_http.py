"""Shared transport policy and envelope parsing for both client variants."""

import math
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .errors import ConfigurationError, ProtocolError, api_error
from .models import APIErrorDetail, JsonObject, PublicModel

DEFAULT_BASE_URL = "https://plgn.aisecschool.ru"
API_PREFIX = "/api/learner/v1/"
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
    url = urlsplit(base_url)
    if (
        url.scheme not in {"https", "http"}
        or not url.netloc
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path.rstrip("/") not in {"", API_PREFIX.rstrip("/")}
    ):
        raise ConfigurationError("base_url must be a server origin or learner v1 API base URL")
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
            "User-Agent": "ai-security-school-sdk/0.1.0",
        },
        "timeout": timeout,
        "follow_redirects": False,
    }


def request_headers(method: str, idempotency_key: str | None) -> dict[str, str]:
    if method == "GET":
        return {}
    key = idempotency_key if idempotency_key is not None else str(uuid4())
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= 200
        or any(ord(c) < 33 or ord(c) > 126 for c in key)
    ):
        raise ConfigurationError("idempotency_key must be 1–200 printable ASCII characters")
    return {"Idempotency-Key": key}


def retry_delay(response: httpx.Response | None, attempt: int, backoff: float) -> float:
    if response is not None:
        try:
            delay = float(response.headers.get("Retry-After", ""))
            if math.isfinite(delay) and delay >= 0:
                return min(delay, 30.0)
        except ValueError:
            pass
    return min(math.ldexp(backoff, min(attempt, 10)), 30.0)


def remaining_timeout(timeout: float, deadline: float | None) -> float:
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Request polling deadline reached")
    return min(timeout, remaining)


def decode_response(response: httpx.Response) -> JsonObject:
    if not 200 <= response.status_code < 300:
        try:
            error = APIErrorDetail.model_validate(response.json()["error"])
        except (ValueError, TypeError, KeyError):
            raise api_error(
                "http_error",
                f"Server returned HTTP {response.status_code}",
                status_code=response.status_code,
            ) from None
        raise api_error(
            error.code, error.message, status_code=response.status_code, details=error.details
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
        # Do not print bodies which may include learner payloads or private data.
        raise ProtocolError(f"Server returned an invalid {model.__name__} envelope") from exc
