import json


def validate_json_payload(payload: dict) -> None:
    """Reject strings PostgreSQL JSONB cannot represent, including nested object keys."""
    remaining = [payload]
    while remaining:
        value = remaining.pop()
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("JSON must not contain null characters")
            value.encode("utf-8")
        elif isinstance(value, dict):
            remaining.extend(value.keys())
            remaining.extend(value.values())
        elif isinstance(value, list):
            remaining.extend(value)


def encode_webhook_body(*, event_id: str, event_type: str, payload: dict) -> bytes:
    validate_json_payload(payload)
    return json.dumps(
        {"id": event_id, "event_type": event_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
