"""
ControlPlane.AI — Responsibility Evaluator

Detects:
1. PII (email, phone, credit card, PAN, SSN, and other sensitive identifiers)
2. Prompt injection / malicious input patterns
3. Basic safety / policy violation patterns

All detection is regex-based with no external dependencies.
"""

from __future__ import annotations

import logging
import re
from typing import List

from controlplane.models import CheckResult, CheckStatus, ResponsibilityResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# PII Patterns
# ─────────────────────────────────────────────

_PII_PATTERNS: List[tuple[str, str, re.Pattern[str]]] = [
    (
        "email",
        "Email address detected",
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I),
    ),
    (
        "phone",
        "Phone number detected",
        re.compile(
            r"(?<!\d)"  # not preceded by digit
            r"(?:\+?\d{1,3}[\s\-]?)?"  # optional country code
            r"(?:\(?\d{2,5}\)?[\s\-]?)?"  # optional area code
            r"\d{3,5}[\s\-]?\d{3,5}"  # main number
            r"(?!\d)"  # not followed by digit
        ),
    ),
    (
        "credit_card",
        "Credit card number detected",
        re.compile(
            r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
            r"[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"
        ),
    ),
    (
        "ssn",
        "SSN-like pattern detected",
        re.compile(r"\b\d{3}[\s\-]\d{2}[\s\-]\d{4}\b"),
    ),
    (
        "pan",
        "PAN (Permanent Account Number) detected",
        re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    ),
    (
        "aadhaar",
        "Aadhaar-like number detected",
        re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    ),
]


# ─────────────────────────────────────────────
# Prompt Injection Patterns
# ─────────────────────────────────────────────

_INJECTION_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    (
        "Ignore previous instructions pattern",
        re.compile(
            r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+"
            r"(instructions|prompts|context|rules)",
            re.I,
        ),
    ),
    (
        "System prompt override attempt",
        re.compile(
            r"(you\s+are\s+now|act\s+as|pretend\s+to\s+be|"
            r"your\s+new\s+(role|instructions?))\b",
            re.I,
        ),
    ),
    (
        "Jailbreak delimiter pattern",
        re.compile(
            r"(```\s*(system|admin|root)|<\|system\|>|"
            r"\[SYSTEM\]|\[INST\]|<<SYS>>)",
            re.I,
        ),
    ),
    (
        "Do anything now (DAN) pattern",
        re.compile(r"\bDAN\b.*\b(mode|jailbreak|bypass)\b", re.I),
    ),
    (
        "Prompt leak attempt",
        re.compile(
            r"(show|reveal|print|output|repeat)\s+"
            r"(\w+\s+){0,3}"  # up to 3 optional words (e.g., "me your system")
            r"(prompt|instructions|rules)\b",
            re.I,
        ),
    ),
]


# ─────────────────────────────────────────────
# Safety / Policy Patterns
# ─────────────────────────────────────────────

_SAFETY_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    (
        "Request for harmful instructions",
        re.compile(
            r"\b(how\s+to\s+(hack|exploit|attack|break\s+into|"
            r"make\s+(a\s+)?(bomb|weapon|drug)))\b",
            re.I,
        ),
    ),
    (
        "Potential self-harm content",
        re.compile(
            r"\b(suicide|self[\s\-]?harm|kill\s+(my|your)self)\b",
            re.I,
        ),
    ),
]


# ─────────────────────────────────────────────
# Evaluator
# ─────────────────────────────────────────────


class ResponsibilityEvaluator:
    """
    Evaluates text for PII, prompt injection, and safety violations.

    Returns a structured ResponsibilityResult with individual check results
    for each detected issue.
    """

    def evaluate(self, text: str) -> ResponsibilityResult:
        """Run all responsibility checks against the provided text."""
        checks: List[CheckResult] = []

        # PII detection
        checks.extend(self._check_pii(text))

        # Prompt injection detection
        checks.extend(self._check_injection(text))

        # Safety / policy violation detection
        checks.extend(self._check_safety(text))

        # Overall status: FAIL if any check failed, UNCERTAIN if any uncertain
        if any(c.status == CheckStatus.FAIL for c in checks):
            overall = CheckStatus.FAIL
        elif any(c.status == CheckStatus.UNCERTAIN for c in checks):
            overall = CheckStatus.UNCERTAIN
        else:
            overall = CheckStatus.PASS

        return ResponsibilityResult(status=overall, checks=checks)

    def _check_pii(self, text: str) -> List[CheckResult]:
        results: List[CheckResult] = []
        for pii_type, description, pattern in _PII_PATTERNS:
            if pattern.search(text):
                results.append(
                    CheckResult(
                        name=f"pii_{pii_type}",
                        status=CheckStatus.FAIL,
                        category="PII",
                        reason=description,
                    )
                )
                logger.warning("PII detected: %s", pii_type)
        return results

    def _check_injection(self, text: str) -> List[CheckResult]:
        results: List[CheckResult] = []
        for description, pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                results.append(
                    CheckResult(
                        name="prompt_injection",
                        status=CheckStatus.FAIL,
                        category="INJECTION",
                        reason=description,
                    )
                )
                logger.warning("Injection pattern detected: %s", description)
        return results

    def _check_safety(self, text: str) -> List[CheckResult]:
        results: List[CheckResult] = []
        for description, pattern in _SAFETY_PATTERNS:
            if pattern.search(text):
                results.append(
                    CheckResult(
                        name="safety_violation",
                        status=CheckStatus.FAIL,
                        category="SAFETY",
                        reason=description,
                    )
                )
                logger.warning("Safety concern: %s", description)
        return results
