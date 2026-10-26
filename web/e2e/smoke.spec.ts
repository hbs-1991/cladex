import { expect, test } from "@playwright/test";
import { installFakeApi } from "./fake-server";

test("login → send → streamed reply with a step group", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/t/web%3A1%3Ax");
  await page.getByPlaceholder("Message CladeX…").fill("привет");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Ответ готов.")).toBeVisible();
  await expect(page.getByText(/^Worked (\d+ s|1 step)$/)).toBeVisible();   // timed once turn.end lands
});
