"""Local demo receiver: fail twice per event ID, then accept subsequent requests."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

MAX_BYTES = 65536
FAILURES_BEFORE_SUCCESS = int(os.getenv("DEMO_FAILURES_BEFORE_SUCCESS", "2"))
attempts: dict[str, int] = {}
attempts_lock = threading.Lock()


class ReceiverHandler(BaseHTTPRequestHandler):
    def respond(self, status: int, body: dict) -> None:
        content = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path == "/health/":
            self.respond(200, {"status": "ok"})
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/webhook/":
            self.respond(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(400, {"error": "Invalid content length"})
            return
        if not 0 < length <= MAX_BYTES:
            self.respond(413, {"error": "Request body exceeds the size limit"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            event_id = str(UUID(body["id"]))
            if self.headers.get("X-HookCourier-Event-ID") != event_id:
                raise ValueError("Event ID header does not match the body")
            if not isinstance(body["payload"], dict) or not isinstance(
                body["event_type"], str
            ):
                raise ValueError("Invalid event body")
        except (ValueError, KeyError, TypeError, AttributeError):
            self.respond(400, {"error": "Invalid webhook event"})
            return

        with attempts_lock:
            number = attempts.get(event_id, 0) + 1
            attempts[event_id] = number
        status = 503 if number <= FAILURES_BEFORE_SUCCESS else 200
        print(f"event_id={event_id} attempt={number} status={status}", flush=True)
        self.respond(status, {"event_id": event_id, "attempt": number})

    def log_message(self, format, *args) -> None:
        pass


if __name__ == "__main__":
    print("Demo receiver listening on port 9000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 9000), ReceiverHandler).serve_forever()
