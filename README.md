# Serverless Browser Fleet Service (`sos`)
### High-Performance, Multi-Engine Browser Automation on AWS

[![AWS CloudFormation](https://img.shields.io/badge/AWS-CloudFormation-orange.svg)](https://aws.amazon.com/cloudformation/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Engine-Playwright-green.svg)](https://playwright.dev/)
[![nodriver](https://img.shields.io/badge/Engine-nodriver-brightgreen.svg)](https://github.com/ultrafunkamsterdam/nodriver)
[![Selenium](https://img.shields.io/badge/Engine-Selenium-blue.svg)](https://www.selenium.dev/)
[![Scale-to-Zero](https://img.shields.io/badge/Architecture-Scale--to--Zero-success.svg)](#)

---

## 1. What We Are Doing

The **Browser Fleet Service** is an enterprise-grade, serverless browser automation platform deployed on **Amazon Web Services (AWS)** using **AWS CDK (Python)**.

### The Problem It Solves:
1. **Running scrapers locally on a laptop:** Opening multiple real Chrome browsers consumes 100% of CPU/RAM, freezes the computer, and leaks your personal IP address to anti-bot protection.
2. **Running on 24/7 Virtual Machines (EC2 / VPS):** Traditional servers incur ongoing hourly costs ($20–$150/month) even when completely idle 95% of the day, and cannot scale up to 500 parallel browsers during sudden spikes without manual intervention.

### The Solution:
We packaged three browser automation engines (**Playwright**, **nodriver**, and **Selenium**) into isolated Docker container images hosted on AWS Lambda behind an Amazon API Gateway:
- **Idle Cost:** Exactly **$0.00** (true scale-to-zero; you only pay fractions of a cent per request).
- **Concurrency:** Automatically scales to hundreds of parallel browsers simultaneously.
- **Stealth & Protection:** Pre-configured with stealth evasion plugins and a domain circuit breaker to prevent bot bans.

---

## 2. System Architecture

```mermaid
flowchart TD
    Client["Client\n(scrape.py / cURL / Web App)"]
    APIGW["Amazon API Gateway (v2 HTTP API)\nhttps://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com"]
    Dispatcher["Dispatcher Lambda\n(Route Validator & Circuit Breaker)"]
    DDB[("Amazon DynamoDB\n(Domain Circuit Breaker & Job State)")]
    
    subgraph EngineFleet ["Isolated Browser Engines (AWS Lambda Containers)"]
        Playwright["Playwright Engine\n(Stealth Headless Chromium)"]
        Nodriver["nodriver Engine\n(Undetected Chrome via CDP)"]
        Selenium["Selenium Engine\n(WebDriver Automation)"]
    end
    
    Target["Target Website\n(e.g., Puma, Quotes, E-Commerce)"]
    S3[("Amazon S3 Bucket\n(Large Artifacts > 4MB)")]

    Client -->|"POST /scrape\n(JSON Payload)"| APIGW
    APIGW --> Dispatcher
    Dispatcher <-->|"Check / Update Failure Rate"| DDB
    Dispatcher -->|"Option A: Sync Direct Invoke"| Playwright
    Dispatcher -.->|"Option A: Sync Direct Invoke"| Nodriver
    Dispatcher -.->|"Option A: Sync Direct Invoke"| Selenium
    
    Playwright -->|"Render JS & Extract"| Target
    Nodriver -.->|"Render JS & Extract"| Target
    Selenium -.->|"Render JS & Extract"| Target
    
    Playwright -->|"Payload < 4MB: Direct JSON"| Dispatcher
    Playwright -->|"Payload > 4MB: Presigned URL"| S3
    Dispatcher -->|"HTTP 200 JSON Response"| APIGW
    APIGW -->|"Result + Excel/Data/Artifact"| Client
```

---

## 3. Why It Is Better

| Feature | Local Laptop Scraping | Traditional EC2 / VPS | Our Serverless Browser Fleet |
| :--- | :--- | :--- | :--- |
| **Idle Cost** | Free (uses your electricity) | **$20 – $150 / month** 24/7 | **$0.00** (Pay only per millisecond of use) |
| **Parallel Scaling** | Crashes after 5–10 tabs | Limited to VM CPU/RAM size | **Hundreds of parallel browsers instantly** |
| **Local Impact** | Laptop freezes, fans spin | None | **Zero impact on your laptop** |
| **Engine Redundancy**| Must install all 3 locally | Complex dependency conflicts | **3 independent, pre-built Docker containers** |
| **Bot Detection** | Personal IP gets banned | Datacenter IP gets banned | **Stealth plugins + Domain Circuit Breakers** |
| **Large File Handling**| Manual file saving | Manual disk management | **Automatic Amazon S3 presigned URL offload** |
| **Access Method** | Local terminal only | SSH or custom server API | **Universal HTTP API Gateway endpoint** |

---

## 4. API Reference

### Base URL:
```text
https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com
```

### Routes:

#### `GET /capabilities`
Returns the operational capabilities and supported output formats of each engine.
```bash
curl -s https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com/capabilities
```

#### `POST /scrape`
Executes an immediate synchronous scrape against any website.

**Request Schema:**
```json
{
  "url": "https://example.com",
  "engine": "playwright",
  "output": "text",
  "options": {
    "stealth": true,
    "timeout_ms": 45000,
    "wait_for": 3000,
    "proxy": "http://user:pass@host:port",
    "extract": {
      "_item": "CSS selector for item list",
      "fieldName": "CSS selector for text",
      "attributeName@attr": "CSS selector extracting HTML attribute"
    }
  }
}
```

---

## 5. Supported Output Formats

| Format | Output in Response | CLI Result File | Description |
| :--- | :--- | :--- | :--- |
| **`text`** | `result.text` | `output/scraped_text.txt` | Clean text content of the page. |
| **`html`** | `result.html` | `output/scraped_page.html` | Fully rendered DOM after JavaScript execution. |
| **`json`** | `result.data` | `output/scraped_data.json` | Array of structured objects extracted by CSS selectors. |
| **`excel`** | `result.excel` (Base64) | `output/scraped_data.xlsx` | Formatted Excel workbook ready to open in Excel. |
| **`screenshot`** | `result.screenshot` (Base64) | `output/scraped_screenshot.png`| Full-page high-resolution PNG render. |

*(For payloads larger than 4 MB, such as massive screenshots, `result.s3_url` is automatically returned with a 24-hour presigned download link).*

---

## 6. How to Use

### A. Python CLI Tool (`scrape.py`)

```powershell
# 1. Scrape text from Puma
python scrape.py https://in.puma.com --output text

# 2. Extract catalog to Excel using schema file
python scrape.py https://us.puma.com/us/en/men/shoes/running --output excel --wait 3000 --extract puma_schema.json

# 3. Capture a full-page screenshot
python scrape.py https://github.com --output screenshot

# 4. Extract structured JSON inline
python scrape.py https://quotes.toscrape.com --output json --extract '{"_item": ".quote", "quote": "span.text", "author": "small.author"}'
```

---

### B. Direct cURL Command (One-liner in PowerShell)

```powershell
curl.exe -s -X POST https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com/scrape -H "Content-Type: application/json" -d '{\"url\":\"https://us.puma.com/us/en/men/shoes/running\",\"engine\":\"playwright\",\"output\":\"text\"}'
```

---

### C. Native PowerShell Script (Save Directly to `.xlsx`)

```powershell
$body = @{
    url = "https://us.puma.com/us/en/men/shoes/running"
    engine = "playwright"
    output = "excel"
    options = @{
        wait_for = 3000
        extract = @{
            "_item" = "li[data-test-id='product-list-item']"
            "title" = "a[data-test-id='product-list-item-link']@aria-label"
            "price" = "[data-test-id='price']"
            "link"  = "a[data-test-id='product-list-item-link']@href"
        }
    }
} | ConvertTo-Json -Depth 5

$res = Invoke-RestMethod -Uri "https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com/scrape" -Method Post -Body $body -ContentType "application/json"
[IO.File]::WriteAllBytes("output/puma_shoes.xlsx", [Convert]::FromBase64String($res.result.excel))
Write-Host "[OK] Saved to output/puma_shoes.xlsx"
```

---

### D. Python `requests` (Microservice / Web App Integration)

```python
import base64
import requests

payload = {
    "url": "https://us.puma.com/us/en/men/shoes/running",
    "engine": "playwright",
    "output": "excel",
    "options": {
        "wait_for": 3000,
        "extract": {
            "_item": "li[data-test-id='product-list-item']",
            "title": "a[data-test-id='product-list-item-link']@aria-label",
            "price": "[data-test-id='price']",
            "link": "a[data-test-id='product-list-item-link']@href",
        },
    },
}

response = requests.post(
    "https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com/scrape",
    json=payload,
    headers={"Content-Type": "application/json"},
    timeout=60,
)

data = response.json()
if data.get("status") == "success":
    excel_bytes = base64.b64decode(data["result"]["excel"])
    with open("puma_products.xlsx", "wb") as f:
        f.write(excel_bytes)
    print("Excel file successfully downloaded!")
```

---

## 7. Cloud Infrastructure Details

Deployed in AWS Account `324608852572` (`us-east-1`):

1. **`BrowserFleet-Storage`**:
   - S3 Artifacts Bucket: `browserfleet-storage-browserfleetartifactsbucket36-ljckhwge7ksb`
   - DynamoDB Table: `BrowserFleet-Storage-BrowserFleetTable4B25B8BD-BMB95M6HQ3NE`
2. **`BrowserFleet-Queue`**:
   - SQS Job Queue: `https://sqs.us-east-1.amazonaws.com/324608852572/BrowserFleet-Queue-BrowserFleetJobsQueue41E37CEE-1Ylm52vrbikT`
   - Dead Letter Queue (DLQ) with 14-day retention.
3. **`BrowserFleet-Engines`**:
   - `browser-fleet-playwright` (Active, stealth Chromium)
   - `browser-fleet-nodriver` (Undetected Chrome via CDP)
   - `browser-fleet-selenium` (WebDriver compatibility)
4. **`BrowserFleet-Dispatcher`**:
   - HTTP API Gateway: `https://v7gqj1d1xg.execute-api.us-east-1.amazonaws.com`
