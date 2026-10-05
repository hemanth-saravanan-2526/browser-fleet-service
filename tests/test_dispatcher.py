import json
import pytest
from dispatcher.handler import handler, build_response


def test_cors_preflight():
    event = {
        "requestContext": {"http": {"method": "OPTIONS"}},
        "rawPath": "/scrape",
    }
    resp = handler(event, None)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"


def test_get_capabilities():
    event = {
        "requestContext": {"http": {"method": "GET"}},
        "rawPath": "/capabilities",
    }
    resp = handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "playwright" in body
    assert "nodriver" in body
    assert "selenium" in body


def test_invalid_scrape_url():
    event = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/scrape",
        "body": json.dumps({"url": "invalid-url", "engine": "playwright"}),
    }
    resp = handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert "error" in body


def test_unsupported_engine():
    event = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/scrape",
        "body": json.dumps({"url": "https://example.com", "engine": "unsupported-browser"}),
    }
    resp = handler(event, None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert "Unsupported engine" in body["error"]
