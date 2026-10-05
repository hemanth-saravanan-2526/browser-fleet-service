from typing import Dict, Any, List, Tuple
from .models import EngineType, ScrapeRequest


CAPABILITIES_MANIFEST: Dict[str, Dict[str, Any]] = {
    EngineType.PLAYWRIGHT.value: {
        "description": "High-fidelity modern automation engine with rich stealth plugins and fast context reuse.",
        "supported_outputs": ["html", "text", "screenshot", "json", "excel"],
        "supports_stealth": True,
        "supports_custom_headers": True,
        "supports_proxy": True,
        "supports_proxy_auth": True,
        "supports_wait_for_selector": True,
        "supports_wait_for_timeout": True,
        "max_timeout_ms": 120000,
        "best_for": ["general_scraping", "complex_spa", "screenshots"],
    },
    EngineType.NODRIVER.value: {
        "description": "Pure CDP-based automation without webdriver instrumentation. High bypass capability for anti-bot defenses.",
        "supported_outputs": ["html", "text", "screenshot", "json", "excel"],
        "supports_stealth": True,
        "supports_custom_headers": True,
        "supports_proxy": True,
        "supports_proxy_auth": False,  # Chrome CDP limitation with basic auth without extension
        "supports_wait_for_selector": True,
        "supports_wait_for_timeout": True,
        "max_timeout_ms": 120000,
        "best_for": ["anti_bot_bypass", "cloudflare", "datadome", "akamai"],
    },
    EngineType.SELENIUM.value: {
        "description": "Standard headless WebDriver engine for legacy compatibility and standard QA test suites.",
        "supported_outputs": ["html", "text", "screenshot", "json", "excel"],
        "supports_stealth": False,  # Leaks webdriver flags easily
        "supports_custom_headers": True,
        "supports_proxy": True,
        "supports_proxy_auth": True,
        "supports_wait_for_selector": True,
        "supports_wait_for_timeout": True,
        "max_timeout_ms": 120000,
        "best_for": ["legacy_compatibility", "standard_headless"],
    },
}


def get_supported_engines() -> List[str]:
    return list(CAPABILITIES_MANIFEST.keys())


def validate_request_capabilities(request: ScrapeRequest) -> Tuple[bool, str]:
    """
    Validates whether the requested engine supports the specific options provided in the request.
    Returns (is_valid, error_message).
    """
    engine_name = request.engine.value if request.engine else EngineType.PLAYWRIGHT.value
    if engine_name not in CAPABILITIES_MANIFEST:
        return False, f"Unknown engine '{engine_name}'. Supported engines: {get_supported_engines()}"

    engine_caps = CAPABILITIES_MANIFEST[engine_name]

    # Validate output format
    if request.output.value not in engine_caps["supported_outputs"]:
        return False, f"Engine '{engine_name}' does not support output format '{request.output.value}'"

    # Validate proxy auth if provided
    if request.options.proxy and "@" in request.options.proxy:
        if not engine_caps.get("supports_proxy_auth", True):
            return (
                False,
                f"Engine '{engine_name}' does not support authenticated proxies. Use Playwright or Selenium instead.",
            )

    return True, ""
