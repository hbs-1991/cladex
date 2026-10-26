import { expect, test } from "@playwright/test";
import { installFakeApi } from "./fake-server";

/** A narrow phone with a fake microphone: Chromium hands the page a synthetic
 *  stream, so the dictation UI runs exactly as it does on a real device. */
test.use({ viewport: { width: 320, height: 720 }, hasTouch: true, isMobile: true, permissions: ["microphone"],
  launchOptions: { args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] } });


test("the waveform stays inside the composer, stop and send included", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/t/web%3A1%3Ax");
  await page.getByRole("button", { name: "Start dictation" }).click();
  await expect(page.getByRole("status", { name: "Listening" })).toBeVisible();
  const shell = await page.locator("[data-slot=aui_composer-shell]").boundingBox();
  for (const name of ["Stop dictation", "Send message"]) {
    const b = await page.getByRole("button", { name }).boundingBox();
    expect(b && shell && b.x + b.width).toBeLessThanOrEqual(shell!.x + shell!.width);
  }
  const viewport = page.locator("[data-slot=aui_thread-viewport]");
  expect(await viewport.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
});
