"""Schemas the models must answer with. Validated with Pydantic after every call."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FindingCategory, Severity


class _Lenient(BaseModel):
    # Models sometimes add harmless extra keys; ignore them rather than fail the call.
    model_config = ConfigDict(extra="ignore")


class CandidateFinding(_Lenient):
    title: str = Field(min_length=3, max_length=200)
    category: FindingCategory
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    file_path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=2000)
    impact: str | None = Field(default=None, max_length=1000)
    suggested_fix: str | None = Field(default=None, max_length=2000)
    code_suggestion: str | None = Field(default=None, max_length=4000)
    evidence: str = Field(min_length=1, max_length=1000)


class ReviewerOutput(_Lenient):
    findings: list[CandidateFinding] = Field(default_factory=list, max_length=25)


class Verdict(_Lenient):
    index: int = Field(ge=0)
    verdict: Literal["confirm", "reject"]
    confidence: float = Field(ge=0, le=1)
    severity: Severity | None = None
    reason: str = Field(default="", max_length=500)


class ValidatorOutput(_Lenient):
    verdicts: list[Verdict] = Field(default_factory=list)


class SummaryOutput(_Lenient):
    summary: str = Field(min_length=1, max_length=2500)
    risk_level: Literal["low", "medium", "high", "critical"]
    highlights: list[str] = Field(default_factory=list, max_length=6)


def json_schema(model: type[BaseModel]) -> dict[str, object]:
    return model.model_json_schema()
