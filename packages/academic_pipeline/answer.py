"""Stage 4: Answer a question from extracted paper text using BM25 + LLM.

Text is split into overlapping chunks, ranked by BM25 against the question,
and the top-k chunks are passed to the configured LLM.  Falls back to a
pure-extractive answer (the top BM25 chunk) when no LLM is available.
"""
from __future__ import annotations

import json
import logging
import re
import textwrap

import httpx
from rank_bm25 import BM25Okapi

_logger = logging.getLogger("wisp.academic_pipeline.answer")

_CHUNK_WORDS = 300
_CHUNK_OVERLAP = 50
_TOP_K = 4


def _chunk_text(text: str, size: int = _CHUNK_WORDS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += size - overlap
    return chunks


def _bm25_retrieve(question: str, chunks: list[str], top_k: int = _TOP_K) -> list[str]:
    tokenised = [c.lower().split() for c in chunks]
    bm25 = BM25Okapi(tokenised)
    scores = bm25.get_scores(question.lower().split())
    ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [chunks[i] for i in ranked_idx[:top_k]]


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


async def answer_question(
    question: str,
    text: str,
    *,
    title: str = "",
    doi: str | None = None,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_timeout: float = 30.0,
) -> str:
    """Return an answer to *question* grounded in *text*.

    Uses BM25 retrieval + LLM if configured, else returns the top BM25 chunk.
    """
    chunks = _chunk_text(text)
    if not chunks:
        return "No extractable text found in this paper."

    top_chunks = _bm25_retrieve(question, chunks)

    if not llm_base_url or not llm_model:
        return top_chunks[0] if top_chunks else chunks[0]

    evidence_block = "\n\n---\n\n".join(
        f"[chunk {i+1}]\n{c}" for i, c in enumerate(top_chunks)
    )
    source_note = f'Paper: "{title}"' + (f"  DOI: {doi}" if doi else "")

    system = (
        "You are a precise research assistant. "
        "Answer the question using ONLY the provided evidence chunks. "
        "If the evidence does not contain enough information, say so explicitly. "
        "Be concise. Do not hallucinate."
    )
    user = (
        f"{source_note}\n\n"
        f"Evidence:\n{evidence_block}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )

    async with httpx.AsyncClient(
        base_url=llm_base_url,
        headers={"Authorization": f"Bearer {llm_api_key}"},
        timeout=httpx.Timeout(connect=5.0, read=llm_timeout, write=10.0, pool=5.0),
    ) as client:
        try:
            r = await client.post(
                "/chat/completions",
                json={
                    "model": llm_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 512,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            _logger.warning("llm_answer_failed error=%s; returning extractive fallback", exc)
            return top_chunks[0] if top_chunks else chunks[0]
