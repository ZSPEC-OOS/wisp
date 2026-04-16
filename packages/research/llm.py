from __future__ import annotations

import json
import logging
import re

import httpx

from apps.api.config import settings
from packages.research.evidence import EvidenceChunk
from packages.research.synthesis_schema import LlmSynthesisResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a grounded research synthesis model.\n\n"
    "Use only the supplied evidence chunks.\n"
    "Do not invent facts, names, dates, or numeric claims.\n"
    "Do not claim consensus unless multiple evidence chunks support it.\n"
    "If the evidence is incomplete, conflicting, or insufficient, state that explicitly.\n"
    "Do not output chain-of-thought.\n"
    "Return valid JSON only."
)


class LlmSynthesisClient:
    """Async client for Qwen3-8B (or any OpenAI-compatible) synthesis endpoint.

    Timeout is managed externally by asyncio.wait_for so the httpx client
    itself has no timeout set.  This lets ResearchService apply per-mode
    budgets without coupling the client to any one value.
    """

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.llm_base_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=None,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _resolve_timeout(self, mode: str) -> float:
        """Return the per-mode timeout in seconds (falls back to llm_timeout_seconds)."""
        return {
            "concise":    settings.llm_timeout_concise_seconds,
            "report":     settings.llm_timeout_report_seconds,
            "structured": settings.llm_timeout_structured_seconds,
        }.get(mode, settings.llm_timeout_seconds)

    def _build_messages(
        self, query: str, evidence: list[EvidenceChunk], mode: str
    ) -> list[dict]:
        blocks: list[str] = []
        for chunk in evidence:
            block = f"[{chunk.evidence_id}]\n"
            if chunk.title:
                block += f"TITLE: {chunk.title}\n"
            block += f"URL:   {chunk.url}\n"
            block += f"TEXT:\n{chunk.text}"
            blocks.append(block)

        evidence_text = "\n\n".join(blocks)
        user_prompt = (
            f"Query:\n{query}\n\n"
            f"Mode:\n{mode}\n\n"
            f"Evidence:\n{evidence_text}\n\n"
            "Requirements:\n"
            "- Use only the evidence above\n"
            "- Synthesise overlapping evidence where useful\n"
            "- Preserve uncertainty and conflicting positions explicitly\n"
            "- Avoid unsupported causal claims\n"
            "- Return JSON only with keys:\n"
            "    final_answer\n"
            "    executive_summary\n"
            "    detailed_report\n"
            "    uncertainty_notes\n"
            "    referenced_evidence_ids"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ]

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove optional markdown code fences the model may wrap around JSON."""
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        return text.strip()

    async def synthesize(
        self,
        query: str,
        evidence: list[EvidenceChunk],
        mode: str = "concise",
    ) -> LlmSynthesisResult | None:
        """Call the LLM and return a validated LlmSynthesisResult, or None on any failure.

        Callers wrap this in asyncio.wait_for with _resolve_timeout(mode) so
        TimeoutError propagates up for the caller to catch and count as fallback.
        All other exceptions should be caught by the caller as well.
        """
        messages = self._build_messages(query, evidence, mode)

        payload: dict = {
            "model":       settings.llm_model,
            "messages":    messages,
            "temperature": settings.llm_temperature,
            "max_tokens":  settings.llm_max_tokens,
        }
        # Qwen3 thinking mode: pass enable_thinking=False when disabled.
        # Thinking mode increases latency and JSON parse instability against
        # the hard timeout budget; off by default per spec §10 / §9.3.
        if not settings.llm_enable_thinking:
            payload["enable_thinking"] = False

        resp = await self._http.post("/chat/completions", json=payload)
        resp.raise_for_status()

        raw     = resp.json()["choices"][0]["message"]["content"]
        cleaned = self._strip_fences(raw)
        data    = json.loads(cleaned)
        result  = LlmSynthesisResult(**data)

        # Validate referenced_evidence_ids — warn only, never discard a
        # valid synthesis response solely because of a bad ID reference.
        valid_ids = {c.evidence_id for c in evidence}
        bad_ids   = [eid for eid in result.referenced_evidence_ids if eid not in valid_ids]
        if bad_ids:
            logger.warning(
                "LLM referenced unknown evidence IDs (ignored): %s", bad_ids
            )

        return result
