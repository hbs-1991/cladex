import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ApiError: actual.ApiError,
    api: {
      skills: {
        list: vi.fn(), get: vi.fn(), create: vi.fn(), save: vi.fn(),
        pin: vi.fn(), unpin: vi.fn(), archive: vi.fn(), restore: vi.fn(),
      },
    },
  };
});

import { ApiError, api } from "@/api/client";
import type { SkillDetail, SkillWire } from "@/api/types";
import { useStore } from "@/store";

const debugging: SkillWire = {
  name: "debugging", title: "Debugging", description: "How to debug", broken: false,
  source: "bundled", state: "active", pinned: false, use_count: 3, patch_count: 1,
  created_at: "2026-09-01T00:00:00Z", last_used: "2026-09-09T00:00:00Z", last_patched: null,
  files: 2, bytes: 900,
};
const detail: SkillDetail = { ...debugging, content: "---\nname: debugging\n---\n## body", etag: "aaa", tree: [{ path: "references/x.md", bytes: 12 }] };

beforeEach(() => { useStore.setState(useStore.getInitialState()); });

it("loads the list and caches a detail by name", async () => {
  vi.mocked(api.skills.list).mockResolvedValue({ skills: [debugging] });
  vi.mocked(api.skills.get).mockResolvedValue(detail);
  await useStore.getState().loadSkills();
  expect(useStore.getState().skillsLoaded).toBe(true);
  await useStore.getState().loadSkill("debugging");
  expect(useStore.getState().skillDetail.debugging).toMatchObject({ etag: "aaa", content: detail.content });
});

it("saving replaces the cached detail and the row", async () => {
  vi.mocked(api.skills.list).mockResolvedValue({ skills: [debugging] });
  vi.mocked(api.skills.get).mockResolvedValue(detail);
  await useStore.getState().loadSkills();
  await useStore.getState().loadSkill("debugging");

  vi.mocked(api.skills.save).mockResolvedValue({ ...detail, content: "new", etag: "bbb", patch_count: 2 });
  await useStore.getState().saveSkill("debugging", "new", "aaa");
  expect(api.skills.save).toHaveBeenCalledWith("debugging", { content: "new", etag: "aaa" });
  expect(useStore.getState().skillDetail.debugging).toMatchObject({ content: "new", etag: "bbb" });
  expect(useStore.getState().skills[0]!.patch_count).toBe(2);
});

it("a stale_edit save surfaces the error and leaves the cached buffer alone", async () => {
  vi.mocked(api.skills.get).mockResolvedValue(detail);
  await useStore.getState().loadSkill("debugging");
  vi.mocked(api.skills.save).mockRejectedValue(new ApiError(409, "stale_edit", "changed"));

  await expect(useStore.getState().saveSkill("debugging", "mine", "aaa")).rejects.toMatchObject({ code: "stale_edit" });
  expect(useStore.getState().skillDetail.debugging).toMatchObject({ content: detail.content, etag: "aaa" });
});

it("creates, pins and archives", async () => {
  vi.mocked(api.skills.list).mockResolvedValue({ skills: [debugging] });
  await useStore.getState().loadSkills();

  const mine: SkillWire = { ...debugging, name: "mine", title: "Mine", source: "user", use_count: 0, patch_count: 0, files: 1, bytes: 40 };
  vi.mocked(api.skills.create).mockResolvedValue(mine);
  await useStore.getState().createSkill("mine", "---\nname: mine\ndescription: d\n---\n");
  expect(useStore.getState().skills.map((s) => s.name)).toEqual(["debugging", "mine"]);

  vi.mocked(api.skills.pin).mockResolvedValue({ ...mine, pinned: true });
  await useStore.getState().pinSkill("mine");
  expect(useStore.getState().skills[1]!.pinned).toBe(true);

  vi.mocked(api.skills.archive).mockResolvedValue({ ...mine, pinned: false, state: "archived" });
  await useStore.getState().archiveSkill("mine");
  expect(useStore.getState().skills[1]!.state).toBe("archived");

  vi.mocked(api.skills.restore).mockResolvedValue({ ...mine, state: "active" });
  await useStore.getState().restoreSkill("mine");
  expect(useStore.getState().skills[1]!.state).toBe("active");
});
