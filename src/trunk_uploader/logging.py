from __future__ import annotations

import logging
import sys
from .security import redact


class ContextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = " ".join(f"{key}={redact(getattr(record, key, '-'))}" for key in ("fingerprint", "profile", "destination_type", "destination"))
        return f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%SZ',) } level={record.levelname} {context} result={redact(record.getMessage())}"


def setup(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("trunk_uploader")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(ContextFormatter())
        logger.addHandler(handler)
    return logger


def extra(call_fp: str = "-", profile: str = "-", destination_type: str = "-", destination: str = "-") -> dict[str, str]:
    return {"fingerprint": call_fp, "profile": profile, "destination_type": destination_type, "destination": destination}
