import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { secrets: { list: vi.fn(), put: vi.fn(), remove: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

import { ApiError, api } from "@/api/client";
import { useStore } from "@/store";
import { SecretsSection } from "./SecretsSection";

const list = vi.mocked(api.secrets.list);
const put = vi.mocked(api.secrets.put);
const remove = vi.mocked(api.secrets.remove);

const STORED = { name: "OPENAI_API_KEY", set: true, updated_at: "2026-09-14T10:00:00+00:00", used_by: [] };
const DECLARED = { name: "FATSECRET_KEY", set: false, updated_at: null, used_by: ["FatSecret"] };

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.getState().setLocale("en");
  list.mockReset().mockResolvedValue({ secrets: [DECLARED, STORED] });
  put.mockReset().mockResolvedValue(undefined);
  remove.mockReset().mockResolvedValue(undefined);
});

it("lists stored and declared-but-unset names, and offers delete only for a stored one", async () => {
  render(<SecretsSection />);
  const stored = await screen.findByText("OPENAI_API_KEY");
  expect(screen.getByText("FATSECRET_KEY")).toBeInTheDocument();
  expect(screen.getByText(/FatSecret.*not set/)).toBeInTheDocument();
  // One delete button on the page: the declared name has nothing to forget.
  expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(1);
  expect(within(stored.closest("li")!).getByRole("button", { name: "Delete" })).toBeInTheDocument();
});

it("adds a secret and re-reads the list", async () => {
  render(<SecretsSection />);
  await userEvent.click(await screen.findByRole("button", { name: /Add secret/ }));
  await userEvent.type(screen.getByLabelText("Name"), "lower");
  await userEvent.type(screen.getByLabelText("Value"), "sk-1");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();   // not a variable name
  await userEvent.clear(screen.getByLabelText("Name"));
  await userEvent.type(screen.getByLabelText("Name"), "NEW_TOKEN");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(put).toHaveBeenCalledWith("NEW_TOKEN", "sk-1");
  expect(list).toHaveBeenCalledTimes(2);
});

it("forgets a secret after the confirmation", async () => {
  render(<SecretsSection />);
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(remove).toHaveBeenCalledWith("OPENAI_API_KEY");
});

it("says why the list is empty when the server refuses", async () => {
  list.mockRejectedValue(new ApiError(403, "forbidden", "owner only"));
  render(<SecretsSection />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Only the owner can manage secrets.");
});
