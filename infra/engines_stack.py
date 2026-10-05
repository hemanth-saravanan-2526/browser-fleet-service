import os
from aws_cdk import (
    Stack,
    Duration,
    Size,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_iam as iam,
)
from constructs import Construct


class EnginesStack(Stack):
    """
    EnginesStack provisions container-based Lambda functions for Playwright, nodriver, and Selenium.
    Each engine runs in its own isolated execution environment with right-sized CPU/RAM and /tmp disk.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        artifacts_bucket: s3.IBucket,
        fleet_table: dynamodb.ITable,
        jobs_queue: sqs.IQueue,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        common_env = {
            "S3_BUCKET_NAME": artifacts_bucket.bucket_name,
            "DYNAMODB_TABLE_NAME": fleet_table.table_name,
        }

        # 1. Playwright Container Function
        self.playwright_func = _lambda.DockerImageFunction(
            self,
            "PlaywrightEngineFunction",
            function_name="browser-fleet-playwright",
            code=_lambda.DockerImageCode.from_image_asset(
                os.path.join(os.path.dirname(__file__), "..", "engines", "playwright")
            ),
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(2048),
            timeout=Duration.seconds(120),
            environment=common_env,
            description="Playwright headless scraping engine with stealth plugins and context reuse",
        )

        # 2. nodriver Container Function
        self.nodriver_func = _lambda.DockerImageFunction(
            self,
            "NodriverEngineFunction",
            function_name="browser-fleet-nodriver",
            code=_lambda.DockerImageCode.from_image_asset(
                os.path.join(os.path.dirname(__file__), "..", "engines", "nodriver")
            ),
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(2048),
            timeout=Duration.seconds(120),
            environment=common_env,
            description="nodriver CDP-based scraping engine for bot-detection bypass",
        )

        # 3. Selenium Container Function
        self.selenium_func = _lambda.DockerImageFunction(
            self,
            "SeleniumEngineFunction",
            function_name="browser-fleet-selenium",
            code=_lambda.DockerImageCode.from_image_asset(
                os.path.join(os.path.dirname(__file__), "..", "engines", "selenium")
            ),
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(2048),
            timeout=Duration.seconds(120),
            environment=common_env,
            description="Selenium WebDriver engine for standard headless automation",
        )

        # Grant S3 & DynamoDB permissions to all three engine functions
        for func in [self.playwright_func, self.nodriver_func, self.selenium_func]:
            artifacts_bucket.grant_read_write(func)
            fleet_table.grant_read_write_data(func)
