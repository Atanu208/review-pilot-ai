import { expect, test } from "@playwright/test";

import { defaultState, mockApi } from "./mock-api";

const withRepo = {
  installations: [{ installation_id: 1, account_login: "acme" }],
  available: [{ github_repo_id: 42, full_name: "acme/api", is_private: false }],
  connected: [42],
};

test.describe("dashboard analytics", () => {
  test("shows KPIs, charts, recent reviews and security issues from the API", async ({ page }) => {
    await mockApi(page, defaultState({ ...withRepo, withData: true }));
    await page.goto("/dashboard");
    await expect(page.getByTestId("stat-reviews")).toContainText("3");
    await expect(page.getByTestId("stat-open-findings")).toContainText("2");
    await expect(page.getByTestId("stat-critical")).toContainText("1");
    await expect(page.getByTestId("stat-duration")).toContainText("3.0 s");
    // Setup is complete, so the checklist is hidden.
    await expect(page.getByTestId("setup-checklist")).toHaveCount(0);
    await expect(page.getByTestId("chart-activity").locator(".recharts-bar-rectangle").first()).toBeVisible();
    await expect(page.getByTestId("recent-reviews")).toContainText("Add user lookup");
    await page.getByTestId("security-issues").getByRole("link", { name: /SQL query/ }).click();
    await expect(page).toHaveURL(/\/findings\/f-1$/);
    await expect(page.getByTestId("finding-detail")).toContainText("reviewpilot.python.sql-injection");
  });

  test("empty organization shows empty states instead of fake data", async ({ page }) => {
    await mockApi(page, defaultState());
    await page.goto("/dashboard");
    await expect(page.getByTestId("stat-reviews")).toContainText("0");
    await expect(page.getByTestId("chart-activity").getByTestId("chart-empty")).toHaveText("No reviews in this period");
    await expect(page.getByTestId("setup-checklist")).toBeVisible();
  });

  test("chart has a table view and the time range refetches", async ({ page }) => {
    const state = defaultState({ ...withRepo, withData: true });
    await mockApi(page, state);
    await page.goto("/dashboard");
    const chart = page.getByTestId("chart-activity");
    await chart.getByRole("button", { name: "Show as table" }).click();
    await expect(chart.getByTestId("chart-table").locator("tbody tr")).toHaveCount(30);
    await page.getByRole("tab", { name: "7 days" }).click();
    await expect(chart.getByTestId("chart-table").locator("tbody tr")).toHaveCount(7);
    expect(state.urls).toContain(`GET /organizations/${state.orgs[0].id}/analytics/timeseries?days=7`);
  });
});

test.describe("analytics page", () => {
  test("renders AI usage, models, quality and team tables", async ({ page }) => {
    await mockApi(page, defaultState({ ...withRepo, withData: true }));
    await page.goto("/analytics");
    await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
    await expect(page.getByTestId("stat-llm-calls")).toContainText("4");
    await expect(page.getByTestId("stat-cost")).toContainText("$0.02");
    await expect(page.getByTestId("models-table")).toContainText("openai/gpt-oss-120b");
    await expect(page.getByTestId("models-table")).toContainText("75%");
    await expect(page.getByTestId("stages-table")).toContainText("review.security");
    await expect(page.getByTestId("quality-table")).toContainText("Semgrep");
    await expect(page.getByTestId("team-table")).toContainText("alice");
    await expect(page.getByTestId("chart-cost")).toBeVisible();
    await expect(page.getByTestId("chart-tokens")).toBeVisible();
  });
});

test.describe("findings", () => {
  test("lists and filters findings, then records feedback on the detail page", async ({ page }) => {
    const state = defaultState({ ...withRepo, withData: true });
    await mockApi(page, state);
    await page.goto("/findings");
    await expect(page.getByTestId("finding-rows").locator("li")).toHaveCount(2);
    await page.getByLabel("Severity").selectOption("critical");
    await expect(page.getByTestId("finding-rows").locator("li")).toHaveCount(1);
    await page.getByTestId("finding-rows").getByRole("link").first().click();
    await expect(page).toHaveURL(/\/findings\/f-1$/);

    const helpful = page.getByTestId("feedback").getByRole("button", { name: /^Helpful/ });
    await expect(helpful).toHaveAttribute("aria-pressed", "false");
    await helpful.click();
    await expect(helpful).toHaveAttribute("aria-pressed", "true");
    await expect(helpful).toContainText("1");
    expect(state.feedback).toEqual({ "f-1": "helpful" });
    expect(state.csrfViolations).toEqual([]);
  });

  test("viewer can rate but cannot triage", async ({ page }) => {
    await mockApi(page, defaultState({ ...withRepo, withData: true, role: "viewer" }));
    await page.goto("/findings/f-2");
    await expect(page.getByTestId("feedback")).toBeVisible();
    await expect(page.getByRole("button", { name: "Resolve" })).toHaveCount(0);
  });
});

test.describe("repository detail", () => {
  test("admin edits review settings and manages rule overrides", async ({ page }) => {
    const state = defaultState(withRepo);
    await mockApi(page, state);
    await page.goto("/repositories");
    await page.getByTestId("repo-link").click();
    await expect(page).toHaveURL(/\/repositories\/repo-42$/);
    await expect(page.getByRole("heading", { name: "acme/api" })).toBeVisible();

    await page.getByLabel("Minimum severity to publish").selectOption("high");
    await page.getByLabel("java", { exact: true }).uncheck();
    await page.getByLabel(/Glob patterns/).fill("migrations/**\n*.generated.ts");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved")).toBeVisible();
    const saved = state.repoSettings?.[42] as Record<string, unknown>;
    expect(saved.min_severity_to_publish).toBe("high");
    expect(saved.enabled_languages).toEqual(["python", "javascript", "typescript"]);
    expect(saved.ignored_paths).toEqual(["migrations/**", "*.generated.ts"]);
    expect(saved.min_confidence).toBe("0.700");

    await page.getByLabel("Rule ID").fill("reviewpilot.python.eval-use");
    await page.getByLabel("Severity", { exact: true }).selectOption("low");
    await page.getByRole("button", { name: "Add override" }).click();
    await expect(page.getByTestId("rules-list")).toContainText("reviewpilot.python.eval-use");
    await expect(page.getByTestId("rules-list")).toContainText("severity low");
    await page.getByRole("button", { name: "Delete reviewpilot.python.eval-use" }).click();
    await expect(page.getByTestId("rules-list")).toHaveCount(0);
    expect(state.csrfViolations).toEqual([]);
  });

  test("non-admin sees read-only settings", async ({ page }) => {
    await mockApi(page, defaultState({ ...withRepo, role: "developer" }));
    await page.goto("/repositories/repo-42");
    await expect(page.getByText("Only admins can change these settings.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save settings" })).toHaveCount(0);
    await expect(page.getByTestId("rule-form")).toHaveCount(0);
  });
});

test.describe("settings", () => {
  test("shows profile, theme picker and AI config without secrets", async ({ page }) => {
    await mockApi(page, defaultState());
    await page.goto("/settings");
    await expect(page.getByTestId("profile")).toContainText("@octocat");
    await expect(page.getByTestId("ai-config")).toContainText("openai/gpt-oss-120b");
    await expect(page.getByTestId("ai-config")).toContainText("Dry run");
    await page.getByRole("radio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  test("navigation includes the new sections", async ({ page }) => {
    await mockApi(page, defaultState());
    await page.goto("/dashboard");
    for (const name of ["Findings", "Analytics", "Settings"]) {
      await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
    }
  });
});
