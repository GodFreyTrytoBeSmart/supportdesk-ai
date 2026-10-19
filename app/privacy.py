"""Best-effort minimization, not a production DLP or secret-detection system."""

import re


def redact_sensitive(text: str) -> str:
    """Remove obvious emails, payment-card shaped numbers, and secret tokens.

    This intentionally favors withholding a value over retaining context. Account
    records never enter model prompts in the first place; this is a second layer
    for sensitive values a visitor may paste into their message.
    """
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", text)
    value = re.sub(r"(?<!\w)(?:\d[ -]?){12,18}\d(?!\w)", "[CARD-LIKE NUMBER]", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [TOKEN]", value)
    value = re.sub(r"\bsk[-_][A-Za-z0-9_-]{8,}\b", "[TOKEN]", value)
    value = re.sub(
        r"(?i)\b(api[_ -]?key|access[_ -]?token|session[_ -]?token|password)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )
    return value
