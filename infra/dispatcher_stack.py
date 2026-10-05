import os
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
)
from constructs import Construct


class DispatcherStack(Stack):
    """
    DispatcherStack provisions the traffic controller layer:
    - Lightweight Dispatcher Lambda.
    - HTTP API Gateway (v2) exposing /scrape, /jobs/{id}, and /capabilities.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        artifacts_bucket: s3.IBucket,
        fleet_table: dynamodb.ITable,
        jobs_queue: sqs.IQueue,
        playwright_func: _lambda.IFunction,
        nodriver_func: _lambda.IFunction,
        selenium_func: _lambda.IFunction,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Dispatcher Lambda function
        self.dispatcher_func = _lambda.Function(
            self,
            "DispatcherFunction",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "dispatcher")
            ),
            memory_size=256,
            timeout=Duration.seconds(30),  # HTTP API Gateway max timeout is 29s
            environment={
                "S3_BUCKET_NAME": artifacts_bucket.bucket_name,
                "DYNAMODB_TABLE_NAME": fleet_table.table_name,
                "SQS_QUEUE_URL": jobs_queue.queue_url,
                "PLAYWRIGHT_FUNCTION_NAME": playwright_func.function_name,
                "NODRIVER_FUNCTION_NAME": nodriver_func.function_name,
                "SELENIUM_FUNCTION_NAME": selenium_func.function_name,
            },
            description="Browser fleet dispatcher and routing controller",
        )

        # Permissions
        fleet_table.grant_read_write_data(self.dispatcher_func)
        jobs_queue.grant_send_messages(self.dispatcher_func)
        artifacts_bucket.grant_read(self.dispatcher_func)

        playwright_func.grant_invoke(self.dispatcher_func)
        nodriver_func.grant_invoke(self.dispatcher_func)
        selenium_func.grant_invoke(self.dispatcher_func)

        # HTTP API Gateway (v2)
        http_api = apigw.HttpApi(
            self,
            "BrowserFleetHttpApi",
            api_name="browser-fleet-api",
            description="Serverless Browser Fleet HTTP API",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_headers=["*"],
                allow_methods=[
                    apigw.CorsHttpMethod.GET,
                    apigw.CorsHttpMethod.POST,
                    apigw.CorsHttpMethod.OPTIONS,
                ],
                allow_origins=["*"],
                max_age=Duration.days(1),
            ),
        )

        integration = integrations.HttpLambdaIntegration(
            "DispatcherIntegration",
            self.dispatcher_func,
        )

        # Routes
        http_api.add_routes(
            path="/scrape",
            methods=[apigw.HttpMethod.POST],
            integration=integration,
        )

        http_api.add_routes(
            path="/jobs/{job_id}",
            methods=[apigw.HttpMethod.GET],
            integration=integration,
        )

        http_api.add_routes(
            path="/capabilities",
            methods=[apigw.HttpMethod.GET],
            integration=integration,
        )

        # Outputs
        CfnOutput(
            self,
            "ApiEndpoint",
            value=http_api.api_endpoint,
            description="The HTTP API Gateway endpoint for scraping jobs",
        )
