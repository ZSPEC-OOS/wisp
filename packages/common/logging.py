import contextvars
import json
import logging
from datetime import datetime, timezone

# Set by RequestIDMiddleware for each inbound request; propagates to all log lines.
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = _request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if hasattr(record, "extra"):
            payload["extra"] = getattr(record, "extra")
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
