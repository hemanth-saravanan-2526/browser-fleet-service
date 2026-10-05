import argparse
import base64
import json
import os
import sys
import time
import re
import ast
import subprocess
import urllib.request
import urllib.error

# Ensure Windows terminal handles international characters and currency symbols cleanly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CLOUD_ENDPOINT = "https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com/scrape"
LOCAL_ENDPOINT = "http://localhost:9000/2015-03-31/functions/function/invocations"


def ensure_container_running(engine: str = "playwright") -> str:
    """Ensures the appropriate Docker container is running locally on port 9000."""
    container_name = f"browser-fleet-{engine}-live"
    image_name = f"browser-fleet-{engine}:test"

    # Check if this exact engine is already running
    try:
        inspect_proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        if inspect_proc.stdout.strip() == "true":
            return container_name
    except Exception:
        pass

    # Stop any other fleet container currently occupying port 9000
    for name in ["browser-fleet-playwright-live", "browser-fleet-nodriver-live", "browser-fleet-selenium-live", container_name]:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    print(f"[*] Starting local {engine.upper()} engine container ({image_name})...")
    run_proc = subprocess.run(
        ["docker", "run", "-d", "--name", container_name, "-p", "9000:8080", image_name],
        capture_output=True,
        text=True,
    )
    if run_proc.returncode != 0:
        print(f"[!] Error starting container: {run_proc.stderr}")
        sys.exit(1)

    # Allow container RIE to initialize
    time.sleep(2)
    return container_name


