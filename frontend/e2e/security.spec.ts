import { expect, test } from "@playwright/test";

import { defaultState, mockApi } from "./mock-api";

test("pages are served with a strict Content-Security-Policy and security headers", async ({ page }) => {
  const response = await page.goto("/");
  const headers = response!.headers();
  const csp = headers["content-security-policy"];
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).toMatch(/connect-src 'self' https?:\/\/[^\s;]+/);
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("the app runs without any CSP violation", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (/Content Security Policy|Refused to/i.test(msg.text())) violations.push(msg.text());
  });
  await mockApi(
    page,
    defaultState({
      installations: [{ installation_id: 1, account_login: "acme" }],
      available: [{ github_repo_id: 42, full_name: "acme/api", is_private: false }],
      connected: [42],
      withData: true,
    }),
  );
  for (const path of ["/dashboard", "/analytics", "/findings", "/repositories/repo-42", "/settings"]) {
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
  }
  // Theme switching injects styles at runtime; it must not trip the policy either.
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  expect(violations).toEqual([]);
});

test("API error details are shown without rendering HTML", async ({ page }) => {
  await mockApi(page, defaultState());
  await page.route("**/api/v1/organizations/*/analytics/overview**", (route) =>
    route.fulfill({
      status: 500,
      json: { error: { code: "internal_error", message: '<img src=x onerror="window.__xss=1">' } },
    }),
  );
  await page.goto("/dashboard");
  await expect(page.getByText('<img src=x onerror="window.__xss=1">')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
});
