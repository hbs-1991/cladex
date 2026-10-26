import { expect, test } from "@playwright/test";
import { FINANCE_SURFACE, installFakeApi } from "./fake-server";

test("the Connectors tab lists the catalog with states and actions", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/customize");
  await expect(page).toHaveURL(/\/customize\/connectors$/);
  await expect(page.getByRole("heading", { name: "Customize" })).toBeVisible();
  await expect(page.getByText("Connected · 30 tools")).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeVisible();  // "Add custom connector" matches loosely
  await page.getByRole("button", { name: "Add custom connector" }).click();
  await expect(page.getByRole("dialog", { name: "Add custom connector" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("link", { name: /GitHub \(token\)/ }).click();
  await expect(page).toHaveURL(/\/customize\/connectors\/github-pat$/);
  await expect(page.getByRole("heading", { name: /GitHub \(token\)/ })).toBeVisible();
  await expect(page.getByText("create_issue")).toBeVisible();
  await expect(page.getByText("https://api.githubcopilot.com/mcp/")).toBeVisible();
});

test("the Plugins tab installs a plugin and the card says so", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/customize/plugins");
  // `ul > li` rather than the listitem role: a sonner toast is a list item too,
  // and "Marketing installed" would join the card in the match.
  const card = page.locator("ul > li").filter({ hasText: "Marketing" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Install" }).click();
  // Install is two steps: the dialog shows what was fetched — including the MCP
  // servers the plugin brings, which the catalog never reviewed — and only the
  // confirm registers it.
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("It brings 1 MCP server")).toBeVisible();
  await expect(dialog.getByText("mcp.slack.com/mcp")).toBeVisible();
  await expect(dialog.getByText(/skills:\s+brand-voice/)).toBeVisible();
  await expect(card.getByText("Installed", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Install" }).click();
  await expect(card.getByText("Installed", { exact: true })).toBeVisible();
  // The control is a menu once there is more than one thing to do with the row.
  await expect(card.getByRole("button", { name: "Actions" })).toBeVisible();
});

test("a skill is read, edited and saved, and the page re-renders the Markdown", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/customize/skills");
  await page.getByRole("link", { name: /Weekly report/ }).click();
  await expect(page).toHaveURL(/\/customize\/skills\/weekly-report$/);
  await expect(page.getByRole("heading", { name: "Steps" })).toBeVisible();   // ## Steps, rendered

  await page.getByRole("button", { name: "Edit" }).click();
  const editor = page.getByLabel("SKILL.md");
  await expect(editor).toContainText("Ask finance for the numbers");
  await editor.fill(
    "---\nname: weekly-report\ndescription: How to put the Friday report together.\n---\n\n" +
    "## Checklist\n\nNumbers first, then the summary.\n",
  );
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("heading", { name: "Checklist" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Steps" })).toHaveCount(0);
});

test("a plugin draft is read verbatim, approved, and becomes an installable plugin", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/customize/plugins");
  await expect(page.getByRole("heading", { name: "Pending review" })).toBeVisible();
  await page.getByRole("button", { name: "Review" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: /Review .*Finance/ })).toBeVisible();
  // Byte for byte, and no heading made out of the `#` line: the surface is what
  // the person is vouching for, so a renderer must never touch it.
  await expect(dialog.locator("pre")).toHaveText(FINANCE_SURFACE);
  await expect(dialog.getByRole("heading", { name: /part of the draft/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Pending review" })).toHaveCount(0);
  const card = page.locator("ul > li").filter({ hasText: "Finance" });   // not the toast
  await expect(card.getByRole("button", { name: "Install" })).toBeVisible();
});

test("a plugin is added to the catalog from the browser and waits for approval", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/customize/plugins");
  await page.getByRole("button", { name: "Add plugin" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a plugin to the catalog" });

  // The link is the only thing anyone actually has; the fields come from it.
  await dialog.getByLabel("Link to the plugin").fill(
    "https://github.com/anthropics/knowledge-work-plugins/tree/11ac5eeaca27c764913a06f1e61a7cb712790b1a/sales");
  // exact: "Repository" is also inside "Directory in the repository", and "Name"
  // inside "Display name".
  await expect(dialog.getByLabel("Repository", { exact: true })).toHaveValue("https://github.com/anthropics/knowledge-work-plugins");
  await expect(dialog.getByLabel("Commit or tag")).toHaveValue("11ac5eeaca27c764913a06f1e61a7cb712790b1a");
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue("sales");
  await dialog.getByLabel("What it is for").fill("Pipeline reviews and call notes.");
  await dialog.getByRole("button", { name: "Queue for approval" }).click();

  // Queued, not installed: it joins the review section, and no card appeared.
  await expect(dialog).toHaveCount(0);
  const queued = page.locator("ul > li").filter({ hasText: "Pipeline reviews" });
  await expect(queued.getByRole("button", { name: "Review" })).toBeVisible();
  await expect(page.locator("ul > li").filter({ hasText: "sales" }).getByRole("button", { name: "Install" })).toHaveCount(0);
});
