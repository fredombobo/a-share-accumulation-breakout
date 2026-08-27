"""Small dependency-free redaction helpers for persistence and API boundaries."""
from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN_VALUE = r"[A-Za-z0-9_-]{20,}"
_PATTERNS = (
    # Vendor wording such as: token不对，您传过来的是<credential>请确认
    re.compile(rf"(?i)(token[^A-Za-z0-9_-]{{0,64}})({_TOKEN_VALUE})"),
    # Common env / JSON / log key-value forms.
    re.compile(
        rf"(?i)((?:access[_-]?token|tushare[_-]?token|api[_-]?key|token)"
        rf"\s*[\"']?\s*[:=]\s*[\"']?)({_TOKEN_VALUE})"
    ),
    re.compile(rf"(?i)(bearer\s+)({_TOKEN_VALUE})"),
)


def redact_sensitive_text(
    value: object,
    *,
    known_secrets: Iterable[str] = (),
) -> str:
    """Return log-safe text without credential-like values.

    Known secrets are replaced first.  Pattern redaction then covers upstream
    services that echo a submitted token even when the local process does not
    know that token (for example, an inherited or malformed credential).
    """
    message = str(value)
    for secret in known_secrets:
        normalized = str(secret or "").strip()
        if normalized:
            message = message.replace(normalized, "[REDACTED]")
    for pattern in _PATTERNS:
        message = pattern.sub(r"\1[REDACTED]", message)
    return message
