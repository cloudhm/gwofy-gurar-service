"""Allocate globally unique 10-digit store_number."""

from __future__ import annotations

import secrets

import boto3
from botocore.exceptions import ClientError

from .models import SK_METADATA, pk_shop, pk_store_number


def random_ten_digit() -> str:
    return str(secrets.randbelow(9_000_000_000) + 1_000_000_000)


def allocate_store_number(table_name: str, shop: str, max_attempts: int = 64) -> str:
    """Return existing store_number for shop or allocate new unique number."""
    ddb = boto3.resource("dynamodb").Table(table_name)
    pk = pk_shop(shop)
    existing = ddb.get_item(Key={"pk": pk, "sk": SK_METADATA}).get("Item")
    if existing and existing.get("store_number"):
        return str(existing["store_number"])

    for _ in range(max_attempts):
        num = random_ten_digit()
        try:
            ddb.put_item(
                Item={"pk": pk_store_number(num), "sk": SK_METADATA, "shop": shop},
                ConditionExpression="attribute_not_exists(pk)",
            )
            return num
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code != "ConditionalCheckFailedException":
                raise
    raise RuntimeError("Failed to allocate unique store_number")
