import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import type { ContentItemWire } from "@/api/types";
import { useStore } from "@/store";
import { ContentItemDialog } from "./ContentItemDialog";

vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

const item = (over: Partial<ContentItemWire>): ContentItemWire => ({
  id: 5, channel: "instagram", format: "reel", title: "Launch reel", body: "Our new thing.",
  media: [{ path: "content/img/cover.jpg", sha256: null }, { path: "content/video/launch.mp4", sha256: null }],
  publish_at: new Date(2026, 9, 1, 10).toISOString(), status: "draft", group_key: null,
  source_path: "content/briefs/launch.md", thread_id: "web:1:abc", created_via: "chat", approved_at: null, attempts: 0,
  published_url: null, error: null, needs_attention: false, created_at: "", updated_at: "",
  theme_approved_at: null, draft_attempts: 0, redrafts: 0, draft_comment: null, card_state: "none", ...over,
});

const actions = {
  createItem: vi.fn(), updateItem: vi.fn(), approveItem: vi.fn(), unapproveItem: vi.fn(), retryItem: vi.fn(), removeItem: vi.fn(),
  approveTheme: vi.fn(), unapproveTheme: vi.fn(), retryDrafting: vi.fn(),
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  for (const f of Object.values(actions)) { f.mockReset(); f.mockResolvedValue(undefined); }
  useStore.setState(actions);
});

function open(it: ContentItemWire | null, publishAt: string | null = null) {
  const onOpenChange = vi.fn();
  render(<MemoryRouter><ContentItemDialog open item={it} publishAt={publishAt} onOpenChange={onOpenChange} /></MemoryRouter>);
  return { dialog: screen.getByRole("dialog"), onOpenChange };
}

it("creates a post from the form, with the local time sent as UTC and one media path per line", async () => {
  const { dialog, onOpenChange } = open(null);
  expect(within(dialog).getByRole("heading", { name: "New post" })).toBeInTheDocument();
  await userEvent.type(within(dialog).getByLabelText("Title"), "Autumn launch");
  await userEvent.type(within(dialog).getByLabelText("Text"), "It is here.");
  await userEvent.type(within(dialog).getByLabelText("Media"), "content/img/a.jpg{Enter}  {Enter}content/img/b.jpg");
  fireEvent.change(within(dialog).getByLabelText("Publish at"), { target: { value: "2026-10-02T09:30" } });
  await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
  expect(actions.createItem).toHaveBeenCalledWith({
    channel: "linkedin", format: "post", title: "Autumn launch", body: "It is here.",
    media: ["content/img/a.jpg", "content/img/b.jpg"], publish_at: new Date(2026, 9, 2, 9, 30).toISOString(), status: "draft",
  });
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});

it("offers the formats of the chosen channel", async () => {
  const { dialog } = open(null);
  await userEvent.click(within(dialog).getByRole("combobox", { name: "Channel" }));
  await userEvent.click(await screen.findByRole("option", { name: /YouTube/ }));
  expect(within(dialog).getByRole("combobox", { name: "Format" })).toHaveTextContent("Video");
  await userEvent.click(within(dialog).getByRole("combobox", { name: "Format" }));
  expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual(["Video", "Short"]);
});

it("saves only what changed, and saves pending edits before approving", async () => {
  const { dialog } = open(item({}));
  const save = within(dialog).getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();                               // nothing to save yet
  await userEvent.clear(within(dialog).getByLabelText("Text"));
  await userEvent.type(within(dialog).getByLabelText("Text"), "Final text.");
  await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(actions.approveItem).toHaveBeenCalledWith(5));
  expect(actions.updateItem).toHaveBeenCalledWith(5, { body: "Final text." });
  expect(actions.updateItem.mock.invocationCallOrder[0]).toBeLessThan(actions.approveItem.mock.invocationCallOrder[0]!);
});

it("shows the server's refusal as the server words it", async () => {
  actions.approveItem.mockRejectedValue(new ApiError(400, "media_missing", "content/video/launch.mp4 does not exist in the agent's home."));
  const { dialog, onOpenChange } = open(item({}));
  await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("content/video/launch.mp4 does not exist in the agent's home.");
  expect(onOpenChange).not.toHaveBeenCalled();
});

