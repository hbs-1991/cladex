/** Store slice: who may talk to this agent, as Settings → Users reads them.
 *
 * Every action re-reads the list rather than patching a row: inviting an id that
 * was only in `allowed_users` turns a `config` entry into a `row` one, and
 * disabling such an id creates a row that was not there a moment ago — neither is
 * a field update on what the client already holds. A grant write answers with the
 * member and could be patched in, but it follows the same rule: the switches then
 * have one way of becoming current, the same one every other write uses, and the
 * `*_inherited` markers are whatever the server resolved rather than a local guess.
 */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { MemberGrants, MemberWire } from "@/api/types";

export type MembersSlice = {
  members: MemberWire[]; membersLoaded: boolean;
  loadMembers: () => Promise<void>;
  inviteMember: (userId: number) => Promise<void>;
  setMemberDisabled: (userId: number, disabled: boolean) => Promise<void>;
  setMemberGrants: (userId: number, grants: MemberGrants) => Promise<void>;
};

export const createMembersSlice: StateCreator<MembersSlice, [], [], MembersSlice> = (set, get) => ({
  members: [], membersLoaded: false,
  async loadMembers() { set({ members: (await api.users.list()).users, membersLoaded: true }); },
  async inviteMember(userId) { await api.users.invite(userId); await get().loadMembers(); },
  async setMemberDisabled(userId, disabled) {
    await (disabled ? api.users.disable(userId) : api.users.enable(userId));
    await get().loadMembers();
  },
  async setMemberGrants(userId, grants) {
    await api.users.setGrants(userId, grants);
    await get().loadMembers();
  },
});
