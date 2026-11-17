"""Organization-scoped analytics. Every query filters on ``organization_id`` first."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, case, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FindingFeedback,
    ModelUsage,
    PullRequest,
    Repository,
    ReviewFinding,
    ReviewJob,
)
from app.models.enums import FindingCategory, FindingStatus, ReviewStatus, Severity

SEVERITIES = [s.value for s in sorted(Severity, key=lambda s: -s.rank)]


def _days(since: date, until: date) -> list[date]:
    return [since + timedelta(days=i) for i in range((until - since).days + 1)]


class AnalyticsService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, days: int) -> None:
        self._s = session
        self._org = org_id
        self.days = days
        self.until = datetime.now(UTC)
        self.since = (self.until - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    async def _scalar(self, stmt: Any) -> Any:
        return await self._s.scalar(stmt)

    # ---- overview -------------------------------------------------------------------

    async def overview(self) -> dict[str, Any]:
        jobs = (ReviewJob.organization_id == self._org, ReviewJob.created_at >= self.since)
        findings = (
            ReviewFinding.organization_id == self._org,
            ReviewFinding.created_at >= self.since,
        )

        severity_rows = await self._s.execute(
            select(ReviewFinding.severity, func.count())
            .where(*findings)
            .group_by(ReviewFinding.severity)
        )
        by_severity = {s: 0 for s in SEVERITIES}
        for sev, n in severity_rows:
            by_severity[sev.value] = n

        recent_reviews = await self._s.execute(
            select(ReviewJob, PullRequest, Repository.full_name)
            .join(PullRequest, PullRequest.id == ReviewJob.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .where(ReviewJob.organization_id == self._org)
            .order_by(ReviewJob.created_at.desc())
            .limit(6)
        )
        recent_security = await self._s.execute(
            select(ReviewFinding, Repository.full_name, PullRequest.number)
            .join(PullRequest, PullRequest.id == ReviewFinding.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .where(
                ReviewFinding.organization_id == self._org,
                ReviewFinding.category == FindingCategory.SECURITY,
                ReviewFinding.severity.in_([Severity.CRITICAL, Severity.HIGH]),
            )
            .order_by(ReviewFinding.created_at.desc())
            .limit(6)
        )
        avg_ms = await self._scalar(
            select(func.avg(ReviewJob.duration_ms)).where(
                *jobs,
                ReviewJob.status == ReviewStatus.COMPLETED,
                ReviewJob.duration_ms.is_not(None),
            )
        )
        return {
            "days": self.days,
            "connected_repositories": int(
                await self._scalar(
                    select(func.count())
                    .select_from(Repository)
                    .where(
                        Repository.organization_id == self._org, Repository.is_connected.is_(True)
                    )
                )
                or 0
            ),
            "reviews_total": int(
                await self._scalar(select(func.count()).select_from(ReviewJob).where(*jobs)) or 0
            ),
            "pull_requests_reviewed": int(
                await self._scalar(
                    select(func.count(distinct(ReviewJob.pull_request_id))).where(
                        *jobs, ReviewJob.status == ReviewStatus.COMPLETED
                    )
                )
                or 0
            ),
            "findings_total": sum(by_severity.values()),
            "open_findings": int(
                await self._scalar(
                    select(func.count())
                    .select_from(ReviewFinding)
                    .where(*findings, ReviewFinding.status == FindingStatus.OPEN)
                )
                or 0
            ),
            "findings_by_severity": by_severity,
            "avg_review_duration_ms": int(avg_ms) if avg_ms is not None else None,
            "recent_reviews": [
                {
                    "id": str(job.id),
                    "status": job.status.value,
                    "created_at": job.created_at.isoformat(),
                    "duration_ms": job.duration_ms,
                    "findings": int(((job.stats or {}).get("findings") or {}).get("total", 0)),
                    "pull_request": {"id": str(pr.id), "number": pr.number, "title": pr.title},
                    "repository": repo_name,
                }
                for job, pr, repo_name in recent_reviews
            ],
            "recent_security_findings": [
                {
                    "id": str(f.id),
                    "title": f.title,
                    "severity": f.severity.value,
                    "file_path": f.file_path,
                    "start_line": f.start_line,
                    "status": f.status.value,
                    "created_at": f.created_at.isoformat(),
                    "repository": repo_name,
                    "pull_request_number": number,
                    "pull_request_id": str(f.pull_request_id),
                }
                for f, repo_name, number in recent_security
            ],
        }

    # ---- activity over time -----------------------------------------------------------

    async def timeseries(self) -> dict[str, Any]:
        day = cast(ReviewJob.created_at, Date)
        review_rows = await self._s.execute(
            select(day, ReviewJob.status, func.count())
            .where(ReviewJob.organization_id == self._org, ReviewJob.created_at >= self.since)
            .group_by(day, ReviewJob.status)
        )
        fday = cast(ReviewFinding.created_at, Date)
        finding_rows = await self._s.execute(
            select(fday, ReviewFinding.severity, func.count())
            .where(
                ReviewFinding.organization_id == self._org, ReviewFinding.created_at >= self.since
            )
            .group_by(fday, ReviewFinding.severity)
        )
        days = _days(self.since.date(), self.until.date())
        reviews = {
            d: {"date": d.isoformat(), "completed": 0, "failed": 0, "other": 0} for d in days
        }
        for d, status, n in review_rows:
            if d in reviews:
                key = status.value if status.value in ("completed", "failed") else "other"
                reviews[d][key] += n
        findings = {d: {"date": d.isoformat(), **{s: 0 for s in SEVERITIES}} for d in days}
        for d, sev, n in finding_rows:
            if d in findings:
                findings[d][sev.value] += n
        return {
            "days": self.days,
            "reviews": list(reviews.values()),
            "findings": list(findings.values()),
        }

    # ---- AI usage and cost ----------------------------------------------------------

    async def ai_usage(self) -> dict[str, Any]:
        where = (ModelUsage.organization_id == self._org, ModelUsage.created_at >= self.since)
        model_rows = await self._s.execute(
            select(
                ModelUsage.provider,
                ModelUsage.model,
                func.count(),
                func.sum(case((ModelUsage.success.is_(True), 1), else_=0)),
                func.sum(ModelUsage.input_tokens),
                func.sum(ModelUsage.output_tokens),
                func.sum(ModelUsage.cost_usd),
                func.avg(ModelUsage.latency_ms),
            )
            .where(*where)
            .group_by(ModelUsage.provider, ModelUsage.model)
            .order_by(func.count().desc())
        )
        stage_rows = await self._s.execute(
            select(
                ModelUsage.stage,
                func.count(),
                func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens),
            )
            .where(*where)
            .group_by(ModelUsage.stage)
            .order_by(func.count().desc())
        )
        uday = cast(ModelUsage.created_at, Date)
        day_rows = await self._s.execute(
            select(
                uday,
                func.sum(ModelUsage.cost_usd),
                func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens),
            )
            .where(*where)
            .group_by(uday)
        )
        per_day = {
            d: {"date": d.isoformat(), "cost_usd": 0.0, "tokens": 0}
            for d in _days(self.since.date(), self.until.date())
        }
        for d, cost, tokens in day_rows:
            if d in per_day:
                per_day[d] = {
                    "date": d.isoformat(),
                    "cost_usd": float(cost or 0),
                    "tokens": int(tokens or 0),
                }
        models = [
            {
                "provider": provider,
                "model": model,
                "calls": int(calls),
                "success_rate": round(int(ok or 0) / int(calls), 3) if calls else 0.0,
                "input_tokens": int(tin or 0),
                "output_tokens": int(tout or 0),
                "cost_usd": float(cost or Decimal(0)),
                "avg_latency_ms": int(lat or 0),
            }
            for provider, model, calls, ok, tin, tout, cost, lat in model_rows
        ]
        reviews_with_ai = int(
            await self._scalar(select(func.count(distinct(ModelUsage.review_job_id))).where(*where))
            or 0
        )
        total_cost = sum(m["cost_usd"] for m in models)
        return {
            "days": self.days,
            "totals": {
                "calls": sum(m["calls"] for m in models),
                "input_tokens": sum(m["input_tokens"] for m in models),
                "output_tokens": sum(m["output_tokens"] for m in models),
                "cost_usd": round(total_cost, 6),
                "reviews": reviews_with_ai,
                "cost_per_review_usd": round(total_cost / reviews_with_ai, 6)
                if reviews_with_ai
                else 0.0,
            },
            "models": models,
            "stages": [
                {"stage": s, "calls": int(n), "tokens": int(t or 0)} for s, n, t in stage_rows
            ],
            "per_day": list(per_day.values()),
        }

    # ---- finding quality ----------------------------------------------------------

    async def quality(self) -> dict[str, Any]:
        where = (ReviewFinding.organization_id == self._org, ReviewFinding.created_at >= self.since)
        status_rows = await self._s.execute(
            select(ReviewFinding.source, ReviewFinding.status, func.count())
            .where(*where)
            .group_by(ReviewFinding.source, ReviewFinding.status)
        )
        feedback_rows = await self._s.execute(
            select(ReviewFinding.source, FindingFeedback.rating, func.count())
            .join(FindingFeedback, FindingFeedback.finding_id == ReviewFinding.id)
            .where(*where)
            .group_by(ReviewFinding.source, FindingFeedback.rating)
        )
        sources: dict[str, dict[str, Any]] = {}

        def bucket(source: str) -> dict[str, Any]:
            return sources.setdefault(
                source,
                {
                    "source": source,
                    "total": 0,
                    "status": {"open": 0, "resolved": 0, "ignored": 0, "false_positive": 0},
                    "feedback": {"helpful": 0, "not_helpful": 0, "false_positive": 0},
                },
            )

        for source, status, n in status_rows:
            b = bucket(source.value)
            b["status"][status.value] += n
            b["total"] += n
        for source, rating, n in feedback_rows:
            bucket(source.value)["feedback"][rating.value] += n
        for b in sources.values():
            fp = b["status"]["false_positive"]
            b["false_positive_rate"] = round(fp / b["total"], 3) if b["total"] else 0.0
            rated = sum(b["feedback"].values())
            b["helpful_rate"] = round(b["feedback"]["helpful"] / rated, 3) if rated else None
        return {"days": self.days, "sources": sorted(sources.values(), key=lambda b: -b["total"])}

    # ---- team ---------------------------------------------------------------------

    async def team(self) -> dict[str, Any]:
        rows = await self._s.execute(
            select(
                PullRequest.author_login,
                func.count(distinct(PullRequest.id)),
                func.count(distinct(ReviewJob.id)),
            )
            .join(ReviewJob, ReviewJob.pull_request_id == PullRequest.id)
            .where(PullRequest.organization_id == self._org, ReviewJob.created_at >= self.since)
            .group_by(PullRequest.author_login)
        )
        finding_rows = await self._s.execute(
            select(
                PullRequest.author_login,
                func.count(ReviewFinding.id),
                func.sum(
                    case(
                        (ReviewFinding.severity.in_([Severity.CRITICAL, Severity.HIGH]), 1), else_=0
                    )
                ),
            )
            .join(ReviewFinding, ReviewFinding.pull_request_id == PullRequest.id)
            .where(PullRequest.organization_id == self._org, ReviewFinding.created_at >= self.since)
            .group_by(PullRequest.author_login)
        )
        findings = {author: (int(n), int(severe or 0)) for author, n, severe in finding_rows}
        authors = [
            {
                "author": author,
                "pull_requests": int(prs),
                "reviews": int(reviews),
                "findings": findings.get(author, (0, 0))[0],
                "critical_or_high": findings.get(author, (0, 0))[1],
            }
            for author, prs, reviews in rows
        ]
        return {"days": self.days, "authors": sorted(authors, key=lambda a: -a["reviews"])}
