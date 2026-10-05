import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger("browser-fleet.dispatcher")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)

# AWS clients with region fallback
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
lambda_client = boto3.client("lambda", region_name=AWS_REGION)
dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
sqs_client = boto3.client("sqs", region_name=AWS_REGION)

# Environment configurations
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE_NAME")
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")

PLAYWRIGHT_FUNCTION_NAME = os.environ.get("PLAYWRIGHT_FUNCTION_NAME", "browser-fleet-playwright")
NODRIVER_FUNCTION_NAME = os.environ.get("NODRIVER_FUNCTION_NAME", "browser-fleet-nodriver")
SELENIUM_FUNCTION_NAME = os.environ.get("SELENIUM_FUNCTION_NAME", "browser-fleet-selenium")

ENGINE_FUNCTION_MAP = {
    "playwright": PLAYWRIGHT_FUNCTION_NAME,
    "nodriver": NODRIVER_FUNCTION_NAME,
    "selenium": SELENIUM_FUNCTION_NAME,
}

# Inlined capabilities for fast execution in dispatcher
CAPABILITIES = {
    "playwright": {"supported_outputs": ["html", "text", "screenshot", "json", "excel"], "supports_proxy_auth": True},
    "nodriver": {"supported_outputs": ["html", "text", "screenshot", "json", "excel"], "supports_proxy_auth": False},
    "selenium": {"supported_outputs": ["html", "text", "screenshot", "json", "excel"], "supports_proxy_auth": True},
}


def build_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to format API Gateway HTTP API JSON response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
        },
        "body": json.dumps(body),
    }


def is_circuit_broken(domain: str) -> bool:
    """Checks if domain has triggered circuit breaker in DynamoDB."""
    if not DYNAMODB_TABLE or not domain:
        return False
    try:
        res = dynamodb_client.get_item(
            TableName=DYNAMODB_TABLE,
            Key={"pk": {"S": f"CIRCUIT#{domain}"}, "sk": {"S": "STATE"}},
        )
        item = res.get("Item")
        if item:
            consecutive_failures = int(item.get("failures", {}).get("N", "0"))
            last_failed = float(item.get("timestamp", {}).get("N", "0"))
            # Circuit breaker trips after 5 consecutive failures within 5 minutes
            if consecutive_failures >= 5 and (time.time() - last_failed) < 300:
                logger.warning(f"Circuit breaker active for domain: {domain}")
                return True
    except Exception as e:
        logger.warning(f"Error checking circuit breaker: {e}")
    return False


def record_scrape_result(domain: str, success: bool):
    """Updates circuit breaker failure counter for target domain."""
    if not DYNAMODB_TABLE or not domain:
        return
    try:
        now = str(int(time.time()))
        if success:
            dynamodb_client.delete_item(
                TableName=DYNAMODB_TABLE,
                Key={"pk": {"S": f"CIRCUIT#{domain}"}, "sk": {"S": "STATE"}},
            )
        else:
            dynamodb_client.update_item(
                TableName=DYNAMODB_TABLE,
                Key={"pk": {"S": f"CIRCUIT#{domain}"}, "sk": {"S": "STATE"}},
                UpdateExpression="ADD failures :inc SET #ts = :now",
                ExpressionAttributeNames={"#ts": "timestamp"},
                ExpressionAttributeValues={":inc": {"N": "1"}, ":now": {"N": now}},
            )
    except Exception as e:
        logger.warning(f"Error updating circuit breaker: {e}")


