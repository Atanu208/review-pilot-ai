# GitHub Integration

ReviewPilot uses one **GitHub App** for three things:

| Purpose | Mechanism | Credentials |
|---|---|---|
| Sign in users | GitHub App user authorization (OAuth web flow) | Client ID + client secret |
| Read PRs / post reviews | Installation access tokens (server-to-server) | App ID + private key → JWT |
| Receive events | Signed webhooks | Webhook secret (HMAC-SHA256) |

## 1. Register the App

GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.

| Field | Local development value |
|---|---|
| Homepage URL | `http://localhost:3000` |
| Callback URL | `http://localhost:8000/api/v1/auth/github/callback` |
| Expire user authorization tokens | on (refresh is handled automatically) |
| Request user authorization during installation | **off** (it disables the Setup URL) |
| Setup URL | `http://localhost:8000/api/v1/github/setup` |
| Redirect on update | on |
| Webhook URL | a relay URL from https://smee.io/new (GitHub cannot reach `localhost`) |
| Webhook secret | `python -c "import secrets; print(secrets.token_hex(32))"` |

**Permissions (least privilege):** Repository: *Pull requests* read & write, *Contents* read-only,
*Metadata* read-only. Account: *Email addresses* read-only (optional). Nothing else.

**Events:** *Pull request* (plus *Meta*, optional). `installation` and
`installation_repositories` events are delivered to every App automatically.

In production replace `localhost` URLs with your public frontend/API domains and point the
webhook directly at `https://<api-domain>/api/v1/webhooks/github`.

## 2. Configure ReviewPilot

In the repo-root `.env`:

```env
GITHUB_APP_ID=...
GITHUB_APP_SLUG=...            # from github.com/settings/apps/<slug>
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITHUB_WEBHOOK_SECRET=...
GITHUB_APP_PRIVATE_KEY_PATH=../secrets/github-app.pem   # host runs; Docker mounts ./secrets
SMEE_URL=https://smee.io/<channel>
```

Save the downloaded private key as `secrets/github-app.pem` (the folder is git-ignored and
mounted read-only into the API and worker containers).

Verify: `curl localhost:8000/api/v1/meta` shows `"github_oauth": true, "github_app": true`.

## 3. Local webhook relay

```bash
npx smee-client --url "$SMEE_URL" --target http://localhost:8000/api/v1/webhooks/github
```

## Flows

### Sign-in

1. `GET /api/v1/auth/github/login?next=/dashboard` sets a short-lived httpOnly
   `rp_oauth_state` cookie (random nonce) and redirects to GitHub with a **signed state**
   containing the nonce and a sanitised `next` path.
2. GitHub redirects to `/api/v1/auth/github/callback?code&state`. The API verifies the state
   signature, expiry **and** that its nonce matches the cookie (login-CSRF protection),
   exchanges the code, fetches `/user`, upserts the user, stores the user token **encrypted**
   (Fernet) and creates a server-side session (`user_sessions`, SHA-256 of the token only).
3. The browser receives an httpOnly, `SameSite=Lax` (and `Secure` over HTTPS) `rp_session`
   cookie and is redirected to the frontend.

State-changing API calls must send `X-CSRF-Token`, an HMAC of the session obtained from
`GET /api/v1/auth/me`.

### Installation → organization linking

1. An org **admin** clicks *Install GitHub App* → `GET /api/v1/organizations/{org}/github/install`
   → redirect to `github.com/apps/<slug>/installations/new?state=<signed org+user>`.
2. After installing, GitHub redirects to the **Setup URL** with `installation_id` and `state`.
3. The API requires a valid session, verifies the signed state belongs to *this* user, that
   the user is still an admin of the org, **and that the user's own GitHub token can see the
   installation** (`GET /user/installations`). Without that last check anyone could claim
   another account's installation by guessing its numeric id.
4. Installation details are fetched with the App JWT and linked to the organization.

### Repositories

- *Available* repositories are listed live from GitHub with an installation token.
- *Connect* re-verifies through GitHub (`GET /repositories/{id}` with the org's installation
  token) that the repository is accessible before creating it with default settings.

### Webhooks

`POST /api/v1/webhooks/github`:

1. Body read with a size cap (`WEBHOOK_MAX_BODY_BYTES`, default 5 MB) → 413 if exceeded.
2. `X-Hub-Signature-256` verified with a constant-time compare → 401 on failure.
3. Delivery recorded with `INSERT … ON CONFLICT (delivery_id) DO NOTHING` (idempotency).
4. Celery task enqueued; response `202` in milliseconds. `ping` returns `200 pong`.
5. Redeliveries of an already processed delivery return `200 {"status":"duplicate"}` and are
   not re-processed; redeliveries of a delivery that never finished are re-enqueued.

