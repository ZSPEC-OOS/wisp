import contextvars
import json
import logging
from datetime import datetime, timezone

# Set by RequestIDMiddleware for each inbound request; propagates to all log lines.
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# Built-in LogRecord attributes that should not be treated as user-supplied extras
_BUILTIN_ATTRS = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs",
    "msg", "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName", "taskName",
})


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
        # Collect all non-standard LogRecord attributes as structured extras.
        # Python's logging merges extra={...} kwargs directly onto the LogRecord
        # as top-level attributes, not under a single "extra" key.
        extras = {k: v for k, v in record.__dict__.items() if k not in _BUILTIN_ATTRS}
        if extras:
            payload["extra"] = extras
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
