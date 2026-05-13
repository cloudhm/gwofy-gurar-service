"""Structured CloudWatch-friendly JSON logs; ``extra=`` fields are merged into the line."""

import json
import logging
import sys

# Keys on LogRecord that are not from logger.*(..., extra={...}).
_LOGRECORD_BUILTIN_KEYS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())
# Top-level keys reserved for the JSON line shape.
_JSON_PAYLOAD_RESERVED = frozenset({"level", "msg", "logger", "exc_info"})


def _json_safe_value(value: object) -> object:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_BUILTIN_KEYS or key in _JSON_PAYLOAD_RESERVED:
                continue
            if key.startswith("_"):
                continue
            payload[key] = _json_safe_value(value)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(name: str = "gwofy") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    logger.addHandler(h)
    return logger
