import json


def encode_webhook_body(*, event_id: str, event_type: str, payload: dict) -> bytes:
    return json.dumps(
        {"id": event_id, "event_type": event_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
