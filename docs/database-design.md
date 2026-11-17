# Database Design

PostgreSQL 17, managed by Alembic (`backend/alembic/versions`). The ORM models in
`backend/app/models` are the source of truth; `alembic check` (run in the test suite)
fails if models and migrations ever drift.

## Conventions

- UUID primary keys (`id`), `created_at` / `updated_at` (`timestamptz`, server defaults).
- Enums stored as `VARCHAR(32)` + `CHECK` constraint.
- Deterministic constraint names: `pk_<table>`, `fk_<table>_<col>_<ref>`, `uq_<table>_<cols>`,
  `ix_<table>_<cols>`, `ck_<table>_<name>`.
- **Tenant isolation:** every tenant-owned row carries `organization_id` (directly, or via a
  parent that does). Deleting an organization cascades to all of its data.

## Entity overview

```
users ─┬─< organization_members >─ organizations ─┬─< github_installations ─< repositories
       ├─< user_sessions                          ├─< organization_invitations
       └─< finding_feedback                       ├─< review_rules (org-wide or per repo)
                                                  └─< audit_logs
repositories ─┬─ repository_settings (1:1)
              └─< pull_requests ─< review_jobs ─┬─< review_findings ─< finding_feedback
                                                ├─< review_comments
                                                └─< model_usage
webhook_deliveries (standalone, keyed by GitHub delivery id)
```

## Tables

| Table | Purpose | Key constraints / indexes |
|---|---|---|
| `users` | GitHub-authenticated users; encrypted user token | `uq(github_id)`, `ix(login)`, `ix(email)` |
| `user_sessions` | server-side sessions (token SHA-256 only) | `uq(token_hash)`, `ix(user_id)` |
| `organizations` | tenants | `uq(slug)` |
| `organization_members` | membership + role (owner/admin/developer/viewer) | `uq(organization_id, user_id)` |
| `organization_invitations` | invitations to a GitHub login (hashed token) | partial unique `(organization_id, github_login) WHERE status='pending'` |
| `github_installations` | GitHub App installations, linked to an org | `uq(installation_id)` |
| `repositories` | connected repos | `uq(github_repo_id)`, `ix(organization_id, is_connected)` |
| `repository_settings` | per-repo review policy (thresholds, approval, languages, model override) | `uq(repository_id)`, `CHECK 0 ≤ min_confidence ≤ 1`, positive limits |
| `review_rules` | rule enable/disable and severity overrides | `uq(org, repo, rule_key)` + partial unique `(org, rule_key) WHERE repository_id IS NULL` |
| `pull_requests` | PR metadata snapshot | `uq(repository_id, number)`, `ix(organization_id, updated_at)` |
| `review_jobs` | one review execution; status, timeline, stats, summary | partial unique `(pull_request_id, head_sha) WHERE status IN ('queued','running')`, `ix(org, status)`, `ix(org, created_at)` |
| `review_findings` | normalised findings (AI + static) | `uq(review_job_id, fingerprint)`, `CHECK confidence ∈ [0,1]`, `CHECK 1 ≤ start_line ≤ end_line`, `ix(org, severity, created_at)`, `ix(pull_request_id, fingerprint)` |
| `finding_feedback` | per-user rating of a finding | `uq(finding_id, user_id)` |
| `review_comments` | comments published (or attempted) on GitHub | `uq(github_comment_id)` |
| `model_usage` | per-LLM-call tokens, cost, latency | `ix(org, created_at)`, `ix(provider, model)` |
| `audit_logs` | privileged actions | `ix(org, created_at)`, `ix(resource_type, resource_id)` |
| `webhook_deliveries` | every webhook received, for idempotency and replay | `uq(delivery_id)`, `ix(event, created_at)` |

## Notable design choices

- **Fingerprints** (`review_findings.fingerprint`) hash file, rule/title and normalised code
  context. They power deduplication within a job (unique constraint) and historical
  comparison across jobs for the same PR (index), e.g. "new / still present / fixed".
- **Denormalised `organization_id`** on `pull_requests`, `review_jobs`, `review_findings` and
  `model_usage` avoids multi-join tenant checks on hot dashboard and analytics queries.
- **Execution timeline** is a JSONB array on `review_jobs` (written once per stage by a single
  worker); it's read as a whole and never queried by field, so a child table isn't needed.
- **Encrypted columns** (`users.github_token_encrypted`) hold Fernet ciphertext only.

## Migrations

```bash
cd backend
alembic revision --autogenerate -m "describe change"   # review the generated file!
alembic upgrade head
alembic downgrade -1
alembic check                                          # fails on model/migration drift
```

In Docker Compose, the one-shot `migrate` service runs `alembic upgrade head` before the
API and worker start.
