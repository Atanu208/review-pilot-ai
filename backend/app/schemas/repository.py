from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import RuleEngine, Severity
from app.schemas.common import ORMModel

SUPPORTED_LANGUAGES = ("python", "java", "javascript", "typescript")
Language = Literal["python", "java", "javascript", "typescript"]
RULE_KEY_PATTERN = r"^[A-Za-z0-9_.:/\-]{1,255}$"


class InstallationOut(ORMModel):
    id: uuid.UUID
    installation_id: int
    account_login: str
    account_type: str
    repository_selection: str | None
    suspended_at: datetime | None
    created_at: datetime


class RepositorySettingsOut(ORMModel):
    auto_review_enabled: bool
    review_drafts: bool
    require_human_approval: bool
    publish_inline_comments: bool
    publish_summary: bool
    min_confidence: Decimal
    min_severity_to_publish: Severity
    max_files: int
    max_diff_lines: int
    enabled_languages: list[str]
    ignored_paths: list[str]
    llm_provider: str | None
    llm_model: str | None


class RepositorySettingsUpdate(BaseModel):
    auto_review_enabled: bool | None = None
    review_drafts: bool | None = None
    require_human_approval: bool | None = None
    publish_inline_comments: bool | None = None
    publish_summary: bool | None = None
    min_confidence: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=3)
    min_severity_to_publish: Severity | None = None
    max_files: int | None = Field(default=None, ge=1, le=300)
    max_diff_lines: int | None = Field(default=None, ge=50, le=20_000)
    enabled_languages: list[Language] | None = Field(default=None, max_length=10)
    ignored_paths: list[str] | None = Field(default=None, max_length=100)
    llm_provider: Literal["anthropic", "openai", "mock"] | None = None
    llm_model: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:/\-]+$")

    @field_validator("ignored_paths")
    @classmethod
    def _validate_globs(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = []
        for pattern in value:
            pattern = pattern.strip()
            if not pattern or len(pattern) > 200 or ".." in pattern:
                raise ValueError(f"invalid path pattern: {pattern!r}")
            if not re.fullmatch(r"[A-Za-z0-9_.*?/\-\[\]{},!]+", pattern):
                raise ValueError(f"invalid characters in path pattern: {pattern!r}")
            cleaned.append(pattern)
        return list(dict.fromkeys(cleaned))


class RepositoryOut(ORMModel):
    id: uuid.UUID
    github_repo_id: int
    full_name: str
    default_branch: str
    is_private: bool
    primary_language: str | None
    html_url: str | None
    is_connected: bool
    connected_at: datetime | None
    settings: RepositorySettingsOut | None


class AvailableRepository(BaseModel):
    github_repo_id: int
    full_name: str
    is_private: bool
    default_branch: str
    primary_language: str | None
    html_url: str | None
    installation_id: int
    connected: bool


class RepositoryConnect(BaseModel):
    github_repo_id: int = Field(gt=0)


class ReviewRuleUpsert(BaseModel):
    engine: RuleEngine
    enabled: bool = True
    severity_override: Severity | None = None
    languages: list[Language] = Field(default_factory=list, max_length=10)
    description: str | None = Field(default=None, max_length=2000)


class ReviewRuleOut(ORMModel):
    id: uuid.UUID
    repository_id: uuid.UUID | None
    rule_key: str
    engine: RuleEngine
    enabled: bool
    severity_override: Severity | None
    languages: list[str]
    description: str | None
    updated_at: datetime
