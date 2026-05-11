"""CDK Custom Resource Provider on_event: ensure Cognito User Pool app client by name."""

from __future__ import annotations

import logging
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_CLIENT_NAME = "GWO-SHIPPING-PROTECTION"


def _find_client_id(cognito, pool_id: str, client_name: str) -> str | None:
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"UserPoolId": pool_id, "MaxResults": 60}
        if token:
            kwargs["NextToken"] = token
        resp = cognito.list_user_pool_clients(**kwargs)
        for c in resp.get("UserPoolClients", []) or []:
            if c.get("ClientName") == client_name:
                return str(c.get("ClientId") or "")
        token = resp.get("NextToken")
        if not token:
            break
    return None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    props = event.get("ResourceProperties") or {}
    pool_id = str(props.get("UserPoolId") or "").strip()
    region = str(props.get("Region") or "").strip()
    client_name = str(props.get("ClientName") or DEFAULT_CLIENT_NAME).strip()
    req = str(event.get("RequestType") or "Create")

    if not pool_id or not region:
        raise ValueError("UserPoolId and Region are required")

    physical_existing = str(event.get("PhysicalResourceId") or "")

    if req == "Delete":
        return {"PhysicalResourceId": physical_existing or "cognito-admin-client-deleted"}

    cognito = boto3.client("cognito-idp", region_name=region)
    cid = _find_client_id(cognito, pool_id, client_name)
    if cid:
        logger.info("cognito_app_client_found name=%s id=%s", client_name, cid)
    else:
        resp = cognito.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=client_name,
            GenerateSecret=False,
            ExplicitAuthFlows=[
                "ALLOW_USER_PASSWORD_AUTH",
                "ALLOW_USER_SRP_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
            ],
            PreventUserExistenceErrors="ENABLED",
        )
        cid = str(resp["UserPoolClient"]["ClientId"])
        logger.info("cognito_app_client_created name=%s id=%s", client_name, cid)

    physical = f"{pool_id}:{client_name}:{cid}"
    return {"PhysicalResourceId": physical, "Data": {"ClientId": cid}}
