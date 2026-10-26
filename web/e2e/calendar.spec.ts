import { expect, test } from "@playwright/test";
import { CONTENT_TITLE, DRAFTING_ID, FAR_ID, FIRE_NAME, IDEA_TITLE, installFakeApi } from "./fake-server";

/** The calendar on a desktop: the month by default, the plan and the routines on
 * the same days, the owner's decisions in the item dialog, drag-and-drop, and the
 * old `/routines` links landing on the Tasks tab. */

const localKey = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

test("the month shows a post and a routine fire on today, and the post can be approved", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await expect(page.getByRole("heading", { name: "Calendar", exact: true, level: 1 })).toBeVisible();
  await expect(page.locator('[data-view="month"]')).toBeVisible();
  const today = page.locator(`[data-day="${localKey(new Date())}"]`);
  await expect(today.getByRole("button", { name: new RegExp(CONTENT_TITLE) })).toBeVisible();
  await expect(today.getByRole("link", { name: new RegExp(FIRE_NAME) }).first()).toBeVisible();
  await expect(today.getByText("Inbox check · Every 5 minutes")).toBeVisible();   // dense: one marker, not 288 chips
  await expect(page.getByRole("heading", { name: /Unscheduled/ })).toBeVisible();

  await today.getByRole("button", { name: new RegExp(CONTENT_TITLE) }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Text")).toHaveValue("Our autumn launch, in thirty seconds.");
  await expect(dialog.getByRole("img", { name: "Media 1" })).toBeVisible();
  await dialog.getByRole("button", { name: "Approve" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Approved — the publisher will post it")).toBeVisible();
  // Approved means "about then": the grid stops promising the minute.
  await expect(today.getByRole("button", { name: new RegExp(CONTENT_TITLE) })).toContainText("≈ 10:00");

  await today.getByRole("button", { name: new RegExp(CONTENT_TITLE) }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "Withdraw approval" })).toBeVisible();
});

test("dragging a post to another day keeps its time of day", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  const now = new Date();
  const chip = page.locator(`[data-day="${localKey(now)}"]`).getByRole("button", { name: new RegExp(CONTENT_TITLE) });
  await expect(chip).toBeVisible();
  // Another day of this month's grid: the day before, unless today opens the month.
  const other = new Date(now);
  other.setDate(now.getDate() + (now.getDate() === 1 ? 1 : -1));
  const target = page.locator(`[data-day="${localKey(other)}"]`);
  const patch = page.waitForRequest((r) => r.method() === "PATCH" && /\/api\/content\/1$/.test(r.url()));
  await chip.dragTo(target);
  const body = (await patch).postDataJSON() as { publish_at: string };
  const moved = new Date(body.publish_at);
  expect(localKey(moved)).toBe(localKey(other));
  expect([moved.getHours(), moved.getMinutes()]).toEqual([10, 0]);
  await expect(target.getByRole("button", { name: new RegExp(CONTENT_TITLE) })).toBeVisible();
});

test("the views switch and are remembered, and a routine fire links to its task", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Agenda" }).click();
  await expect(page.locator('[data-view="agenda"]')).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Agenda" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Week", exact: true }).click();
  await expect(page.locator('[data-view="week"]')).toBeVisible();
  await expect(page.getByRole("link", { name: new RegExp(FIRE_NAME) }).first()).toHaveAttribute("href", "/calendar/tasks/3");
});

test("the old /routines URLs land on the Tasks tab", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/routines");
  await expect(page).toHaveURL(/\/calendar\/tasks$/);
  await expect(page.getByRole("link", { name: "Tasks" })).toHaveAttribute("aria-current", "page");
  await page.goto("/routines/3");
  await expect(page).toHaveURL(/\/calendar\/tasks\/3$/);
});

test("the new post dialog lines up its two-column rows even with a hint under one side", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await page.getByRole("button", { name: "New post" }).click();
  const dialog = page.getByRole("dialog");
  // The dialog zooms in, and each boundingBox below is read in a later frame than
  // the one before it: measure the settled layout, not four frames of an animation.
  await dialog.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
  // "Publish at" carries a two-line hint and "Status" none: a stretched column
  // spread the spare height between its own rows and sank the status control.
  const top = async (name: string) => (await dialog.getByText(name, { exact: true }).boundingBox())!.y;
  const controlTop = async (label: string) => (await dialog.getByLabel(label).boundingBox())!.y;
  expect(Math.abs(await top("Publish at") - await top("Status"))).toBeLessThan(1);
  expect(Math.abs(await controlTop("Publish at") - await controlTop("Status"))).toBeLessThan(1);
  expect(Math.abs(await top("Channel") - await top("Format"))).toBeLessThan(1);
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/desk-new-post.png` });
});

test("an idea's theme is approved from its dialog, and the approval can be taken back", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Agenda" }).click();
  await page.getByRole("button", { name: new RegExp(IDEA_TITLE) }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);   // nothing written yet
  await dialog.getByRole("button", { name: "Approve theme" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Theme approved — the drafter will write the post")).toBeVisible();
  const chip = page.getByRole("button", { name: new RegExp(IDEA_TITLE) });
  await expect(chip).toHaveAttribute("data-status", "planned");
  await expect(chip).toContainText("Theme approved");

  await chip.click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove theme approval" }).click();
  await expect(chip).toHaveAttribute("data-status", "idea");
});

test("this week's themes are approved in one go, after a count", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Week", exact: true }).click();
  await page.getByRole("button", { name: "Approve week's themes" }).click();
  const dialog = page.getByRole("dialog", { name: "Approve this week's themes" });
  await expect(dialog.getByText("1 idea will be approved.")).toBeVisible();
  const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/api/content/approve-themes"));
  await dialog.getByRole("button", { name: "Approve themes" }).click();
  const body = (await request).postDataJSON() as { from: string; to: string };
  expect(new Date(body.to).getTime() - new Date(body.from).getTime()).toBeGreaterThanOrEqual(167 * 3_600_000);   // a week, DST or not
  await expect(page.getByText("1 theme approved")).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(IDEA_TITLE) })).toHaveAttribute("data-status", "planned");
});

test("a /calendar?item= link opens that post, even far outside the range on screen, and is forgotten on close", async ({ page }) => {
  await installFakeApi(page);
  await page.goto(`/calendar?item=${FAR_ID}`);
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Text")).toHaveValue("The drafted text.");
  await expect(dialog.getByText(/Telegram card: sent/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/calendar$/);
});

test("a post the drafter is writing is read-only", async ({ page }) => {
  await installFakeApi(page);
  await page.goto(`/calendar?item=${DRAFTING_ID}`);
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/drafter is writing this post/)).toBeVisible();
  await expect(dialog.getByLabel("Text")).toBeDisabled();
  for (const name of ["Save", "Approve", "Delete"]) await expect(dialog.getByRole("button", { name, exact: true })).toHaveCount(0);
});

test("the drafter is picked next to the publisher", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Drafter: No drafter" }).click();
  await expect(page.getByRole("dialog", { name: "Drafter" }).getByRole("button", { name: "Create a drafter task" })).toBeVisible();
});