def scrape(url: str, engine: str = "playwright", output_format: str = "text", wait_for: str = None, extract: dict = None, endpoint: str = None, local: bool = False) -> dict:
    target_endpoint = endpoint
    if not target_endpoint:
        if local:
            ensure_container_running(engine)
            target_endpoint = LOCAL_ENDPOINT
        else:
            target_endpoint = CLOUD_ENDPOINT

    payload = {
        "url": url,
        "engine": engine,
        "output": output_format,
        "options": {
            "stealth": True,
            "timeout_ms": 45000,
        },
    }
    if wait_for:
        payload["options"]["wait_for"] = wait_for
    if extract:
        payload["options"]["extract"] = extract

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        target_endpoint,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
    )

    mode_label = "LOCAL DOCKER" if "localhost" in target_endpoint else "AWS CLOUD FLEET"
    print(f"\n[>] Scraping '{url}' via {mode_label} [{engine.upper()}] (output: {output_format})...")
    start_t = time.time()

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_response = resp.read().decode("utf-8")
            result = json.loads(raw_response)
    except urllib.error.URLError as e:
        print(f"[!] Network error contacting container: {e}")
        sys.exit(1)

    total_time = int((time.time() - start_t) * 1000)

    # Ensure output directory exists
    os.makedirs("output", exist_ok=True)

    status = result.get("status", "unknown")
    engine_used = result.get("engine_used", engine)
    duration_ms = result.get("duration_ms", total_time)
    res_data = result.get("result", {})

    print(f"\n{'='*60}")
    print(f"Status:       {status.upper()}")
    print(f"Engine Used:  {engine_used}")
    print(f"Duration:     {duration_ms} ms (Total roundtrip: {total_time} ms)")
    if status != "success":
        print(f"Error:        {result.get('error')}")
    print(f"{'='*60}\n")

    if output_format == "text":
        text_content = res_data.get("text", "")
        file_path = os.path.join("output", "scraped_text.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text_content)
        print("--- EXTRACTED TEXT PREVIEW (First 500 chars) ---")
        print(text_content[:500] + ("..." if len(text_content) > 500 else ""))
        print(f"\n[OK] Full text saved to: {file_path}")

    elif output_format == "html":
        html_content = res_data.get("html", "")
        s3_url = res_data.get("s3_url")
        file_path = os.path.join("output", "scraped_page.html")
        if html_content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"--- EXTRACTED HTML ({len(html_content)} bytes) ---")
            print(f"[OK] Full HTML saved to: {file_path}")
        elif s3_url:
            print(f"[*] Large HTML stored in Amazon S3, fetching presigned URL...")
            urllib.request.urlretrieve(s3_url, file_path)
            print(f"[OK] Full HTML downloaded ({os.path.getsize(file_path)} bytes): {file_path}")
            print(f"    S3 Artifact: {s3_url}")

    elif output_format == "json":
        data_records = res_data.get("data") or res_data.get("json_data", {})
        file_path = os.path.join("output", "scraped_data.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data_records, f, indent=2)
        print("--- EXTRACTED STRUCTURED JSON ---")
        if isinstance(data_records, list):
            print(f"Total Records Extracted: {len(data_records)}\n")
            for i, item in enumerate(data_records[:5], 1):
                print(f"[{i}] {json.dumps(item, ensure_ascii=False)}")
            if len(data_records) > 5:
                print(f"... and {len(data_records) - 5} more records")
        else:
            print(json.dumps(data_records, indent=2))
        print(f"\n[OK] Saved JSON to: {file_path}")

    elif output_format == "excel":
        excel_b64 = res_data.get("excel", "")
        data_records = res_data.get("data")
        if excel_b64:
            excel_bytes = base64.b64decode(excel_b64)
            file_path = os.path.join("output", "scraped_data.xlsx")
            with open(file_path, "wb") as f:
                f.write(excel_bytes)
            print(f"--- EXCEL SPREADSHEET GENERATED ({len(excel_bytes)} bytes) ---")
            if data_records and isinstance(data_records, list):
                print(f"Total Rows Saved: {len(data_records)}")
                # Show first 3 rows as text table preview
                headers = list(data_records[0].keys()) if data_records else []
                print(f"Columns: {', '.join(headers)}")
            print(f"\n[OK] Excel spreadsheet saved to: {file_path}")

    elif output_format == "screenshot":
        b64_img = res_data.get("screenshot", "")
        s3_url = res_data.get("s3_url")
        file_path = os.path.join("output", "scraped_screenshot.png")
        if b64_img:
            img_bytes = base64.b64decode(b64_img)
            with open(file_path, "wb") as f:
                f.write(img_bytes)
            print(f"[OK] Full-page screenshot saved ({len(img_bytes)} bytes): {file_path}")
        elif s3_url:
            print(f"[*] Large screenshot stored in Amazon S3, fetching presigned URL...")
            urllib.request.urlretrieve(s3_url, file_path)
            print(f"[OK] Full-page screenshot downloaded ({os.path.getsize(file_path)} bytes): {file_path}")
            print(f"    S3 Artifact: {s3_url}")

    return result



def parse_extract_schema(extract_arg: str) -> dict:
    """Parses JSON schema from string, file path, or PowerShell-formatted CLI arguments."""
    if not extract_arg:
        return None
    # 1. If it's a file path, load from file
    if os.path.isfile(extract_arg):
        with open(extract_arg, "r", encoding="utf-8") as f:
            return json.load(f)
    # 2. Try standard json.loads
    try:
        return json.loads(extract_arg)
    except Exception:
        pass
    # 3. Try ast.literal_eval for Python dictionary syntax
    try:
        val = ast.literal_eval(extract_arg)
        if isinstance(val, dict):
            return val
    except Exception:
        pass
    # 4. Handle PowerShell-stripped unquoted key-values: {_item: ..., title: ...}
    clean = extract_arg.strip()
    if clean.startswith("{") and clean.endswith("}"):
        clean = clean[1:-1].strip()
    result = {}
    parts = re.split(r",\s*(?=[a-zA-Z_][a-zA-Z0-9_]*\s*:)", clean)
    for p in parts:
        if ":" in p:
            k, v = p.split(":", 1)
            result[k.strip().strip("'\"")] = v.strip().strip("'\"")
    if result:
        return result
    raise ValueError(f"Could not parse extraction schema: {extract_arg}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Browser Fleet CLI Scraper")
    parser.add_argument("url", nargs="?", default="https://quotes.toscrape.com", help="Target URL to scrape")
    parser.add_argument("--engine", choices=["playwright", "nodriver", "selenium"], default="playwright", help="Browser engine")
    parser.add_argument("--output", choices=["text", "html", "screenshot", "json", "excel"], default="text", help="Output format")
    parser.add_argument("--extract", default=None, help="JSON extraction schema or path to schema .json file")
    parser.add_argument("--wait", default=None, help="CSS selector or ms delay to wait for")

    parser.add_argument("--endpoint", default=None, help="Custom API endpoint (defaults to cloud API Gateway)")
    parser.add_argument("--local", action="store_true", help="Run against local Docker container instead of AWS cloud")

    args = parser.parse_args()

    extract_dict = None
    if args.extract:
        try:
            extract_dict = parse_extract_schema(args.extract)
        except Exception as e:
            print(f"[!] Invalid extraction schema in --extract: {e}")
            sys.exit(1)


    scrape(
        url=args.url,
        engine=args.engine,
        output_format=args.output,
        wait_for=args.wait,
        extract=extract_dict,
        endpoint=args.endpoint,
        local=args.local,
    )

