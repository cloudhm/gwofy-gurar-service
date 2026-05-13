"""HTTP API + Lambda functions (OAuth, webhooks, worker, API, reconciliation)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import jsii
from aws_cdk import Aws, BundlingOptions, CfnOutput, CustomResource, Duration, ILocalBundling, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authorizers
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as aws_lambda
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk.custom_resources import Provider
from constructs import Construct

from gwofy_guard_service.storage_stack import StorageStack


LAMBDA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lambda"))


@jsii.implements(ILocalBundling)
class _LocalPipBundle:
    """Bundle Lambda assets with host `pip` so `cdk synth` works without Docker."""

    def try_bundle(self, output_dir: str, options: BundlingOptions) -> bool:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "-r",
                "requirements.txt",
                "-t",
                output_dir,
            ],
            cwd=LAMBDA_DIR,
        )
        for entry in os.listdir(LAMBDA_DIR):
            if entry in ("__pycache__",):
                continue
            src = os.path.join(LAMBDA_DIR, entry)
            dst = os.path.join(output_dir, entry)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return True


def _week_log_group(scope: Construct, construct_id: str) -> logs.LogGroup:
    return logs.LogGroup(
        scope,
        construct_id,
        retention=logs.RetentionDays.ONE_WEEK,
        removal_policy=RemovalPolicy.DESTROY,
    )


def _route53_record_label_for_zone(*, fqdn: str, zone_name: str) -> str:
    """Relative name for an A/alias record in ``zone_name`` (e.g. ``sp-prod`` for ``sp-prod.gwofy.com``)."""
    f = fqdn.strip().lower().rstrip(".")
    z = zone_name.strip().lower().rstrip(".")
    if not f.endswith(z):
        raise ValueError(f"custom domain {fqdn!r} must be under Route53 zone {zone_name!r}")
    if f == z:
        return ""
    suffix = f".{z}"
    if not f.endswith(suffix):
        raise ValueError(f"custom domain {fqdn!r} must be a subdomain of {zone_name!r}")
    return f[: -len(suffix)]


def _lambda_bundle_code() -> aws_lambda.Code:
    return aws_lambda.Code.from_asset(
        LAMBDA_DIR,
        bundling=BundlingOptions(
            image=aws_lambda.Runtime.PYTHON_3_12.bundling_image,
            local=_LocalPipBundle(),
            command=[
                "bash",
                "-c",
                "pip install --no-cache-dir -r requirements.txt -t /asset-output "
                "&& cp -r /asset-input/. /asset-output/",
            ],
        ),
    )


class ApiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        storage: StorageStack,
        stage: str,
        webhook_base_url: str | None = None,
        post_install_redirect_url: str | None = None,
        feishu_webhook_url: str | None = None,
        custom_domain_fqdn: str | None = None,
        custom_domain_certificate_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = storage.table
        archived_table = storage.archived_table
        token_key = storage.token_key
        work_queue = storage.work_queue

        cid = self.node.try_get_context("shopify_client_id") or os.environ.get("SHOPIFY_CLIENT_ID", "")
        csec = self.node.try_get_context("shopify_client_secret") or os.environ.get(
            "SHOPIFY_CLIENT_SECRET", ""
        )

        common_env = {
            "TABLE_NAME": table.table_name,
            "KMS_KEY_ID": token_key.key_id,
            "WORK_QUEUE_URL": work_queue.queue_url,
            "SHOPIFY_CLIENT_ID": cid,
            "SHOPIFY_CLIENT_SECRET": csec,
            "SHOPIFY_API_VERSION": self.node.try_get_context("shopify_api_version") or "2024-10",
            "WEBHOOK_BASE_URL": webhook_base_url
            or self.node.try_get_context("webhook_base_url")
            or "",
            "POST_INSTALL_REDIRECT_URL": post_install_redirect_url
            or self.node.try_get_context("post_install_redirect_url")
            or "",
            "FEISHU_WEBHOOK_URL": feishu_webhook_url
            or self.node.try_get_context("feishu_webhook_url")
            or "",
            "ORDER_PROTECTION_TAG": self.node.try_get_context("order_protection_tag")
            or os.environ.get("ORDER_PROTECTION_TAG", "gwofy-shipping-protection"),
        }

        code = _lambda_bundle_code()

        admin_pool_id = (
            self.node.try_get_context("admin_cognito_user_pool_id")
            or os.environ.get("ADMIN_COGNITO_USER_POOL_ID", "")
        ).strip()
        admin_client_id = (
            self.node.try_get_context("admin_cognito_client_id")
            or os.environ.get("ADMIN_COGNITO_CLIENT_ID", "")
        ).strip()
        admin_cognito_region = (
            self.node.try_get_context("admin_cognito_region") or os.environ.get("ADMIN_COGNITO_REGION", "")
        ).strip() or Stack.of(self).region
        if not admin_pool_id:
            raise ValueError(
                "Existing Cognito User Pool is required for /admin JWT: set ADMIN_COGNITO_USER_POOL_ID "
                "(or CDK context admin_cognito_user_pool_id). "
                "If ADMIN_COGNITO_CLIENT_ID is omitted, a Custom Resource creates or reuses an app client named "
                "GWO-SHIPPING-PROTECTION in that pool. "
                "Optional: ADMIN_COGNITO_REGION (or admin_cognito_region) if the pool is outside this stack region."
            )
        issuer = f"https://cognito-idp.{admin_cognito_region}.amazonaws.com/{admin_pool_id}"

        cognito_admin_pool_arn = (
            f"arn:{Aws.PARTITION}:cognito-idp:{admin_cognito_region}:{self.account}:userpool/{admin_pool_id}"
        )
        if admin_client_id:
            resolved_admin_client_id = admin_client_id
        else:
            cognito_client_on_event = aws_lambda.Function(
                self,
                "CognitoAdminAppClientOnEventFn",
                runtime=aws_lambda.Runtime.PYTHON_3_12,
                handler="cognito_admin_app_client.handler",
                code=code,
                timeout=Duration.seconds(120),
                memory_size=256,
                log_group=_week_log_group(self, "CognitoAdminAppClientOnEventFnLogGroup"),
            )
            cognito_client_on_event.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "cognito-idp:ListUserPoolClients",
                        "cognito-idp:CreateUserPoolClient",
                    ],
                    resources=[cognito_admin_pool_arn],
                )
            )
            cognito_client_provider = Provider(
                self,
                "CognitoAdminAppClientProvider",
                on_event_handler=cognito_client_on_event,
            )
            cognito_client_cr = CustomResource(
                self,
                "CognitoAdminAppClient",
                service_token=cognito_client_provider.service_token,
                properties={
                    "UserPoolId": admin_pool_id,
                    "Region": admin_cognito_region,
                    "ClientName": "GWO-SHIPPING-PROTECTION",
                },
            )
            resolved_admin_client_id = cognito_client_cr.get_att_string("ClientId")

        _wb = (
            (webhook_base_url or "").strip()
            or (self.node.try_get_context("webhook_base_url") or "").strip()
            or os.environ.get("WEBHOOK_BASE_URL", "").strip()
        ).rstrip("/")
        cognito_redirect_uri = f"{_wb}/auth/callback" if _wb else ""
        cognito_hosted_ui_domain = (
            os.environ.get("COGNITO_HOSTED_UI_DOMAIN")
            or self.node.try_get_context("cognito_hosted_ui_domain")
            or ""
        ).strip()

        oauth_fn = aws_lambda.Function(
            self,
            "OAuthCallbackFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="oauth_handler.handler",
            code=code,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={**common_env},
            log_group=_week_log_group(self, "OAuthCallbackFnLogGroup"),
        )

        cognito_callback_fn = aws_lambda.Function(
            self,
            "CognitoAuthCallbackFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="cognito_callback_handler.handler",
            code=code,
            timeout=Duration.seconds(29),
            memory_size=256,
            environment={
                "COGNITO_CLIENT_ID": resolved_admin_client_id,
                "COGNITO_REDIRECT_URI": cognito_redirect_uri,
                "COGNITO_HOSTED_UI_DOMAIN": cognito_hosted_ui_domain,
            },
            log_group=_week_log_group(self, "CognitoAuthCallbackFnLogGroup"),
        )

        webhook_fn = aws_lambda.Function(
            self,
            "WebhookIngressFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="webhook_handler.handler",
            code=code,
            timeout=Duration.seconds(29),
            memory_size=256,
            environment={
                "SHOPIFY_CLIENT_SECRET": common_env["SHOPIFY_CLIENT_SECRET"],
                "WORK_QUEUE_URL": work_queue.queue_url,
            },
            log_group=_week_log_group(self, "WebhookIngressFnLogGroup"),
        )

        worker_fn = aws_lambda.Function(
            self,
            "WorkerFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="worker_handler.handler",
            code=code,
            timeout=Duration.seconds(900),
            memory_size=1024,
            environment={**common_env, "ARCHIVED_TABLE_NAME": archived_table.table_name},
            log_group=_week_log_group(self, "WorkerFnLogGroup"),
        )

        merchant_fn = aws_lambda.Function(
            self,
            "MerchantApiFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="merchant_api_handler.handler",
            code=code,
            timeout=Duration.seconds(120),
            memory_size=512,
            environment={
                **common_env,
                "TABLE_NAME": table.table_name,
                "SHOPIFY_CLIENT_ID": common_env["SHOPIFY_CLIENT_ID"],
                "SHOPIFY_CLIENT_SECRET": common_env["SHOPIFY_CLIENT_SECRET"],
            },
            log_group=_week_log_group(self, "MerchantApiFnLogGroup"),
        )

        admin_group_name = (
            self.node.try_get_context("admin_cognito_group")
            or os.environ.get("ADMIN_COGNITO_GROUP", "GWOFY-SHIPPING-PROTECTION")
        )
        admin_fn = aws_lambda.Function(
            self,
            "AdminApiFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="admin_handler.handler",
            code=code,
            timeout=Duration.seconds(29),
            memory_size=512,
            environment={**common_env, "ADMIN_COGNITO_GROUP": admin_group_name},
            log_group=_week_log_group(self, "AdminApiFnLogGroup"),
        )

        reconcile_fn = aws_lambda.Function(
            self,
            "ReconciliationFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="reconciliation_handler.handler",
            code=code,
            timeout=Duration.seconds(120),
            memory_size=512,
            environment={
                "TABLE_NAME": table.table_name,
                "WORK_QUEUE_URL": work_queue.queue_url,
            },
            log_group=_week_log_group(self, "ReconciliationFnLogGroup"),
        )

        table.grant_read_write_data(oauth_fn)
        table.grant_read_write_data(worker_fn)
        table.grant_read_write_data(merchant_fn)
        table.grant_read_write_data(admin_fn)
        table.grant_read_data(reconcile_fn)
        archived_table.grant_read_write_data(worker_fn)

        token_key.grant_encrypt_decrypt(oauth_fn)
        token_key.grant_decrypt(worker_fn)
        token_key.grant_decrypt(merchant_fn)
        token_key.grant_decrypt(admin_fn)

        work_queue.grant_send_messages(oauth_fn)
        work_queue.grant_send_messages(reconcile_fn)
        work_queue.grant_send_messages(merchant_fn)
        work_queue.grant_consume_messages(worker_fn)

        worker_fn.add_event_source(
            lambda_event_sources.SqsEventSource(
                work_queue,
                batch_size=5,
                max_batching_window=Duration.seconds(5),
            )
        )

        http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name=f"gwofy-guard-api-{stage}",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_headers=[
                    "Authorization",
                    "Content-Type",
                    "X-Shopify-*",
                    "X-Gwofy-Shop",
                    "X-Gwofy-Signature",
                ],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_origins=["*"],
            ),
        )

        admin_jwt = apigwv2_authorizers.HttpJwtAuthorizer(
            "AdminJwtAuthorizer",
            issuer,
            jwt_audience=[resolved_admin_client_id],
            identity_source=["$request.header.Authorization"],
        )

        http_api.add_routes(
            path="/oauth/callback",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration("OAuthInteg", oauth_fn),
        )

        http_api.add_routes(
            path="/auth/callback",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "CognitoAuthCallbackInteg", cognito_callback_fn
            ),
        )

        http_api.add_routes(
            path="/webhooks/shopify",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("WebhookInteg", webhook_fn),
        )

        merchant_integ = apigwv2_integrations.HttpLambdaIntegration("MerchantInteg", merchant_fn)
        http_api.add_routes(
            path="/api/me",
            methods=[apigwv2.HttpMethod.GET],
            integration=merchant_integ,
        )
        http_api.add_routes(
            path="/api/activate",
            methods=[apigwv2.HttpMethod.POST],
            integration=merchant_integ,
        )
        http_api.add_routes(
            path="/api/me/embed",
            methods=[apigwv2.HttpMethod.PATCH],
            integration=merchant_integ,
        )
        http_api.add_routes(
            path="/api/cart-config",
            methods=[apigwv2.HttpMethod.POST],
            integration=merchant_integ,
        )
        http_api.add_routes(
            path="/api/shop-enabled-currencies/sync",
            methods=[apigwv2.HttpMethod.POST],
            integration=merchant_integ,
        )

        admin_integ = apigwv2_integrations.HttpLambdaIntegration("AdminInteg", admin_fn)
        http_api.add_routes(
            path="/admin/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=admin_integ,
            authorizer=admin_jwt,
        )

        events.Rule(
            self,
            "ReconcileSchedule",
            schedule=events.Schedule.rate(Duration.minutes(30)),
        ).add_target(targets.LambdaFunction(reconcile_fn))

        if custom_domain_fqdn and custom_domain_certificate_arn:
            cert = acm.Certificate.from_certificate_arn(
                self,
                "GwofyHttpApiCert",
                custom_domain_certificate_arn,
            )
            dn = apigwv2.DomainName(
                self,
                "GwofyHttpApiDomain",
                domain_name=custom_domain_fqdn,
                certificate=cert,
            )
            apigwv2.ApiMapping(
                self,
                "GwofyHttpApiMapping",
                api=http_api,
                domain_name=dn,
                stage=http_api.default_stage,
            )
            public_custom = f"https://{custom_domain_fqdn}"
            CfnOutput(
                self,
                "PublicApiUrl",
                value=public_custom,
                description=f"Public HTTPS base for OAuth & Shopify webhooks (stage={stage})",
            )
            CfnOutput(
                self,
                "CustomDomainRegionalTarget",
                value=dn.regional_domain_name,
                description="DNS CNAME target for the custom subdomain (API Gateway regional)",
            )
            CfnOutput(
                self,
                "CustomDomainRegionalHostedZoneId",
                value=dn.regional_hosted_zone_id,
                description="Route53 alias hosted zone ID for API Gateway",
            )

            r53_zone_id = (
                self.node.try_get_context("route53_hosted_zone_id")
                or os.environ.get("GWOFY_ROUTE53_HOSTED_ZONE_ID", "")
            ).strip()
            r53_zone_name = (
                self.node.try_get_context("route53_zone_name")
                or os.environ.get("GWOFY_ROUTE53_ZONE_NAME", "")
            ).strip()
            if r53_zone_id and r53_zone_name:
                label = _route53_record_label_for_zone(fqdn=custom_domain_fqdn, zone_name=r53_zone_name)
                hz = route53.HostedZone.from_hosted_zone_attributes(
                    self,
                    "GwofyPublicHostedZone",
                    hosted_zone_id=r53_zone_id,
                    zone_name=r53_zone_name,
                )
                route53.ARecord(
                    self,
                    "GwofyCustomDomainAlias",
                    zone=hz,
                    record_name=label,
                    target=route53.RecordTarget.from_alias(
                        route53_targets.ApiGatewayv2DomainProperties(
                            dn.regional_domain_name,
                            dn.regional_hosted_zone_id,
                        )
                    ),
                )
                CfnOutput(
                    self,
                    "Route53AliasCreated",
                    value=f"{custom_domain_fqdn} → API Gateway (alias in zone {r53_zone_name})",
                    description="Managed Route53 alias record (deploy creates/updates this name)",
                )

        self.http_api = http_api
        self.http_api_url = http_api.api_endpoint
        self.oauth_function = oauth_fn
        self.worker_function = worker_fn

        CfnOutput(
            self,
            "HttpApiUrl",
            value=http_api.api_endpoint,
            description="Default execute-api URL; prefer PublicApiUrl when custom domain is configured",
        )
        CfnOutput(
            self,
            "AdminCognitoUserPoolId",
            value=admin_pool_id,
            description="Existing Cognito User Pool ID used for /admin JWT",
        )
        CfnOutput(
            self,
            "AdminCognitoUserPoolClientId",
            value=resolved_admin_client_id,
            description="Cognito app client ID for JWT aud (explicit or ensured as GWO-SHIPPING-PROTECTION)",
        )
        CfnOutput(
            self,
            "AdminCognitoIssuer",
            value=issuer,
            description="JWT issuer URL for API Gateway authorizer",
        )
        CfnOutput(
            self,
            "AdminCognitoRegion",
            value=admin_cognito_region,
            description="AWS region of the Cognito User Pool (issuer host)",
        )
