import type { Page, Route } from "@playwright/test";

/** Stateful in-memory mock of the ReviewPilot API for browser tests. */
export interface MockState {
  authenticated: boolean;
  role: "owner" | "admin" | "developer" | "viewer";
  orgs: { id: string; name: string; slug: string }[];
  installations: { installation_id: number; account_login: string }[];
  available: { github_repo_id: number; full_name: string; is_private: boolean }[];
  connected: number[];
  invitations: { id: string; github_login: string; role: string }[];
  csrfViolations: string[];
  calls: string[];
  /** Full request URLs (path + query) in call order. */
  urls?: string[];
  /** Artificial latency for GET /auth/me (exposes cache/navigation races). */
  meDelayMs?: number;
  /** Saved repository settings by GitHub repo id. */
  repoSettings?: Record<number, Record<string, unknown>>;
  /** Seed analytics, findings and AI usage (otherwise every analytics endpoint is empty). */
  withData?: boolean;
  /** Feedback rating submitted per finding id. */
  feedback?: Record<string, string>;
  /** Repository rule overrides by rule key. */
  rules?: Record<string, Record<string, unknown>>;
}

export const CSRF = "c".repeat(64);
const ORG_ID = "11111111-1111-1111-1111-111111111111";

export function defaultState(overrides: Partial<MockState> = {}): MockState {
  return {
    authenticated: true,
    role: "owner",
    orgs: [{ id: ORG_ID, name: "Acme", slug: "acme" }],
    installations: [],
    available: [],
    connected: [],
    invitations: [],
    csrfViolations: [],
    calls: [],
    ...overrides,
  };
}

const user = {
  id: "u1",
  login: "octocat",
  name: "Octo Cat",
  email: null,
  avatar_url: null,
};

function repoOut(r: MockState["available"][number], settings: Record<string, unknown> | null = null) {
  return {
    id: `repo-${r.github_repo_id}`,
    github_repo_id: r.github_repo_id,
    full_name: r.full_name,
    default_branch: "main",
    is_private: r.is_private,
    primary_language: "Python",
    html_url: `https://github.com/${r.full_name}`,
    is_connected: true,
    connected_at: "2026-11-15T00:00:00Z",
    settings,
  };
}

