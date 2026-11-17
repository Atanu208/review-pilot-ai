import { expect, test, type Page } from "@playwright/test";

import { CSRF, defaultState, mockApi, type MockState } from "./mock-api";

const ORG = "11111111-1111-1111-1111-111111111111";
const PR_ID = "pr-1";
const SHA = "7f72633500000000000000000000000000000000";

interface Review {
  id: string;
  status: string;
  status_reason: string | null;
  trigger: string;
  head_sha: string;
  attempt: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  pull_request_id: string;
  summary: string | null;
  error_message: string | null;
  stats: Record<string, unknown>;
  timeline: unknown[];
}

function completedReview(id = "rev-1"): Review {
  return {
    id,
    status: "completed",
    status_reason: null,
    trigger: "webhook",
    head_sha: SHA,
    attempt: 1,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    duration_ms: 1840,
    pull_request_id: PR_ID,
    summary: "Prepared 1 of 2 changed files for review (+6/-2).",
    error_message: null,
    stats: {
      scope: {
        files_total: 2,
        files_in_scope: 1,
        files_skipped: { generated_or_vendored: 1 },
        additions: 6,
        deletions: 2,
        diff_lines_in_scope: 4,
        truncated: false,
        files: [
          { path: "app/main.py", status: "modified", additions: 3, deletions: 1, in_scope: true, skip_reason: null },
          { path: "yarn.lock", status: "modified", additions: 3, deletions: 1, in_scope: false, skip_reason: "generated_or_vendored" },
        ],
      },
    },
    timeline: [
      { stage: "fetch_pull_request", status: "ok", started_at: new Date().toISOString(), duration_ms: 320, detail: "#1 at 7f72633" },
      { stage: "fetch_changed_files", status: "ok", started_at: new Date().toISOString(), duration_ms: 410, detail: "1/2 files in scope" },
    ],
  };
}

const summary = (r: Review) => {
  const { pull_request_id, summary: _s, error_message, stats, timeline, ...rest } = r;
  void pull_request_id; void _s; void error_message; void stats; void timeline;
  return rest;
};

interface MockFinding {
  id: string;
  review_job_id: string;
  pull_request_id: string;
  title: string;
  category: string;
  severity: string;
  confidence: string;
  file_path: string;
  start_line: number;
  end_line: number;
  description: string;
  impact: string | null;
  suggested_fix: string | null;
  code_suggestion: string | null;
  evidence: string | null;
  source: string;
  rule_id: string | null;
  fingerprint: string;
  status: string;
  created_at: string;
}

function finding(id: string, severity: string, line: number, extra: Partial<MockFinding> = {}): MockFinding {
  return {
    id,
    review_job_id: "rev-1",
    pull_request_id: PR_ID,
    title: severity === "critical" ? "Potential SQL injection" : "Mutable default argument",
    category: severity === "critical" ? "security" : "bug",
    severity,
    confidence: severity === "critical" ? "0.900" : "0.850",
    file_path: "app/users.py",
    start_line: line,
    end_line: line,
    description: "User-controlled input is interpolated into SQL.",
    impact: "An attacker may manipulate database queries.",
    suggested_fix: "Use parameterised queries.",
    code_suggestion: null,
    evidence: '    cursor.execute(f"SELECT * FROM users WHERE id = {uid}")',
    source: severity === "critical" ? "semgrep" : "tree_sitter",
    rule_id: severity === "critical" ? "python.sql-injection.string-built-query" : "tree-sitter.python.mutable-default-argument",
    fingerprint: "f".repeat(64),
    status: "open",
    created_at: new Date().toISOString(),
    ...extra,
  };
}

