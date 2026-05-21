"""Theme sync SQS enqueue."""

import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-east-1")
os.environ.setdefault("WORK_QUEUE_URL", "https://sqs.example.com/queue")

from unittest.mock import patch

from worker_handler import _enqueue_theme_sync


def test_enqueue_theme_sync_sends_oauth_event():
    with patch("worker_handler.sqs") as sqs_mock:
        _enqueue_theme_sync("a.myshopify.com", "1001", "2026-04")
    sqs_mock.send_message.assert_called_once()
    body = json.loads(sqs_mock.send_message.call_args.kwargs["MessageBody"])
    assert body == {
        "source": "oauth",
        "event": "THEME_SYNC",
        "shop": "a.myshopify.com",
        "store_number": "1001",
        "api_version": "2026-04",
    }
