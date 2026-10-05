from schemas.models import ScrapeRequest, EngineType, OutputFormat, ScrapeOptions
from schemas.capabilities import (
    validate_request_capabilities,
    get_supported_engines,
    CAPABILITIES_MANIFEST,
)


def test_supported_engines():
    engines = get_supported_engines()
    assert "playwright" in engines
    assert "nodriver" in engines
    assert "selenium" in engines


def test_valid_playwright_request():
    req = ScrapeRequest(
        url="https://example.com",
        engine=EngineType.PLAYWRIGHT,
        output=OutputFormat.SCREENSHOT,
        options=ScrapeOptions(stealth=True, proxy="http://user:pass@proxy:8080"),
    )
    is_valid, err = validate_request_capabilities(req)
    assert is_valid is True
    assert err == ""


def test_nodriver_authenticated_proxy_rejected():
    req = ScrapeRequest(
        url="https://example.com",
        engine=EngineType.NODRIVER,
        options=ScrapeOptions(proxy="http://user:secret@proxy.com:8080"),
    )
    is_valid, err = validate_request_capabilities(req)
    assert is_valid is False
    assert "authenticated proxies" in err
