/** Store slice: the deploy-wide extension secrets, as Settings → Secrets reads them.
 *
 * Names only — the wire carries no value, so neither does the store. Every action
 * re-reads the list rather than patching a row: `used_by` and the declared-but-unset
 * entries come from the catalog, so what a write changes is not only the row written.
 */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { SecretWire } from "@/api/types";

export type SecretsSlice = {
  secrets: SecretWire[]; secretsLoaded: boolean;
  loadSecrets: () => Promise<void>;
  putSecret: (name: string, value: string) => Promise<void>;
  forgetSecret: (name: string) => Promise<void>;
};

export const createSecretsSlice: StateCreator<SecretsSlice, [], [], SecretsSlice> = (set, get) => ({
  secrets: [], secretsLoaded: false,
  async loadSecrets() { set({ secrets: (await api.secrets.list()).secrets, secretsLoaded: true }); },
  async putSecret(name, value) { await api.secrets.put(name, value); await get().loadSecrets(); },
  async forgetSecret(name) { await api.secrets.remove(name); await get().loadSecrets(); },
});
