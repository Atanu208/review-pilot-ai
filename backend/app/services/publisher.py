"""Publish a completed review to GitHub as one COMMENT review (summary + inline comments).

Guarantees:
* Never posts a stale review: the PR must still be open and at the reviewed commit.
* Inline comments only on lines GitHub accepts (inside the PR's diff hunks, RIGHT side). If
  GitHub still rejects a position, the review is re-sent with everything in the summary.
* Findings already posted on this PR by an earlier review (same fingerprint) are not
  repeated, and earlier summaries are marked "superseded".
* Idempotent: a job is published at most once; retries resume safely.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.analysis.diff import parse_patch
from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.github import review_format
from app.integrations.github.app_api import GitHubAppAPI
from app.integrations.github.http import GitHubAPIError, GitHubError
from app.models import PullRequest, Repository, ReviewComment, ReviewFinding, ReviewJob
from app.models.enums import (
    CommentKind,
    CommentStatus,
    FindingStatus,
    ReviewStatus,
)
from app.services.reviews import effective_settings

logger = get_logger(__name__)


@dataclass(slots=True)
class PublishOutcome:
    status: str  # published | skipped | failed | noop | retry
    detail: str = ""


class ReviewPublisher:
    def __init__(self, session: AsyncSession, app_api: GitHubAppAPI, settings: Settings) -> None:
        self._session = session
        self._gh = app_api
        self._settings = settings

    async def publish(self, job_id: uuid.UUID, *, final_attempt: bool) -> PublishOutcome:
        job = await self._session.scalar(
            select(ReviewJob).where(ReviewJob.id == job_id).with_for_update(skip_locked=True)
        )
        if job is None or job.status != ReviewStatus.PUBLISHING:
            await self._session.rollback()
            return PublishOutcome("noop", "not in publishing state")
        pr = await self._session.scalar(
            select(PullRequest)
            .options(
                joinedload(PullRequest.repository).selectinload(Repository.settings),
                joinedload(PullRequest.repository).joinedload(Repository.installation),
            )
            .where(PullRequest.id == job.pull_request_id)
        )
        if pr is None:
            raise RuntimeError("pr is missing")
        repo = pr.repository
        inst = repo.installation.installation_id

        try:
            return await self._publish(job, pr, repo, inst)
        except GitHubAPIError as exc:
            if exc.is_transient and not final_attempt:
                await self._session.rollback()
                return PublishOutcome("retry", str(exc))
            return await self._fail(
                job_id, f"GitHub rejected the review ({exc.status_code}): {exc.message}"
            )
        except GitHubError as exc:
            if not final_attempt:
                await self._session.rollback()
                return PublishOutcome("retry", str(exc))
            return await self._fail(job_id, f"GitHub unavailable: {exc}")

    # ---- steps --------------------------------------------------------------------------

    async def _publish(
        self, job: ReviewJob, pr: PullRequest, repo: Repository, inst: int
    ) -> PublishOutcome:
        live = await self._gh.get_pull_request(inst, repo.full_name, pr.number)
        if live.get("state") != "open" or (live.get("head") or {}).get("sha") != job.head_sha:
            reason = "pr_closed" if live.get("state") != "open" else "head_moved"
            return await self._complete(job, {"status": "skipped", "reason": reason})

        settings = effective_settings(repo)
        min_rank = settings.min_severity_to_publish.rank
        findings = list(
            await self._session.scalars(
                select(ReviewFinding).where(
                    ReviewFinding.review_job_id == job.id,
                    ReviewFinding.status == FindingStatus.OPEN,
                )
            )
        )
        eligible = [
            f
            for f in findings
            if f.severity.rank >= min_rank and f.confidence >= settings.min_confidence
        ]
        posted_before = await self._previously_posted_fingerprints(pr.id, job.id)
        new = [f for f in eligible if f.fingerprint not in posted_before]
        already = len(eligible) - len(new)

        hunks = await self._hunks(inst, repo.full_name, pr.number)
        inline: list[ReviewFinding] = []
        if settings.publish_inline_comments:
            for f in sorted(new, key=lambda x: (-x.severity.rank, x.file_path, x.start_line)):
                if len(inline) >= self._settings.publish_max_inline_comments:
                    break
                if _inlinable(f, hunks):
                    inline.append(f)

        if not inline and not settings.publish_summary:
            return await self._complete(job, {"status": "skipped", "reason": "nothing_to_publish"})

        stats = job.stats or {}
        ai = stats.get("ai") or {}
        summary_input = review_format.SummaryInput(
            job_id=str(job.id),
            head_sha=job.head_sha,
            ai_summary=ai.get("summary_text") or _ai_summary_from(job.summary),
            risk_level=ai.get("risk_level"),
            findings=eligible,
            inline_ids={str(f.id) for f in inline},
            already_reported=already,
            dashboard_url=f"{self._settings.frontend_url}/pull-requests/{pr.id}",
        )
        comments_payload = [_comment_payload(f) for f in inline]

        # Fresh attempt: drop rows from earlier failed attempts of this job.
        await self._session.execute(
            delete(ReviewComment).where(
                ReviewComment.review_job_id == job.id,
                ReviewComment.status != CommentStatus.PUBLISHED,
            )
        )
        rows = {
            str(f.id): ReviewComment(
                review_job_id=job.id,
                finding_id=f.id,
                kind=CommentKind.INLINE,
                status=CommentStatus.PENDING,
                path=f.file_path,
                line=f.end_line,
                side="RIGHT",
                body=p["body"],
            )
            for f, p in zip(inline, comments_payload, strict=True)
        }
        summary_row = ReviewComment(
            review_job_id=job.id,
            kind=CommentKind.SUMMARY,
            status=CommentStatus.PENDING,
            body=review_format.summary_body(summary_input),
        )
        self._session.add_all([summary_row, *rows.values()])
        await self._session.flush()

        fallback_used = False
        try:
            review = await self._gh.create_review(
                inst,
                repo.full_name,
                pr.number,
                commit_id=job.head_sha,
                body=summary_row.body,
                comments=comments_payload,
            )
        except GitHubAPIError as exc:
            if exc.status_code != 422 or not comments_payload:
                raise
            # A position GitHub won't accept: post everything in the summary instead.
            logger.warning(
                "publish_inline_rejected", review_job_id=str(job.id), message=exc.message
            )
            fallback_used = True
            summary_input.inline_ids = set()
            summary_row.body = review_format.summary_body(summary_input)
            for row in rows.values():
                row.status = CommentStatus.SKIPPED
                row.error_message = f"GitHub rejected inline position: {exc.message}"[:2000]
            review = await self._gh.create_review(
                inst,
                repo.full_name,
                pr.number,
                commit_id=job.head_sha,
                body=summary_row.body,
                comments=[],
            )

        now = datetime.now(UTC)
        review_id = int(review["id"])
        summary_row.status = CommentStatus.PUBLISHED
        summary_row.github_review_id = review_id
        summary_row.published_at = now
        if not fallback_used and rows:
            await self._map_comment_ids(
                inst, repo.full_name, pr.number, review_id, rows, inline, now
            )

        superseded = await self._supersede_previous(inst, repo.full_name, pr.number, pr.id, job)
        return await self._complete(
            job,
            {
                "status": "published",
                "review_id": review_id,
                "html_url": review.get("html_url"),
                "inline_comments": 0 if fallback_used else len(inline),
                "summary_only": len(eligible) - (0 if fallback_used else len(inline)) - already,
                "already_reported": already,
                "inline_fallback": fallback_used,
                "superseded_reviews": superseded,
                "published_at": now.isoformat(),
            },
        )

    async def _map_comment_ids(
        self,
        inst: int,
        full_name: str,
        number: int,
        review_id: int,
        rows: dict[str, ReviewComment],
        inline: list[ReviewFinding],
        now: datetime,
    ) -> None:
        by_marker = {review_format.finding_marker(f.fingerprint): rows[str(f.id)] for f in inline}
        try:
            posted = await self._gh.list_review_comments(inst, full_name, number, review_id)
        except GitHubError:
            posted = []  # ids are a convenience; the review itself was accepted
        for row in rows.values():
            row.status = CommentStatus.PUBLISHED
            row.github_review_id = review_id
            row.published_at = now
        for item in posted:
            body = str(item.get("body") or "")
            for marker, row in by_marker.items():
                if marker in body:
                    row.github_comment_id = int(item["id"])

    async def _previously_posted_fingerprints(
        self, pr_id: uuid.UUID, job_id: uuid.UUID
    ) -> set[str]:
        rows = await self._session.scalars(
            select(ReviewFinding.fingerprint)
            .join(ReviewComment, ReviewComment.finding_id == ReviewFinding.id)
            .join(ReviewJob, ReviewJob.id == ReviewComment.review_job_id)
            .where(
                ReviewJob.pull_request_id == pr_id,
                ReviewJob.id != job_id,
                ReviewComment.kind == CommentKind.INLINE,
                ReviewComment.status == CommentStatus.PUBLISHED,
            )
        )
        return set(rows)

    async def _hunks(
        self, inst: int, full_name: str, number: int
    ) -> dict[str, list[tuple[int, int]]]:
        files = await self._gh.list_pull_request_files(inst, full_name, number)
        return {
            str(f.get("filename")): parse_patch(f.get("patch")).hunks
            for f in files
            if f.get("patch") and f.get("status") != "removed"
        }

    async def _supersede_previous(
        self, inst: int, full_name: str, number: int, pr_id: uuid.UUID, job: ReviewJob
    ) -> int:
        previous = list(
            await self._session.scalars(
                select(ReviewComment)
                .join(ReviewJob, ReviewJob.id == ReviewComment.review_job_id)
                .where(
                    ReviewJob.pull_request_id == pr_id,
                    ReviewJob.id != job.id,
                    ReviewJob.head_sha != job.head_sha,
                    ReviewComment.kind == CommentKind.SUMMARY,
                    ReviewComment.status == CommentStatus.PUBLISHED,
                    ReviewComment.github_review_id.is_not(None),
                )
            )
        )
        updated = 0
        for row in previous:
            if row.body.startswith("> ⚠️ **Superseded**"):
                continue
            new_body = review_format.superseded_body(row.body, job.head_sha)
            try:
                if row.github_review_id is None:
                    raise RuntimeError("row.github_review_id is missing")
                await self._gh.update_review_body(
                    inst, full_name, number, row.github_review_id, new_body
                )
            except GitHubError as exc:  # cosmetic: never fail a publication over it
                logger.warning(
                    "supersede_failed", github_review_id=row.github_review_id, error=str(exc)
                )
                continue
            row.body = new_body
            updated += 1
        return updated

    async def _complete(self, job: ReviewJob, publication: dict[str, Any]) -> PublishOutcome:
        job.status = ReviewStatus.COMPLETED
        job.status_reason = (
            None if publication["status"] == "published" else f"publish_{publication.get('reason')}"
        )
        job.stats = {**(job.stats or {}), "publication": publication}
        job.timeline = [
            *job.timeline,
            {
                "stage": "publish",
                "status": "ok" if publication["status"] == "published" else "skipped",
                "started_at": datetime.now(UTC).isoformat(),
                "duration_ms": 0,
                "detail": _publication_detail(publication),
            },
        ]
        await self._session.commit()
        logger.info(
            "review_publication_finished",
            review_job_id=str(job.id),
            **{"result": publication["status"]},
        )
        return PublishOutcome(publication["status"], _publication_detail(publication))

    async def _fail(self, job_id: uuid.UUID, message: str) -> PublishOutcome:
        await self._session.rollback()
        job = await self._session.get(ReviewJob, job_id)
        if job is None:
            raise RuntimeError("job is missing")
        job.status = ReviewStatus.FAILED
        job.status_reason = "publish_failed"
        job.error_message = message[:2000]
        job.stats = {
            **(job.stats or {}),
            "publication": {"status": "failed", "error": message[:500]},
        }
        job.timeline = [
            *job.timeline,
            {
                "stage": "publish",
                "status": "failed",
                "started_at": datetime.now(UTC).isoformat(),
                "duration_ms": 0,
                "detail": message[:500],
            },
        ]
        await self._session.commit()
        logger.warning("review_publication_failed", review_job_id=str(job_id))
        return PublishOutcome("failed", message)


def _inlinable(f: ReviewFinding, hunks: dict[str, list[tuple[int, int]]]) -> bool:
    """GitHub accepts a (multi-)line comment only if all lines sit in one diff hunk."""
    return any(lo <= f.start_line and f.end_line <= hi for lo, hi in hunks.get(f.file_path, []))


def _comment_payload(f: ReviewFinding) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": f.file_path,
        "line": f.end_line,
        "side": "RIGHT",
        "body": review_format.inline_body(f),
    }
    if f.start_line < f.end_line:
        payload["start_line"] = f.start_line
        payload["start_side"] = "RIGHT"
    return payload


def _ai_summary_from(summary: str | None) -> str | None:
    """Job summaries are "<counts>\\n\\n<AI summary>"; post only the AI part."""
    if summary and "\n\n" in summary:
        return summary.split("\n\n", 1)[1]
    return None


def _publication_detail(p: dict[str, Any]) -> str:
    if p["status"] == "published":
        detail = f"{p['inline_comments']} inline comment(s) + summary posted to GitHub"
        if p.get("already_reported"):
            detail += f"; {p['already_reported']} already reported earlier"
        if p.get("inline_fallback"):
            detail += "; inline positions rejected, posted as summary only"
        return detail
    return f"not posted: {p.get('reason')}"
