"""Active PII and URL tracking parameter redaction pipeline."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?!\w)"
)
_API_SECRET_RE = re.compile(
    r"\b(?:AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,}|ya29\.[0-9A-Za-z_-]{20,}|Bearer\s+[0-9A-Za-z._~+/=-]{16,})\b"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IPV4_RE = re.compile(
    r"\b(?!127\.0\.0\.1\b)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"')]+")

_SENSITIVE_KEY_SUBSTRINGS = (
    "secret",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
    "authorization",
    "credential",
)

_TRACKING_QUERY_KEYS = {
    "si",
    "nd",
    "pt",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}


class PIIRedactor:
    """Scrubs PII, secrets, and URL tracking tokens before logging or persistence."""

    def __init__(self, enable_dlp_hook: bool = False, gcp_project: str | None = None) -> None:
        self.enable_dlp_hook = enable_dlp_hook
        self.gcp_project = gcp_project

    def strip_url_tracking(self, url_or_text: str) -> str:
        """Strip `?si=...` and tracking query parameters from URLs while keeping path intact."""

        def _clean_single_url(match: re.Match[str]) -> str:
            raw_url = match.group(0)
            try:
                parts = urlsplit(raw_url)
                if not parts.query:
                    return raw_url
                filtered = [
                    (k, v)
                    for k, v in parse_qsl(parts.query, keep_blank_values=True)
                    if k.lower() not in _TRACKING_QUERY_KEYS
                ]
                new_query = urlencode(filtered)
                return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))
            except ValueError:
                return re.sub(r"[?&]si=[^&\s]+", "", raw_url)

        cleaned = _URL_IN_TEXT_RE.sub(_clean_single_url, url_or_text)
        # Also catch bare query fragments if someone passed an ID?si=...
        cleaned = re.sub(r"\?si=[A-Za-z0-9_-]+(?:&[^\s]*)?", "", cleaned)
        cleaned = re.sub(r"&si=[A-Za-z0-9_-]+", "", cleaned)
        return cleaned

    def scrub_text(self, text: str) -> str:
        """Redact emails, phone numbers, secrets, SSNs, IPs, and `?si=` tokens from text."""
        if not text:
            return text
        scrubbed = self.strip_url_tracking(text)
        scrubbed = _API_SECRET_RE.sub("[REDACTED_SECRET]", scrubbed)
        scrubbed = _EMAIL_RE.sub("[REDACTED_EMAIL]", scrubbed)
        scrubbed = _SSN_RE.sub("[REDACTED_ID]", scrubbed)
        scrubbed = _PHONE_RE.sub("[REDACTED_PHONE]", scrubbed)
        scrubbed = _IPV4_RE.sub("[REDACTED_IP]", scrubbed)
        return scrubbed

    def scrub_user_id(self, user_id: str) -> str:
        """Sanitize a user_id so email/phone identifiers are hashed/redacted before DB storage."""
        if not user_id:
            return "anonymous_listener"
        cleaned = self.scrub_text(user_id.strip())
        return cleaned[:128]

    def scrub_payload(self, value: Any) -> Any:
        """Recursively scrub strings and sensitive keys in dicts, lists, and tuples."""
        if isinstance(value, str):
            return self.scrub_text(value)
        if isinstance(value, dict):
            sanitized: dict[Any, Any] = {}
            for k, v in value.items():
                key_lower = str(k).lower()
                if any(marker in key_lower for marker in _SENSITIVE_KEY_SUBSTRINGS):
                    sanitized[k] = "[REDACTED_SECRET]"
                else:
                    sanitized[k] = self.scrub_payload(v)
            return sanitized
        if isinstance(value, list):
            return [self.scrub_payload(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.scrub_payload(item) for item in value)
        return value


_DEFAULT_REDACTOR = PIIRedactor()


def get_pii_redactor() -> PIIRedactor:
    """Return shared PIIRedactor instance."""
    return _DEFAULT_REDACTOR