The worker locks the delivery row (`FOR UPDATE SKIP LOCKED`), processes it once and marks it
`processed`, `ignored` or `failed` (retried with exponential backoff, max 5 attempts).

| Event | Handling |
|---|---|
| `installation.created` / `new_permissions_accepted` | upsert installation (not linked to an org until the Setup flow) |
| `installation.suspend` / `unsuspend` | toggle `suspended_at` |
| `installation.deleted` | soft-delete, unlink, disconnect its repositories |
| `installation_repositories.removed` | disconnect removed repositories |
| `pull_request.*` (opened, synchronize, reopened, closed, edited, …) | upsert PR metadata for connected repos (review jobs: Phase 3) |

## GitHub API client

`app/integrations/github/http.py`: versioned headers, retries with exponential backoff on
connection errors and 5xx, primary/secondary rate-limit handling (`Retry-After`,
`X-RateLimit-Reset`, capped wait), `Link`-header pagination, and an SSRF guard that refuses
any absolute URL outside the configured API base (pagination links included). Redirects are
not followed. Installation tokens are cached in Redis, encrypted, until 5 minutes before
expiry.

## Publishing reviews (Phase 6)

After analysis a review job goes to one of:

| Outcome | When |
|---|---|
| `completed` (publication `disabled`) | `GITHUB_PUBLISH_ENABLED=false` (dry run), or the repository turns off both inline comments and the summary |
| `awaiting_approval` | Repository setting `require_human_approval` is on |
| `publishing` → `completed` | Otherwise: the `github` queue posts the review |

**What is posted:** one review with `event: COMMENT`. ReviewPilot never approves or requests
changes. It contains:

- a **summary**: risk level, AI summary, findings by severity, findings that couldn't be
  inlined, and a link back to ReviewPilot;
- **inline comments** on the exact lines (`side: RIGHT`, multi-line ranges via `start_line`):
  severity, category, description, impact, suggested fix, and the code suggestion. Semgrep
  autofixes are posted as one-click GitHub ```suggestion``` blocks; AI snippets are shown as
  plain code.

Only **open** findings at or above the repository's `min_confidence` and
`min_severity_to_publish` are posted, at most `PUBLISH_MAX_INLINE_COMMENTS` inline (default
25). The rest appear in the summary.

**Position safety:**

- An inline comment is sent only if all its lines sit inside one diff hunk of the PR's
  current patch.
- If GitHub still answers `422`, the review is re-sent with every finding in the summary,
  and the inline rows are recorded as `skipped` with the reason.

**Safe updates when a PR changes:**

- **Stale reviews are never posted.** Before posting, the PR is re-read from GitHub; if it
  closed or its head moved, publication is skipped (`publish_head_moved` /
  `publish_pr_closed`).
- **Pending approvals are cancelled** by new pushes (`superseded`).
- **Already-reported issues are not repeated.** Findings with the same fingerprint as one
  already posted on this PR are counted instead ("N issues from an earlier review are still
  present").
- **Earlier summaries are marked "Superseded by … `<sha>`"** by editing their body (GitHub
  doesn't allow deleting submitted reviews). Old inline threads become *outdated* on
  GitHub automatically.

**Untrusted text:**

- @-mentions are neutralised with a zero-width space.
- HTML comments (which could forge our markers) are stripped.
- Secrets are redacted.
- Code fences in suggestions can't be broken out of.
- Bodies are capped below GitHub's 65,536-character limit.

Hidden markers (`<!-- reviewpilot:review … -->`, `<!-- reviewpilot:finding fp=… -->`) link
GitHub comments back to jobs and findings.

**Approval and retry:**

- `POST /organizations/{org}/reviews/{id}/publish` (developer+) approves an
  `awaiting_approval` review, or retries one that `failed` with `publish_failed`.
- It is refused with `409 review_stale` if the PR moved since the review ran.
- Transient GitHub errors are retried by the task (exponential backoff, 5 attempts); other
  errors mark the job `failed` / `publish_failed` with the GitHub message.
- The maintenance sweep re-enqueues jobs stuck in `publishing`, which is safe because
  publishing is row-locked and idempotent.

Every attempt is stored in `review_comments` (kind, path, line, body, status, GitHub review
and comment ids, error).
