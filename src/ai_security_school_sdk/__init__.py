"""AI Security School SDK: documentation and actions for existing agent environments."""

from importlib.metadata import version as _package_version

from .async_client import AsyncClient
from .client import Client
from .errors import (
    ActionValidationError,
    APIError,
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    LimitExceededError,
    NotFoundError,
    PermissionDeniedError,
    ProtocolError,
    SDKError,
    TransportError,
)
from .models import (
    ActionDescriptor,
    InstanceDocumentation,
    RuntimeResponse,
    TaskDocumentation,
    TaskSummary,
)

__version__ = _package_version("ai-security-school-sdk")

__all__ = [
    "APIError",
    "ActionDescriptor",
    "ActionValidationError",
    "AsyncClient",
    "AuthenticationError",
    "Client",
    "ConfigurationError",
    "ConflictError",
    "InstanceDocumentation",
    "LimitExceededError",
    "NotFoundError",
    "PermissionDeniedError",
    "ProtocolError",
    "RuntimeResponse",
    "SDKError",
    "TaskDocumentation",
    "TaskSummary",
    "TransportError",
]
