/**
 * The model pill: what this lane runs on, what a switch costs, and which model
 * actually answered — at desktop width and on a phone.
 */

import { expect, test, type Page } from "@playwright/test";
import { installFakeApi } from "./fake-server";

const pill = (page: Page) => page.locator('[data-slot="cladex_model-pill"]');

test("names the lane's model, switches it with the price of the cold re-read, and labels the answer", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/t/web%3A1%3Ax");

  // Nothing is chosen yet, so the pill names what a turn would actually run on.
  await expect(pill(page)).toHaveText(/Sonnet 5 · Auto/);
  // The replayed turn says which model wrote it — the one that RAN, effort and all.
  await expect(page.getByText("Ответ готов.")).toBeVisible();
  await expect(page.locator('[data-slot="cladex_answer-model"]')).toHaveText("Sonnet 5 · Medium");

  await pill(page).click();
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitemradio", { name: /Sonnet 5/ })).toContainText("$3 / $15");
  await expect(menu.getByRole("menuitemradio", { name: /Opus 5/ })).toContainText("$15 / $75");
  await expect(menu.getByRole("menuitemradio", { name: "Auto (Medium)" })).toBeVisible();

  await menu.getByRole("menuitemradio", { name: /Opus 5/ }).click();
  // 50K tokens are in the window; Opus 5 re-reads them once at its own price.
  await expect(menu.getByText("The next reply re-reads ~50K tokens on Opus 5: ≈$0.94.")).toBeVisible();
  await expect(pill(page)).toHaveText(/Opus 5 · Auto/);

  // Opus 5's levels are offered now, and the pair survives closing the menu.
  await menu.getByRole("menuitemradio", { name: "Max" }).click();
  await page.keyboard.press("Escape");
  await expect(pill(page)).toHaveText(/Opus 5 · Max/);
});

test("a /model draft opens the picker instead of being sent", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/t/web%3A1%3Ax");
  await expect(pill(page)).toBeVisible();

  const sent: string[] = [];
  page.on("request", (r) => { if (r.url().endsWith("/messages")) sent.push(r.url()); });
  await page.getByPlaceholder("Message CladeX…").fill("/model");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("menuitemradio", { name: /Opus 5/ })).toBeVisible();
  expect(sent).toEqual([]);                                   // the CLI never sees it
  await expect(page.getByPlaceholder("Message CladeX…")).toHaveValue("/model");   // and the draft is kept
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the pill fits the composer rail and its menu does not push the page sideways", async ({ page }) => {
    await installFakeApi(page);
    await page.goto("/t/web%3A1%3Ax");
    await expect(pill(page)).toBeVisible();

    // The rail is one line: the pill truncates rather than wrapping the row.
    const rail = page.locator('[data-slot="aui_composer-shell"]');
    const before = await rail.boundingBox();
    await pill(page).click();
    await expect(page.getByRole("menuitemradio", { name: /Opus 5/ })).toBeVisible();
    const after = await rail.boundingBox();
    expect(after!.height).toBe(before!.height);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
});
