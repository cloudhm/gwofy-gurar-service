"""KMS encrypt/decrypt for Shopify offline access tokens."""

from __future__ import annotations

import base64

import boto3

_CTX_ACCESS = {"purpose": "shopify_offline_token"}
_CTX_REFRESH = {"purpose": "shopify_offline_refresh_token"}


def encrypt_token(key_id: str, plaintext: str) -> str:
    kms = boto3.client("kms")
    resp = kms.encrypt(
        KeyId=key_id,
        Plaintext=plaintext.encode("utf-8"),
        EncryptionContext=_CTX_ACCESS,
    )
    return base64.b64encode(resp["CiphertextBlob"]).decode("ascii")


def decrypt_token(key_id: str, ciphertext_b64: str) -> str:
    kms = boto3.client("kms")
    blob = base64.b64decode(ciphertext_b64.encode("ascii"))
    resp = kms.decrypt(
        CiphertextBlob=blob,
        EncryptionContext=_CTX_ACCESS,
    )
    return resp["Plaintext"].decode("utf-8")


def encrypt_refresh_token(key_id: str, plaintext: str) -> str:
    kms = boto3.client("kms")
    resp = kms.encrypt(
        KeyId=key_id,
        Plaintext=plaintext.encode("utf-8"),
        EncryptionContext=_CTX_REFRESH,
    )
    return base64.b64encode(resp["CiphertextBlob"]).decode("ascii")


def decrypt_refresh_token(key_id: str, ciphertext_b64: str) -> str:
    kms = boto3.client("kms")
    blob = base64.b64decode(ciphertext_b64.encode("ascii"))
    resp = kms.decrypt(
        CiphertextBlob=blob,
        EncryptionContext=_CTX_REFRESH,
    )
    return resp["Plaintext"].decode("utf-8")
