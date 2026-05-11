"""DynamoDB, KMS, SQS (main + DLQ) for Gwofy Guard Service."""

import re

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_kms as kms,
    aws_sqs as sqs,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
)
from constructs import Construct


class StorageStack(Stack):
    """Core data plane: online + archived DynamoDB tables, KMS for tokens, work queue."""

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        retain = self.node.try_get_context("retain_data") is True

        # DynamoDB table names: 3–255 chars; [a-zA-Z0-9_.-]+
        stage_slug = re.sub(r"[^a-zA-Z0-9_.-]", "", stage).strip("-.") or "dev"
        stage_slug = stage_slug[:200]
        physical_table_name = f"gwofy-guard-{stage_slug}"
        if len(physical_table_name) > 255:
            physical_table_name = physical_table_name[:255]

        self.token_key = kms.Key(
            self,
            "ShopifyTokenKey",
            alias=f"alias/{construct_id.lower()}-shopify-token",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN if retain else RemovalPolicy.DESTROY,
        )

        self.table = dynamodb.Table(
            self,
            "AppTable",
            table_name=physical_table_name,
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            removal_policy=RemovalPolicy.RETAIN if retain else RemovalPolicy.DESTROY,
        )

        archived_physical_name = f"{physical_table_name}-archived"
        if len(archived_physical_name) > 255:
            archived_physical_name = archived_physical_name[:255]

        self.archived_table = dynamodb.Table(
            self,
            "ArchivedAppTable",
            table_name=archived_physical_name,
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            removal_policy=RemovalPolicy.RETAIN if retain else RemovalPolicy.DESTROY,
        )

        # GSI: variant price history point-in-time (optional queries)
        self.table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(name="gsi1pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="gsi1sk", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI2: list installed shops for admin (pk=SHOP_INDEX on METADATA rows)
        self.table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(name="gsi2pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="gsi2sk", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # SQS queue names: max 80 chars; alphanumeric, hyphen, underscore
        q_prefix = re.sub(r"[^a-zA-Z0-9_-]", "", f"gwofy-guard-{stage_slug}")[:50]

        dlq = sqs.Queue(
            self,
            "WorkDlq",
            queue_name=f"{q_prefix}-work-dlq"[:80],
            retention_period=Duration.days(14),
            visibility_timeout=Duration.seconds(300),
        )

        self.work_queue = sqs.Queue(
            self,
            "WorkQueue",
            queue_name=f"{q_prefix}-work"[:80],
            visibility_timeout=Duration.seconds(900),
            retention_period=Duration.days(14),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq),
        )

        self.dlq = dlq

        # DLQ depth alarm (optional SNS — topic created only if email in context)
        alarm_topic_arn = self.node.try_get_context("alarm_topic_arn")
        if alarm_topic_arn:
            topic = sns.Topic.from_topic_arn(self, "AlarmTopic", alarm_topic_arn)
            alarm = cloudwatch.Alarm(
                self,
                "DlqMessagesAlarm",
                metric=dlq.metric_approximate_number_of_messages_visible(),
                threshold=1,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(topic))