async function mockReviews(page: Page, state: MockState, reviews: Review[], findings: MockFinding[] = []) {
  const pr = () => ({
    id: PR_ID,
    number: 1,
    title: "test",
    author_login: "Atanu9547",
    author_avatar_url: null,
    base_ref: "master",
    head_ref: "test",
    head_sha: SHA,
    state: "open",
    is_draft: false,
    html_url: "https://github.com/Atanu9547/alberta_backend/pull/1",
    additions: 3,
    deletions: 1,
    changed_files: 1,
    opened_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    repository: { id: "repo-1", full_name: "Atanu9547/alberta_backend", html_url: null },
    latest_review: reviews[0] ? summary(reviews[0]) : null,
  });

  await page.route(`**/api/v1/organizations/${ORG}/{pull-requests,reviews,findings}**`, async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(`/api/v1/organizations/${ORG}`, "");
    const method = req.method();
    state.calls.push(`${method} ${path}`);
    if (method !== "GET" && req.headers()["x-csrf-token"] !== CSRF) {
      state.csrfViolations.push(path);
      return route.fulfill({ status: 403, json: { error: { code: "csrf_failed", message: "CSRF" } } });
    }
    if (path === "/pull-requests" && method === "GET") {
      const filter = new URL(req.url()).searchParams.get("state");
      const items = !filter || filter === "open" ? [pr()] : [];
      return route.fulfill({ json: { items, total: items.length, limit: 50, offset: 0 } });
    }
    if (path === `/pull-requests/${PR_ID}`) {
      return route.fulfill({ json: { ...pr(), reviews: reviews.map(summary) } });
    }
    if (path === `/pull-requests/${PR_ID}/reviews` && method === "POST") {
      const r: Review = { ...completedReview(`rev-${reviews.length + 1}`), status: "queued", trigger: "manual", summary: null, stats: {}, timeline: [], completed_at: null, started_at: null, duration_ms: null };
      reviews.unshift(r);
      return route.fulfill({ status: 202, json: r });
    }
    const fm = path.match(/^\/reviews\/([\w-]+)\/findings$/);
    if (fm) return route.fulfill({ json: findings.filter((f) => f.review_job_id === fm[1]) });
    const pm = path.match(/^\/findings\/([\w-]+)$/);
    if (pm && method === "PATCH") {
      const f = findings.find((x) => x.id === pm[1])!;
      f.status = (req.postDataJSON() as { status: string }).status;
      return route.fulfill({ json: f });
    }
    const pub = path.match(/^\/reviews\/([\w-]+)\/publish$/);
    if (pub && method === "POST") {
      const r = reviews.find((x) => x.id === pub[1])!;
      r.status = "publishing";
      r.stats = { ...r.stats, publication: { status: "queued" } };
      return route.fulfill({ status: 202, json: r });
    }
    const m = path.match(/^\/reviews\/([\w-]+)(\/(cancel|retry))?$/);
    if (m) {
      const r = reviews.find((x) => x.id === m[1]);
      if (!r) return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Review not found" } } });
      if (m[3] === "cancel") {
        r.status = "cancelled";
        r.status_reason = "user_cancelled";
        return route.fulfill({ json: r });
      }
      if (m[3] === "retry") {
        const next: Review = { ...r, id: `rev-${reviews.length + 1}`, status: "queued", status_reason: null, trigger: "retry", attempt: r.attempt + 1, timeline: [], stats: {} };
        reviews.unshift(next);
        return route.fulfill({ status: 202, json: next });
      }
      return route.fulfill({ json: r });
    }
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: path } } });
  });
}

test("pull request list shows PRs with their latest review status", async ({ page }) => {
  const state = defaultState();
  await mockApi(page, state);
  await mockReviews(page, state, [completedReview()]);
  await page.goto("/pull-requests");
  const list = page.getByTestId("pr-list");
  await expect(list).toContainText("test #1");
  await expect(list).toContainText("Atanu9547/alberta_backend · test → master");
  await expect(list.getByTestId("review-status")).toHaveText("Completed");

  await page.getByRole("tab", { name: "closed" }).click();
  await expect(page.getByText("No pull requests match these filters.")).toBeVisible();
});

test("pull request detail shows summary, timeline and file scope", async ({ page }) => {
  const state = defaultState();
  await mockApi(page, state);
  await mockReviews(page, state, [completedReview()]);
  await page.goto(`/pull-requests/${PR_ID}`);
  await expect(page.getByTestId("pr-title")).toContainText("test #1");
  await expect(page.getByTestId("review-summary")).toHaveText("Prepared 1 of 2 changed files for review (+6/-2).");
  await expect(page.getByTestId("timeline")).toContainText("Fetch pull request");
  await expect(page.getByTestId("timeline")).toContainText("Fetch changed files");
  const files = page.getByTestId("scope-files");
  await expect(files).toContainText("app/main.py");
  await expect(files).toContainText("skipped: generated / vendored");
  await expect(page.getByRole("link", { name: "View on GitHub" })).toHaveAttribute("href", /pull\/1$/);
});

