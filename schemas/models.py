from enum import Enum
from typing import Dict, Any, Optional, Union, List
import uuid
from pydantic import BaseModel, Field, HttpUrl, field_validator


class EngineType(str, Enum):
    PLAYWRIGHT = "playwright"
    NODRIVER = "nodriver"
    SELENIUM = "selenium"


class OutputFormat(str, Enum):
    HTML = "html"
    TEXT = "text"
    SCREENSHOT = "screenshot"
    JSON = "json"
    EXCEL = "excel"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class ScrapeOptions(BaseModel):
    wait_for: Optional[str] = Field(
        default=None,
        description="CSS selector to wait for, or stringified integer ms timeout (e.g. '5000' or '#content')",
    )
    extract: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Extraction schema mapping field names to CSS selectors, e.g. {'_item': '.quote', 'quote': '.text', 'author': '.author'}",
    )
    proxy: Optional[str] = Field(
        default=None,
        description="Proxy URL (e.g. 'http://username:password@proxy.example.com:8080')",
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Custom HTTP headers to include with the request",
    )
    stealth: bool = Field(
        default=True,
        description="Whether to enable anti-detection measures (e.g., evasion plugins, user-agent randomization)",
    )
    timeout_ms: int = Field(
        default=30000,
        ge=1000,
        le=120000,
        description="Max execution timeout in milliseconds (between 1s and 120s)",
    )


class ScrapeRequest(BaseModel):
    url: str = Field(..., description="Target URL to scrape")
    engine: Optional[EngineType] = Field(
        default=EngineType.PLAYWRIGHT,
        description="Browser engine to use (playwright | nodriver | selenium)",
    )
    output: OutputFormat = Field(
        default=OutputFormat.HTML,
        description="Desired output format (html | text | screenshot | json)",
    )
    options: ScrapeOptions = Field(
        default_factory=ScrapeOptions,
        description="Engine options and parameters",
    )
    async_mode: bool = Field(
        default=False,
        description="If True, returns a job_id immediately and processes via SQS queue (Option B)",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class ScrapeResult(BaseModel):
    html: Optional[str] = None
    text: Optional[str] = None
    screenshot: Optional[str] = Field(
        default=None,
        description="Base64-encoded image string if small, or omitted if stored in S3",
    )
    json_data: Optional[Dict[str, Any]] = None
    data: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = Field(
        default=None,
        description="Structured extracted records from selector-based extraction",
    )
    excel: Optional[str] = Field(
        default=None,
        description="Base64-encoded .xlsx file string if small, or omitted if stored in S3",
    )
    s3_url: Optional[str] = Field(
        default=None,
        description="Presigned S3 URL for large payload delivery",
    )


class ScrapeResponse(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.SUCCESS
    engine_used: EngineType = EngineType.PLAYWRIGHT
    duration_ms: int = Field(default=0, ge=0)
    result: Optional[ScrapeResult] = None
    error: Optional[str] = None
