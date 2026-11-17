import { expect, test } from "@playwright/test";

/**
 * Runs only against a real running stack (no API mocking):
 *   E2E_LIVE=1 E2E_BASE_URL=http://localhost:3000 npx playwright test live-stack
 */
test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run against a running docker compose stack");

test("status page reports operational dependencies from the real API", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByTestId("status-overall")).toContainText("All systems operational", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("check-database")).toContainText("Operational");
  await expect(page.getByTestId("check-redis")).toContainText("Operational");
});
