"""Resolve public URL / custom domain (gwofy.com) and validate deploy-time config."""

from __future__ import annotations

import re
from dataclasses import dataclass


def normalize_https_base(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    u = u.rstrip("/")
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    return u


def suggested_custom_fqdn(
    *,
    stage: str,
    domain_base: str = "gwofy.com",
    subdomain_prefix: str = "sp",
) -> str:
    """Return e.g. sp-dev.gwofy.com for stage ``dev``."""
    base = (domain_base or "gwofy.com").strip().lower().strip(".")
    prefix = (subdomain_prefix or "sp").strip().lower()
    st = (stage or "dev").strip().lower()
    st = re.sub(r"[^a-z0-9-]", "", st)
    if not st:
        st = "dev"
    label = f"{prefix}-{st}"
    return f"{label}.{base}"


@dataclass(frozen=True)
class ResolvedDeployUrls:
    """Effective webhook base URL and optional API Gateway custom domain."""

    webhook_base_url: str
    custom_domain_fqdn: str | None
    certificate_arn: str | None
    """ACM cert ARN (same region as API). Covers *.gwofy.com or the concrete hostname."""


def resolve_deploy_urls(
    *,
    stage: str,
    certificate_arn: str | None,
    domain_base: str,
    subdomain_prefix: str,
    custom_domain_name: str | None,
    webhook_base_url_env: str | None,
    webhook_base_url_ctx: str | None,
    strict_deploy_config: bool,
) -> ResolvedDeployUrls:
    """
    When ``certificate_arn`` is set, CDK will create API Gateway custom domain for
    ``custom_domain_name`` or ``{prefix}-{stage}.{domain_base}``.

    Webhook/OAuth base URL priority: explicit env → CDK context → ``https://`` custom FQDN.
    """
    cert = (certificate_arn or "").strip() or None
    override = (custom_domain_name or "").strip().lower() or None

    fqdn: str | None = None
    if cert:
        fqdn = override or suggested_custom_fqdn(
            stage=stage,
            domain_base=domain_base,
            subdomain_prefix=subdomain_prefix,
        )

    default_https = f"https://{fqdn}" if fqdn else None

    explicit = normalize_https_base(webhook_base_url_env or "") or normalize_https_base(
        webhook_base_url_ctx or ""
    )
    effective = explicit or (default_https or "")

    if cert and fqdn and default_https:
        if explicit and explicit != default_https:
            msg = (
                f"WEBHOOK_BASE_URL ({explicit!r}) does not match custom domain base "
                f"({default_https!r}). Remove WEBHOOK_BASE_URL to use the domain default, "
                "or fix the value."
            )
            if strict_deploy_config:
                raise ValueError(msg)
            import warnings

            warnings.warn(msg, UserWarning, stacklevel=2)

    return ResolvedDeployUrls(
        webhook_base_url=effective,
        custom_domain_fqdn=fqdn,
        certificate_arn=cert,
    )


def resolve_admin_cognito_jwt_audiences(
    *,
    primary_client_id: str,
    extra_client_ids: str | None = None,
) -> list[str]:
    """Primary app client plus optional comma/space-separated extras (deduped, order preserved)."""
    audiences: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        cid = raw.strip()
        if cid and cid not in seen:
            seen.add(cid)
            audiences.append(cid)

    add(primary_client_id)
    extra = (extra_client_ids or "").strip()
    if extra:
        for part in re.split(r"[,\s]+", extra):
            add(part)
    return audiences
