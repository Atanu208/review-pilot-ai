"""Executes one review job, stage by stage, with a persisted execution timeline.

Each stage commits its result so progress is visible in the UI and survives worker
crashes. Between stages the job row is re-read so user cancellation and superseding
take effect promptly. The ``ai_review`` stage runs the LangGraph workflow in ``app.ai``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.ai import prompts
from app.ai.factory import AINotConfiguredError, build_router
from app.ai.graph import AIReviewGraph, AIReviewRequest
from app.ai.router import LLMRouter
from app.analysis.diff import parse_patch
from app.analysis.engine import RuleConfig, StaticAnalysisEngine
from app.analysis.languages import detect_language
from app.analysis.source import SourceFile
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.github.app_api import GitHubAppAPI
from app.integrations.github.http import GitHubAPIError, GitHubError, GitHubRateLimitError
from app.models import (
    ModelUsage,
    PullRequest,
    Repository,
    ReviewFinding,
    ReviewJob,
    ReviewRule,
)
from app.models.enums import PullRequestState, ReviewStatus, ReviewTrigger, Severity
from app.schemas.finding import Finding
from app.services.pull_requests import apply_pr_payload
from app.services.review_scope import ReviewScope, build_scope
from app.services.reviews import effective_settings

logger = get_logger(__name__)

MAX_TRANSIENT_RETRY_DELAY = 300


class StageSkip(Exception):
    """Stop the pipeline and mark the job skipped (not a failure)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class StageDegraded(Exception):
    """The stage could not fully run, but the review continues (e.g. Semgrep unavailable)."""


