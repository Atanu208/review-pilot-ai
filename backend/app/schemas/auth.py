from __future__ import annotations

import uuid

from app.models.enums import OrgRole
from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: uuid.UUID
    login: str
    name: str | None
    email: str | None
    avatar_url: str | None


class MembershipSummary(ORMModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    role: OrgRole


class MeResponse(ORMModel):
    user: UserOut
    organizations: list[MembershipSummary]
    # Echoed in the X-CSRF-Token header on state-changing requests.
    csrf_token: str
