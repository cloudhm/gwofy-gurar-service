"""Cognito Hosted UI redirect: exchange authorization code for tokens at /auth/callback."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


def _normalize_host(domain: str) -> str:
    d = (domain or "").strip()
    if d.startswith("https://"):
        d = d[8:]
    if d.startswith("http://"):
        d = d[7:]
    return d.rstrip("/")


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def handler(event, context):
    headers_in = event.get("headers") or {}
    accept = (headers_in.get("accept") or headers_in.get("Accept") or "").lower()

    qs = event.get("queryStringParameters") or {}
    err = qs.get("error")
    if err:
        desc = qs.get("error_description") or ""
        return _resp(400, "text/html", _html_error(f"Cognito error: {err}", desc))

    code = (qs.get("code") or "").strip()
    if not code:
        return _resp(
            400,
            "text/html",
            _html_error("Missing code", "Start login from Cognito /oauth2/authorize with this redirect_uri."),
        )

    domain = _normalize_host(os.environ.get("COGNITO_HOSTED_UI_DOMAIN", ""))
    client_id = (os.environ.get("COGNITO_CLIENT_ID") or "").strip()
    redirect_uri = (os.environ.get("COGNITO_REDIRECT_URI") or "").strip()

    if not domain or not client_id or not redirect_uri:
        return _resp(
            503,
            "text/html",
            _html_error(
                "Callback not fully configured",
                "Deploy with WEBHOOK_BASE_URL (becomes redirect URI {base}/auth/callback) and "
                "COGNITO_HOSTED_UI_DOMAIN (e.g. myprefix.auth.ap-east-1.amazoncognito.com). "
                "Redirect URI must match the Cognito app client exactly.",
            ),
        )

    token_url = f"https://{domain}/oauth2/token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        token_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return _resp(400, "text/html", _html_error("Token exchange failed", detail[:4000]))
    except Exception as e:
        return _resp(500, "text/html", _html_error("Token request failed", str(e)))

    id_token = data.get("id_token") or ""
    access_token = data.get("access_token") or ""
    refresh_token = data.get("refresh_token") or ""

    if "application/json" in accept:
        out = {
            "token_type": data.get("token_type"),
            "expires_in": data.get("expires_in"),
            "id_token": id_token,
            "access_token": access_token,
        }
        if refresh_token:
            out["refresh_token"] = refresh_token
        return _resp(200, "application/json", json.dumps(out))

    return _resp(200, "text/html", _html_success(id_token, access_token))


def _html_error(title: str, detail: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{_esc(title)}</title></head>
<body><h1>{_esc(title)}</h1><pre>{_esc(detail)}</pre></body></html>"""


def _html_success(id_token: str, access_token: str) -> str:
    # Id token is what API Gateway /admin expects in Authorization: Bearer
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Signed in</title></head>
<body>
<h1>Cognito login successful</h1>
<p>Copy the <strong>Id token</strong> below for <code>Authorization: Bearer …</code> when calling <code>/admin/…</code>.</p>
<h2>Id token</h2>
<textarea readonly rows="8" cols="100" style="width:100%;max-width:900px">{_esc(id_token)}</textarea>
<h2>Access token</h2>
<textarea readonly rows="6" cols="100" style="width:100%;max-width:900px">{_esc(access_token)}</textarea>
<p style="color:#666">Treat tokens as secrets; do not share or log in production.</p>
</body></html>"""


def _resp(status: int, content_type: str, body: str):
    return {
        "statusCode": status,
        "headers": {"Content-Type": f"{content_type}; charset=utf-8"},
        "body": body,
    }
