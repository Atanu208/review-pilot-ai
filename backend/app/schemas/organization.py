from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import InvitationStatus, OrgRole
from app.schemas.auth import UserOut
from app.schemas.common import ORMModel

GITHUB_LOGIN_PATTERN = r"^[A-Za-z0-9-]{1,39}$"


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("name must have at least 2 visible characters")
        return value


class OrganizationOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class OrganizationDetail(OrganizationOut):
    role: OrgRole
    member_count: int
    installation_count: int
    connected_repository_count: int


class MemberOut(ORMModel):
    id: uuid.UUID
    role: OrgRole
    created_at: datetime
    user: UserOut


class MemberRoleUpdate(BaseModel):
    role: OrgRole


class InvitationCreate(BaseModel):
    github_login: str = Field(pattern=GITHUB_LOGIN_PATTERN)
    role: OrgRole = OrgRole.DEVELOPER

    @field_validator("github_login")
    @classmethod
    def _github_login_rules(cls, value: str) -> str:
        # GitHub logins: alphanumerics and single hyphens, not leading/trailing.
        if value.startswith("-") or value.endswith("-") or "--" in value:
            raise ValueError("not a valid GitHub username")
        return value


class InvitationOut(ORMModel):
    id: uuid.UUID
    github_login: str
    role: OrgRole
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime


class InvitationCreated(InvitationOut):
    # Shown exactly once; only its hash is stored.
    invite_url: str


class InvitationAccepted(BaseModel):
    organization_id: uuid.UUID
    role: OrgRole
