"""Scheduled reconciliation — enqueue GraphQL refresh per ACTIVE shop (§1g)."""

from __future__ import annotations

import json
import os

import boto3

from boto3.dynamodb.conditions import Attr

from lib.logging_json import setup_logging
from lib.models import SK_METADATA

logger = setup_logging("reconcile")

ddb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")


def handler(event, context):
    table_name = os.environ["TABLE_NAME"]
    queue_url = os.environ["WORK_QUEUE_URL"]
    table = ddb.Table(table_name)

    scan_kwargs = {
        "FilterExpression": Attr("sk").eq(SK_METADATA)
        & Attr("pk").begins_with("SHOP#")
        & Attr("installation_status").eq("ACTIVE"),
    }
    sent = 0
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            shop = item.get("shop")
            sn = item.get("store_number")
            if not shop or not sn:
                continue
            body = {
                "source": "reconcile",
                "shop": shop,
                "store_number": str(sn),
                "resource": "ALL",
            }
            sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))
            sent += 1
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        scan_kwargs["ExclusiveStartKey"] = lek

    logger.info("reconcile_enqueued", extra={"count": sent})
    return {"statusCode": 200, "body": json.dumps({"enqueued": sent})}
