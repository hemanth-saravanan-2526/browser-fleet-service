from aws_cdk import (
    Stack,
    Duration,
    aws_sqs as sqs,
)
from constructs import Construct


class QueueStack(Stack):
    """
    QueueStack provisions the asynchronous SQS queue buffering system for Option B.
    Smooths traffic spikes and provides backpressure against concurrency limits.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Dead Letter Queue for failed scraping tasks
        self.dlq = sqs.Queue(
            self,
            "BrowserFleetDLQ",
            retention_period=Duration.days(14),
        )

        # Primary Async Jobs Queue
        self.jobs_queue = sqs.Queue(
            self,
            "BrowserFleetJobsQueue",
            visibility_timeout=Duration.seconds(150),  # Must exceed engine Lambda timeout (120s)
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.dlq,
            ),
        )
