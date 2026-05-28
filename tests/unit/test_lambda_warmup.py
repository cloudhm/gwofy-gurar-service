from lib.lambda_warmup import WARMUP_EVENT_SOURCE, is_warmup_event


def test_is_warmup_event_custom_source():
    assert is_warmup_event({"source": WARMUP_EVENT_SOURCE}) is True


def test_is_warmup_event_aws_events_schedule():
    assert (
        is_warmup_event(
            {"source": "aws.events", "detail-type": "Scheduled Event", "detail": {}}
        )
        is True
    )


def test_is_warmup_event_normal_api_gateway():
    assert is_warmup_event({"httpMethod": "POST", "body": "{}"}) is False


def test_is_warmup_event_non_dict():
    assert is_warmup_event(None) is False
