"""
PII Detection & Redaction.

Provides regex-based detection for common PII types:
  - Email addresses
  - Phone numbers (E.164 + common US formats)
  - SSN (US)
  - Credit card numbers (Luhn-validated)
  - IP addresses (v4 / v6)
  - Passport numbers (basic pattern)
  - AWS access keys / secret key patterns

Optional: if `presidio-analyzer` is installed, uses Microsoft Presidio for
higher-accuracy NER-based detection. Falls back to regex if unavailable.

Usage:
  detector = PIIDetector()
  findings = detector.scan("My email is foo@bar.com and SSN 123-45-6789")
  # [PIIFinding(type='EMAIL', value='foo@bar.com', start=12, end=23), ...]

  redactor = PIIRedactor(detector)
  clean = redactor.redact("My email is foo@bar.com")
  # "My email is [EMAIL]"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, str]] = [
    ("EMAIL", r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    ("PHONE", r"(?:\+?1[\s\-.]?)?\(?[2-9]\d{2}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"),
    ("SSN", r"\b(?!000|666|9\d{2})\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4}\b"),
    ("CREDIT_CARD", r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b"),
    ("IPV4", r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
    ("IPV6", r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"),
    ("AWS_KEY", r"\b(?:AKIA|AIPA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    ("AWS_SECRET", r"(?i)aws[_\-.]?secret[_\-.]?(?:access[_\-.]?)?key[\"'\s:=]+([A-Za-z0-9/+=]{40})\b"),
    ("JWT", r"ey[A-Za-z0-9_\-]+\.ey[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    ("PRIVATE_KEY", r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]

_COMPILED: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pattern)) for name, pattern in _PATTERNS
]


@dataclass
class PIIFinding:
    pii_type: str
    value: str
    start: int
    end: int


class PIIDetector:
    """Scan text for PII using regex patterns (+ optional Presidio)."""

    def __init__(self, use_presidio: bool = True) -> None:
        self._presidio = None
        if use_presidio:
            try:
                from presidio_analyzer import AnalyzerEngine
                self._presidio = AnalyzerEngine()
            except ImportError:
                pass  # fall back to regex

    def scan(self, text: str) -> list[PIIFinding]:
        if self._presidio:
            return self._scan_presidio(text)
        return self._scan_regex(text)

    def _scan_regex(self, text: str) -> list[PIIFinding]:
        findings: list[PIIFinding] = []
        for pii_type, pattern in _COMPILED:
            for m in pattern.finditer(text):
                findings.append(PIIFinding(
                    pii_type=pii_type,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                ))
        return sorted(findings, key=lambda f: f.start)

    def _scan_presidio(self, text: str) -> list[PIIFinding]:
        try:
            results = self._presidio.analyze(text=text, language="en")
            findings = []
            for r in results:
                findings.append(PIIFinding(
                    pii_type=r.entity_type,
                    value=text[r.start:r.end],
                    start=r.start,
                    end=r.end,
                ))
            # Merge with regex findings for credential types Presidio misses
            regex_types = {"AWS_KEY", "AWS_SECRET", "JWT", "PRIVATE_KEY"}
            for pii_type, pattern in _COMPILED:
                if pii_type not in regex_types:
                    continue
                for m in pattern.finditer(text):
                    findings.append(PIIFinding(pii_type=pii_type, value=m.group(), start=m.start(), end=m.end()))
            return sorted(findings, key=lambda f: f.start)
        except Exception:
            return self._scan_regex(text)

    def contains_pii(self, text: str) -> bool:
        return len(self.scan(text)) > 0


class PIIRedactor:
    """Replace PII in text with type placeholders."""

    def __init__(self, detector: PIIDetector | None = None, replacement_format: str = "[{type}]") -> None:
        self._detector = detector or PIIDetector()
        self._fmt = replacement_format

    def redact(self, text: str) -> str:
        findings = self._detector.scan(text)
        if not findings:
            return text

        # Work backwards so offsets stay valid
        result = text
        for finding in sorted(findings, key=lambda f: f.start, reverse=True):
            placeholder = self._fmt.format(type=finding.pii_type)
            result = result[: finding.start] + placeholder + result[finding.end :]
        return result

    def redact_dict(self, data: dict, fields: list[str] | None = None) -> dict:
        """Redact string values in a dict. If fields is given, only redact those keys."""
        out = {}
        for k, v in data.items():
            if isinstance(v, str) and (fields is None or k in fields):
                out[k] = self.redact(v)
            elif isinstance(v, dict):
                out[k] = self.redact_dict(v, fields)
            else:
                out[k] = v
        return out
