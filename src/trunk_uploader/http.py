from __future__ import annotations

import requests
from dataclasses import dataclass


@dataclass(frozen=True)
class ResponseResult:
    success: bool
    retryable: bool
    status: int | None
    error: str = ""


def classify(response: requests.Response | None = None, error: Exception | None = None) -> ResponseResult:
    if error is not None:
        msg = f"{type(error).__name__}: {str(error)[:500]}" if str(error) else type(error).__name__
        return ResponseResult(False, True, None, msg)
    assert response is not None
    if 200 <= response.status_code < 300: return ResponseResult(True, False, response.status_code)
    retryable = response.status_code in {408, 429} or 500 <= response.status_code <= 599
    body = response.text[:500] if response.text else ""
    return ResponseResult(False, retryable, response.status_code, f"HTTP {response.status_code} {body}".strip())
