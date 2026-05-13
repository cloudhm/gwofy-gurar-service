import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")

from webhook_handler import _utf8_body_for_log


def test_utf8_body_for_log_short_body():
    text, truncated = _utf8_body_for_log(b'{"id":1}')
    assert text == '{"id":1}'
    assert truncated is False


def test_utf8_body_for_log_truncates_long_body():
    raw = b"x" * 250_000
    text, truncated = _utf8_body_for_log(raw)
    assert truncated is True
    assert len(text) == 200_000


def test_utf8_body_for_log_invalid_utf8():
    text, truncated = _utf8_body_for_log(b"\xff\xfe")
    assert truncated is False
    assert len(text) == 2
    assert text == "\ufffd\ufffd"
