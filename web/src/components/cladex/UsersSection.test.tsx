import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { users: { list: vi.fn(), invite: vi.fn(), disable: vi.fn(), enable: vi.fn(), setGrants: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

import { ApiError, api } from "@/api/client";
import type { MemberWire } from "@/api/types";
import { useStore } from "@/store";
import { toast } from "sonner";
import { UsersSection } from "./UsersSection";

const list = vi.mocked(api.users.list);
const invite = vi.mocked(api.users.invite);
const disable = vi.mocked(api.users.disable);
const setGrants = vi.mocked(api.users.setGrants);

const owner: MemberWire = {
  user_id: 1, role: "owner", display_name: "Вера", username: "vera",
  created_at: "2026-09-01T09:00:00+00:00", last_seen_at: "2026-09-21T09:00:00+00:00",
  disabled: false, source: "row",
  shell: true, learning: true, shell_inherited: false, learning_inherited: false,
};
// No shell, following the deploy default; learning chosen for them explicitly.
const mate: MemberWire = {
  ...owner, user_id: 2, role: "user", display_name: "", username: "mate",
  shell: false, shell_inherited: true, learning: true, learning_inherited: false,
};
const fromConfig: MemberWire = {
  user_id: 777, role: "user", display_name: "", username: "", created_at: "",
  last_seen_at: "", disabled: false, source: "config",
  shell: false, learning: false, shell_inherited: true, learning_inherited: true,
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.getState().setLocale("en");
  list.mockReset().mockResolvedValue({ users: [owner, mate, fromConfig] });
  invite.mockReset().mockResolvedValue(owner);
  disable.mockReset().mockResolvedValue({ ...mate, disabled: true });
  setGrants.mockReset().mockResolvedValue(mate);
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

it("names everyone, marks the owner and says where a config-only id came from", async () => {
  render(<UsersSection />);
  const ownerRow = (await screen.findByText("Вера")).closest("li")!;
  expect(within(ownerRow).getByText("Owner")).toBeInTheDocument();
  // The owner has no revoke control: it would be a door that locks from the inside.
  expect(within(ownerRow).queryByRole("button")).not.toBeInTheDocument();
  expect(screen.getByText("@mate")).toBeInTheDocument();
  expect(screen.getByText(/from allowed_users/)).toBeInTheDocument();
});

it("invites a numeric id and refuses anything else", async () => {
  render(<UsersSection />);
  await userEvent.click(await screen.findByRole("button", { name: /Add user/ }));
  const field = screen.getByLabelText("Telegram user id");
  await userEvent.type(field, "@someone");
  expect(screen.getByRole("button", { name: "Add user" })).toBeDisabled();
  await userEvent.clear(field);
  await userEvent.type(field, "4242");
  await userEvent.click(screen.getByRole("button", { name: "Add user" }));
  expect(invite).toHaveBeenCalledWith(4242);
  expect(list).toHaveBeenCalledTimes(2);
});

it("revokes access after the confirmation", async () => {
  render(<UsersSection />);
  const row = (await screen.findByText("@mate")).closest("li")!;
  await userEvent.click(within(row).getByRole("button", { name: "Revoke access" }));
  await userEvent.click(await screen.findByRole("button", { name: "Revoke access", hidden: false }));
  expect(disable).toHaveBeenCalledWith(2);
});

it("says why the list is empty when the server refuses", async () => {
  list.mockRejectedValue(new ApiError(403, "forbidden", "owner only"));
  render(<UsersSection />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Only the owner can manage users.");
});

it("shows each grant as the server resolved it, and marks the ones that follow the default", async () => {
  render(<UsersSection />);
  const row = (await screen.findByText("@mate")).closest("li")!;
  const bash = within(row).getByRole("switch", { name: "Bash for @mate" });
  const learning = within(row).getByRole("switch", { name: "Learning for @mate" });
  expect(bash).not.toBeChecked();
  expect(learning).toBeChecked();
  // One marker, on the grant that has no choice of its own — and it is what the
  // switch's description says, not only what the eye sees.
  expect(within(row).getAllByText("default")).toHaveLength(1);
  expect(bash).toHaveAccessibleDescription(/default/);
  expect(learning).not.toHaveAccessibleDescription(/default/);
  // The permanent group-chat caveat sits under the list.
  expect(screen.getByText(/not a boundary for someone who can reach the bot in a group/)).toBeInTheDocument();
});

it("keeps a config-only id's switches disabled until their first message", async () => {
  render(<UsersSection />);
  const row = (await screen.findByText(/from allowed_users/)).closest("li")!;
  expect(within(row).getByRole("switch", { name: "Bash for 777" })).toBeDisabled();
  expect(within(row).getByRole("switch", { name: "Learning for 777" })).toBeDisabled();
  expect(within(row).getByText("can be set after their first message")).toBeInTheDocument();
});

it("gives nobody a switch on the owner's row", async () => {
  render(<UsersSection />);
  const ownerRow = (await screen.findByText("Вера")).closest("li")!;
  expect(within(ownerRow).queryByRole("switch")).not.toBeInTheDocument();
});

it("asks before giving someone a shell, and only then writes it", async () => {
  render(<UsersSection />);
  const row = (await screen.findByText("@mate")).closest("li")!;
  await userEvent.click(within(row).getByRole("switch", { name: "Bash for @mate" }));
  const confirm = await screen.findByRole("dialog", { name: "Enable Bash for @mate?" });
  expect(confirm).toHaveTextContent(/every user's files and credentials/);
  expect(confirm).toHaveTextContent(/secrets\.env/);
  expect(confirm).toHaveTextContent(/open conversations restart/);
  expect(confirm).toHaveTextContent(/routines get the shell too/);
  expect(setGrants).not.toHaveBeenCalled();
  await userEvent.click(within(confirm).getByRole("button", { name: "Enable Bash" }));
  expect(setGrants).toHaveBeenCalledWith(2, { shell: true });
  await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("@mate now has Bash"));
  expect(list).toHaveBeenCalledTimes(2);
});

it("cancelling the shell confirmation writes nothing", async () => {
  render(<UsersSection />);
  const row = (await screen.findByText("@mate")).closest("li")!;
  await userEvent.click(within(row).getByRole("switch", { name: "Bash for @mate" }));
  const confirm = await screen.findByRole("dialog", { name: "Enable Bash for @mate?" });
  await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
  expect(setGrants).not.toHaveBeenCalled();
});

it("turns learning off at once, with no confirmation", async () => {
  render(<UsersSection />);
  const row = (await screen.findByText("@mate")).closest("li")!;
  await userEvent.click(within(row).getByRole("switch", { name: "Learning for @mate" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(setGrants).toHaveBeenCalledWith(2, { learning: false });
  await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Learning is off for @mate"));
});

it("says why a grant was refused", async () => {
  setGrants.mockRejectedValue(new ApiError(404, "not_found", "no such user"));
  render(<UsersSection />);
  const row = (await screen.findByText("@mate")).closest("li")!;
  await userEvent.click(within(row).getByRole("switch", { name: "Learning for @mate" }));
  await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith(
    "Nobody by that id has been here, and they are not in allowed_users."));
});
