import asyncio
import base64
import logging
import os
import shutil
import sys
import time
import uuid
from typing import Any, Dict, Optional

import boto3
import nodriver as uc
from extractor import DOM_EXTRACTOR_JS, records_to_excel_bytes

logger = logging.getLogger("browser-fleet.nodriver")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)

S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
S3_CLIENT = boto3.client("s3") if S3_BUCKET_NAME else None
PAYLOAD_SIZE_LIMIT_BYTES = 4 * 1024 * 1024


def find_chrome_binary() -> Optional[str]:
    """Finds the Chromium executable installed in the container."""
    base_dir = os.environ.get("CHROMIUM_PATH", "/opt/chrome")
    if os.path.exists(base_dir):
        for root, dirs, files in os.walk(base_dir):
            if "chrome" in files:
                candidate = os.path.join(root, "chrome")
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
    return None


def upload_to_s3(key: str, data: bytes, content_type: str) -> Optional[str]:
    if not S3_CLIENT or not S3_BUCKET_NAME:
        return None
    try:
        S3_CLIENT.put_object(Bucket=S3_BUCKET_NAME, Key=key, Body=data, ContentType=content_type)
        return S3_CLIENT.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": key},
            ExpiresIn=86400,
        )
    except Exception as e:
        logger.error(f"Failed to upload to S3: {e}")
        return None


async def execute_scrape(event: Dict[str, Any]) -> Dict[str, Any]:
    start_time = time.time()
    job_id = event.get("job_id", str(uuid.uuid4()))
    url = event.get("url")
    output_format = event.get("output", "html").lower()
    options = event.get("options", {})

    timeout_ms = options.get("timeout_ms", 30000)
    wait_for = options.get("wait_for")
    proxy_url = options.get("proxy")

    chrome_bin = find_chrome_binary()
    logger.info(f"Using Chrome binary: {chrome_bin}")

    browser_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-zygote",
        "--homedir=/tmp",
        "--disk-cache-dir=/tmp/nodriver-cache",
    ]
    if proxy_url:
        browser_args.append(f"--proxy-server={proxy_url}")

    user_data_dir = f"/tmp/nodriver-profile-{job_id}"

    result_data: Dict[str, Any] = {}
    error_msg = None
    status = "success"
    browser = None

    try:
        logger.info(f"Launching nodriver CDP browser (job_id: {job_id})...")
        browser = await uc.start(
            headless=True,
            sandbox=False,
            browser_executable_path=chrome_bin,
            browser_args=browser_args,
            user_data_dir=user_data_dir,
        )

        logger.info(f"Navigating to {url}...")
        page = await browser.get(url)
        try:
            await page.select("body", timeout=10.0)
        except Exception:
            pass

        if wait_for:
            if str(wait_for).isdigit():
                await asyncio.sleep(int(wait_for) / 1000.0)
            else:
                await page.select(wait_for, timeout=min(timeout_ms / 1000.0, 15.0))

        extract_schema = options.get("extract")
        extracted_data = None
        if extract_schema:
            logger.info(f"Executing selector extraction in nodriver: {extract_schema}")
            eval_res = await page.evaluate(f"({DOM_EXTRACTOR_JS})({json.dumps(extract_schema)})", return_by_value=True)
            if hasattr(eval_res, "value"):
                extracted_data = eval_res.value
            else:
                extracted_data = eval_res
            result_data["data"] = extracted_data

        if output_format == "html":
            html_content = await page.get_content()
            data_bytes = html_content.encode("utf-8")
            if len(data_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                s3_key = f"results/{job_id}/page.html"
                result_data["s3_url"] = upload_to_s3(s3_key, data_bytes, "text/html")
            else:
                result_data["html"] = html_content

        elif output_format == "text":
            eval_res = await page.evaluate("document.body.innerText", return_by_value=True)
            if hasattr(eval_res, "value") and eval_res.value is not None:
                text_content = eval_res.value
            else:
                text_content = str(eval_res) if eval_res else ""
            result_data["text"] = text_content

        elif output_format == "screenshot":
            screenshot_path = f"/tmp/screenshot_{job_id}.png"
            await page.save_screenshot(screenshot_path)
            with open(screenshot_path, "rb") as f:
                screenshot_bytes = f.read()
            if len(screenshot_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                s3_key = f"results/{job_id}/screenshot.png"
                result_data["s3_url"] = upload_to_s3(s3_key, screenshot_bytes, "image/png")
            else:
                result_data["screenshot"] = base64.b64encode(screenshot_bytes).decode("utf-8")
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)

        elif output_format == "json":
            if extracted_data is not None:
                result_data["json_data"] = extracted_data
            else:
                meta = await page.evaluate(
                    """({
                        title: document.title,
                        url: window.location.href,
                        meta: Array.from(document.querySelectorAll('meta')).map(m => ({
                            name: m.getAttribute('name') || m.getAttribute('property'),
                            content: m.getAttribute('content')
                        })).filter(m => m.name && m.content)
                    })""",
                    return_by_value=True,
                )
                if hasattr(meta, "value"):
                    meta = meta.value
                result_data["json_data"] = meta

        elif output_format == "excel":
            if extracted_data is None:
                title_res = await page.evaluate("document.title", return_by_value=True)
                title = title_res.value if hasattr(title_res, "value") else str(title_res)
                extracted_data = [{"url": url, "title": title, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}]
            excel_bytes = records_to_excel_bytes(extracted_data, sheet_title="Scraped Data")
            if len(excel_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                s3_key = f"results/{job_id}/data.xlsx"
                result_data["s3_url"] = upload_to_s3(s3_key, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                result_data["excel"] = base64.b64encode(excel_bytes).decode("utf-8")

    except asyncio.TimeoutError:
        status = "timeout"
        error_msg = f"Operation timed out after {timeout_ms}ms"
    except Exception as e:
        status = "error"
        error_msg = f"nodriver error: {str(e)}"
        logger.exception(f"Error scraping {url}: {e}")
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass
        shutil.rmtree(user_data_dir, ignore_errors=True)
        shutil.rmtree("/tmp/nodriver-cache", ignore_errors=True)

    duration_ms = int((time.time() - start_time) * 1000)
    return {
        "job_id": job_id,
        "status": status,
        "engine_used": "nodriver",
        "duration_ms": duration_ms,
        "result": result_data,
        "error": error_msg,
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logger.info("nodriver Lambda invocation started.")
    return uc.loop().run_until_complete(execute_scrape(event))
