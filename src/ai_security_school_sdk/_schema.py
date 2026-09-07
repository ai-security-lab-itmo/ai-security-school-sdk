"""Local schema checks never resolve or download remote references."""

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

from .errors import ActionValidationError, ProtocolError
from .models import ActionDescriptor, JsonObject


def _check_references(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                raise ProtocolError("Action schemas may only use local fragment references")
            _check_references(child)
    elif isinstance(value, list):
        for child in value:
            _check_references(child)


def validate_arguments(action: ActionDescriptor, arguments: JsonObject) -> JsonObject:
    """Freeze a JSON copy, so caller mutations cannot change a retried request."""
    if not isinstance(arguments, dict):
        raise ActionValidationError("Action arguments must be a JSON object")
    try:
        frozen: JsonObject = json.loads(json.dumps(arguments, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionValidationError(
            "Action arguments must contain only finite JSON values"
        ) from exc
    schema = action.input_schema
    _check_references(schema)
    try:
        Draft202012Validator.check_schema(schema)
        # An explicit empty Registry has no remote retriever. Do not use
        # jsonschema's default registry, which may attempt network retrieval.
        validator = Draft202012Validator(schema, registry=Registry())
        violation = next(validator.iter_errors(frozen), None)
    except SchemaError as exc:
        raise ProtocolError("Server returned an invalid action input schema") from exc
    except Exception as exc:
        raise ProtocolError("Action input schema contains an unresolvable reference") from exc
    if violation is not None:
        path = list(violation.absolute_path)
        location = ".".join(str(part) for part in path) or "arguments"
        # jsonschema's full messages may contain passwords or entire payloads.
        raise ActionValidationError(
            f"{action.name}: {location} fails {violation.validator} validation", path=path
        )
    return frozen
