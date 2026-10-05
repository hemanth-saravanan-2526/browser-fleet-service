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
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from extractor import DOM_EXTRACTOR_JS, records_to_excel_bytes

logger = logging.getLogger("browser-fleet.selenium")
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


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    start_time = time.time()
    job_id = event.get("job_id", str(uuid.uuid4()))
    url = event.get("url")
    output_format = event.get("output", "html").lower()
    options = event.get("options", {})

    timeout_ms = options.get("timeout_ms", 30000)
    wait_for = options.get("wait_for")
    proxy_url = options.get("proxy")

    chrome_bin = find_chrome_binary()
    logger.info(f"Using Chrome binary for Selenium: {chrome_bin}")

    user_data_dir = f"/tmp/selenium-profile-{job_id}"

    chrome_options = Options()
    if chrome_bin:
        chrome_options.binary_location = chrome_bin

    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--homedir=/tmp")
    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
    chrome_options.add_argument("--disk-cache-dir=/tmp/selenium-cache")

    if proxy_url:
        chrome_options.add_argument(f"--proxy-server={proxy_url}")

    driver = None
    result_data: Dict[str, Any] = {}
    error_msg = None
    status = "success"

    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(timeout_ms / 1000.0)

        logger.info(f"Navigating to {url} (job_id: {job_id})...")
        driver.get(url)

        if wait_for:
            if str(wait_for).isdigit():
                time.sleep(int(wait_for) / 1000.0)
            else:
                WebDriverWait(driver, min(timeout_ms / 1000.0, 15.0)).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_for))
                )

        extract_schema = options.get("extract")
        extracted_data = None
        if extract_schema:
            logger.info(f"Executing selector extraction in Selenium: {extract_schema}")
            extracted_data = driver.execute_script(f"return ({DOM_EXTRACTOR_JS})({json.dumps(extract_schema)});")
            result_data["data"] = extracted_data

        if output_format == "html":
            html_content = driver.page_source
            data_bytes = html_content.encode("utf-8")
            if len(data_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                s3_key = f"results/{job_id}/page.html"
                result_data["s3_url"] = upload_to_s3(s3_key, data_bytes, "text/html")
            else:
                result_data["html"] = html_content

        elif output_format == "text":
            body = driver.find_element(By.TAG_NAME, "body")
            result_data["text"] = body.text if body else ""

        elif output_format == "screenshot":
            screenshot_bytes = driver.get_screenshot_as_png()
            if len(screenshot_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                s3_key = f"results/{job_id}/screenshot.png"
                result_data["s3_url"] = upload_to_s3(s3_key, screenshot_bytes, "image/png")
            else:
                result_data["screenshot"] = base64.b64encode(screenshot_bytes).decode("utf-8")

        elif output_format == "json":
            if extracted_data is not None:
                result_data["json_data"] = extracted_data
            else:
                meta = driver.execute_script(
                    """return {
                        title: document.title,
                        url: window.location.href,
                        meta: Array.from(document.querySelectorAll('meta')).map(m => ({
                            name: m.getAttribute('name') || m.getAttribute('property'),
                            content: m.getAttribute('content')
                        })).filter(m => m.name && m.content)
                    };"""
                )
                result_data["json_data"] = meta

        elif output_format == "excel":
            if extracted_data is None:
                extracted_data = [{"url": url, "title": driver.title, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}]
            excel_bytes = records_to_excel_bytes(extracted_data, sheet_title="Scraped Data")
            if len(excel_bytes) > PAYLOAD_SIZE_LIMIT_BYTES:
                s3_key = f"results/{job_id}/data.xlsx"
                result_data["s3_url"] = upload_to_s3(s3_key, excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                result_data["excel"] = base64.b64encode(excel_bytes).decode("utf-8")

    except TimeoutException:
        status = "timeout"
        error_msg = f"Selenium page load timed out after {timeout_ms}ms"
    except WebDriverException as we:
        status = "error"
        error_msg = f"Selenium WebDriver error: {str(we)}"
        logger.error(f"WebDriver error for {url}: {we}")
    except Exception as e:
        status = "error"
        error_msg = f"Unexpected error: {str(e)}"
        logger.exception(f"Unexpected error for {url}: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        shutil.rmtree(user_data_dir, ignore_errors=True)
        shutil.rmtree("/tmp/selenium-cache", ignore_errors=True)

    duration_ms = int((time.time() - start_time) * 1000)
    return {
        "job_id": job_id,
        "status": status,
        "engine_used": "selenium",
        "duration_ms": duration_ms,
        "result": result_data,
        "error": error_msg,
    }