function lastDays(days: number): string[] {
  const out: string[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

export const FINDINGS = [
  {
    id: "f-1",
    review_job_id: "rev-1",
    pull_request_id: "pr-1",
    title: "SQL query built with string formatting",
    category: "security",
    severity: "critical",
    confidence: "0.950",
    file_path: "app/db.py",
    start_line: 12,
    end_line: 12,
    description: "User input is interpolated into SQL.",
    impact: "Attackers can read or modify any row.",
    suggested_fix: "Use bound parameters.",
    code_suggestion: null,
    evidence: "cur.execute(f\"SELECT * FROM users WHERE id = {uid}\")",
    source: "semgrep",
    rule_id: "reviewpilot.python.sql-injection",
    fingerprint: "a".repeat(64),
    status: "open",
    created_at: new Date().toISOString(),
  },
  {
    id: "f-2",
    review_job_id: "rev-1",
    pull_request_id: "pr-1",
    title: "N+1 query inside loop",
    category: "performance",
    severity: "medium",
    confidence: "0.820",
    file_path: "app/views.py",
    start_line: 40,
    end_line: 44,
    description: "Each iteration issues a query.",
    impact: null,
    suggested_fix: null,
    code_suggestion: null,
    evidence: "for o in orders: o.customer.name",
    source: "ai",
    rule_id: null,
    fingerprint: "b".repeat(64),
    status: "open",
    created_at: new Date().toISOString(),
  },
];

function analytics(kind: string, days: number, withData: boolean) {
  const dates = lastDays(days);
  const zeroSev = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  const last = dates.length - 1;
  const today = (i: number) => withData && i === last;
  switch (kind) {
    case "overview":
      return {
        days,
        connected_repositories: withData ? 1 : 0,
        reviews_total: withData ? 3 : 0,
        pull_requests_reviewed: withData ? 2 : 0,
        findings_total: withData ? 2 : 0,
        open_findings: withData ? 2 : 0,
        findings_by_severity: withData ? { ...zeroSev, critical: 1, medium: 1 } : zeroSev,
        avg_review_duration_ms: withData ? 3000 : null,
        recent_reviews: withData
          ? [
              {
                id: "rev-1",
                status: "completed",
                created_at: new Date().toISOString(),
                duration_ms: 3000,
                findings: 2,
                pull_request: { id: "pr-1", number: 7, title: "Add user lookup" },
                repository: "acme/api",
              },
            ]
          : [],
        recent_security_findings: withData
          ? [
              {
                id: "f-1",
                title: FINDINGS[0].title,
                severity: "critical",
                file_path: "app/db.py",
                start_line: 12,
                status: "open",
                created_at: new Date().toISOString(),
                repository: "acme/api",
                pull_request_number: 7,
                pull_request_id: "pr-1",
              },
            ]
          : [],
      };
    case "timeseries":
      return {
        days,
        reviews: dates.map((date, i) => ({ date, completed: today(i) ? 2 : 0, failed: today(i) ? 1 : 0, other: 0 })),
        findings: dates.map((date, i) => ({ date, ...zeroSev, ...(today(i) ? { critical: 1, medium: 1 } : {}) })),
      };
    case "ai-usage":
      return {
        days,
        totals: withData
          ? { calls: 4, input_tokens: 3000, output_tokens: 600, cost_usd: 0.024, reviews: 2, cost_per_review_usd: 0.012 }
          : { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, reviews: 0, cost_per_review_usd: 0 },
        models: withData
          ? [
              {
                provider: "openai",
                model: "openai/gpt-oss-120b",
                calls: 4,
                success_rate: 0.75,
                input_tokens: 3000,
                output_tokens: 600,
                cost_usd: 0.024,
                avg_latency_ms: 1500,
              },
            ]
          : [],
        stages: withData
          ? [
              { stage: "review.security", calls: 2, tokens: 2400 },
              { stage: "summary", calls: 2, tokens: 1200 },
            ]
          : [],
        per_day: dates.map((date, i) => ({ date, cost_usd: today(i) ? 0.024 : 0, tokens: today(i) ? 3600 : 0 })),
      };
    case "quality": {
      const status = { open: 1, resolved: 0, ignored: 0, false_positive: 0 };
      return {
        days,
        sources: withData
          ? [
              { source: "semgrep", total: 1, status, feedback: { helpful: 1, not_helpful: 0, false_positive: 0 }, false_positive_rate: 0, helpful_rate: 1 },
              { source: "ai", total: 1, status, feedback: { helpful: 0, not_helpful: 0, false_positive: 0 }, false_positive_rate: 0, helpful_rate: null },
            ]
          : [],
      };
    }
    case "team":
      return {
        days,
        authors: withData ? [{ author: "alice", pull_requests: 2, reviews: 3, findings: 2, critical_or_high: 1 }] : [],
      };
  }
  return null;
}

export async function mockApi(page: Page, state: MockState) {
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, json: body });

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = req.method();
    state.calls.push(`${method} ${path}`);
    (state.urls ??= []).push(`${method} ${path}${url.search}`);

    if (method !== "GET" && req.headers()["x-csrf-token"] !== CSRF) {
      state.csrfViolations.push(`${method} ${path}`);
      return json(route, { error: { code: "csrf_failed", message: "CSRF" } }, 403);
    }
    if (path === "/meta") {
      return json(route, {
        name: "ReviewPilot AI",
        version: "0.1.0",
        environment: "test",
        features: { github_oauth: true, github_app: true, llm_provider: "mock", demo_mode: false },
      });
    }
    if (!state.authenticated) {
      return json(route, { error: { code: "unauthorized", message: "Authentication required" } }, 401);
    }

    const org = state.orgs[0];
    const orgBase = org ? `/organizations/${org.id}` : "__none__";

    if (path === "/auth/me") {
      if (state.meDelayMs) await new Promise((r) => setTimeout(r, state.meDelayMs));
      return json(route, {
        user,
        csrf_token: CSRF,
        organizations: state.orgs.map((o) => ({
          organization_id: o.id,
          organization_name: o.name,
          organization_slug: o.slug,
          role: state.role,
        })),
      });
    }
    if (path === "/auth/logout" && method === "POST") {
      state.authenticated = false;
      return route.fulfill({ status: 204 });
    }
    if (path === "/organizations" && method === "POST") {
      const body = req.postDataJSON() as { name: string };
      const created = { id: ORG_ID, name: body.name, slug: "acme", created_at: "2026-11-15T00:00:00Z" };
      state.orgs.push(created);
      return json(route, created, 201);
    }
    if (path === orgBase) {
      return json(route, {
        ...org,
        created_at: "2026-11-15T00:00:00Z",
        role: state.role,
        member_count: 1,
        installation_count: state.installations.length,
        connected_repository_count: state.connected.length,
      });
    }
    if (path === `${orgBase}/github/installations`) {
      return json(
        route,
        state.installations.map((i) => ({
          id: `inst-${i.installation_id}`,
          ...i,
          account_type: "Organization",
          repository_selection: "selected",
          suspended_at: null,
          created_at: "2026-11-15T00:00:00Z",
        })),
      );
    }
    if (path === `${orgBase}/repositories` && method === "GET") {
      const items = state.available.filter((r) => state.connected.includes(r.github_repo_id)).map((r) => repoOut(r, state.repoSettings?.[r.github_repo_id] ?? null));
      return json(route, { items, total: items.length, limit: 100, offset: 0 });
    }
    if (path === `${orgBase}/repositories/available`) {
      return json(
        route,
        state.available.map((r) => ({
          ...r,
          default_branch: "main",
          primary_language: "Python",
          html_url: null,
          installation_id: 1,
          connected: state.connected.includes(r.github_repo_id),
        })),
      );
    }
    if (path === `${orgBase}/repositories` && method === "POST") {
      const { github_repo_id } = req.postDataJSON() as { github_repo_id: number };
      state.connected.push(github_repo_id);
      const repo = state.available.find((r) => r.github_repo_id === github_repo_id)!;
      return json(route, repoOut(repo), 201);
    }
    const analyticsMatch = path.match(new RegExp(`^${orgBase}/analytics/([a-z-]+)$`));
    if (analyticsMatch) {
      const days = Number(url.searchParams.get("days") ?? 30);
      return json(route, analytics(analyticsMatch[1], days, Boolean(state.withData)));
    }
    if (path === `${orgBase}/ai/config`) {
      return json(route, {
        provider: "openai",
        configured: true,
        enabled: true,
        endpoint: "https://api.groq.com/openai/v1",
        primary_model: "openai/gpt-oss-120b",
        fallback_models: ["openai/gpt-oss-20b"],
        fast_model: "openai/gpt-oss-20b",
        effort: null,
        tokens_per_minute: 8000,
        reviewers: ["security", "bugs", "performance", "quality"],
        verify_findings: true,
        min_confidence_floor: 0.75,
        max_input_tokens: 24000,
        max_batches_per_reviewer: 4,
        prompt_version: "2026-11-16.2",
        prompt_versions: ["2026-11-16.1", "2026-11-16.2"],
        publishing: { enabled: false, max_inline_comments: 25 },
      });
    }
    if (path === `${orgBase}/findings` && method === "GET") {
      const q = url.searchParams;
      const items = (state.withData ? FINDINGS : []).filter(
        (f) =>
          (!q.get("severity") || f.severity === q.get("severity")) &&
          (!q.get("status") || f.status === q.get("status")) &&
          (!q.get("source") || f.source === q.get("source")),
      );
      return json(route, { items, total: items.length, limit: 25, offset: 0 });
    }
    const feedbackMatch = path.match(new RegExp(`^${orgBase}/findings/([\\w-]+)/feedback$`));
    if (feedbackMatch) {
      const id = feedbackMatch[1];
      state.feedback ??= {};
      if (method === "PUT") state.feedback[id] = (req.postDataJSON() as { rating: string }).rating;
      const mine = state.feedback[id] ?? null;
      const counts = { helpful: 0, not_helpful: 0, false_positive: 0 };
      if (mine) counts[mine as keyof typeof counts] += 1;
      return json(route, { counts, mine, my_comment: null });
    }
    const findingMatch = path.match(new RegExp(`^${orgBase}/findings/([\\w-]+)$`));
    if (findingMatch && method === "GET") {
      const f = FINDINGS.find((x) => x.id === findingMatch[1]);
      return f ? json(route, f) : json(route, { error: { code: "not_found", message: "Finding not found" } }, 404);
    }
    const rulesMatch = path.match(new RegExp(`^${orgBase}/repositories/(repo-\\d+)/rules(?:/(.+))?$`));
    if (rulesMatch) {
      state.rules ??= {};
      const key = rulesMatch[2] ? decodeURIComponent(rulesMatch[2]) : null;
      if (method === "PUT" && key) {
        state.rules[key] = {
          id: `rule-${key}`,
          repository_id: rulesMatch[1],
          rule_key: key,
          languages: [],
          description: null,
          updated_at: new Date().toISOString(),
          ...(req.postDataJSON() as object),
        };
        return json(route, state.rules[key]);
      }
      if (method === "DELETE" && key) {
        delete state.rules[key];
        return route.fulfill({ status: 204 });
      }
      return json(route, Object.values(state.rules));
    }
    const repoMatch = path.match(new RegExp(`^${orgBase}/repositories/repo-(\\d+)$`));
    if (repoMatch && method === "GET") {
      const id = Number(repoMatch[1]);
      const repo = state.available.find((r) => r.github_repo_id === id);
      return repo
        ? json(route, repoOut(repo, state.repoSettings?.[id] ?? null))
        : json(route, { error: { code: "not_found", message: "Repository not found" } }, 404);
    }
    if (path === `${orgBase}/pull-requests` && method === "GET") {
      return json(route, { items: [], total: 0, limit: 10, offset: 0 });
    }
    if (path.startsWith(`${orgBase}/repositories/repo-`) && path.endsWith("/settings") && method === "PATCH") {
      const id = Number(path.split("repo-")[1].split("/")[0]);
      const repo = state.available.find((r) => r.github_repo_id === id)!;
      state.repoSettings = { ...state.repoSettings, [id]: { ...state.repoSettings?.[id], ...req.postDataJSON() } };
      return json(route, repoOut(repo, state.repoSettings[id]));
    }
    if (path.startsWith(`${orgBase}/repositories/repo-`) && method === "DELETE") {
      const id = Number(path.split("repo-")[1]);
      state.connected = state.connected.filter((c) => c !== id);
      return route.fulfill({ status: 204 });
    }
    if (path === `${orgBase}/members`) {
      return json(route, [
        { id: "m1", role: state.role, created_at: "2026-11-15T00:00:00Z", user },
      ]);
    }
    if (path === `${orgBase}/invitations` && method === "GET") {
      return json(
        route,
        state.invitations.map((i) => ({
          ...i,
          status: "pending",
          expires_at: "2026-11-22T00:00:00Z",
          created_at: "2026-11-15T00:00:00Z",
        })),
      );
    }
    if (path === `${orgBase}/invitations` && method === "POST") {
      const body = req.postDataJSON() as { github_login: string; role: string };
      const inv = { id: `inv-${state.invitations.length + 1}`, ...body };
      state.invitations.push(inv);
      return json(
        route,
        {
          ...inv,
          status: "pending",
          expires_at: "2026-11-22T00:00:00Z",
          created_at: "2026-11-15T00:00:00Z",
          invite_url: "http://localhost:3000/invitations/tok123",
        },
        201,
      );
    }
    return json(route, { error: { code: "not_found", message: `Unmocked ${method} ${path}` } }, 404);
  });
}
