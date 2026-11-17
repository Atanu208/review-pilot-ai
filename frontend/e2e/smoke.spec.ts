import { expect, test, type Page } from "@playwright/test";

const meta = {
  name: "ReviewPilot AI",
  version: "0.1.0",
  environment: "test",
  features: { github_oauth: false, github_app: false, llm_provider: "mock", demo_mode: false },
};

async function mockApi(page: Page, readiness: { status: number; body?: unknown } | "offline") {
  await page.route("**/api/v1/meta", (route) => route.fulfill({ json: meta }));
  await page.route("**/health/ready", (route) =>
    readiness === "offline"
      ? route.abort("connectionrefused")
      : route.fulfill({ status: readiness.status, json: readiness.body }),
  );
}

test("landing page renders product overview and navigates to status", async ({ page }) => {
  await mockApi(page, {
    status: 200,
    body: { status: "ok", checks: [] },
  });
  await page.goto("/");
  await expect(page).toHaveTitle(/ReviewPilot AI/);
  await expect(
    page.getByRole("heading", { name: "Intelligent code review for every pull request" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Check system status" }).click();
  await expect(page).toHaveURL(/\/status$/);
});

test("status page shows healthy dependencies", async ({ page }) => {
  await mockApi(page, {
    status: 200,
    body: {
      status: "ok",
      checks: [
        { name: "database", status: "ok", latency_ms: 1.2, error: null },
        { name: "redis", status: "ok", latency_ms: 0.4, error: null },
      ],
    },
  });
  await page.goto("/status");
  await expect(page.getByTestId("status-overall")).toContainText("All systems operational");
  await expect(page.getByTestId("status-overall")).toContainText("LLM provider: mock");
  await expect(page.getByTestId("check-database")).toContainText("PostgreSQL");
  await expect(page.getByTestId("check-redis")).toContainText("Operational");
});

test("status page shows degraded dependency from a 503", async ({ page }) => {
  await mockApi(page, {
    status: 503,
    body: {
      status: "degraded",
      checks: [
        { name: "database", status: "error", latency_ms: 3000, error: "TimeoutError" },
        { name: "redis", status: "ok", latency_ms: 0.4, error: null },
      ],
    },
  });
  await page.goto("/status");
  await expect(page.getByTestId("status-overall")).toContainText("Degraded performance");
  await expect(page.getByTestId("check-database")).toContainText("TimeoutError");
});

test("status page shows error state when API is unreachable", async ({ page }) => {
  await mockApi(page, "offline");
  await page.goto("/status");
  await expect(page.getByTestId("status-error")).toContainText("API unreachable", {
    timeout: 15_000,
  });
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("theme toggle switches to dark mode", async ({ page }) => {
  await mockApi(page, { status: 200, body: { status: "ok", checks: [] } });
  await page.goto("/");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.getByRole("menuitem", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
});
