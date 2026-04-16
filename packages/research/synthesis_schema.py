from __future__ import annotations

from pydantic import BaseModel


class LlmSynthesisResult(BaseModel):
    final_answer: str
    executive_summary: str | None = None
    detailed_report: str | None = None
    uncertainty_notes: str | None = None
    # Informational only — IDs not present in the evidence set are warned, not fatal.
    # Missing or empty triggers a warning; an otherwise-valid result is never discarded
    # solely because of a bad reference.
    referenced_evidence_ids: list[str] = []
