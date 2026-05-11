import hashlib
import hmac

from lib.shopify_api import verify_oauth_hmac, verify_webhook_hmac


def test_verify_webhook_hmac_accepts_valid_signature():
    secret = "test_secret"
    body = b'{"id":1}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_hmac(body, digest, secret) is True


def test_verify_oauth_hmac_sorts_params():
    secret = "shp_sec"
    qs = {"shop": "a.myshopify.com", "code": "abc", "hmac": "", "timestamp": "1"}
    msg_parts = []
    for k in sorted(q for q in qs if q != "hmac"):
        msg_parts.append(f"{k}={qs[k]}")
    message = "&".join(msg_parts).encode()
    qs["hmac"] = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    assert verify_oauth_hmac(qs, secret) is True
