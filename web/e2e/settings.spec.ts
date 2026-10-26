import { expect, test } from "@playwright/test";
import { installFakeApi } from "./fake-server";

test("Settings opens from the user menu and manages secrets", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Secrets" })).toBeVisible();
  // Both kinds of row: one stored, and one a connector declares and nobody set.
  await expect(dialog.getByText("OPENAI_API_KEY")).toBeVisible();
  await expect(dialog.getByText(/GitHub \(token\).*not set/)).toBeVisible();

  await dialog.getByRole("button", { name: "Add secret" }).click();
  await dialog.getByLabel("Name").fill("NEW_TOKEN");
  await dialog.getByLabel("Value").fill("s3cr3t");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByText("NEW_TOKEN")).toBeVisible();
  // The value was sent, never rendered back.
  await expect(dialog.getByText("s3cr3t")).toHaveCount(0);

  const row = dialog.locator("li").filter({ hasText: "OPENAI_API_KEY" });
  await row.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog", { name: /Delete “OPENAI_API_KEY”/ }).getByRole("button", { name: "Delete" }).click();
  await expect(dialog.getByText("OPENAI_API_KEY")).toHaveCount(0);
});

test("Settings manages who may talk to the agent", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();

  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Users" }).click();
  await expect(dialog.getByRole("heading", { name: "Users" })).toBeVisible();
  const ownerRow = dialog.locator("li").filter({ hasText: "Vera" });
  await expect(ownerRow.getByText("Owner")).toBeVisible();
  await expect(ownerRow.getByRole("button")).toHaveCount(0);   // no door that locks from the inside

  await dialog.getByRole("button", { name: "Add user" }).click();
  await dialog.getByLabel("Telegram user id").fill("4242");
  await dialog.getByRole("button", { name: "Add user" }).click();
  await expect(dialog.getByText("4242")).toBeVisible();

  const mate = dialog.locator("li").filter({ hasText: "@mate" });
  await mate.getByRole("button", { name: "Revoke access" }).click();
  await page.getByRole("dialog", { name: /Revoke access for @mate/ })
    .getByRole("button", { name: "Revoke access" }).click();
  await expect(mate.getByText(/no access/)).toBeVisible();
  await expect(mate.getByRole("button", { name: "Give access back" })).toBeVisible();
});

test("Settings grants a user learning and a shell, the shell behind a confirmation", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();

  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Users" }).click();
  await expect(dialog.getByText(/not a boundary for someone who can reach the bot in a group/)).toBeVisible();
  // The owner's reach is the owner's: nothing to grant there.
  await expect(dialog.locator("li").filter({ hasText: "Vera" }).getByRole("switch")).toHaveCount(0);
  // An id only in allowed_users has no row to keep a choice in.
  const fromConfig = dialog.locator("li").filter({ hasText: "from allowed_users" });
  await expect(fromConfig.getByRole("switch", { name: "Bash for 777" })).toBeDisabled();

  const mate = dialog.locator("li").filter({ hasText: "@mate" });
  const bash = mate.getByRole("switch", { name: "Bash for @mate" });
  const learning = mate.getByRole("switch", { name: "Learning for @mate" });
  await expect(bash).not.toBeChecked();
  await expect(learning).not.toBeChecked();
  await expect(mate.getByText("default", { exact: true })).toHaveCount(2);

  // Learning applies at once — from the keyboard, and focus stays on the switch
  // through the write and the re-read, so the next Tab starts where it was.
  await learning.focus();
  await page.keyboard.press("Space");
  await expect(learning).toBeChecked();
  await expect(learning).toBeFocused();
  await expect(mate.getByText("default", { exact: true })).toHaveCount(1);

  // Bash does not: first the confirmation says what a shell reaches.
  await bash.click();
  const confirm = page.getByRole("dialog", { name: "Enable Bash for @mate?" });
  await expect(confirm).toContainText("secrets.env");
  await confirm.getByRole("button", { name: "Enable Bash" }).click();

  await expect(bash).toBeChecked();
  await expect(learning).toBeChecked();
  await expect(mate.getByText("default", { exact: true })).toHaveCount(0);
});
