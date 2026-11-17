"""The common finding schema shared by static analysis and AI reviewers."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    FeedbackRating,
    FindingCategory,
    FindingSource,
    FindingStatus,
    Severity,
)
from app.schemas.common import ORMModel

_WS = re.compile(r"\s+")


class Finding(BaseModel):
    """Normalized finding. Every engine (Semgrep, Tree-sitter, AI) must produce this shape."""

    # No global whitespace stripping: evidence and code suggestions keep their indentation.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=255)
    category: FindingCategory
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    file_path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=4000)
    impact: str | None = Field(default=None, max_length=2000)
    suggested_fix: str | None = Field(default=None, max_length=4000)
    code_suggestion: str | None = Field(default=None, max_length=8000)
    source: FindingSource
    evidence: str | None = Field(default=None, max_length=2000)
    rule_id: str | None = Field(default=None, max_length=255)

    @field_validator("title", "description", "impact", "suggested_fix", "rule_id", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("file_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or any(p == ".." for p in value.split("/")):
            raise ValueError("file_path must be a repository-relative POSIX path")
        return value

    @model_validator(mode="after")
    def _line_order(self) -> Finding:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self

    def fingerprint(self, occurrence: int = 0) -> str:
        """Stable identity independent of line numbers (code moving doesn't change it)."""
        evidence = _WS.sub(" ", (self.evidence or "")).strip().lower()
        key = "|".join(
            [
                self.source.value,
                self.rule_id or self.title.lower(),
                self.file_path,
                evidence,
                str(occurrence),
            ]
        )
        return hashlib.sha256(key.encode()).hexdigest()


class FindingOut(ORMModel):
    id: uuid.UUID
    review_job_id: uuid.UUID
    pull_request_id: uuid.UUID
    title: str
    category: FindingCategory
    severity: Severity
    confidence: Decimal
    file_path: str
    start_line: int
    end_line: int
    description: str
    impact: str | None
    suggested_fix: str | None
    code_suggestion: str | None
    evidence: str | None
    source: FindingSource
    rule_id: str | None
    fingerprint: str
    status: FindingStatus
    created_at: datetime


class FindingStatusUpdate(BaseModel):
    status: FindingStatus


class FeedbackIn(BaseModel):
    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=2000)
