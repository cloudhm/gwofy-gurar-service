import json
import logging

from lib.logging_json import JsonFormatter


def test_json_formatter_includes_extra_fields():
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname="x.py",
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.shop = "a.myshopify.com"
    record.payload_len = 42
    line = fmt.format(record)
    data = json.loads(line)
    assert data["msg"] == "hello"
    assert data["level"] == "WARNING"
    assert data["logger"] == "test"
    assert data["shop"] == "a.myshopify.com"
    assert data["payload_len"] == 42


def test_json_formatter_coerces_non_serializable_extra():
    fmt = JsonFormatter()

    class Opaque:
        def __str__(self):
            return "opaque"

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="m",
        args=(),
        exc_info=None,
    )
    record.thing = Opaque()
    data = json.loads(fmt.format(record))
    assert data["thing"] == "opaque"
