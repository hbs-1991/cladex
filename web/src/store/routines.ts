/** Store slice: routines (scheduled jobs), their runs and the actions on them. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { RoutineWire, RunWire } from "@/api/types";

type Create = { name: string; schedule: string; prompt: string; timeout_seconds?: number | null;
                model?: string | null; effort?: string | null };

export type RoutinesSlice = {
  routines: RoutineWire[]; routinesLoaded: boolean; runs: Record<number, RunWire[]>;
  loadRoutines: () => Promise<void>; createRoutine: (b: Create) => Promise<RoutineWire>;
  updateRoutine: (id: number, b: Partial<Create>) => Promise<void>;
  pauseRoutine: (id: number) => Promise<void>; resumeRoutine: (id: number) => Promise<void>;
  runRoutine: (id: number) => Promise<void>; removeRoutine: (id: number) => Promise<void>;
  loadRuns: (id: number) => Promise<void>;
};

export const createRoutinesSlice: StateCreator<RoutinesSlice, [], [], RoutinesSlice> = (set) => {
  const patch = (id: number, p: Partial<RoutineWire>) =>
    set((s) => ({ routines: s.routines.map((r) => (r.id === id ? { ...r, ...p } : r)) }));
  return {
    routines: [], routinesLoaded: false, runs: {},
    async loadRoutines() { set({ routines: (await api.routines.list()).routines, routinesLoaded: true }); },
    async createRoutine(b) { const r = await api.routines.create(b); set((s) => ({ routines: [...s.routines, r] })); return r; },
    async updateRoutine(id, b) { patch(id, await api.routines.update(id, b)); },
    async pauseRoutine(id) { patch(id, { state: "paused" }); await api.routines.pause(id); },
    async resumeRoutine(id) { patch(id, { state: "active" }); await api.routines.resume(id); },
    async runRoutine(id) { await api.routines.run(id); },
    async removeRoutine(id) { await api.routines.remove(id); set((s) => ({ routines: s.routines.filter((r) => r.id !== id) })); },
    async loadRuns(id) { const { runs } = await api.routines.runs(id); set((s) => ({ runs: { ...s.runs, [id]: runs } })); },
  };
};
