from __future__ import annotations

from app.models.enums import OrgRole, ReviewStatus, Severity


def test_role_hierarchy() -> None:
    assert OrgRole.OWNER.at_least(OrgRole.ADMIN)
    assert OrgRole.ADMIN.at_least(OrgRole.DEVELOPER)
    assert OrgRole.DEVELOPER.at_least(OrgRole.VIEWER)
    assert not OrgRole.VIEWER.at_least(OrgRole.DEVELOPER)
    assert not OrgRole.ADMIN.at_least(OrgRole.OWNER)


def test_severity_ordering() -> None:
    ordered = sorted(Severity, key=lambda s: s.rank)
    assert ordered == [
        Severity.INFO,
        Severity.LOW,
        Severity.MEDIUM,
        Severity.HIGH,
        Severity.CRITICAL,
    ]


def test_terminal_review_statuses() -> None:
    assert ReviewStatus.COMPLETED.is_terminal
    assert ReviewStatus.CANCELLED.is_terminal
    assert not ReviewStatus.RUNNING.is_terminal
    assert not ReviewStatus.AWAITING_APPROVAL.is_terminal
