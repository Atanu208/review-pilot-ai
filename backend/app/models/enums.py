"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class OrgRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def at_least(self, other: OrgRole) -> bool:
        return self.rank >= other.rank


_ROLE_RANK = {OrgRole.VIEWER: 0, OrgRole.DEVELOPER: 1, OrgRole.ADMIN: 2, OrgRole.OWNER: 3}


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class ReviewTrigger(StrEnum):
    WEBHOOK = "webhook"
    MANUAL = "manual"
    RETRY = "retry"


class ReviewStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_REVIEW_STATUSES


TERMINAL_REVIEW_STATUSES = frozenset(
    {ReviewStatus.COMPLETED, ReviewStatus.FAILED, ReviewStatus.CANCELLED, ReviewStatus.SKIPPED}
)
ACTIVE_REVIEW_STATUSES = frozenset({ReviewStatus.QUEUED, ReviewStatus.RUNNING})


class FindingCategory(StrEnum):
    SECURITY = "security"
    BUG = "bug"
    PERFORMANCE = "performance"
    QUALITY = "quality"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    BEST_PRACTICE = "best_practice"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class FindingSource(StrEnum):
    AI = "ai"
    SEMGREP = "semgrep"
    TREE_SITTER = "tree_sitter"
    CUSTOM_RULE = "custom_rule"


class FindingStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"
    FALSE_POSITIVE = "false_positive"


class FeedbackRating(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    FALSE_POSITIVE = "false_positive"


class CommentKind(StrEnum):
    INLINE = "inline"
    SUMMARY = "summary"


class CommentStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


class RuleEngine(StrEnum):
    SEMGREP = "semgrep"
    AI = "ai"
    TREE_SITTER = "tree_sitter"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"