it("previews images and videos from the media route, and links the source and the conversation", () => {
  const { dialog } = open(item({}));
  expect(within(dialog).getByRole("img", { name: "Media 1" })).toHaveAttribute("src", "/api/content/5/media/0");
  expect(dialog.querySelector("video")).toHaveAttribute("src", "/api/content/5/media/1");
  expect(within(dialog).getByText("content/briefs/launch.md")).toBeInTheDocument();
  expect(within(dialog).getByRole("link", { name: "Open the conversation" })).toHaveAttribute("href", "/t/web%3A1%3Aabc");
});

it("withdraws an approval, and offers no second approval", async () => {
  const { dialog } = open(item({ status: "scheduled", approved_at: new Date().toISOString() }));
  expect(within(dialog).queryByRole("button", { name: "Approve" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: "Withdraw approval" }));
  await waitFor(() => expect(actions.unapproveItem).toHaveBeenCalledWith(5));
});

it("locks the form while the publisher holds the post", () => {
  const { dialog } = open(item({ status: "publishing" }));
  expect(within(dialog).getByLabelText("Text")).toBeDisabled();
  expect(within(dialog).queryByRole("button", { name: "Save" })).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "Approve" })).toBeNull();
  expect(within(dialog).getByText(/publisher has this post/)).toBeInTheDocument();
});

it("resolves a failed post only by an explicit choice: re-post, or published with a URL", async () => {
  const { dialog } = open(item({ status: "failed", needs_attention: true, error: "Media ID is not available", attempts: 3 }));
  expect(within(dialog).getByText("Media ID is not available")).toBeInTheDocument();
  expect(within(dialog).getByRole("img", { name: "Needs attention" })).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
  const resolve = screen.getByRole("dialog");
  await userEvent.click(within(resolve).getByLabelText(/It was published/));
  expect(within(resolve).getByRole("button", { name: "Confirm" })).toBeDisabled();   // a URL is required
  await userEvent.type(within(resolve).getByLabelText("Post URL"), "https://instagram.com/p/abc");
  await userEvent.click(within(resolve).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(actions.retryItem).toHaveBeenCalledWith(5, { mode: "published", url: "https://instagram.com/p/abc" }));
});

