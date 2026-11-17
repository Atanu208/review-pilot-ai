# Security

How ReviewPilot AI protects accounts, tenant data, credentials and the GitHub repositories
it reviews, and how each control is verified. Read this before changing authentication,
webhooks, the review pipeline or anything that writes to GitHub.

Threat model in one paragraph: the application holds GitHub App credentials that can comment
on customers' pull requests, OAuth tokens of its users, and LLM API keys. It processes
**untrusted input from three directions**: browsers (authenticated users of other
organizations), the internet (anything can hit the webhook URL), and the code under review
(any contributor can open a pull request containing hostile text aimed at the LLM or at
reviewers reading our comments).

## Controls at a glance

| Requirement | Implementation | Verified by |
|---|---|---|
| Secure authentication | GitHub OAuth (signed `state` + nonce cookie), server-side sessions: random 256-bit token in an `httpOnly` cookie, only its SHA-256 stored, 7-day expiry, revocable on logout | `test_auth_api.py`, `test_expired_and_revoked_sessions_are_rejected` |
| CSRF | Every state-changing request needs `X-CSRF-Token` = HMAC(secret, session hash), delivered by `GET /auth/me`, kept in memory by the SPA; `SameSite=Lax` cookie as a second layer | `test_state_changing_routes_require_csrf_token` (every write route) |
| Role-based authorization | `viewer < developer < admin < owner`, enforced by dependencies before any work; the last owner cannot be removed or demoted | `test_role_matrix` (every write route × role), `test_organizations_api.py` |
| Organization-level isolation | Every tenant query filters on an indexed `organization_id`; non-members get **404** (not 403) so ids cannot be probed | `test_outsiders_get_404_on_every_organization_route` (every org route), tenant tests per module |
| Anonymous access | Only an explicit allowlist is public (health, meta, OAuth login/callback, App setup redirect, webhook) | `test_anonymous_requests_are_rejected_everywhere_but_public_routes` |
| Webhook signature validation | HMAC-SHA256 over the raw body, constant-time compare, before parsing; 5 MB body cap; header format validation | `test_webhooks.py`, `test_security.py` |
| Webhook replay | Delivery id is unique (idempotent re-delivery); an identical signed body under a **new** delivery id within 72 h is acknowledged and dropped | `test_replayed_body_under_new_delivery_id_is_ignored`, `test_replay_window_expires` |
| Encrypted token storage | OAuth tokens and cached installation tokens encrypted with Fernet (`MultiFernet` for key rotation); the GitHub App key is a mounted file, never in the image | `test_security.py`, `test_tokens.py` |
| Secret redaction | Log processor masks sensitive keys and token-shaped strings (GitHub, Anthropic, OpenAI, Groq, AWS, Google, Slack, Stripe, bearer tokens, private keys); code sent to an LLM and comments posted to GitHub are redacted too | `test_redact_*`, `test_review_format.py` |
| Input validation | Pydantic schemas with length caps, enums, regexes (logins, rule keys, model names, path globs), bounded pagination; malformed JSON → 422 envelope | `test_oversized_and_hostile_inputs_are_rejected`, schema tests |
| SQL injection | SQLAlchemy Core/ORM with bound parameters only; no string-built SQL anywhere (Bandit clean) | Bandit in CI, hostile-input tests |
| Rate limiting | Redis fixed windows. Per IP: login/callback 30/min, webhooks 1000/min. Per session: all writes 120/min, review run/retry/publish 10/min (LLM cost). Fails open if Redis is down | `test_rate_limits.py`, `test_webhook_flood_is_rate_limited_per_ip`, `test_login_rate_limited` |
| CORS | Explicit origin allowlist (`CORS_ORIGINS`), credentials allowed only for those origins, fixed method/header lists | `test_cors_*` |
| Secure cookies | `httpOnly`, `SameSite=Lax`, `Secure` whenever the backend is HTTPS or the environment is staging/production | `test_auth_api.py` |
| Security headers | API: `nosniff`, `X-Frame-Options: DENY`, referrer policy, permissions policy, COOP, HSTS in production — on **every** response including 500s. Frontend: strict CSP (`default-src 'self'`, API-only `connect-src`, `frame-ancestors 'none'`, `object-src 'none'`) | `test_request_id_generated_and_security_headers`, `test_unhandled_error_is_generic_and_leaks_nothing`, Playwright `security.spec.ts` |
| Audit logging | Append-only `audit_logs` for sign-in/out, organization, membership, invitation, installation link, repository connect/disconnect/settings, rule changes, review trigger/retry/cancel/approve/publish, finding status and feedback; actor, IP, user agent, request id | integration tests per action |
| SSRF | Outbound HTTP only to configured hosts: the GitHub client refuses URLs outside its base URL (pagination links included) and never follows redirects; no endpoint fetches user-supplied URLs; the LLM endpoint is operator configuration, not user input | `test_github_client.py` |
| Prompt injection | Untrusted code wrapped in nonce fences, explicit "data, not instructions" system prompts, detection of instruction-like text, model output schema-validated and **grounded** against the real diff (unknown files, unchanged lines and quotes not present in the code are dropped); the model has no tools and triggers no actions | `test_ai.py`, `test_ai_review.py` |
| Safe GitHub comment publishing | Mentions neutralised, marker-forging HTML comments stripped, secrets redacted, code fences escaped, length caps; posts only `COMMENT` reviews (never approve/request changes); stale PRs are not commented on | `test_review_format.py`, `test_publishing.py` |
| Untrusted source code | Never executed. Only changed files are fetched (size/count caps) into a temp dir with path-traversal checks; Semgrep runs with `--metrics=off`, no network rules, CPU/memory/time limits, empty `HOME`, then the dir is deleted | `test_static_analysis.py`, `test_analysis.py` |
| Error handling | Uniform envelope with `request_id`; internal errors and database outages never echo exception text (hosts, SQL, credentials); DB loss → `503 + Retry-After` | `test_failures.py` |
| Secrets in the repository | `.env`, `secrets/`, `*.pem` ignored by git and Docker; gitleaks in CI; settings refuse to start outside local/test without real `SECRET_KEY`/`ENCRYPTION_KEY` or with debug/demo mode | `test_config.py`, CI `security` job |

