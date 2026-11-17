# Architecture

ReviewPilot AI is a **modular monolith**: one FastAPI codebase deployed as two process
types (API and Celery worker), with a Next.js frontend, PostgreSQL and Redis. Module
boundaries are enforced by package layout and dependency direction, not by network hops,
so the system stays simple to run while keeping a path open to extract services later.

```
                     ┌────────────────────────┐
  Browser ──HTTPS──▶ │  Next.js (frontend)    │  no secrets; httpOnly session cookie
                     └──────────┬─────────────┘
                                │ REST /api/v1 (CORS allow-list, credentials)
                                ▼
 GitHub ──webhook──▶ ┌────────────────────────┐   ┌──────────────┐
  (signed HMAC)      │  FastAPI (backend)     │──▶│ PostgreSQL 17│
                     │  verify → store → 202  │   └──────────────┘
                     └──────────┬─────────────┘          ▲
                                │ enqueue (JSON only)    │
                                ▼                        │
                     ┌────────────────────────┐          │
                     │  Redis 7 (broker,      │          │
                     │  results, rate limits) │          │
                     └──────────┬─────────────┘          │
                                ▼                        │
                     ┌────────────────────────┐          │
                     │  Celery workers        │──────────┘
                     │  queues: reviews,      │──▶ GitHub REST API (installation tokens)
                     │  github, default       │──▶ LLM providers (Anthropic / OpenAI-compatible)
                     │  Semgrep + Tree-sitter │    (sandboxed subprocesses, resource-limited)
                     └────────────────────────┘
```

## Request paths

**Webhook (hot path, must be fast).** `POST /api/v1/webhooks/github` → verify
`X-Hub-Signature-256` (constant-time) → insert into `webhook_deliveries` keyed by
`X-GitHub-Delivery` (unique constraint = idempotency) → enqueue a Celery task → `202`.
No GitHub or LLM calls happen inside the request.

**Review job (worker).** Fetch PR + diff → static analysis → LangGraph AI stages →
validation, dedupe, scoring → persist findings → (optional human approval) → publish
review on GitHub. A partial unique index guarantees at most one *active* job per
`(pull_request_id, head_sha)`.

**Dashboard.** Next.js calls the API with the session cookie; every query is scoped by
`organization_id` after an RBAC check (Owner > Admin > Developer > Viewer).

## Backend layout

| Package | Responsibility |
|---|---|
| `app/api` | HTTP routers only: parse/validate input, call a service, shape the response |
| `app/core` | config, logging, DB/Redis lifecycle, middleware, error envelope |
| `app/models` | SQLAlchemy 2.0 ORM models (single source of truth for Alembic) |
| `app/schemas` | Pydantic v2 request/response models |
| `app/services` | business logic and transactions |
| `app/repositories` | tenant-scoped data access |
| `app/workers` | Celery app and tasks (thin wrappers around services) |
| `app/ai` | provider abstraction, LangGraph pipeline, versioned prompts, evals |
| `app/analysis` | Semgrep, Tree-sitter, language detection, finding normalisation |
| `app/integrations/github` | App JWT, installation tokens, REST client, webhook event handlers |
| `app/security` | encryption, signatures, authz helpers |

Dependency direction: `api → services → repositories → models`; `workers → services`.
Route handlers never contain business logic.

## Cross-cutting decisions

| Concern | Decision |
|---|---|
| IDs | UUIDv4 primary keys; GitHub numeric IDs stored as `BIGINT` with unique constraints |
| Enums | `VARCHAR + CHECK` (non-native) so adding values is a simple migration |
| Constraint names | deterministic naming convention → stable Alembic diffs |
| Sessions | random token in httpOnly cookie, SHA-256 stored in `user_sessions` (revocable) |
| Secrets at rest | Fernet (`MultiFernet` for rotation) for GitHub tokens and provider keys |
| Serialisation | Celery accepts JSON only (never pickle: payloads derive from webhooks) |
| Logging | structlog JSON, `request_id` / `task_id` / `review_job_id` bound via contextvars, secret redaction processor |
| Errors | one envelope: `{"error": {code, message, details, request_id}}` |
| Untrusted code | repository content is data: never executed; analysed by Semgrep/Tree-sitter in resource-limited subprocesses and passed to LLMs inside delimited, instruction-isolated blocks |
| Docs | OpenAPI at `/docs` in local/test only; disabled in staging/production |

## Technology versions (resolved 2026-11-15)

Backend (`backend/requirements.lock` is authoritative): Python 3.12, FastAPI 0.163.1,
Starlette 1.10, Pydantic 2.14.3, pydantic-settings 2.16.3, SQLAlchemy 2.0.57, asyncpg 0.32,
Alembic 1.20.1, Celery 5.7.0, redis-py 6.4.0, structlog 26.1, cryptography 49,
pytest 9.2 / pytest-asyncio 1.5, ruff 0.17.5, mypy 1.20.2.

Frontend (`frontend/package-lock.json` is authoritative): Next.js 16.4.6, React 19.3,
TypeScript 5.9, Tailwind CSS 4.3, shadcn/ui CLI 4.23 (Base UI 1.9 primitives),
TanStack Query 5.107, Recharts 3.10, lucide-react 1.73, next-themes 0.4, Playwright 1.65.

Infrastructure: PostgreSQL 17, Redis 7.4, Docker Compose v2 spec.

## Technical risks

1. **LLM false positives / hallucinated lines.** Mitigation: static analysis first, a
   validation stage that requires evidence present in the diff, confidence thresholds,
   and diff-position validation before any inline comment is published.
2. **Prompt injection via repository content.** Mitigation: code passed only as delimited
   data, system prompts forbid following embedded instructions, outputs are schema-validated
   and never trigger tool calls with side effects.
3. **GitHub rate limits on large PRs.** Mitigation: per-installation token caching,
   conditional requests, pagination, back-off on `403/429` with `Retry-After`, file/diff caps.
4. **Cost blow-up.** Mitigation: skip unchanged/generated/vendored files, per-repo caps,
   per-stage token accounting in `model_usage`, cheaper models for triage stages.
5. **Running Semgrep on untrusted code.** Semgrep doesn't execute code, but runs with CPU,
   memory and time limits, a temp directory, and no network.
6. **Webhook storms.** Mitigation: idempotent deliveries; superseding queued jobs when a
   newer `synchronize` event arrives for the same PR.
