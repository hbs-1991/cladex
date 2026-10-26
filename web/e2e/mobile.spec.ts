import { expect, test } from "@playwright/test";
import { CONTENT_TITLE, installFakeApi } from "./fake-server";

/** A phone (DESIGN.md §4: below 768px the sidebar is an overlay). Set SHOTS to a
 * directory to also save what the phone sees. */
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

const shot = async (page: import("@playwright/test").Page, name: string) => {
  if (!process.env.SHOTS) return;
  await page.waitForTimeout(600);                          // let the drawer and fade-ins settle
  await page.screenshot({ path: `${process.env.SHOTS}/${name}.png` });
};

test("the sidebar is a drawer behind the menu button, and a tap in it navigates and closes it", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/t/web%3A1%3Ax");
  await expect(page.getByPlaceholder("Message CladeX…")).toBeVisible();
  const docked = page.locator("aside", { has: page.getByRole("link", { name: "Calendar" }) });
  await expect(docked).toBeHidden();                       // no rail on a phone
  await expect(page.getByRole("button", { name: "Menu" })).toBeVisible();
  await shot(page, "phone-chat");

  await page.getByRole("button", { name: "Menu" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("link", { name: "Calendar" })).toBeVisible();
  await expect(drawer.getByText("Recent")).toBeVisible();
  await shot(page, "phone-drawer");

  await drawer.getByRole("link", { name: "Calendar" }).click();
  await expect(page).toHaveURL(/\/calendar$/);
  await expect(drawer).toBeHidden();                       // navigating closes it
  await expect(page.getByRole("heading", { name: "Calendar", exact: true, level: 1 })).toBeVisible();
  // A phone opens on the agenda, and the agenda is one column: nothing in it may
  // push the page sideways, however long a title or a routine name gets.
  await expect(page.locator('[data-view="agenda"]')).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(CONTENT_TITLE) })).toBeVisible();
  await shot(page, "phone-calendar");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);

  await page.goto("/t/web%3A1%3Ax");
  await page.getByRole("button", { name: "Menu" }).click();
  const list = page.getByRole("dialog");
  // No hover on a phone: the row's actions must simply be there.
  await list.getByRole("button", { name: "Actions" }).first().click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const field = page.getByLabel("Title");                  // a dialog, not window.prompt
  await field.fill("Планёрка");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(list.getByRole("link", { name: "Планёрка" })).toBeVisible();
  await shot(page, "phone-rename");
  await page.keyboard.press("Escape");

  await page.goto("/customize/connectors");
  await expect(page.getByRole("heading", { name: "Customize" })).toBeVisible();
  await shot(page, "phone-customize");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);                            // the page never scrolls sideways
});

/** Whether the element or any box around it scrolls sideways: the calendar page
 *  scrolls inside its own container, where the document's width would not show it. */
const sideways = (page: import("@playwright/test").Page, selector: string) => page.evaluate((sel) => {
  for (let el = document.querySelector(sel); el; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth + 1) return true;
  }
  return false;
}, selector);

test("the calendar's agenda and item dialog fit a phone, in the phone's own time", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/calendar");
  await expect(page.locator('[data-view="agenda"]')).toBeVisible();
  expect(await sideways(page, '[data-view="agenda"]')).toBe(false);

  await page.getByRole("button", { name: new RegExp(CONTENT_TITLE) }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Text")).toBeVisible();
  // The item opens in a side drawer that slides in from the right: measured mid-slide
  // it still hangs off the screen, so wait for the slide to end.
  await dialog.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
  const box = await dialog.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);    // inside the screen, with its margin
  // The fixture is planned for 10:00 local; the field shows the device's wall time.
  await expect(dialog.getByLabel("Publish at")).toHaveValue(/T10:00$/);
  await shot(page, "phone-calendar-item");
  await dialog.getByRole("button", { name: "Approve" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Approved — the publisher will post it")).toBeVisible();
  expect(await sideways(page, '[data-view="agenda"]')).toBe(false);
});
