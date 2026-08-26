"""
ControlPlane.AI — Stream Guardrail (extracted from notebook)

Portable Python module re-exporting the teammate's streaming guardrail
components from output.ipynb without modifying the notebook.

Components:
- TokenChunk: dataclass representing a discrete inspection payload
- GuardrailChecker: async PII / toxicity / safety inspector
- StreamGuardrailManager: semaphore-gated stream inspection pipeline

These are faithful re-implementations of the notebook code, adapted for
import from the controlplane package.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional


# ─────────────────────────────────────────────
# TokenChunk — from notebook cell 1
# ─────────────────────────────────────────────


@dataclass
class TokenChunk:
    """Discrete inspection payload in the token stream."""

    chunk_id: str
    stream_id: str
    chunk_index: int
    text: str
    token_count: int
    timestamp: float
    evaluation_results: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, inspected, flagged, blocked


# ─────────────────────────────────────────────
# GuardrailChecker — from notebook cell 2
# ─────────────────────────────────────────────


class GuardrailChecker:
    """Responsible AI inspection engine (PII, Toxicity, Bias)."""

    PII_KEYWORDS = ["ssn", "password", "secret", "credit card"]
    TOXIC_KEYWORDS = ["hate", "kill", "attack"]

    async def evaluate_chunk(self, chunk: TokenChunk) -> Dict[str, Any]:
        """Evaluate a single token chunk for safety violations."""
        # Simulated inspection latency
        await asyncio.sleep(0.01)

        lower = chunk.text.lower()
        is_pii = any(kw in lower for kw in self.PII_KEYWORDS)
        is_toxic = any(kw in lower for kw in self.TOXIC_KEYWORDS)

        risk_score = 0.85 if (is_pii or is_toxic) else 0.05

        return {
            "chunk_id": chunk.chunk_id,
            "pii_detected": is_pii,
            "toxic_content": is_toxic,
            "risk_score": risk_score,
            "action": "FLAG" if risk_score > 0.5 else "ALLOW",
            "latency_ms": 10,
        }


# ─────────────────────────────────────────────
# StreamGuardrailManager — from notebook cell 3
# ─────────────────────────────────────────────


class StreamGuardrailManager:
    """
    Consumes a raw LLM token stream, packages tokens into chunks,
    and dispatches concurrent guardrail inspection.

    Faithful port of ControlPlaneStreamManager from output.ipynb,
    adapted for programmatic (non-notebook) use:
    - No print() calls (uses structured results instead)
    - Accumulates full response text
    - Reports violations as structured data
    """

    def __init__(
        self,
        target_chunk_size: int = 30,
        max_concurrent_evaluations: int = 4,
    ) -> None:
        self.target_chunk_size = target_chunk_size
        self.semaphore = asyncio.Semaphore(max_concurrent_evaluations)
        self.checker = GuardrailChecker()
        self.audit_log: List[TokenChunk] = []

    def count_tokens(self, text: str) -> int:
        """Approximate token count (char//4 heuristic, matching notebook fallback)."""
        return max(1, len(text) // 4)

    async def _evaluate_worker(self, chunk: TokenChunk) -> None:
        """Worker task bounded by the semaphore channel."""
        async with self.semaphore:
            results = await self.checker.evaluate_chunk(chunk)
            chunk.evaluation_results = results
            chunk.status = (
                "flagged" if results["action"] == "FLAG" else "inspected"
            )
            self.audit_log.append(chunk)

    async def process_stream(
        self,
        token_generator: AsyncGenerator[str, None],
        stream_id: Optional[str] = None,
    ) -> "StreamResult":
        """
        Consume a token stream with chunk-level holding and release.

        Tokens are held in a chunk buffer until inspected:
        - If a chunk passes inspection ('ALLOW'), it is released into safe_text.
        - If a chunk fails inspection ('FLAG'), it is suppressed/redacted.

        Returns a StreamResult with full_text, safe_text, audit log,
        and violation summary.
        """
        stream_id = stream_id or str(uuid.uuid4())[:8]
        self.audit_log = []  # reset for this stream

        buffer_text = ""
        full_text = ""
        safe_text = ""
        chunk_idx = 0

        async for token in token_generator:
            full_text += token
            buffer_text += token

            token_count = self.count_tokens(buffer_text)
            if token_count >= self.target_chunk_size:
                chunk = TokenChunk(
                    chunk_id=f"{stream_id}-c{chunk_idx}",
                    stream_id=stream_id,
                    chunk_index=chunk_idx,
                    text=buffer_text,
                    token_count=token_count,
                    timestamp=time.time(),
                )
                chunk_idx += 1
                buffer_text = ""

                # Evaluate chunk BEFORE releasing to safe output (holding buffer)
                async with self.semaphore:
                    results = await self.checker.evaluate_chunk(chunk)
                    chunk.evaluation_results = results
                    if results["action"] == "FLAG":
                        chunk.status = "flagged"
                        # Suppress violating content in safe stream release
                        violation_type = "PII" if results.get("pii_detected") else "SAFETY"
                        safe_text += f"[REDACTED {violation_type}]"
                    else:
                        chunk.status = "inspected"
                        safe_text += chunk.text
                    self.audit_log.append(chunk)

        # Flush residual buffer (held and evaluated before release)
        if buffer_text.strip():
            chunk = TokenChunk(
                chunk_id=f"{stream_id}-c{chunk_idx}",
                stream_id=stream_id,
                chunk_index=chunk_idx,
                text=buffer_text,
                token_count=self.count_tokens(buffer_text),
                timestamp=time.time(),
            )
            async with self.semaphore:
                results = await self.checker.evaluate_chunk(chunk)
                chunk.evaluation_results = results
                if results["action"] == "FLAG":
                    chunk.status = "flagged"
                    violation_type = "PII" if results.get("pii_detected") else "SAFETY"
                    safe_text += f"[REDACTED {violation_type}]"
                else:
                    chunk.status = "inspected"
                    safe_text += chunk.text
                self.audit_log.append(chunk)

        return StreamResult.from_audit_log(
            stream_id=stream_id,
            full_text=full_text,
            safe_text=safe_text,
            audit_log=self.audit_log,
        )

    async def stream_gated(
        self,
        token_generator: AsyncGenerator[str, None],
        stream_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Gated chunk-level streaming generator for real-time clients.

        Holds tokens in buffer until the chunk is verified by GuardrailChecker.
        Yields chunk text only when approved, or redaction token when flagged.
        """
        stream_id = stream_id or str(uuid.uuid4())[:8]
        buffer_text = ""
        chunk_idx = 0

        async for token in token_generator:
            buffer_text += token
            token_count = self.count_tokens(buffer_text)
            if token_count >= self.target_chunk_size:
                chunk = TokenChunk(
                    chunk_id=f"{stream_id}-c{chunk_idx}",
                    stream_id=stream_id,
                    chunk_index=chunk_idx,
                    text=buffer_text,
                    token_count=token_count,
                    timestamp=time.time(),
                )
                chunk_idx += 1
                buffer_text = ""

                async with self.semaphore:
                    results = await self.checker.evaluate_chunk(chunk)
                    chunk.evaluation_results = results
                    if results["action"] == "FLAG":
                        chunk.status = "flagged"
                        violation_type = "PII" if results.get("pii_detected") else "SAFETY"
                        yield f"[REDACTED {violation_type}]"
                    else:
                        chunk.status = "inspected"
                        yield chunk.text
                    self.audit_log.append(chunk)

        if buffer_text.strip():
            chunk = TokenChunk(
                chunk_id=f"{stream_id}-c{chunk_idx}",
                stream_id=stream_id,
                chunk_index=chunk_idx,
                text=buffer_text,
                token_count=self.count_tokens(buffer_text),
                timestamp=time.time(),
            )
            async with self.semaphore:
                results = await self.checker.evaluate_chunk(chunk)
                chunk.evaluation_results = results
                if results["action"] == "FLAG":
                    chunk.status = "flagged"
                    violation_type = "PII" if results.get("pii_detected") else "SAFETY"
                    yield f"[REDACTED {violation_type}]"
                else:
                    chunk.status = "inspected"
                    yield chunk.text
                self.audit_log.append(chunk)


# ─────────────────────────────────────────────
# StreamResult — structured output
# ─────────────────────────────────────────────


@dataclass
class StreamResult:
    """Structured result from stream guardrail processing."""

    stream_id: str
    full_text: str
    safe_text: str
    chunks_inspected: int
    chunks_flagged: int
    has_violations: bool
    violations: List[Dict[str, Any]]
    audit_log: List[TokenChunk]

    @classmethod
    def from_audit_log(
        cls,
        stream_id: str,
        full_text: str,
        safe_text: str,
        audit_log: List[TokenChunk],
    ) -> "StreamResult":
        flagged = [c for c in audit_log if c.status == "flagged"]
        violations = []
        for c in flagged:
            v: Dict[str, Any] = {
                "chunk_id": c.chunk_id,
                "chunk_index": c.chunk_index,
                "text_preview": c.text[:80],
            }
            if c.evaluation_results.get("pii_detected"):
                v["type"] = "PII"
            elif c.evaluation_results.get("toxic_content"):
                v["type"] = "TOXICITY"
            else:
                v["type"] = "UNKNOWN"
            violations.append(v)

        return cls(
            stream_id=stream_id,
            full_text=full_text,
            safe_text=safe_text,
            chunks_inspected=len(audit_log),
            chunks_flagged=len(flagged),
            has_violations=len(flagged) > 0,
            violations=violations,
            audit_log=audit_log,
        )