def handle_scrape_request(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validates and routes the scrape request."""
    url = body.get("url")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return build_response(400, {"error": "Invalid or missing 'url'. Must start with http:// or https://"})

    engine = body.get("engine", "playwright").lower()
    if engine not in ENGINE_FUNCTION_MAP:
        return build_response(400, {"error": f"Unsupported engine '{engine}'. Valid: {list(ENGINE_FUNCTION_MAP.keys())}"})

    output_format = body.get("output", "html").lower()
    engine_caps = CAPABILITIES.get(engine, {})
    if output_format not in engine_caps.get("supported_outputs", []):
        return build_response(400, {"error": f"Engine '{engine}' does not support output '{output_format}'"})

    options = body.get("options", {})
    proxy = options.get("proxy")
    if proxy and "@" in proxy and not engine_caps.get("supports_proxy_auth", True):
        return build_response(400, {"error": f"Engine '{engine}' does not support authenticated proxies."})

    domain = urlparse(url).netloc
    if is_circuit_broken(domain):
        return build_response(
            503,
            {
                "status": "error",
                "error": f"Circuit breaker tripped for domain '{domain}'. Too many recent failures. Retry later.",
            },
        )

    job_id = body.get("job_id", str(uuid.uuid4()))
    async_mode = body.get("async_mode", False)

    # Option B: Asynchronous Job via SQS
    if async_mode:
        if not SQS_QUEUE_URL:
            return build_response(500, {"error": "SQS_QUEUE_URL not configured for async jobs."})

        job_payload = {
            "job_id": job_id,
            "url": url,
            "engine": engine,
            "output": output_format,
            "options": options,
            "created_at": int(time.time()),
        }

        # Save initial pending status in DynamoDB
        if DYNAMODB_TABLE:
            try:
                dynamodb_client.put_item(
                    TableName=DYNAMODB_TABLE,
                    Item={
                        "pk": {"S": f"JOB#{job_id}"},
                        "sk": {"S": "STATUS"},
                        "status": {"S": "pending"},
                        "url": {"S": url},
                        "engine": {"S": engine},
                        "created_at": {"N": str(int(time.time()))},
                    },
                )
            except Exception as e:
                logger.error(f"Failed to record pending job in DynamoDB: {e}")

        # Enqueue job to SQS
        sqs_client.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(job_payload),
            MessageAttributes={
                "engine": {"DataType": "String", "StringValue": engine},
            },
        )

        return build_response(
            202,
            {
                "job_id": job_id,
                "status": "pending",
                "engine_used": engine,
                "message": "Job queued successfully. Poll GET /jobs/{id} for result.",
            },
        )

    # Option A: Direct Synchronous Invocation
    target_function = ENGINE_FUNCTION_MAP[engine]
    logger.info(f"Synchronously invoking {target_function} for job {job_id}...")

    invoke_payload = {
        "job_id": job_id,
        "url": url,
        "engine": engine,
        "output": output_format,
        "options": options,
    }

    try:
        start_t = time.time()
        invoke_res = lambda_client.invoke(
            FunctionName=target_function,
            InvocationType="RequestResponse",
            Payload=json.dumps(invoke_payload).encode("utf-8"),
        )
        raw_output = invoke_res["Payload"].read().decode("utf-8")
        result_json = json.loads(raw_output)

        is_success = result_json.get("status") == "success"
        record_scrape_result(domain, is_success)

        # Store completed job status in DynamoDB for persistence/caching
        if DYNAMODB_TABLE:
            try:
                dynamodb_client.put_item(
                    TableName=DYNAMODB_TABLE,
                    Item={
                        "pk": {"S": f"JOB#{job_id}"},
                        "sk": {"S": "STATUS"},
                        "status": {"S": result_json.get("status", "unknown")},
                        "url": {"S": url},
                        "engine": {"S": engine},
                        "duration_ms": {"N": str(result_json.get("duration_ms", 0))},
                        "updated_at": {"N": str(int(time.time()))},
                    },
                )
            except Exception as e:
                logger.warning(f"Error persisting job status to DynamoDB: {e}")

        return build_response(200, result_json)

    except ClientError as ce:
        logger.error(f"Lambda invoke ClientError: {ce}")
        record_scrape_result(domain, False)
        return build_response(502, {"status": "error", "error": f"Failed invoking engine Lambda: {str(ce)}"})
    except Exception as e:
        logger.exception(f"Unexpected invocation error: {e}")
        record_scrape_result(domain, False)
        return build_response(500, {"status": "error", "error": str(e)})


def handle_get_job(job_id: str) -> Dict[str, Any]:
    """Retrieves job status and result from DynamoDB."""
    if not DYNAMODB_TABLE:
        return build_response(500, {"error": "DYNAMODB_TABLE_NAME not configured"})

    try:
        res = dynamodb_client.get_item(
            TableName=DYNAMODB_TABLE,
            Key={"pk": {"S": f"JOB#{job_id}"}, "sk": {"S": "STATUS"}},
        )
        item = res.get("Item")
        if not item:
            return build_response(404, {"error": f"Job '{job_id}' not found"})

        status_val = item.get("status", {}).get("S", "unknown")
        engine_val = item.get("engine", {}).get("S", "unknown")
        duration_val = int(item.get("duration_ms", {}).get("N", "0"))

        return build_response(
            200,
            {
                "job_id": job_id,
                "status": status_val,
                "engine_used": engine_val,
                "duration_ms": duration_val,
            },
        )
    except Exception as e:
        logger.error(f"Failed to fetch job {job_id}: {e}")
        return build_response(500, {"error": f"Failed to retrieve job: {str(e)}"})


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main HTTP API Gateway routing entrypoint."""
    logger.info("Dispatcher event received.")

    # Check HTTP method and path
    http_context = event.get("requestContext", {}).get("http", {})
    http_method = http_context.get("method") or event.get("httpMethod", "POST")
    raw_path = event.get("rawPath") or event.get("path", "/scrape")

    # CORS preflight
    if http_method == "OPTIONS":
        return build_response(200, {"message": "OK"})

    if raw_path == "/capabilities" and http_method == "GET":
        return build_response(200, CAPABILITIES)

    if raw_path.startswith("/jobs/") and http_method == "GET":
        job_id = raw_path.split("/")[-1]
        return handle_get_job(job_id)

    if raw_path == "/scrape" and http_method == "POST":
        body_raw = event.get("body", "{}")
        if event.get("isBase64Encoded", False):
            import base64
            body_raw = base64.b64decode(body_raw).decode("utf-8")

        try:
            body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
        except Exception:
            return build_response(400, {"error": "Invalid JSON in request body"})

        return handle_scrape_request(body)

    return build_response(404, {"error": f"Route not found: {http_method} {raw_path}"})