class StageFail(Exception):
    """Permanent failure: retrying cannot help."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class RunOutcome:
    status: ReviewStatus | None
    retry_in: float | None = None


@dataclass(slots=True)
class ReviewContext:
    job: ReviewJob
    pr: PullRequest
    repo: Repository
    installation_id: int
    pr_payload: dict[str, Any] | None = None
    scope: ReviewScope | None = None
    sources: list[SourceFile] = field(default_factory=list)
    rule_config: RuleConfig = field(default_factory=RuleConfig)
    findings: list[Finding] = field(default_factory=list)
    finding_counts: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


Stage = Callable[["ReviewRunner", ReviewContext], Awaitable[str | None]]
RouterFactory = Callable[[Repository], LLMRouter]


class ReviewRunner:
    def __init__(
        self,
        session: AsyncSession,
        app_api: GitHubAppAPI,
        analysis: StaticAnalysisEngine | None = None,
        settings: Settings | None = None,
        router_factory: RouterFactory | None = None,
        publish_enqueue: Callable[[str], Any] | None = None,
    ) -> None:
        self._session = session
        self._gh = app_api
        self._settings = settings or get_settings()
        self._analysis = analysis or StaticAnalysisEngine.from_settings(self._settings)
        self._router_factory = router_factory or self._default_router
        self._publish_enqueue = publish_enqueue or _default_publish_enqueue

    def _default_router(self, repo: Repository) -> LLMRouter:
        repo_settings = effective_settings(repo)
        return build_router(
            self._settings,
            provider_override=repo_settings.llm_provider,
            model_override=repo_settings.llm_model,
        )

    # ---- entry point ----------------------------------------------------------------

    async def run(
        self, job_id: uuid.UUID, *, final_attempt: bool, task_id: str | None = None
    ) -> RunOutcome:
        ctx = await self._start(job_id, task_id)
        if ctx is None:
            job = await self._session.get(ReviewJob, job_id)
            return RunOutcome(status=job.status if job else None)

        for name, stage in STAGES:
            if await self._cancelled(job_id):
                logger.info("review_cancelled_midway", review_job_id=str(job_id), before_stage=name)
                return RunOutcome(status=ReviewStatus.CANCELLED)
            started = time.perf_counter()
            started_at = datetime.now(UTC)
            try:
                detail = await stage(self, ctx)
            except StageSkip as skip:
                await self._record(ctx.job, name, "skipped", started_at, started, str(skip))
                return await self._finish(ctx.job, ReviewStatus.SKIPPED, skip.reason, str(skip))
            except StageDegraded as degraded:
                await self._record(ctx.job, name, "degraded", started_at, started, str(degraded))
                continue
            except StageFail as fail:
                await self._record(ctx.job, name, "failed", started_at, started, str(fail))
                return await self._finish(ctx.job, ReviewStatus.FAILED, fail.reason, str(fail))
            except GitHubRateLimitError as exc:
                return await self._transient(
                    ctx.job,
                    name,
                    started_at,
                    started,
                    exc,
                    final_attempt,
                    delay=min(exc.reset_in_seconds, MAX_TRANSIENT_RETRY_DELAY),
                )
            except (GitHubError, OSError) as exc:
                if isinstance(exc, GitHubAPIError) and not exc.is_transient:
                    await self._record(ctx.job, name, "failed", started_at, started, str(exc))
                    return await self._finish(
                        ctx.job, ReviewStatus.FAILED, f"github_{exc.status_code}", exc.message
                    )
                return await self._transient(ctx.job, name, started_at, started, exc, final_attempt)
            except Exception as exc:
                logger.exception("review_stage_crashed", review_job_id=str(job_id), stage=name)
                await self._session.rollback()
                job = await self._session.get(ReviewJob, job_id)
                if job is None:
                    raise RuntimeError("job is missing") from exc
                await self._record(job, name, "failed", started_at, started, type(exc).__name__)
                return await self._finish(
                    job, ReviewStatus.FAILED, "internal_error", "Unexpected error during review"
                )
            await self._record(ctx.job, name, "ok", started_at, started, detail)

        status, publication = self._publication_plan(ctx)
        ctx.job.stats = {**ctx.job.stats, "publication": publication}
        outcome = await self._finish(ctx.job, status, None, None, summary=self._summary(ctx))
        if outcome.status == ReviewStatus.PUBLISHING:
            # Enqueued only after the commit in _finish, so the publisher always sees the job.
            try:
                self._publish_enqueue(str(ctx.job.id))
            except Exception as exc:  # broker down: the maintenance sweep re-enqueues it
                logger.error("publish_enqueue_failed", error_type=type(exc).__name__)
        return outcome

    def _publication_plan(self, ctx: ReviewContext) -> tuple[ReviewStatus, dict[str, Any]]:
        """Decide what happens after analysis: done, wait for approval, or publish."""
        if not self._settings.github_publish_enabled:
            return ReviewStatus.COMPLETED, {"status": "disabled", "reason": "dry_run"}
        repo_settings = effective_settings(ctx.repo)
        if not (repo_settings.publish_inline_comments or repo_settings.publish_summary):
            return ReviewStatus.COMPLETED, {"status": "disabled", "reason": "repository_settings"}
        if repo_settings.require_human_approval:
            return ReviewStatus.AWAITING_APPROVAL, {"status": "awaiting_approval"}
        return ReviewStatus.PUBLISHING, {"status": "queued"}

    @staticmethod
    def _summary(ctx: ReviewContext) -> str:
        counts = ReviewRunner._counts_summary(ctx)
        ai_summary = ctx.extra.get("ai_summary")
        return f"{counts}\n\n{ai_summary}" if ai_summary else counts

    @staticmethod
    def _counts_summary(ctx: ReviewContext) -> str:
        analyzed = len(ctx.sources)
        files = f"{analyzed} file{'s' if analyzed != 1 else ''}"
        total = sum(ctx.finding_counts.values())
        if total == 0:
            return f"Analyzed {files}: no issues found."
        order = [s.value for s in sorted(Severity, key=lambda s: s.rank, reverse=True)]
        parts = [f"{ctx.finding_counts[s]} {s}" for s in order if ctx.finding_counts.get(s)]
        plural = "s" if total != 1 else ""
        return f"Analyzed {files}: {total} issue{plural} found ({', '.join(parts)})."

    # ---- lifecycle helpers ------------------------------------------------------------

    async def _start(self, job_id: uuid.UUID, task_id: str | None) -> ReviewContext | None:
        """Atomically move queued → running. Returns None if another worker has it or it's done."""
        job = await self._session.scalar(
            select(ReviewJob).where(ReviewJob.id == job_id).with_for_update(skip_locked=True)
        )
        if job is None or job.status != ReviewStatus.QUEUED:
            await self._session.rollback()
            return None
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
        job.status = ReviewStatus.RUNNING
        job.started_at = job.started_at or datetime.now(UTC)
        job.celery_task_id = task_id
        job.error_message = None
        await self._session.commit()

        inst = repo.installation
        if not repo.is_connected or inst.deleted_at is not None or inst.suspended_at is not None:
            await self._finish(
                job,
                ReviewStatus.SKIPPED,
                "repository_unavailable",
                "Repository is disconnected or its GitHub installation is unavailable",
            )
            return None
        return ReviewContext(job=job, pr=pr, repo=repo, installation_id=inst.installation_id)

    async def _cancelled(self, job_id: uuid.UUID) -> bool:
        status = await self._session.scalar(select(ReviewJob.status).where(ReviewJob.id == job_id))
        await self._session.commit()  # end the read transaction; don't hold snapshots
        return status == ReviewStatus.CANCELLED

    async def _record(
        self,
        job: ReviewJob,
        stage: str,
        status: str,
        started_at: datetime,
        t0: float,
        detail: str | None,
    ) -> None:
        entry = {
            "stage": stage,
            "status": status,
            "started_at": started_at.isoformat(),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }
        if detail:
            entry["detail"] = detail[:500]
        job.timeline = [*job.timeline, entry]  # reassign so SQLAlchemy detects the change
        await self._session.commit()

    async def _finish(
        self,
        job: ReviewJob,
        status: ReviewStatus,
        reason: str | None,
        error: str | None,
        *,
        summary: str | None = None,
    ) -> RunOutcome:
        current = await self._session.scalar(
            select(ReviewJob.status).where(ReviewJob.id == job.id).with_for_update()
        )
        if current == ReviewStatus.CANCELLED:  # cancelled while the last stage ran: keep it
            await self._session.commit()
            return RunOutcome(status=ReviewStatus.CANCELLED)
        now = datetime.now(UTC)
        job.status = status
        job.status_reason = reason
        job.error_message = error[:2000] if error and status == ReviewStatus.FAILED else None
        job.completed_at = now
        if job.started_at:
            job.duration_ms = int((now - job.started_at).total_seconds() * 1000)
        if summary:
            job.summary = summary
        await self._session.commit()
        logger.info(
            "review_finished", review_job_id=str(job.id), status=status.value, reason=reason
        )
        return RunOutcome(status=status)

    async def _transient(
        self,
        job: ReviewJob,
        stage: str,
        started_at: datetime,
        t0: float,
        exc: Exception,
        final_attempt: bool,
        delay: float | None = None,
    ) -> RunOutcome:
        await self._record(job, stage, "error", started_at, t0, f"{type(exc).__name__}: {exc}")
        if final_attempt:
            return await self._finish(job, ReviewStatus.FAILED, "retries_exhausted", str(exc))
        job.status = ReviewStatus.QUEUED  # the Celery retry will pick it up again
        await self._session.commit()
        # 0.0 = "retry with the task's exponential backoff"; >0 = wait this long (rate limit).
        return RunOutcome(status=ReviewStatus.QUEUED, retry_in=delay if delay is not None else 0.0)

    # ---- stages -----------------------------------------------------------------------

    async def stage_fetch_pull_request(self, ctx: ReviewContext) -> str:
        payload = await self._gh.get_pull_request(
            ctx.installation_id, ctx.repo.full_name, ctx.pr.number
        )
        apply_pr_payload(ctx.pr, payload)
        await self._session.commit()
        ctx.pr_payload = payload
        if ctx.pr.state != PullRequestState.OPEN:
            raise StageSkip("pr_closed", "Pull request is no longer open")
        if ctx.pr.head_sha != ctx.job.head_sha and ctx.job.trigger != ReviewTrigger.WEBHOOK:
            # A user asked to review "this PR": follow the latest commit (our copy of the head
            # was stale, e.g. a push webhook was missed), unless that commit is already
            # being reviewed by another job.
            other = await self._session.scalar(
                select(ReviewJob.id).where(
                    ReviewJob.pull_request_id == ctx.pr.id,
                    ReviewJob.head_sha == ctx.pr.head_sha,
                    ReviewJob.status.in_([ReviewStatus.QUEUED, ReviewStatus.RUNNING]),
                    ReviewJob.id != ctx.job.id,
                )
            )
            if other is None:
                previous = ctx.job.head_sha
                ctx.job.head_sha = ctx.pr.head_sha
                await self._session.commit()
                return (
                    f"#{ctx.pr.number}: moved from {previous[:7]} to latest {ctx.pr.head_sha[:7]}"
                )
        if ctx.pr.head_sha != ctx.job.head_sha:
            raise StageSkip(
                "head_moved",
                f"Head moved to {ctx.pr.head_sha[:7]}; a newer review supersedes this one",
            )
        return f"#{ctx.pr.number} at {ctx.job.head_sha[:7]}"

    async def stage_fetch_changed_files(self, ctx: ReviewContext) -> str:
        settings = effective_settings(ctx.repo)
        files = await self._gh.list_pull_request_files(
            ctx.installation_id, ctx.repo.full_name, ctx.pr.number
        )
        ctx.scope = build_scope(
            files,
            ignored_paths=list(settings.ignored_paths or []),
            max_files=settings.max_files,
            max_diff_lines=settings.max_diff_lines,
        )
        ctx.job.stats = {**ctx.job.stats, "scope": ctx.scope.stats()}
        await self._session.commit()
        if not ctx.scope.in_scope:
            raise StageSkip("nothing_to_review", "No changed files are in review scope")
        return f"{len(ctx.scope.in_scope)}/{len(files)} files in scope"

    async def stage_fetch_source_files(self, ctx: ReviewContext) -> str:
        if ctx.scope is None:
            raise RuntimeError("ctx.scope is missing")
        settings = effective_settings(ctx.repo)
        enabled = set(settings.enabled_languages or [])
        skipped: Counter[str] = Counter()
        wanted = []
        for entry in ctx.scope.in_scope:
            language = detect_language(entry.path)
            if language is None:
                skipped["unsupported_language"] += 1
            elif language not in enabled:
                skipped["language_disabled"] += 1
            else:
                wanted.append((entry, language))

        semaphore = asyncio.Semaphore(self._settings.analysis_fetch_concurrency)

        async def fetch(path: str) -> bytes | None:
            async with semaphore:
                return await self._gh.get_file_content(
                    ctx.installation_id,
                    ctx.repo.full_name,
                    path,
                    ctx.job.head_sha,
                    max_bytes=self._settings.analysis_max_file_bytes,
                )

        contents = await asyncio.gather(*(fetch(entry.path) for entry, _ in wanted))
        for (entry, language), raw in zip(wanted, contents, strict=True):
            if raw is None:
                skipped["missing_or_too_large"] += 1
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped["not_utf8"] += 1
                continue
            ctx.sources.append(SourceFile(entry.path, language, text, parse_patch(entry.patch)))

        rules = await self._session.scalars(
            select(ReviewRule).where(
                ReviewRule.organization_id == ctx.repo.organization_id,
                or_(ReviewRule.repository_id == ctx.repo.id, ReviewRule.repository_id.is_(None)),
            )
        )
        ctx.rule_config = RuleConfig.from_rules(list(rules))
        by_language = Counter(src.language for src in ctx.sources)
        ctx.job.stats = {
            **ctx.job.stats,
            "sources": {
                "analyzed": len(ctx.sources),
                "by_language": dict(sorted(by_language.items())),
                "skipped": dict(sorted(skipped.items())),
            },
        }
        await self._session.commit()
        if not ctx.sources:
            raise StageSkip(
                "no_supported_files",
                "No changed files in an enabled language (Python, Java, JavaScript, TypeScript)",
            )
        langs = ", ".join(f"{n} {lang}" for lang, n in sorted(by_language.items()))
        return f"{len(ctx.sources)} files ({langs})"

    async def stage_semgrep(self, ctx: ReviewContext) -> str:
        findings, stats = await self._analysis.run_semgrep(ctx.sources, ctx.rule_config)
        ctx.findings.extend(findings)
        ctx.job.stats = {**ctx.job.stats, "semgrep": {**stats, "findings": len(findings)}}
        await self._session.commit()
        if stats["status"] != "ok":
            reasons = {"unavailable": "Semgrep is not installed", "timeout": "Semgrep timed out"}
            message = reasons.get(stats["status"], "Semgrep failed")
            raise StageDegraded(f"{message}; continuing without it")
        return f"{len(findings)} findings on changed lines ({stats['raw_matches']} matches total)"

    async def stage_tree_sitter(self, ctx: ReviewContext) -> str:
        findings, stats = await self._analysis.run_tree_sitter(ctx.sources)
        ctx.findings.extend(findings)
        ctx.job.stats = {**ctx.job.stats, "tree_sitter": {**stats, "findings": len(findings)}}
        await self._session.commit()
        return f"{len(findings)} findings"

    async def stage_ai_review(self, ctx: ReviewContext) -> str:
        if not self._settings.ai_review_enabled:
            return "disabled by configuration"
        repo_settings = effective_settings(ctx.repo)
        reviewers = [
            r for r in self._settings.ai_reviewers if f"ai.{r}" not in ctx.rule_config.disabled
        ]
        if not reviewers:
            return "all AI reviewers disabled by review rules"
        try:
            router = self._router_factory(ctx.repo)
        except AINotConfiguredError as exc:
            raise StageDegraded(f"AI review not configured ({exc}); static analysis only") from exc

        static = [f for f, _ in self._analysis.finalize(ctx.findings, ctx.rule_config)]
        request = AIReviewRequest(
            sources=ctx.sources,
            static_findings=static,
            pr_title=ctx.pr.title,
            repository=ctx.repo.full_name,
            reviewers=reviewers,
            min_confidence=max(
                float(repo_settings.min_confidence), self._settings.ai_min_confidence_floor
            ),
            max_input_tokens=self._settings.ai_max_input_tokens,
            max_output_tokens=self._settings.ai_max_output_tokens,
            max_units=self._settings.ai_max_batches_per_reviewer,
            verify=self._settings.ai_verify_findings,
        )
        result = await AIReviewGraph(router, request).run()

        for usage in router.stats.usage:
            self._session.add(
                ModelUsage(
                    organization_id=ctx.job.organization_id,
                    review_job_id=ctx.job.id,
                    provider=usage.provider,
                    model=usage.model,
                    stage=usage.stage,
                    prompt_version=usage.prompt_version,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    cost_usd=usage.cost_usd,
                    latency_ms=usage.latency_ms,
                    success=usage.success,
                    error_type=usage.error_type,
                )
            )
        ctx.findings.extend(result.findings)
        if result.summary:
            ctx.extra["ai_summary"] = result.summary
        ctx.job.prompt_version = prompts.PROMPT_VERSION
        ctx.job.timeline = [*ctx.job.timeline, *result.timeline]
        ctx.job.stats = {
            **ctx.job.stats,
            "ai": {
                **result.stats,
                "risk_level": result.risk_level,
                "highlights": result.highlights,
                "warnings": result.warnings,
            },
        }
        await self._session.commit()

        calls, failures = result.stats["llm_calls"], result.stats["llm_failures"]
        if calls and failures == calls:
            raise StageDegraded("Every AI model call failed; static analysis results only")
        cost = result.stats["cost_usd"]
        return (
            f"{len(result.findings)} findings from {', '.join(result.stats['reviewers_run'])}; "
            f"{calls} LLM calls, cost ${cost}"
        )

    async def stage_persist_findings(self, ctx: ReviewContext) -> str:
        final = self._analysis.finalize(ctx.findings, ctx.rule_config)
        # Idempotent on Celery retries: this job's findings are replaced, never duplicated.
        await self._session.execute(
            delete(ReviewFinding).where(ReviewFinding.review_job_id == ctx.job.id)
        )
        for finding, fingerprint in final:
            self._session.add(
                ReviewFinding(
                    organization_id=ctx.job.organization_id,
                    review_job_id=ctx.job.id,
                    pull_request_id=ctx.job.pull_request_id,
                    fingerprint=fingerprint,
                    **finding.model_dump(),
                )
            )
        ctx.finding_counts = dict(Counter(f.severity.value for f, _ in final))
        ctx.job.stats = {
            **ctx.job.stats,
            "findings": {
                "total": len(final),
                "by_severity": ctx.finding_counts,
                "by_source": dict(Counter(f.source.value for f, _ in final)),
                "suppressed_by_rules": len(ctx.findings) - len(final),
            },
        }
        await self._session.commit()
        return f"{len(final)} findings saved"


STAGES: list[tuple[str, Stage]] = [
    ("fetch_pull_request", ReviewRunner.stage_fetch_pull_request),
    ("fetch_changed_files", ReviewRunner.stage_fetch_changed_files),
    ("fetch_source_files", ReviewRunner.stage_fetch_source_files),
    ("semgrep", ReviewRunner.stage_semgrep),
    ("tree_sitter", ReviewRunner.stage_tree_sitter),
    ("ai_review", ReviewRunner.stage_ai_review),
    ("persist_findings", ReviewRunner.stage_persist_findings),
]


def _default_publish_enqueue(job_id: str) -> None:
    from app.workers.tasks.github import enqueue_publish

    enqueue_publish(job_id)
