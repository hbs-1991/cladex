import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { useStore } from "@/store";
import { FileCard } from "./FileCard";

const file = (media_type: string, name: string) =>
  ({ file_id: "f1", name, media_type, size: 2048, url: "/api/files/f1", telegram_sent: false });

it("shows a delivered picture in the thread", () => {
  render(<FileCard args={file("image/png", "cat.png")} />);
  const img = screen.getByRole("img", { name: "cat.png" });
  expect(img).toHaveAttribute("src", "/api/files/f1");
  expect(screen.getByText("cat.png")).toBeInTheDocument();
});

it("keeps every other file a card that opens the panel", async () => {
  render(<FileCard args={file("text/markdown", "report.md")} />);
  expect(screen.queryByRole("img")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: /report\.md/ }));
  expect(useStore.getState().openFile?.name).toBe("report.md");
});

it("labels a file by kind and offers a download beside it", () => {
  render(<FileCard args={file("text/markdown", "report.md")} />);
  expect(screen.getByText("Document · MD")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Download report.md" })).toHaveAttribute("href", "/api/files/f1");
});
