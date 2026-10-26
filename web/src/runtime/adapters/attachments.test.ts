import { expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { uploads: { put: vi.fn(async () => ({
  file_id: "f1", name: "a.png", media_type: "image/png", size: 3, url: "/api/files/f1" })) } } }));

import { attachmentAdapter } from "./attachments";

it("uploads on add and completes on send with the file id", async () => {
  const ad = attachmentAdapter("web:1:x");
  const file = new File(["abc"], "a.png", { type: "image/png" });
  const pending = await ad.add({ file });
  expect(pending).toMatchObject({ id: "f1", type: "image", name: "a.png", status: { type: "requires-action", reason: "composer-send" } });
  const done = await ad.send(pending);
  expect(done.status).toEqual({ type: "complete" });
  expect(done.content).toEqual([]);
});
