import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FilePreview } from "./FilePreview";

afterEach(() => vi.restoreAllMocks());
const file = (media_type: string, name = "f") => ({ file_id: "f1", name, media_type, size: 3, url: "/api/files/f1" });

it("renders markdown text fetched from the file url", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("# Заголовок", { status: 200 }));
  render(<FilePreview file={file("text/markdown", "r.md")} />);
  expect(screen.getByText("Loading…")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Заголовок" })).toBeInTheDocument();
});

it("shows a text file as code, under the name's language", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("x = 1", { status: 200 }));
  const { container } = render(<FilePreview file={file("text/plain", "calc.py")} />);
  expect(await screen.findByText(/x = 1/)).toBeInTheDocument();
  // Shiki tokenizes asynchronously; the plain code is in the same container either way.
  expect(container.querySelector(".aui-shiki-base pre")).not.toBeNull();
});

it("renders images with an img tag and pdfs in an iframe", () => {
  const { rerender } = render(<FilePreview file={file("image/png", "a.png")} />);
  expect(screen.getByRole("img")).toHaveAttribute("src", "/api/files/f1");
  rerender(<FilePreview file={file("application/pdf", "a.pdf")} />);
  expect(screen.getByTitle("a.pdf")).toHaveAttribute("src", "/api/files/f1");
});

it("offers a download for other types", () => {
  render(<FilePreview file={file("application/zip", "a.zip")} />);
  expect(screen.getByText("No preview for this file type.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", "/api/files/f1");
});

it("shows a markdown file's source when asked, frontmatter included", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("---\ntitle: x\n---\n# Заголовок", { status: 200 }));
  const { container } = render(<FilePreview file={file("text/markdown", "r.md")} view="source" />);
  expect(await screen.findByText(/# Заголовок/)).toBeInTheDocument();
  expect(screen.queryByRole("heading")).toBeNull();
  expect(container.textContent).toContain("title: x");
});

it("wraps a wide markdown table in its own scroller and drops the frontmatter", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("---\ntitle: x\n---\n| a | b |\n|---|---|\n| 1 | 2 |", { status: 200 }));
  const { container } = render(<FilePreview file={file("text/markdown", "r.md")} />);
  expect(await screen.findByRole("table")).toBeInTheDocument();
  expect(container.querySelector("[data-slot=file-md-table] > table")).not.toBeNull();
  expect(container.textContent).not.toContain("title: x");
});
