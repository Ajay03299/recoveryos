"""Convert a Pydantic model into the OpenAPI subset Gemini accepts.

Gemini rejects $ref/$defs and lowercase type names, so we inline and uppercase.
"""
from typing import Any

from pydantic import BaseModel

_KEEP = {"type", "properties", "required", "items", "enum", "description"}
_TYPES = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER",
          "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT"}


def _clean(node: Any, defs: dict) -> Any:
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _clean(defs.get(name, {}), defs)

    # Enums arrive as anyOf/allOf wrappers in some Pydantic versions.
    for key in ("allOf", "anyOf"):
        if key in node and len(node[key]) == 1:
            merged = {**node, **_clean(node[key][0], defs)}
            merged.pop(key, None)
            return _clean(merged, defs)

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _KEEP:
            continue
        if key == "type":
            out[key] = _TYPES.get(value, "STRING")
        elif key == "properties":
            out[key] = {k: _clean(v, defs) for k, v in value.items()}
        elif key == "items":
            out[key] = _clean(value, defs)
        else:
            out[key] = value

    if out.get("enum") and "type" not in out:
        out["type"] = "STRING"
    return out


def to_gemini_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    return _clean(raw, raw.get("$defs", {}))
