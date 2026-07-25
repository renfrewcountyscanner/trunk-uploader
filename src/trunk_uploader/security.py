from __future__ import annotations

import re

SECRET_KEYS = re.compile(r"(api[_-]?key|authorization|token|password|secret|credential)", re.I)


def redact(value: object) -> str:
    text = str(value)
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|password|secret|key)=)[^\s&,;]+", r"\1[REDACTED]", text)
    return text


def redact_mapping(values: dict[str, object]) -> dict[str, object]:
    return {key: "[REDACTED]" if SECRET_KEYS.search(key) else redact(value) for key, value in values.items()}
