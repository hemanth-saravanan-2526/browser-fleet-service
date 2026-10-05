import pytest
from schemas.models import (
    ScrapeRequest,
    ScrapeResponse,
    ScrapeOptions,
    EngineType,
    OutputFormat,
    JobStatus,
)


def test_scrape_request_defaults():
    req = ScrapeRequest(url="https://example.com")
    assert req.url == "https://example.com"
    assert req.engine == EngineType.PLAYWRIGHT
    assert req.output == OutputFormat.HTML
    assert req.options.stealth is True
    assert req.options.timeout_ms == 30000
    assert req.async_mode is False


def test_scrape_request_invalid_url():
    with pytest.raises(ValueError, match="URL must start with http:// or https://"):
        ScrapeRequest(url="ftp://invalid.com")


def test_scrape_options_timeout_bounds():
    with pytest.raises(ValueError):
        ScrapeOptions(timeout_ms=500)  # ge=1000 violated

    with pytest.raises(ValueError):
        ScrapeOptions(timeout_ms=150000)  # le=120000 violated


def test_scrape_response_serialization():
    resp = ScrapeResponse(
        job_id="test-123",
        status=JobStatus.SUCCESS,
        engine_used=EngineType.NODRIVER,
        duration_ms=1250,
        result={"html": "<html><body>Hello</body></html>"},
    )
    dumped = resp.model_dump()
    assert dumped["job_id"] == "test-123"
    assert dumped["status"] == "success"
    assert dumped["engine_used"] == "nodriver"
    assert dumped["duration_ms"] == 1250
    assert dumped["result"]["html"] == "<html><body>Hello</body></html>"
