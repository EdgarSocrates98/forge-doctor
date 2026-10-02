"""Minimal JSON-Schema validator for Forge contract verification.

Covers the subset used by ``core/schemas.py``: ``type``, ``required``,
``properties``, ``items``, ``enum``, ``const``, ``oneOf``,
``additionalProperties`` (bool), and ``pattern``. Deliberately small —
``contracts verify`` is a gate for handoff bundles, not a general JSON
Schema implementation.
"""

from __future__ import annotations

import re
from typing import Any

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a sorted list of violations; empty means valid."""
    errors: list[str] = []

    if "oneOf" in schema:
        branches = schema["oneOf"]
        if not any(not validate(instance, branch, path) for branch in branches):
            errors.append(f"{path}: matches no oneOf branch")
        return errors

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, t) for t in allowed):
            errors.append(f"{path}: expected type {'|'.join(allowed)}, got {_type_name(instance)}")
            return errors  # further checks would be noise

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: {instance!r} != const {schema['const']!r}")
    if (
        "pattern" in schema
        and isinstance(instance, str)
        and not re.search(schema["pattern"], instance)
    ):
        errors.append(f"{path}: {instance!r} fails pattern {schema['pattern']!r}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required key {key!r}")
        props = schema.get("properties", {})
        for key, subschema in props.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema, f"{path}.{key}"))
        additional = schema.get("additionalProperties")
        extras = set(instance) - set(props)
        if additional is False:
            for key in sorted(extras):
                errors.append(f"{path}: unexpected key {key!r}")
        elif isinstance(additional, dict):
            for key in sorted(extras):
                errors.extend(validate(instance[key], additional, f"{path}.{key}"))

    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{path}[{i}]"))

    return errors


def _type_ok(instance: Any, t: str) -> bool:
    py = _TYPES.get(t)
    if py is None:
        return True  # unknown type keyword - don't gate on it
    if t == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    return isinstance(instance, py)


def _type_name(instance: Any) -> str:
    return type(instance).__name__


def verify_contract(instance: Any, name: str) -> list[str]:
    """Validate ``instance`` against a registered contract by name."""
    from forge_doctor.core.schemas import SCHEMAS

    schema = SCHEMAS.get(name)
    if schema is None:
        return [f"unknown contract {name!r} (valid: {', '.join(sorted(SCHEMAS))})"]
    return validate(instance, schema)
