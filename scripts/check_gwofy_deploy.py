#!/usr/bin/env python3
"""部署后检查：解析 sp-{stage}.gwofy.com 并验证 DNS + HTTPS 可访问。"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import urllib.error
import urllib.request

# 允许从仓库根目录运行：python scripts/check_gwofy_deploy.py
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from gwofy_guard_service.deploy_config import suggested_custom_fqdn  # noqa: E402


def _check_dns(host: str) -> None:
    socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)


def _check_https(url: str, timeout: float) -> int:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def main() -> int:
    p = argparse.ArgumentParser(description="Check DNS + HTTPS for Gwofy API custom domain")
    p.add_argument(
        "--stage",
        default=os.environ.get("CDK_STAGE", "dev"),
        help="与 CDK stage 一致（默认 CDK_STAGE 或 dev）",
    )
    p.add_argument(
        "--domain-base",
        default=os.environ.get("GWOFY_DOMAIN_BASE", "gwofy.com"),
        help="根域名，默认 gwofy.com",
    )
    p.add_argument(
        "--prefix",
        default=os.environ.get("GWOFY_SUBDOMAIN_PREFIX", "sp"),
        help="子域名前缀，默认 sp → sp-dev.gwofy.com",
    )
    p.add_argument(
        "--host",
        default=None,
        help="覆盖完整主机名（不拼 sp-{stage}）",
    )
    p.add_argument("--timeout", type=float, default=15.0, help="HTTP 超时秒数")
    args = p.parse_args()

    host = (args.host or "").strip() or suggested_custom_fqdn(
        stage=args.stage,
        domain_base=args.domain_base,
        subdomain_prefix=args.prefix,
    )
    base = f"https://{host}"

    print(f"Checking host={host!r} ...")
    try:
        _check_dns(host)
        print("  DNS: OK")
    except socket.gaierror as e:
        print(f"  DNS: FAILED ({e})", file=sys.stderr)
        return 1

    try:
        code = _check_https(base + "/", timeout=args.timeout)
    except OSError as e:
        print(f"  HTTPS: FAILED ({e})", file=sys.stderr)
        return 1

    # API Gateway 根路径常为 404，仍表示 TLS 与路由可达
    print(f"  HTTPS: OK (HTTP {code})")
    print(f"  OAuth callback: {base}/oauth/callback")
    print(f"  Cognito admin callback: {base}/auth/callback")
    print(f"  Shopify webhook: {base}/webhooks/shopify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