test("developer runs, cancels and retries a review", async ({ page }) => {
  const state = defaultState({ role: "developer" });
  const reviews: Review[] = [];
  await mockApi(page, state);
  await mockReviews(page, state, reviews);
  await page.goto(`/pull-requests/${PR_ID}`);
  await expect(page.getByText("No review yet")).toBeVisible();

  await page.getByTestId("run-review").click();
  const panel = page.getByTestId("review-panel");
  await expect(panel.getByTestId("review-status").first()).toHaveText("Queued");
  await expect(panel).toContainText("Waiting for a worker");

  await panel.getByRole("button", { name: "Cancel" }).click();
  await expect(panel.getByTestId("review-reason")).toHaveText("Cancelled by a user");

  await panel.getByRole("button", { name: "Retry" }).click();
  await expect(panel).toContainText("attempt 2");
  await expect(page.getByTestId("review-history").getByRole("listitem")).toHaveCount(2);
  expect(state.csrfViolations).toEqual([]);
  expect(state.calls).toEqual(expect.arrayContaining([
    `POST /pull-requests/${PR_ID}/reviews`,
    "POST /reviews/rev-1/cancel",
    "POST /reviews/rev-1/retry",
  ]));
});

test("viewers cannot run reviews", async ({ page }) => {
  const state = defaultState({ role: "viewer" });
  await mockApi(page, state);
  await mockReviews(page, state, [completedReview()]);
  await page.goto(`/pull-requests/${PR_ID}`);
  await expect(page.getByTestId("review-panel")).toBeVisible();
  await expect(page.getByTestId("run-review")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
});

test("AI review card shows risk, usage, highlights and injection warning", async ({ page }) => {
  const state = defaultState();
  const review = completedReview();
  review.summary = "Analyzed 1 file: 2 issues found (1 critical, 1 medium).\n\nAdds a user lookup with a SQL injection.";
  review.stats = {
    ...review.stats,
    ai: {
      provider: "openai",
      models: ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
      prompt_version: "2026-11-16.1",
      reviewers_run: ["security", "bugs"],
      llm_calls: 6,
      llm_failures: 0,
      fallbacks: 0,
      input_tokens: 9000,
      output_tokens: 3000,
      cost_usd: "0.000000",
      candidates: 5,
      findings: 2,
      prompt_injection_suspected: 1,
      risk_level: "high",
      highlights: ["SQL injection in get_user"],
      warnings: ["Possible prompt-injection text in app/users.py:3; treated as untrusted data."],
    },
  };
  review.timeline = [
    ...review.timeline,
    { stage: "ai.security_reviewer", status: "ok", started_at: new Date().toISOString(), duration_ms: 2100, detail: "2 candidate(s)" },
  ];
  await mockApi(page, state);
  await mockReviews(page, state, [review]);
  await page.goto(`/pull-requests/${PR_ID}`);
  const card = page.getByTestId("ai-review");
  await expect(card.getByTestId("risk-level")).toHaveText("high risk");
  await expect(card.getByTestId("ai-usage")).toContainText("6 model calls · 12,000 tokens · no billed cost");
  await expect(card.getByTestId("ai-highlights")).toContainText("SQL injection in get_user");
  await expect(card.getByTestId("ai-warning")).toContainText("prompt-injection");
  await expect(page.getByTestId("review-summary")).toContainText("Adds a user lookup");
  await expect(page.getByTestId("timeline")).toContainText("AI · security reviewer");
});

test("completed review shows findings grouped by severity with evidence", async ({ page }) => {
  const state = defaultState();
  await mockApi(page, state);
  await mockReviews(page, state, [completedReview()], [finding("f-2", "medium", 3), finding("f-1", "critical", 4)]);
  await page.goto(`/pull-requests/${PR_ID}`);
  const list = page.getByTestId("findings");
  await expect(list).toContainText(/Findings\s*\(2\)/);
  const cards = list.getByTestId("finding");
  await expect(cards).toHaveCount(2);
  await expect(list.getByTestId("evidence").first()).toContainText('cursor.execute(f"SELECT');
  await expect(list).toContainText("app/users.py:4");
  await expect(list).toContainText("Semgrep");
  await expect(list).toContainText("90% confidence");

  await list.getByRole("tab", { name: /critical/ }).click();
  await expect(cards).toHaveCount(1);
  await expect(cards.first().getByTestId("severity")).toHaveText("critical");
});

test("developer marks a finding as false positive", async ({ page }) => {
  const state = defaultState({ role: "developer" });
  const findings = [finding("f-1", "critical", 4)];
  await mockApi(page, state);
  await mockReviews(page, state, [completedReview()], findings);
  await page.goto(`/pull-requests/${PR_ID}`);
  const card = page.getByTestId("finding").first();
  await card.getByRole("button", { name: "False positive" }).click();
  await expect(card.getByTestId("finding-status")).toHaveText("False positive");
  expect(findings[0].status).toBe("false_positive");
  expect(state.csrfViolations).toEqual([]);
});

test("clean review shows no findings message; viewers cannot triage", async ({ page }) => {
  const state = defaultState({ role: "viewer" });
  await mockApi(page, state);
  await mockReviews(page, state, [completedReview()], []);
  await page.goto(`/pull-requests/${PR_ID}`);
  await expect(page.getByTestId("no-findings")).toBeVisible();

  const withFinding = defaultState({ role: "viewer" });
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockApi(page, withFinding);
  await mockReviews(page, withFinding, [completedReview()], [finding("f-1", "critical", 4)]);
  await page.reload();
  await expect(page.getByTestId("finding")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Resolve" })).toHaveCount(0);
});

test("developer approves a review waiting for approval", async ({ page }) => {
  const state = defaultState({ role: "developer" });
  const review = completedReview();
  review.status = "awaiting_approval";
  review.stats = { ...review.stats, publication: { status: "awaiting_approval" } };
  await mockApi(page, state);
  await mockReviews(page, state, [review]);
  await page.goto(`/pull-requests/${PR_ID}`);
  const panel = page.getByTestId("review-panel");
  await expect(panel.getByTestId("publication")).toContainText("Waiting for approval");
  await panel.getByTestId("publish-review").click();
  await expect(panel.getByTestId("publication")).toContainText("Publishing to GitHub");
  await expect(panel.getByTestId("review-status").first()).toHaveText("Publishing");
  expect(state.calls).toContain("POST /reviews/rev-1/publish");
  expect(state.csrfViolations).toEqual([]);
});

test("published review links to GitHub; failed publication offers retry", async ({ page }) => {
  const state = defaultState();
  const published = completedReview();
  published.stats = {
    ...published.stats,
    publication: {
      status: "published",
      inline_comments: 2,
      already_reported: 1,
      html_url: "https://github.com/Atanu9547/alberta_backend/pull/1#pullrequestreview-1",
    },
  };
  await mockApi(page, state);
  await mockReviews(page, state, [published]);
  await page.goto(`/pull-requests/${PR_ID}`);
  const pub = page.getByTestId("publication");
  await expect(pub).toContainText("2 inline comments and a summary");
  await expect(pub).toContainText("1 unchanged finding(s)");
  await expect(pub.getByRole("link", { name: "View on GitHub" })).toHaveAttribute("href", /pullrequestreview-1$/);

  const failed = completedReview("rev-9");
  failed.status = "failed";
  failed.status_reason = "publish_failed";
  failed.stats = { ...failed.stats, publication: { status: "failed", error: "GitHub rejected the review (403)" } };
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockApi(page, state);
  await mockReviews(page, state, [failed]);
  await page.reload();
  await expect(page.getByTestId("publication")).toContainText("Publishing to GitHub failed: GitHub rejected");
  await expect(page.getByTestId("publish-review")).toHaveText("Retry publishing");
});

test("dry run is explained", async ({ page }) => {
  const state = defaultState();
  const review = completedReview();
  review.stats = { ...review.stats, publication: { status: "disabled", reason: "dry_run" } };
  await mockApi(page, state);
  await mockReviews(page, state, [review]);
  await page.goto(`/pull-requests/${PR_ID}`);
  await expect(page.getByTestId("publication")).toContainText("Dry run");
  await expect(page.getByTestId("publish-review")).toHaveCount(0);
});
