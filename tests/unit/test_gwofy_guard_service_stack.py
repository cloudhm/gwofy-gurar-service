import aws_cdk as core
import aws_cdk.assertions as assertions

from gwofy_guard_service.api_stack import ApiStack
from gwofy_guard_service.storage_stack import StorageStack

_ADMIN_COGNITO_CTX = {
    "admin_cognito_user_pool_id": "us-east-1_TestPoolForSynth123",
    "admin_cognito_client_id": "abcdefghijklmnopqrstuvwx",
}


def _app_with_admin_cognito() -> core.App:
    return core.App(context=_ADMIN_COGNITO_CTX)


def test_storage_creates_dynamodb_and_queues():
    app = core.App()
    stack = StorageStack(app, "gwofy-guard-storage-test", stage="test")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"TableName": "gwofy-guard-test"},
    )
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"TableName": "gwofy-guard-test-archived"},
    )
    template.resource_count_is("AWS::DynamoDB::Table", 2)
    template.resource_count_is("AWS::SQS::Queue", 2)


def test_api_stack_wires_http_api():
    app = _app_with_admin_cognito()
    storage = StorageStack(app, "gwofy-guard-storage-test2", stage="test2")
    api = ApiStack(app, "gwofy-guard-api-test", storage=storage, stage="test2")
    template = assertions.Template.from_stack(api)

    template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {"Name": "gwofy-guard-api-test2", "ProtocolType": "HTTP"},
    )
    # Six app Lambdas: OAuth, Webhook, Worker, Merchant API, Admin API, Reconcile
    template.resource_count_is("AWS::Lambda::Function", 7)
    template.resource_count_is("AWS::Cognito::UserPool", 0)


def test_api_stack_requires_admin_cognito_user_pool_id():
    app = core.App()
    storage = StorageStack(app, "gwofy-guard-storage-err", stage="e1")
    try:
        ApiStack(app, "gwofy-guard-api-err", storage=storage, stage="e1")
        raise AssertionError("expected ValueError when admin Cognito pool is not configured")
    except ValueError as e:
        assert "ADMIN_COGNITO_USER_POOL_ID" in str(e)


def test_api_stack_synth_without_explicit_admin_client_id():
    """Omit client id: Custom Resource ensures GWO-SHIPPING-PROTECTION app client."""
    app = core.App(
        context={
            "admin_cognito_user_pool_id": "us-east-1_TestPoolForSynth123",
        }
    )
    storage = StorageStack(app, "gwofy-guard-storage-poolonly", stage="p1")
    api = ApiStack(app, "gwofy-guard-api-poolonly", storage=storage, stage="p1")
    template = assertions.Template.from_stack(api)

    template.resource_count_is("AWS::Cognito::UserPool", 0)
    template.resource_count_is("AWS::CloudFormation::CustomResource", 1)
    # Six app Lambdas + on_event + Provider framework
    template.resource_count_is("AWS::Lambda::Function", 9)


def test_api_stack_custom_domain():
    app = _app_with_admin_cognito()
    storage = StorageStack(app, "gwofy-guard-storage-dom", stage="dev")
    api = ApiStack(
        app,
        "gwofy-guard-api-dom",
        storage=storage,
        stage="dev",
        webhook_base_url="https://sp-dev.gwofy.com",
        custom_domain_fqdn="sp-dev.gwofy.com",
        custom_domain_certificate_arn=(
            "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        ),
    )
    template = assertions.Template.from_stack(api)

    template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {"Name": "gwofy-guard-api-dev"},
    )
    template.resource_count_is("AWS::ApiGatewayV2::DomainName", 1)
    template.resource_count_is("AWS::ApiGatewayV2::ApiMapping", 1)
    template.resource_count_is("AWS::Route53::RecordSet", 0)


def test_api_stack_custom_domain_with_route53_alias():
    app = core.App(
        context={
            **_ADMIN_COGNITO_CTX,
            "route53_hosted_zone_id": "Z1234567890ABC",
            "route53_zone_name": "gwofy.com",
        }
    )
    storage = StorageStack(app, "gwofy-guard-storage-r53", stage="prod")
    api = ApiStack(
        app,
        "gwofy-guard-api-r53",
        storage=storage,
        stage="prod",
        webhook_base_url="https://sp-prod.gwofy.com",
        custom_domain_fqdn="sp-prod.gwofy.com",
        custom_domain_certificate_arn=(
            "arn:aws:acm:us-east-1:123456789012:certificate/bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        ),
    )
    template = assertions.Template.from_stack(api)

    template.resource_count_is("AWS::Route53::RecordSet", 1)
    template.has_resource_properties(
        "AWS::Route53::RecordSet",
        {"Name": "sp-prod.gwofy.com.", "Type": "A"},
    )
