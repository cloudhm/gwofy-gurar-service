#!/usr/bin/env python3
import os
import re

import aws_cdk as cdk

from gwofy_guard_service.api_stack import ApiStack
from gwofy_guard_service.deploy_config import resolve_deploy_urls
from gwofy_guard_service.storage_stack import StorageStack


app = cdk.App()

# 环境隔离：同一 AWS 账号内用 stage 后缀区分栈（例：dev / staging / prod）
_stage_raw = app.node.try_get_context("stage") or os.environ.get("CDK_STAGE", "dev")
stage = re.sub(r"[^a-zA-Z0-9-]", "", str(_stage_raw))[:32] or "dev"

_cert_raw = os.environ.get("GWOFY_API_CERTIFICATE_ARN") or app.node.try_get_context("certificate_arn")
_certificate_arn = (_cert_raw or "").strip() or None
_domain_base = (app.node.try_get_context("gwofy_domain_base") or os.environ.get("GWOFY_DOMAIN_BASE") or "gwofy.com").strip()
_sub_prefix = (
    app.node.try_get_context("gwofy_subdomain_prefix") or os.environ.get("GWOFY_SUBDOMAIN_PREFIX") or "sp"
).strip()
_custom_name_raw = os.environ.get("GWOFY_CUSTOM_DOMAIN") or app.node.try_get_context("custom_domain_name")
_strict = app.node.try_get_context("strict_deploy_config") is True

resolved = resolve_deploy_urls(
    stage=stage,
    certificate_arn=_certificate_arn,
    domain_base=_domain_base,
    subdomain_prefix=_sub_prefix,
    custom_domain_name=(_custom_name_raw or "").strip() or None,
    webhook_base_url_env=os.environ.get("WEBHOOK_BASE_URL"),
    webhook_base_url_ctx=app.node.try_get_context("webhook_base_url"),
    strict_deploy_config=_strict,
)

storage = StorageStack(app, f"GwofyGuardStorage-{stage}", stage=stage)
ApiStack(
    app,
    f"GwofyGuardApi-{stage}",
    storage=storage,
    stage=stage,
    webhook_base_url=resolved.webhook_base_url or None,
    post_install_redirect_url=os.environ.get("POST_INSTALL_REDIRECT_URL"),
    feishu_webhook_url=os.environ.get("FEISHU_WEBHOOK_URL"),
    custom_domain_fqdn=resolved.custom_domain_fqdn,
    custom_domain_certificate_arn=resolved.certificate_arn,
)

app.synth()
