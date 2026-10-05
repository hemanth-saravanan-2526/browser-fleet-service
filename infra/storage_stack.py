from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class StorageStack(Stack):
    """
    StorageStack manages persistence resources:
    - S3 bucket for storing large scraping payloads (HTML, full DOM, screenshots).
    - DynamoDB table for job status tracking and domain-level circuit breaker state.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for scraping outputs & screenshots
        self.bucket = s3.Bucket(
            self,
            "BrowserFleetArtifactsBucket",
            bucket_name=None,  # AWS auto-generates unique name
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3600,
                )
            ],
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldArtifacts",
                    expiration=Duration.days(14),
                    enabled=True,
                )
            ],
        )

        # DynamoDB table for job status & circuit breaker records (Scale to zero / Pay-per-request)
        self.table = dynamodb.Table(
            self,
            "BrowserFleetTable",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )
