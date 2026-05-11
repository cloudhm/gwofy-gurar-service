import aws_cdk as core
import aws_cdk.assertions as assertions

from gwofy_guard_service.api_stack import ApiStack
from gwofy_guard_service.storage_stack import StorageStack


def test_storage_creates_dynamodb_and_queues():
    app = core.App()
    stack = StorageStack(app, "gwofy-guard-storage-test", stage="test")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"TableName": "gwofy-guard-test"},
    )
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    template.resource_count_is("AWS::SQS::Queue", 2)


def test_api_stack_wires_http_api():
    app = core.App()
    storage = StorageStack(app, "gwofy-guard-storage-test2", stage="test2")
    api = ApiStack(app, "gwofy-guard-api-test", storage=storage, stage="test2")
    template = assertions.Template.from_stack(api)

    template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {"ProtocolType": "HTTP"},
    )
    # Five app Lambdas (explicit LogGroup — no LogRetention custom resource)
    template.resource_count_is("AWS::Lambda::Function", 5)


def test_api_stack_custom_domain():
    app = core.App()
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

    template.resource_count_is("AWS::ApiGatewayV2::DomainName", 1)
    template.resource_count_is("AWS::ApiGatewayV2::ApiMapping", 1)
