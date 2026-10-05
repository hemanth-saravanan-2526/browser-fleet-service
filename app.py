#!/usr/bin/env python3
import os
import aws_cdk as cdk

from infra.storage_stack import StorageStack
from infra.queue_stack import QueueStack
from infra.engines_stack import EnginesStack
from infra.dispatcher_stack import DispatcherStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# 1. Persistence Layer: S3 + DynamoDB
storage = StorageStack(app, "BrowserFleet-Storage", env=env)

# 2. Asynchronous Buffering: SQS + DLQ
queue = QueueStack(app, "BrowserFleet-Queue", env=env)

# 3. Execution Engines: Playwright, nodriver, Selenium Lambda Containers
engines = EnginesStack(
    app,
    "BrowserFleet-Engines",
    artifacts_bucket=storage.bucket,
    fleet_table=storage.table,
    jobs_queue=queue.jobs_queue,
    env=env,
)

# 4. Dispatcher & Ingress: HTTP API Gateway + Dispatcher Lambda
dispatcher = DispatcherStack(
    app,
    "BrowserFleet-Dispatcher",
    artifacts_bucket=storage.bucket,
    fleet_table=storage.table,
    jobs_queue=queue.jobs_queue,
    playwright_func=engines.playwright_func,
    nodriver_func=engines.nodriver_func,
    selenium_func=engines.selenium_func,
    env=env,
)

app.synth()
