# API Documentation

Interactive OpenAPI docs: `http://localhost:8000/docs` (local/test only; disabled in
staging/production). Raw schema: `/openapi.json`.

## Conventions

- Base path `/api/v1`; JSON in and out.
- **Auth:** httpOnly session cookie `rp_session` (set by the GitHub sign-in flow).
- **CSRF:** every `POST`/`PUT`/`PATCH`/`DELETE` must send `X-CSRF-Token` with the
  `csrf_token` from `GET /auth/me`.
- **Tenancy:** organization resources live under `/organizations/{org_id}/…`. Non-members get
  `404` (ids are not confirmable); members lacking the role get `403`.
- **Roles:** `owner > admin > developer > viewer`.
- **Pagination:** `?limit=` (1-100, default 20) and `?offset=`; responses are
  `{"items": [...], "total", "limit", "offset"}`.
- **Errors:** always `{"error": {"code", "message", "details", "request_id"}}`; every
  response carries `X-Request-ID`.
- **Rate limits:** `429 rate_limited` with `details.retry_after_seconds`. Per IP: sign-in,
  organization creation, invitations, webhooks. Per session: every state-changing request
  (120/min) and review run/retry/publish (10/min). Configurable via `RATE_LIMIT_*`.
- **Database unavailable:** `503 service_unavailable` with `Retry-After`; safe to retry.

## Endpoints

### Health and meta

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health/live` | none | Liveness |
| GET | `/health/ready` | none | DB + Redis readiness (`503` when degraded) |
| GET | `/api/v1/meta` | none | Version and enabled features |

### Authentication

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/auth/github/login?next=/path` | none | Redirect to GitHub sign-in |
| GET | `/auth/github/callback` | none | OAuth callback → sets session, redirects to frontend |
| GET | `/auth/me` | session | User, memberships, CSRF token |
| POST | `/auth/logout` | session | Revoke session |

### Organizations and team

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/organizations` | any user | Create (caller becomes owner) |
| GET | `/organizations` | any user | My organizations with counts |
| GET | `/organizations/{org}` | viewer | Details |
| GET | `/organizations/{org}/members` | viewer | Members |
| PATCH | `/organizations/{org}/members/{member}` | admin* | Change role |
| DELETE | `/organizations/{org}/members/{member}` | admin* / self | Remove or leave |
| GET | `/organizations/{org}/invitations` | admin | Pending invitations |
| POST | `/organizations/{org}/invitations` | admin* | Invite a GitHub login → one-time `invite_url` |
| DELETE | `/organizations/{org}/invitations/{id}` | admin* | Revoke |
| POST | `/invitations/{token}/accept` | invitee | Accept (login must match) |

\* Only owners can grant, change or remove the owner role; the last owner can neither leave
nor be demoted (`409 last_owner`).

### GitHub App

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/organizations/{org}/github/install` | admin | Redirect to GitHub App installation |
| GET | `/github/setup` | session | GitHub Setup URL; links installation → redirects to frontend |
| GET | `/organizations/{org}/github/installations` | viewer | Linked installations |
| POST | `/webhooks/github` | HMAC signature | Webhook receiver (`202`; `200` for ping, duplicates and `replay_ignored`) |

