import { expect, test } from "@playwright/test";

import { defaultState, mockApi } from "./mock-api";

test.describe("authentication", () => {
  test("login page links to the API GitHub sign-in with next path", async ({ page }) => {
    await mockApi(page, defaultState({ authenticated: false }));
    await page.goto("/login?next=/team");
    const button = page.getByRole("link", { name: "Continue with GitHub" });
    await expect(button).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/auth/github/login?next=%2Fteam",
    );
  });

  test("login page explains OAuth errors", async ({ page }) => {
    await mockApi(page, defaultState({ authenticated: false }));
    await page.goto("/login?error=invalid_state");
    await expect(page.getByTestId("login-error")).toContainText("sign-in link expired");
  });

  test("protected pages redirect anonymous users to login", async ({ page }) => {
    await mockApi(page, defaultState({ authenticated: false }));
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/);
  });

  test("sign out revokes the session and returns to login", async ({ page }) => {
    const state = defaultState();
    await mockApi(page, state);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(state.calls).toContain("POST /auth/logout");
    expect(state.csrfViolations).toEqual([]);
  });
});

test.describe("onboarding", () => {
  test("user without organizations creates one (with CSRF header)", async ({ page }) => {
    const state = defaultState({ orgs: [] });
    await mockApi(page, state);
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/onboarding$/);
    await page.getByLabel("Organization name").fill("Acme Engineering");
    await page.getByRole("button", { name: "Create organization" }).click();
    await expect(page).toHaveURL(/\/repositories\?org=/);
    expect(state.calls).toContain("POST /organizations");
    expect(state.csrfViolations).toEqual([]);
  });

  test("after creating an org the user stays in the app (no bounce back to onboarding)", async ({ page }) => {
    const state = defaultState({ orgs: [], meDelayMs: 400 });
    await mockApi(page, state);
    await page.goto("/onboarding");
    await page.getByLabel("Organization name").fill("ReviewPilot Demo");
    await page.getByRole("button", { name: "Create organization" }).click();
    await expect(page.getByRole("heading", { name: "Repositories" })).toBeVisible();
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL(/\/repositories\?org=/);
    await expect(page.getByTestId("org-name")).toContainText("ReviewPilot Demo");
  });

  test("onboarding offers a way back for users who already have an organization", async ({ page }) => {
    await mockApi(page, defaultState());
    await page.goto("/onboarding");
    await page.getByTestId("existing-orgs").getByRole("link", { name: "Go to your dashboard" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });
});

test.describe("dashboard", () => {
  test("shows real organization counts and setup checklist", async ({ page }) => {
    await mockApi(page, defaultState({ installations: [{ installation_id: 1, account_login: "acme" }] }));
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Acme" })).toBeVisible();
    await expect(page.getByTestId("stat-installs")).toContainText("1");
    await expect(page.getByTestId("stat-repos")).toContainText("0");
  });
});

test.describe("repositories", () => {
  test("admin without installation sees the install button", async ({ page }) => {
    await mockApi(page, defaultState());
    await page.goto("/repositories");
    await expect(page.getByTestId("install-github-app").first()).toHaveAttribute(
      "href",
      /\/api\/v1\/organizations\/[\w-]+\/github\/install$/,
    );
  });

  test("viewer cannot install or connect", async ({ page }) => {
    await mockApi(page, defaultState({ role: "viewer" }));
    await page.goto("/repositories");
    await expect(page.getByText("An organization admin needs to install")).toBeVisible();
    await expect(page.getByTestId("install-github-app")).toHaveCount(0);
  });

  test("shows GitHub installation errors from the setup redirect", async ({ page }) => {
    await mockApi(page, defaultState());
    await page.goto("/repositories?github_error=installation_not_accessible");
    await expect(page.getByTestId("github-error")).toContainText("cannot access that installation");
  });

  test("connect and disconnect a repository", async ({ page }) => {
    const state = defaultState({
      installations: [{ installation_id: 1, account_login: "acme" }],
      available: [
        { github_repo_id: 101, full_name: "acme/api", is_private: true },
        { github_repo_id: 102, full_name: "acme/web", is_private: false },
      ],
    });
    await mockApi(page, state);
    await page.goto("/repositories?github=installed");
    await expect(page.getByTestId("github-installed")).toBeVisible();
    await expect(page.getByText("No repositories connected")).toBeVisible();

    await page.getByTestId("toggle-available").click();
    const available = page.getByTestId("available-repos");
    await available.getByRole("listitem").filter({ hasText: "acme/api" }).getByRole("button", { name: "Connect" }).click();

    const connected = page.getByTestId("connected-repos");
    await expect(connected).toContainText("acme/api");
    await expect(available).not.toContainText("acme/api");

    await connected.getByRole("button", { name: "Disconnect" }).click();
    await expect(page.getByText("No repositories connected")).toBeVisible();
    expect(state.csrfViolations).toEqual([]);
  });
});

test.describe("team", () => {
  test("admin invites a teammate and gets a shareable link", async ({ page }) => {
    const state = defaultState({ role: "admin" });
    await mockApi(page, state);
    await page.goto("/team");
    await expect(page.getByTestId("members")).toContainText("@octocat");
    await page.getByLabel("GitHub username").fill("new-dev");
    await page.getByLabel("Invitation role").selectOption("viewer");
    await page.getByRole("button", { name: "Create invitation" }).click();
    await expect(page.getByTestId("invite-url")).toContainText("/invitations/tok123");
    await expect(page.getByTestId("invitations")).toContainText("@new-dev");
    expect(state.invitations[0]).toMatchObject({ github_login: "new-dev", role: "viewer" });
  });

  test("admin cannot offer the owner role", async ({ page }) => {
    await mockApi(page, defaultState({ role: "admin" }));
    await page.goto("/team");
    const options = page.getByLabel("Invitation role").locator("option");
    await expect(options).toHaveText(["admin", "developer", "viewer"]);
  });

  test("developers do not see the invite form", async ({ page }) => {
    await mockApi(page, defaultState({ role: "developer" }));
    await page.goto("/team");
    await expect(page.getByTestId("members")).toBeVisible();
    await expect(page.getByText("Invite a teammate")).toHaveCount(0);
  });
});

test("admin toggles 'require approval before posting' for a repository", async ({ page }) => {
  const state = defaultState({
    installations: [{ installation_id: 1, account_login: "acme" }],
    available: [{ github_repo_id: 101, full_name: "acme/api", is_private: true }],
    connected: [101],
  });
  await mockApi(page, state);
  await page.goto("/repositories");
  await page.getByTestId("require-approval").check();
  await expect(page.getByText("Reviews now wait for approval")).toBeVisible();
  expect(state.calls).toContain("PATCH /organizations/11111111-1111-1111-1111-111111111111/repositories/repo-101/settings");
  expect(state.csrfViolations).toEqual([]);
});
