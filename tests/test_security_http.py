import requests
from trunk_uploader.http import classify
from trunk_uploader.security import redact


def response(status):
    r = requests.Response(); r.status_code = status; return r


def test_redaction():
    assert "super-secret" not in redact("Authorization: Bearer super-secret api_key=super-secret")
    assert "[REDACTED]" in redact("Bearer super-secret")


def test_retry_classification():
    assert classify(response(202)).success
    assert classify(response(500)).retryable
    assert classify(response(429)).retryable
    assert not classify(response(401)).retryable
    assert classify(error=TimeoutError()).retryable


def test_permanent_4xx_classification():
    assert not classify(response(400)).retryable
    assert not classify(response(401)).retryable
    assert not classify(response(404)).retryable
