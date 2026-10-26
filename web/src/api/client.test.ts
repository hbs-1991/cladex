import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";

const ok = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => vi.restoreAllMocks());

describe("api", () => {
  it("sends JSON mutations with the JSON content type and same-origin credentials", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ id: "web:1:x" }, 201));
    await api.threads.create();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/threads");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init?.credentials).toBe("same-origin");
  });

  it("url-encodes thread ids in paths", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ events: [] }));
    await api.threads.events("tg:private:1:t9", 7);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/threads/tg%3Aprivate%3A1%3At9/events?after=7");
  });

  it("lists the composer's commands", async () => {
    const commands = [{ name: "compact", description: "", source: "sdk" }];
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ commands }));
    expect(await api.commands.list()).toEqual({ commands });
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/commands");
  });

  it("throws ApiError with the server code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      ok({ error: { code: "busy", message: "the agent is busy in this thread" } }, 409),
    );
    await expect(api.threads.send("web:1:x", { text: "hi", attachment_ids: [] }))
      .rejects.toMatchObject<Partial<ApiError>>({ status: 409, code: "busy" });
  });

  it("uploads as multipart without a manual content type", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ file_id: "f1" }, 201));
    await api.uploads.put("web:1:x", new File(["x"], "a.txt", { type: "text/plain" }));
    const init = fetchMock.mock.calls[0]![1]!;
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });
});
