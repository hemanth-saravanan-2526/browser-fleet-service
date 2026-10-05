import base64
import json
import logging
import os
import shutil
import sys
import time
import uuid
from typing import Any, Dict, Optional

import boto3
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Error as PlaywrightError
from extractor import DOM_EXTRACTOR_JS, records_to_excel_bytes

# Configure structured logging
logger = logging.getLogger("browser-fleet.playwright")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)

S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
S3_CLIENT = boto3.client("s3") if S3_BUCKET_NAME else None
PAYLOAD_SIZE_LIMIT_BYTES = 4 * 1024 * 1024  # 4MB threshold for S3 offload

def get_chromium_args():
    return [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-zygote",
        "--homedir=/tmp",
        "--disk-cache-dir=/tmp/chromium-cache",
    ]


def cleanup_tmp():
    """Cleans up temporary Chromium cache files to prevent /tmp saturation across warm invocations."""
    try:
        cache_dir = "/tmp/chromium-cache"
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:
        pass


def upload_to_s3(key: str, data: bytes, content_type: str) -> Optional[str]:
    """Uploads large artifacts to S3 and returns a presigned URL valid for 24 hours."""
    if not S3_CLIENT or not S3_BUCKET_NAME:
        logger.warning("S3_BUCKET_NAME not configured; cannot upload large payload to S3.")
        return None
    try:
        S3_CLIENT.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        url = S3_CLIENT.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": key},
            ExpiresIn=86400,
        )
        return url
    except Exception as e:
        logger.error(f"Failed to upload artifact to S3: {e}")
        return None


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entrypoint handler using synchronous Playwright API."""
    start_time = time.time()
    job_id = event.get("job_id", str(uuid.uuid4()))
    url = event.get("url")
    output_format = event.get("output", "html").lower()
    options = event.get("options", {})

    timeout_ms = options.get("timeout_ms", 30000)
    wait_for = options.get("wait_for")
    proxy_url = options.get("proxy")
    headers = options.get("headers", {})
    stealth = options.get("stealth", True)

    result_data: Dict[str, Any] = {}
    error_msg = None
    status = "success"

    browser: Optional[Browser] = None
    try:
        with sync_playwright() as p:
            launch_args = get_chromium_args()
            executable_path = os.environ.get("CHROMIUM_PATH")

            launch_kwargs = {
                "headless": True,
                "args": launch_args,
            }
            if executable_path and os.path.exists(executable_path):
                launch_kwargs["executable_path"] = executable_path

            logger.info(f"Launching Chromium (job_id: {job_id})...")
            browser = p.chromium.launch(**launch_kwargs)

            context_options: Dict[str, Any] = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "viewport": {"width": 1920, "height": 1080},
                "java_script_enabled": True,
                "ignore_https_errors": True,
            }

            if headers:
                context_options["extra_http_headers"] = headers

            if proxy_url:
                context_options["proxy"] = {"server": proxy_url}

            browser_context: BrowserContext = browser.new_context(**context_options)
            page: Page = browser_context.new_page()

            if stealth:
                page.add_init_script(
                    """
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                    window.chrome = { runtime: {} };
                    """
                )

            logger.info(f"Navigating to {url} (timeout: {timeout_ms}ms)...")
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

            if wait_for:
                if str(wait_for).isdigit():
                    delay_ms = int(wait_for)
                    logger.info(f"Waiting for explicit timeout: {delay_ms}ms")
                    page.wait_for_timeout(delay_ms)
                else:
                    logger.info(f"Waiting for selector: '{wait_for}'")
                    page.wait_for_selector(wait_for, timeout=min(timeout_ms, 15000))

            extract_schema = options.get("extract")
            extracted_data = None

            if extract_schema:
                logger.info(f"Executing selector-based extraction with schema: {extract_schema}")
                extracted_data = page.evaluate(f"({DOM_EXTRACTOR_JS})({json.dumps(extract_schema)})")
                result_data["data"] = extracted_data

            if output_format == "html":
                html_content = page.content()
                data_bytes = html_content.encode("utf-8")
                if len(data_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                    s3_key = f"results/{job_id}/page.html"
                    s3_url = upload_to_s3(s3_key, data_bytes, "text/html")
                    result_data["s3_url"] = s3_url
                else:
                    result_data["html"] = html_content

            elif output_format == "text":
                text_content = page.evaluate("() => document.body ? document.body.innerText : ''")
                result_data["text"] = text_content

            elif output_format == "screenshot":
                screenshot_bytes = page.screenshot(full_page=True)
                if len(screenshot_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                    s3_key = f"results/{job_id}/screenshot.png"
                    s3_url = upload_to_s3(s3_key, screenshot_bytes, "image/png")
                    result_data["s3_url"] = s3_url
                else:
                    result_data["screenshot"] = base64.b64encode(screenshot_bytes).decode("utf-8")

            elif output_format == "json":
                if extracted_data is not None:
                    result_data["json_data"] = extracted_data
                else:
                    meta = page.evaluate(
                        """() => ({
                            title: document.title,
                            url: window.location.href,
                            meta: Array.from(document.querySelectorAll('meta')).map(m => ({
                                name: m.getAttribute('name') || m.getAttribute('property'),
                                content: m.getAttribute('content')
                            })).filter(m => m.name && m.content)
                        })"""
                    )
                    result_data["json_data"] = meta

            elif output_format == "excel":
                if extracted_data is None:
                    # Generic fallback: extract tables if any, else page metadata
                    extracted_data = [{"url": url, "title": page.title(), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}]
                excel_bytes = records_to_excel_bytes(extracted_data, sheet_title="Scraped Data")
                if len(excel_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                    s3_key = f"results/{job_id}/data.xlsx"
                    s3_url = upload_to_s3(s3_key, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    result_data["s3_url"] = s3_url
                else:
                    result_data["excel"] = base64.b64encode(excel_bytes).decode("utf-8")

    except PlaywrightError as pe:
        if "Timeout" in str(pe):
            status = "timeout"
            error_msg = f"Operation timed out after {timeout_ms}ms"
        else:
            status = "error"
            error_msg = f"Playwright error: {str(pe)}"
        logger.error(f"Error scraping {url}: {error_msg}")
    except Exception as e:
        status = "error"
        error_msg = f"Unexpected error: {str(e)}"
        logger.exception(f"Unexpected error scraping {url}: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        cleanup_tmp()

    duration_ms = int((time.time() - start_time) * 1000)

    response = {
        "job_id": job_id,
        "status": status,
        "engine_used": "playwright",
        "duration_ms": duration_ms,
        "result": result_data,
        "error": error_msg,
    }
    return response