it("re-posts when asked to", async () => {
  const { dialog } = open(item({ status: "stuck", needs_attention: true }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(actions.retryItem).toHaveBeenCalledWith(5, { mode: "repost" }));
});

it("deletes after a confirmation, and never offers to delete a published post", async () => {
  const { dialog } = open(item({}));
  await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  const confirm = await screen.findByRole("dialog", { name: "Delete" });
  expect(confirm).toHaveTextContent("Delete the post “Launch reel”?");
  await userEvent.click(within(confirm).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(actions.removeItem).toHaveBeenCalledWith(5));
});

it("has no delete for a published post", () => {
  const { dialog } = open(item({ status: "published", published_url: "https://youtu.be/x" }));
  expect(within(dialog).queryByRole("button", { name: "Delete" })).toBeNull();
  expect(within(dialog).getByRole("link", { name: /youtu\.be/ })).toHaveAttribute("href", "https://youtu.be/x");
});

const idea = (over: Partial<ContentItemWire> = {}) =>
  item({ status: "idea", body: "", media: [], format: "carousel", title: "Five hooks", ...over });

it("offers an idea its theme approval, saving pending edits first, and no content approval while nothing is written", async () => {
  const { dialog, onOpenChange } = open(idea());
  expect(within(dialog).queryByRole("button", { name: "Approve" })).toBeNull();
  expect(within(dialog).getByText(/drafter write this post a day before its time/)).toBeInTheDocument();
  await userEvent.type(within(dialog).getByLabelText("Title"), " that work");
  await userEvent.click(within(dialog).getByRole("button", { name: "Approve theme" }));
  await waitFor(() => expect(actions.approveTheme).toHaveBeenCalledWith(5));
  expect(actions.updateItem).toHaveBeenCalledWith(5, { title: "Five hooks that work" });
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});

it("still lets the owner approve an idea they wrote themselves", () => {
  const { dialog } = open(idea({ format: "post", body: "Written by hand." }));
  expect(within(dialog).getByRole("button", { name: "Approve theme" })).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Approve" })).toBeEnabled();
});

it("greys out Approve with the reason when the text is empty or the format's media are missing", async () => {
  const { dialog } = open(item({ status: "review", format: "carousel", media: [{ path: "content/a.png", sha256: null }] }));
  const approve = within(dialog).getByRole("button", { name: "Approve" });
  expect(approve).toBeDisabled();
  expect(approve).toHaveAccessibleDescription("A carousel needs 2 to 10 images.");
  await userEvent.type(within(dialog).getByLabelText("Media"), "{Enter}content/b.png");
  expect(approve).toBeEnabled();
  await userEvent.clear(within(dialog).getByLabelText("Text"));
  expect(approve).toBeDisabled();
  expect(approve).toHaveAccessibleDescription("Write the text first: an empty post cannot be approved.");
});

it("withdraws a theme approval, and retries drafting only after the drafter gave up", async () => {
  const planned = item({ status: "planned", body: "", media: [], theme_approved_at: new Date(2026, 8, 28, 9).toISOString() });
  const first = open(planned);
  expect(within(first.dialog).getByText(/^Theme approved /)).toBeInTheDocument();
  expect(within(first.dialog).queryByRole("button", { name: "Retry drafting" })).toBeNull();
  expect(within(first.dialog).queryByRole("button", { name: "Approve" })).toBeNull();
  await userEvent.click(within(first.dialog).getByRole("button", { name: "Remove theme approval" }));
  await waitFor(() => expect(actions.unapproveTheme).toHaveBeenCalledWith(5));
  cleanup();

  const stuck = open({ ...planned, error: "The drafter never picked this item up.", needs_attention: true, draft_attempts: 2 });
  expect(within(stuck.dialog).getByText("The drafter never picked this item up.")).toBeInTheDocument();
  expect(within(stuck.dialog).getByText("2 failed drafts")).toBeInTheDocument();
  await userEvent.click(within(stuck.dialog).getByRole("button", { name: "Retry drafting" }));
  await waitFor(() => expect(actions.retryDrafting).toHaveBeenCalledWith(5));
});

it("drafts a post again after its drafting failed, instead of re-posting it", async () => {
  const { dialog } = open(item({ status: "failed", body: "", media: [], approved_at: null, error: "No voice tool",
    theme_approved_at: new Date(2026, 8, 28, 9).toISOString(), draft_attempts: 3, needs_attention: true }));
  expect(within(dialog).queryByRole("button", { name: "Retry" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: "Retry drafting" }));
  await waitFor(() => expect(actions.retryDrafting).toHaveBeenCalledWith(5));
});

it("keeps a post read-only while the drafter writes it", () => {
  const { dialog } = open(item({ status: "drafting", body: "" }));
  expect(within(dialog).getByText("Drafting")).toBeInTheDocument();
  expect(within(dialog).getByText(/drafter is writing this post/)).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Text")).toBeDisabled();
  for (const name of ["Save", "Approve", "Delete", "Approve theme"]) {
    expect(within(dialog).queryByRole("button", { name })).toBeNull();
  }
});

it("shows the redraft comment, the redraft count and the Telegram card's state", () => {
  const { dialog } = open(item({ status: "review", redrafts: 1, draft_comment: "Shorter, and lead with the price.", card_state: "superseded" }));
  expect(within(dialog).getByText("Shorter, and lead with the price.")).toBeInTheDocument();
  expect(within(dialog).getByText("1 redraft")).toBeInTheDocument();
  expect(within(dialog).getByText(/Telegram card: out of date/)).toBeInTheDocument();
});

it("says a post whose time passed unapproved was not published", () => {
  const { dialog } = open(item({ status: "review", publish_at: new Date(Date.now() - 3_600_000).toISOString(), needs_attention: true }));
  expect(within(dialog).getByText(/passed before the post was approved/)).toBeInTheDocument();
});
