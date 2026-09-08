"""Local schema validation never resolves or downloads remote references."""

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

from .errors import ActionValidationError, ProtocolError
from .models import JsonObject, TaskDocumentation


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


def json_object(value: JsonObject) -> JsonObject:
    if not isinstance(value, dict):
        raise ActionValidationError("Action payload must be a JSON object")
    try:
        frozen: JsonObject = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ActionValidationError("Action payload must contain only finite JSON values") from exc
    return frozen


def validate_payload(
    schema: JsonObject, payload: JsonObject, *, name: str = "action"
) -> JsonObject:
    frozen = json_object(payload)
    _check_references(schema)
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=Registry())
        violation = next(validator.iter_errors(frozen), None)
    except SchemaError as exc:
        raise ProtocolError("Server returned an invalid action input schema") from exc
    except Exception as exc:
        raise ProtocolError("Action input schema contains an unresolvable reference") from exc
    if violation is not None:
        path = list(violation.absolute_path)
        location = ".".join(str(part) for part in path) or "payload"
        raise ActionValidationError(
            f"{name}: {location} fails {violation.validator} validation", path=path
        )
    return frozen


def action_payload(
    documentation: TaskDocumentation, name: str, arguments: JsonObject
) -> JsonObject:
    descriptor = next((action for action in documentation.actions if action.name == name), None)
    if descriptor is None:
        raise ActionValidationError("This action is not documented for the task")
    arguments = json_object(arguments)
    if "action" in arguments:
        raise ActionValidationError("Pass the action name separately, without arguments.action")
    validated = validate_payload(descriptor.input_schema, arguments, name=name)
    return validate_payload(
        documentation.action_payload_schema, {"action": name, **validated}, name=name
    )