### Repositories

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/organizations/{org}/repositories` | viewer | Connected repositories (`?include_disconnected=true`) |
| GET | `/organizations/{org}/repositories/available` | admin | Repositories the installations can access |
| POST | `/organizations/{org}/repositories` | admin | Connect `{"github_repo_id": 123}` |
| GET | `/organizations/{org}/repositories/{repo}` | viewer | Details + settings |
| DELETE | `/organizations/{org}/repositories/{repo}` | admin | Disconnect |
| PATCH | `/organizations/{org}/repositories/{repo}/settings` | admin | Partial update of review settings |
| GET | `/organizations/{org}/repositories/{repo}/rules` | viewer | Repository + org-wide rules |
| PUT | `/organizations/{org}/repositories/{repo}/rules/{rule_key}` | admin | Create/update rule |
| DELETE | `/organizations/{org}/repositories/{repo}/rules/{rule_key}` | admin | Delete rule |

Repository settings fields: `auto_review_enabled`, `review_drafts`,
`require_human_approval`, `publish_inline_comments`, `publish_summary`, `min_confidence`
(0-1), `min_severity_to_publish`, `max_files` (1-300), `max_diff_lines` (50-20000),
`enabled_languages` (`python|java|javascript|typescript`), `ignored_paths` (globs, no `..`),
`llm_provider`, `llm_model`.

### Pull requests and reviews

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/organizations/{org}/pull-requests` | viewer | List (`?state=open\|closed\|merged`, `?repository_id=`), each with `latest_review` |
| GET | `/organizations/{org}/pull-requests/{pr}` | viewer | Details + review history (latest 20) |
| POST | `/organizations/{org}/pull-requests/{pr}/reviews` | developer | Review the current head commit → `202`; `409 review_already_active` with `details.review_id` |
| GET | `/organizations/{org}/reviews/{review}` | viewer | Status, `status_reason`, summary, execution `timeline`, `stats.scope` |
| POST | `/organizations/{org}/reviews/{review}/retry` | developer | New job for a failed/cancelled/skipped review → `202` |
| POST | `/organizations/{org}/reviews/{review}/cancel` | developer | Cancel a queued/running/awaiting-approval review |
| POST | `/organizations/{org}/reviews/{review}/publish` | developer | Approve an `awaiting_approval` review or retry a `publish_failed` one → `202 publishing`; `409 review_stale` if the PR moved |

Review statuses: `queued`, `running`, `completed`, `failed`, `cancelled`, `skipped`
`awaiting_approval` and `publishing` (Phase 6). Publication details are in `stats.publication`.

### Findings

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/organizations/{org}/reviews/{review}/findings` | viewer | Findings of one review, most severe first |
| GET | `/organizations/{org}/findings` | viewer | Search; filters `severity`, `category`, `status`, `source`, `repository_id`, `pull_request_id`; paginated |
| GET | `/organizations/{org}/findings/{finding}` | viewer | Details |
| PATCH | `/organizations/{org}/findings/{finding}` | developer | `{"status": "open\|resolved\|ignored\|false_positive"}` (audited) |

Finding shape: `title`, `category`, `severity`, `confidence`, `file_path`, `start_line`,
`end_line`, `description`, `impact`, `suggested_fix`, `code_suggestion`, `source`
(`semgrep`, `tree_sitter`, `ai`), `evidence`, `rule_id`, `fingerprint`, `status`.

| GET | `/organizations/{org}/findings/{finding}/feedback` | viewer | `{"counts": {"helpful", "not_helpful", "false_positive"}, "mine", "my_comment"}` |
| PUT | `/organizations/{org}/findings/{finding}/feedback` | viewer | `{"rating": "helpful\|not_helpful\|false_positive", "comment"?}`; one rating per user (replaces the previous one); audited |

### Analytics

All analytics endpoints are read-only, available to every member (viewer and up) and accept
`?days=` (1-365, default 30). Dates are UTC calendar days; daily series contain every day in the
window, zero-filled.

| Method | Path | Returns |
|---|---|---|
| GET | `/organizations/{org}/analytics/overview` | `connected_repositories`, `reviews_total`, `pull_requests_reviewed`, `findings_total`, `open_findings`, `findings_by_severity`, `avg_review_duration_ms`, `recent_reviews[]`, `recent_security_findings[]` |
| GET | `/organizations/{org}/analytics/timeseries` | `reviews[] {date, completed, failed, other}`, `findings[] {date, critical, high, medium, low, info}` |
| GET | `/organizations/{org}/analytics/ai-usage` | `totals {calls, input_tokens, output_tokens, cost_usd, reviews, cost_per_review_usd}`, `models[] {provider, model, calls, success_rate, tokens, cost_usd, avg_latency_ms}`, `stages[]`, `per_day[] {date, cost_usd, tokens}` |
| GET | `/organizations/{org}/analytics/quality` | `sources[] {source, total, status{...}, feedback{...}, false_positive_rate, helpful_rate}` |
| GET | `/organizations/{org}/analytics/team` | `authors[] {author, pull_requests, reviews, findings, critical_or_high}` |

`false_positive_rate` = findings with status `false_positive` / findings reported.
`helpful_rate` = helpful ratings / all ratings (`null` when nobody rated).

### AI configuration

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/organizations/{org}/ai/config` | viewer | Provider, whether it is configured, endpoint host (OpenAI-compatible only), primary/fallback/fast models, effort, token budget, reviewers, verification, confidence floor, prompt version history, publishing mode. Never returns API keys. |
