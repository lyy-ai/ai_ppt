#!/usr/bin/env python3
"""Small security helpers for Cloud Generator runtime logs."""

from __future__ import annotations

import os
import re


SECRET_ENV_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "ACCESS")


def redact_secrets(text: str) -> str:
    """Best-effort redaction for logs and surfaced subprocess errors."""
    value = str(text or "")
    for env_key, env_value in os.environ.items():
        if not env_value or len(env_value) < 8:
            continue
        if any(marker in env_key.upper() for marker in SECRET_ENV_MARKERS):
            value = value.replace(env_value, _mask(env_key))
    patterns = [
        (r"(Authorization\s*[:=]\s*Bearer\s+)[A-Za-z0-9._\-+/=]{12,}", r"\1[REDACTED]"),
        (r"(Bearer\s+)[A-Za-z0-9._\-+/=]{12,}", r"\1[REDACTED]"),
        (r"\bsk-[A-Za-z0-9._\-]{12,}", "sk-[REDACTED]"),
        (r"\bsk-or-v1-[A-Za-z0-9._\-]{12,}", "sk-or-v1-[REDACTED]"),
        (r"([?&](?:token|access_token|api_key|key|secret)=)[^&\s]+", r"\1[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


def _mask(env_key: str) -> str:
    return f"[REDACTED:{env_key}]"
