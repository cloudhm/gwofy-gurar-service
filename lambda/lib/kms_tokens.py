"""KMS encrypt/decrypt for Shopify offline access tokens."""

from __future__ import annotations

import base64
from typing import Any

import boto3


def encrypt_token(key_id: str, plaintext: str) -> str:
    kms = boto3.client("kms")
    resp = kms.encrypt(
        KeyId=key_id,
        Plaintext=plaintext.encode("utf-8"),
        EncryptionContext={"purpose": "shopify_offline_token"},
    )
    return base64.b64encode(resp["CiphertextBlob"]).decode("ascii")


def decrypt_token(key_id: str, ciphertext_b64: str) -> str:
    kms = boto3.client("kms")
    blob = base64.b64decode(ciphertext_b64.encode("ascii"))
    resp = kms.decrypt(
        CiphertextBlob=blob,
        EncryptionContext={"purpose": "shopify_offline_token"},
    )
    return resp["Plaintext"].decode("utf-8")
