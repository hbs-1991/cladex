/** Store slice: the agent's skill library — the list, one cached detail per open
 *  skill, and the owner's edits. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { SkillDetail, SkillWire } from "@/api/types";

export type SkillsSlice = {
  skills: SkillWire[]; skillsLoaded: boolean;
  /** Keyed by name; the page reads its buffer from here so a re-render keeps it. */
  skillDetail: Record<string, SkillDetail>;
  loadSkills: () => Promise<void>;
  loadSkill: (name: string) => Promise<SkillDetail>;
  createSkill: (name: string, content: string) => Promise<SkillWire>;
  /** `etag` is the one read with the content; a mismatch throws `stale_edit` and
   *  the cached detail is left exactly as it was, so nothing the owner typed is lost. */
  saveSkill: (name: string, content: string, etag: string) => Promise<SkillDetail>;
  pinSkill: (name: string) => Promise<void>;
  unpinSkill: (name: string) => Promise<void>;
  archiveSkill: (name: string) => Promise<void>;
  restoreSkill: (name: string) => Promise<void>;
};

export const createSkillsSlice: StateCreator<SkillsSlice, [], [], SkillsSlice> = (set) => {
  const replace = (s0: SkillWire) => set((s) => ({ skills: s.skills.map((x) => (x.name === s0.name ? s0 : x)) }));
  const cache = (d: SkillDetail) => set((s) => ({ skillDetail: { ...s.skillDetail, [d.name]: d } }));
  return {
    skills: [], skillsLoaded: false, skillDetail: {},
    async loadSkills() { set({ skills: (await api.skills.list()).skills, skillsLoaded: true }); },
    async loadSkill(name) {
      const d = await api.skills.get(name);
      cache(d);
      return d;
    },
    async createSkill(name, content) {
      const w = await api.skills.create({ name, content });
      set((s) => ({ skills: [...s.skills, w] }));
      return w;
    },
    async saveSkill(name, content, etag) {
      const d = await api.skills.save(name, { content, etag });   // throws on 409 stale_edit; nothing below runs
      cache(d);
      replace(d);      // a detail is a row plus text: patch_count and last_patched moved with it
      return d;
    },
    async pinSkill(name) { replace(await api.skills.pin(name)); },
    async unpinSkill(name) { replace(await api.skills.unpin(name)); },
    async archiveSkill(name) { replace(await api.skills.archive(name)); },
    async restoreSkill(name) { replace(await api.skills.restore(name)); },
  };
};