## Automated security checks (CI `security` job)

| Tool | Scope | Current result |
|---|---|---|
| Bandit | Python source (excluding tests) | 0 issues (one documented false positive: a Redis key prefix named like a token) |
| pip-audit | `backend/requirements.lock` | 0 known vulnerabilities |
| npm audit | `frontend/package-lock.json` | 0 vulnerabilities |
| Gitleaks | Full git history | runs in CI |

Run them locally:

```bash
cd backend
pip install -e ".[dev]"
bandit -q -r app -x app/tests
pip-audit -r requirements.lock
cd ../frontend && npm audit
```

## Findings fixed in Phase 8

| Finding | Risk | Fix |
|---|---|---|
| A captured webhook could be replayed under a new `X-GitHub-Delivery` id (GitHub signs only the body) | Re-triggered reviews, LLM spend, duplicate comments | Body-hash replay detection with a 72 h window (indexed `payload_sha256`) |
| No limit on review run/retry/publish or on general writes | One account could burn LLM budget and GitHub quota | Per-session limits (10/min review actions, 120/min writes) |
| Webhook endpoint unthrottled | Floods of unsigned requests consume CPU/DB connections | Per-IP limit (checked before signature and database work) |
| Unhandled-error responses skipped the security-header middleware | 500 pages without `nosniff`/frame protection or request id | Request middleware now renders the generic 500 itself |
| Database outages surfaced as generic 500 | Clients could not tell transient failures apart | `503` + `Retry-After`, no driver message in the body |
| Redaction missed Groq (`gsk_`), AWS, Google, Slack and Stripe key formats | A pasted key could reach logs, the LLM or a GitHub comment unmasked | Patterns added, each covered by a test |
| `assert`-based invariants in services | Silently skipped under `python -O` | Explicit checks raising errors |
| `cryptography` 49.0.0 (PYSEC-2026-3552) | Known vulnerability in the encryption dependency | Upgraded to 50.0.0 |
| `minimatch` ReDoS through shadcn CLI / typescript-eslint | Dev/build tooling only | Forced patched `minimatch` 11.0.9 via npm `overrides` |
| No Content-Security-Policy on the frontend | XSS impact not contained | Strict CSP (see table), verified to cause no violations |

## Operating securely

- **Secrets**: set `SECRET_KEY` and `ENCRYPTION_KEY` (the app refuses to start without them in
  staging/production). Rotate `ENCRYPTION_KEY` by prepending a new key: `ENCRYPTION_KEY=new,old`.
- **Behind a reverse proxy / load balancer**: run uvicorn with `--proxy-headers
  --forwarded-allow-ips=<proxy ip>` so rate limits see real client IPs; terminate TLS so
  cookies are `Secure` and HSTS is sent.
- **Webhook secret**: use a long random value; rotate it in GitHub and `.env` together.
- **Publishing**: `GITHUB_PUBLISH_ENABLED=false` keeps all results inside ReviewPilot; per
  repository, "require human approval" makes every comment wait for a developer.
- **Data retention**: webhook payloads are kept for replay detection and debugging; prune
  `webhook_deliveries` older than your retention period.

## Known limitations

- The CSP allows inline scripts because Next.js and the theme bootstrapper use them; there is
  no user-controlled HTML anywhere in the UI, which keeps this low-risk. Moving to nonce-based
  CSP requires dynamic rendering of every page.
- Rate limits are fixed windows (bursts of up to 2× the limit across a window boundary are
  possible) and fail open when Redis is unavailable, by design.
- The LLM endpoint (`OPENAI_BASE_URL`) is trusted operator configuration. Do not expose it to
  tenants without adding an allowlist.
- Two simultaneous copies of the same replayed webhook can both pass the replay check; the
  one-active-review-per-commit database constraint still prevents duplicate reviews.
