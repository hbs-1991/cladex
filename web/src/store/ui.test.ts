import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { commands: { list: vi.fn() } } }));

import { api } from "@/api/client";
import { useStore } from "@/store";

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  vi.mocked(api.commands.list).mockReset();
});

it("loads the command list once and keeps it", async () => {
  const commands = [{ name: "compact", description: "", source: "sdk" as const }];
  vi.mocked(api.commands.list).mockResolvedValue({ commands });
  await Promise.all([useStore.getState().loadCommands(), useStore.getState().loadCommands()]);
  await useStore.getState().loadCommands();
  expect(api.commands.list).toHaveBeenCalledTimes(1);
  expect(useStore.getState().commands).toEqual(commands);
  expect(useStore.getState().commandsStatus).toBe("ready");
});

it("a failed load settles quietly with an empty list", async () => {
  vi.mocked(api.commands.list).mockRejectedValue(new Error("offline"));
  await expect(useStore.getState().loadCommands()).resolves.toBeUndefined();
  expect(useStore.getState().commands).toEqual([]);
  expect(useStore.getState().commandsStatus).toBe("ready");
});
