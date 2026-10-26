/**
 * A turn caught mid-flight: the stream stops after a tool call, with no result
 * and no turn.end, so the page holds the running state for as long as the test
 * cares to look at it.
 */

import { expect, test } from "@playwright/test";
import { ev, installFakeApi } from "./fake-server";

/** thought → finished fetch → thought → a fetch still in flight. Never ends. */
const midFlight =
  ev(1, "t1", "turn.start", { channel: "web", user_id: 1 }) +
  ev(2, "t1", "user.message", { text: "привет", attachments: [] }) +
  ev(3, "t1", "thinking", { text: "I should look this up." }) +
  ev(4, "t1", "tool.call", { call_id: "c1", name: "WebSearch", input: { query: "incident" } }) +
  ev(5, "t1", "tool.result", { call_id: "c1", output: "ok", is_error: false, duration_ms: 900 }) +
  ev(6, "t1", "thinking", { text: "The first hit looks right." }) +
  ev(7, "t1", "tool.call", { call_id: "c2", name: "WebFetch", input: { url: "https://example.com" } });

/** What a page load mid-turn replays: the snapshot ends with the not-yet-flushed
 *  reasoning, appended after the tool call that is still in flight. */
const reconnect = midFlight + ev(8, "t1", "thinking.delta", { delta: "Still weighing it." });

test("a not-yet-flushed thought does not take the header from a running tool", async ({ page }) => {
  await installFakeApi(page, reconnect);
  await page.goto("/t/web%3A1%3Ax");
  await page.getByPlaceholder("Message CladeX…").fill("привет");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Still weighing it.")).toBeVisible();
  await expect(page.locator('[data-slot="step-group"] > button').first()).toHaveText(/Opening page/);
});

test("the header names the tool in flight, and the group can still be collapsed", async ({ page }) => {
  await installFakeApi(page, midFlight);
  await page.goto("/t/web%3A1%3Ax");
  await page.getByPlaceholder("Message CladeX…").fill("привет");
  await page.keyboard.press("Enter");

  const header = page.locator('[data-slot="step-group"] > button').first();
  await expect(header).toHaveText(/Opening page/);
  await expect(page.getByText("The first hit looks right.")).toBeVisible();

  await header.click();                                   // collapsing mid-turn must work
  await expect(page.getByText("The first hit looks right.")).toBeHidden();
  await header.click();
  await expect(page.getByText("The first hit looks right.")).toBeVisible();
});

/** What arrives after the stream above drops: the fetch returns, a thought lands,
 *  the next call goes out. Delivered on the client's own reconnect. */
const later =
  ev(9, "t1", "tool.result", { call_id: "c2", output: "page", is_error: false, duration_ms: 1200 }) +
  ev(10, "t1", "thinking", { text: "Now I have what I needed." }) +
  ev(11, "t1", "tool.call", { call_id: "c3", name: "Bash", input: { command: "ls" } });

test("a group closed by hand stays closed while the turn goes on", async ({ page }) => {
  let phase = 0;
  await installFakeApi(page, midFlight);
  await page.route("**/api/threads/web%3A1%3Ax/stream*", (r) => {
    phase += 1;
    r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" },
      body: phase === 1 ? midFlight : later });
  });
  await page.goto("/t/web%3A1%3Ax");
  await page.getByPlaceholder("Message CladeX…").fill("привет");
  await page.keyboard.press("Enter");

  const header = page.locator('[data-slot="step-group"] > button').first();
  await expect(page.getByText("The first hit looks right.")).toBeVisible();
  await header.click();
  await expect(page.getByText("The first hit looks right.")).toBeHidden();

  // The reconnect brings a result, a thought and another call — none of which is
  // the user asking to see any of it again.
  await expect(header).toHaveText(/Running command/, { timeout: 15000 });
  await expect(page.getByText("Now I have what I needed.")).toBeHidden();
  await expect(page.getByText("The first hit looks right.")).toBeHidden();
});

/** What follows a text block: it ends one chain of thought, and the next thought
 *  opens another. Delivered on the client's own reconnect. */
const afterText =
  ev(9, "t1", "tool.result", { call_id: "c2", output: "page", is_error: false, duration_ms: 900 }) +
  ev(10, "t1", "text.block", { text: "Пока собираю источники." }) +
  ev(11, "t1", "thinking", { text: "Second thought, new group." }) +
  ev(12, "t1", "tool.call", { call_id: "c3", name: "Bash", input: { command: "ls" } });

test("closing the thoughts closes them for the turn, not for one group", async ({ page }) => {
  let phase = 0;
  await installFakeApi(page, midFlight);
  await page.route("**/api/threads/web%3A1%3Ax/stream*", (r) => {
    phase += 1;
    r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" },
      body: phase === 1 ? midFlight : afterText });
  });
  await page.goto("/t/web%3A1%3Ax");
  await page.getByPlaceholder("Message CladeX…").fill("привет");
  await page.keyboard.press("Enter");

  await expect(page.getByText("The first hit looks right.")).toBeVisible();
  await page.locator('[data-slot="step-group"] > button').first().click();
  await expect(page.getByText("The first hit looks right.")).toBeHidden();

  // A text block lands and the turn opens a second group. The reader closed the
  // thoughts of this turn, so the new one arrives closed too.
  await expect(page.getByText("Пока собираю источники.")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("Second thought, new group.")).toBeHidden();
});
