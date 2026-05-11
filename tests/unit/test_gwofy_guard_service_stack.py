import aws_cdk as core
import aws_cdk.assertions as assertions

from gwofy_guard_service.gwofy_guard_service_stack import GwofyGuardServiceStack

# example tests. To run these tests, uncomment this file along with the example
# resource in gwofy_guard_service/gwofy_guard_service_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = GwofyGuardServiceStack(app, "gwofy-guard-service")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
