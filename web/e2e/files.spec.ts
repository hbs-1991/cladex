import { expect, test, type Page } from "@playwright/test";
import { ev, installFakeApi } from "./fake-server";

/** A turn that delivers three files: the panel's chooser has something to choose from. */
const threeFiles = () =>
  ev(1, "t1", "turn.start", { channel: "web", user_id: 1 }) +
  ev(2, "t1", "user.message", { text: "отчёт", attachments: [] }) +
  ev(3, "t1", "text.block", { text: "Готово." }) +
  ev(4, "t1", "file", { file_id: "f1", name: "report.md", media_type: "text/markdown", size: 2048, url: "/api/files/f1" }) +
  ev(5, "t1", "file", { file_id: "f2", name: "compare_prices.py", media_type: "text/x-python", size: 900, url: "/api/files/f2" }) +
  ev(6, "t1", "file", { file_id: "f3", name: "report.pdf", media_type: "application/pdf", size: 120000, url: "/api/files/f3" }) +
  ev(7, "t1", "turn.end", { status: "ok", cost_usd: 0, sdk_session_id: "s", error: null });

async function setUp(page: Page) {
  await installFakeApi(page, threeFiles());
  await page.route("**/api/files/f1", (r) => r.fulfill({ body: "# Отчёт\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", contentType: "text/markdown" }));
  await page.route("**/api/files/f2", (r) => r.fulfill({ body: "print('hi')\n", contentType: "text/plain" }));
  await page.goto("/t/web%3A1%3Ax");
  await expect(page.getByText("Готово.")).toBeVisible();
}

test("a chat with several files lets the reader choose which one the panel shows", async ({ page }) => {
  await setUp(page);
  await page.getByRole("button", { name: /report\.md/ }).click();
  const panel = page.getByRole("complementary");
  await expect(panel.getByRole("heading", { name: "Отчёт" })).toBeVisible();

  // Markdown reads as a document or as its source.
  await panel.getByRole("button", { name: "Source" }).click();
  await expect(panel.getByText("# Отчёт")).toBeVisible();
  await panel.getByRole("button", { name: "Preview" }).click();
  await expect(panel.getByRole("heading", { name: "Отчёт" })).toBeVisible();

  await panel.getByRole("button", { name: /Choose a file/ }).click();
  const items = page.getByRole("menuitem");
  await expect(items).toHaveCount(3);
  await items.filter({ hasText: "compare_prices.py" }).click();
  await expect(page.getByRole("complementary", { name: "compare_prices.py" })).toContainText("print('hi')");
  await expect(page.getByRole("complementary").getByRole("button", { name: "Source" })).toHaveCount(0);   // code has one view

  // The thread header offers the same list.
  await page.getByRole("complementary").getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: /Files: 3/ }).click();
  await page.getByRole("menuitem").filter({ hasText: "report.pdf" }).click();
  await expect(page.getByRole("complementary", { name: "report.pdf" })).toBeVisible();
});

test("the docked panel is resizable, remembers its width and never squeezes the thread away", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setUp(page);
  await page.getByRole("button", { name: /report\.md/ }).click();
  const panel = page.locator("[data-slot=file-panel]");
  const handle = panel.getByRole("separator", { name: "Resize the file panel" });
  const before = (await panel.boundingBox())!.width;

  // Drag the left edge 200px to the left: the panel grows by as much.
  const h = (await handle.boundingBox())!;
  await page.mouse.move(h.x + h.width / 2, h.y + 200);
  await page.mouse.down();
  await page.mouse.move(h.x + h.width / 2 - 100, h.y + 200, { steps: 5 });
  await page.mouse.move(h.x + h.width / 2 - 200, h.y + 200, { steps: 5 });
  await page.mouse.up();
  const dragged = (await panel.boundingBox())!.width;
  expect(Math.abs(dragged - (before + 200))).toBeLessThanOrEqual(2);

  // The keyboard moves it too: left widens, right narrows.
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  expect(Math.round((await panel.boundingBox())!.width)).toBe(Math.round(dragged) - 32);

  // Remembered across a reload.
  const kept = Math.round((await panel.boundingBox())!.width);
  await page.reload();
  await page.getByRole("button", { name: /report\.md/ }).click();
  expect(Math.round((await page.locator("[data-slot=file-panel]").boundingBox())!.width)).toBe(kept);

  // Home asks for the widest: 256px of sidebar and 320px of thread stay.
  await page.locator("[data-slot=file-panel]").getByRole("separator").focus();
  await page.keyboard.press("Home");
  expect(Math.round((await page.locator("[data-slot=file-panel]").boundingBox())!.width)).toBe(1440 - 256 - 320);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1440);

  // End is the narrowest, double-click the default.
  await page.keyboard.press("End");
  expect(Math.round((await page.locator("[data-slot=file-panel]").boundingBox())!.width)).toBe(320);
  await page.locator("[data-slot=file-panel]").getByRole("separator").dblclick();
  expect(Math.round((await page.locator("[data-slot=file-panel]").boundingBox())!.width)).toBe(Math.round(before));
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 320, height: 720 }, hasTouch: true, isMobile: true });

  test("the file panel takes the screen and never scrolls the page sideways", async ({ page }) => {
    await setUp(page);
    await page.getByRole("button", { name: /report\.md/ }).click();
    const panel = page.getByRole("complementary");
    await expect(panel.getByRole("heading", { name: "Отчёт" })).toBeVisible();
    const box = await panel.boundingBox();
    expect(box?.width).toBe(320);
    for (const name of ["Copy", "More file actions", "Close"]) {
      const b = await panel.getByRole("button", { name, exact: true }).boundingBox();
      expect(b && b.x + b.width).toBeLessThanOrEqual(320);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
    await expect(panel.getByRole("separator")).toBeHidden();   // the panel is the screen; nothing to resize
  });
});
