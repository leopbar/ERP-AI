"""Catalog field schemas drive two things: the strict output schema offered to the model and the
deterministic validation of what it answered. The model-facing form is a lossless transformation of
the catalog schema that OpenAI strict mode accepts; `from_model_value` undoes it."""
import copy
from jsonschema import Draft202012Validator

UNSUPPORTED = {"pattern", "format", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
               "minItems", "maxItems", "uniqueItems", "default", "title", "description", "examples", "$comment"}

def is_map(schema):
    """An object whose keys are data (e.g. group name → steps), not a fixed property set."""
    return isinstance(schema, dict) and schema.get("type") == "object" and not schema.get("properties") and isinstance(schema.get("additionalProperties"), dict)

def to_model_schema(schema):
    """Strict-mode form: every object closed with all properties required, maps become key/value lists,
    optional properties become nullable, numeric and string constraints removed (validated afterwards)."""
    if isinstance(schema, list):
        return [to_model_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if is_map(schema):
        return {"type": "array", "items": {"type": "object", "properties": {"key": {"type": "string"}, "value": to_model_schema(schema["additionalProperties"])},
                                           "required": ["key", "value"], "additionalProperties": False}}
    result = {}
    for key, value in schema.items():
        if key in UNSUPPORTED:
            continue
        if key == "properties":
            required = set(schema.get("required", []))
            result["properties"] = {}
            for name, sub in value.items():
                converted = to_model_schema(sub)
                result["properties"][name] = converted if name in required else nullable(converted)
            result["required"] = list(value)
            result["additionalProperties"] = False
        elif key in ("required", "additionalProperties") and "properties" in schema:
            continue
        elif key in ("items", "anyOf", "oneOf", "allOf"):
            result[key] = to_model_schema(value)
        else:
            result[key] = copy.deepcopy(value)
    if result.get("type") == "object" and "properties" not in result:
        result.setdefault("properties", {}); result["required"] = []; result["additionalProperties"] = False
    return result

def nullable(schema):
    if isinstance(schema.get("type"), list):
        return {**schema, "type": [*schema["type"], "null"]} if "null" not in schema["type"] else schema
    if isinstance(schema.get("type"), str) and len(schema) == 1:
        return {"type": [schema["type"], "null"]}
    return {"anyOf": [schema, {"type": "null"}]}

def from_model_value(value, schema):
    """Undo `to_model_schema`: key/value lists back to maps, nulls dropped from optional properties."""
    if not isinstance(schema, dict):
        return value
    if is_map(schema):
        if isinstance(value, list):
            return {str(item.get("key")): from_model_value(item.get("value"), schema["additionalProperties"]) for item in value if isinstance(item, dict)}
        return value
    if schema.get("type") == "object" and isinstance(value, dict) and "properties" in schema:
        required = set(schema.get("required", []))
        result = {}
        for name, sub in schema["properties"].items():
            if name not in value:
                continue
            if value[name] is None and name not in required:
                continue
            result[name] = from_model_value(value[name], sub)
        return result
    if schema.get("type") == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [from_model_value(item, schema["items"]) for item in value]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            for option in schema[key]:
                if not Draft202012Validator(to_model_schema(option)).is_valid(value):
                    continue
                return from_model_value(value, option)
    return value

def value_errors(value, schema, label=""):
    """Human-readable validation errors of a value against a catalog field schema."""
    errors = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)):
        where = "/".join(str(part) for part in error.path)
        errors.append((f"{label}: " if label else "") + (f"{where}: " if where else "") + error.message)
    return errors
