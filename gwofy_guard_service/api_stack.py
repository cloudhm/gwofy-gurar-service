"""HTTP API + Lambda functions (OAuth, webhooks, worker, API, reconciliation)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import jsii
from aws_cdk import BundlingOptions, CfnOutput, Duration, ILocalBundling, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_lambda as aws_lambda
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_logs as logs
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
        }

        code = _lambda_bundle_code()

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
            environment={**common_env},
            log_group=_week_log_group(self, "WorkerFnLogGroup"),
        )

        api_fn = aws_lambda.Function(
            self,
            "SessionApiFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="api_handler.handler",
            code=code,
            timeout=Duration.seconds(29),
            memory_size=256,
            environment={
                "TABLE_NAME": table.table_name,
                "SHOPIFY_CLIENT_ID": common_env["SHOPIFY_CLIENT_ID"],
                "SHOPIFY_CLIENT_SECRET": common_env["SHOPIFY_CLIENT_SECRET"],
            },
            log_group=_week_log_group(self, "SessionApiFnLogGroup"),
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
        table.grant_read_write_data(api_fn)
        table.grant_read_data(reconcile_fn)

        token_key.grant_encrypt_decrypt(oauth_fn)
        token_key.grant_decrypt(worker_fn)

        work_queue.grant_send_messages(oauth_fn)
        work_queue.grant_send_messages(reconcile_fn)
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
            api_name="gwofy-guard-api",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_headers=["Authorization", "Content-Type", "X-Shopify-*"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_origins=["*"],
            ),
        )

        http_api.add_routes(
            path="/oauth/callback",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration("OAuthInteg", oauth_fn),
        )

        http_api.add_routes(
            path="/webhooks/shopify",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("WebhookInteg", webhook_fn),
        )

        http_api.add_routes(
            path="/api/me",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration("ApiInteg", api_fn),
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
