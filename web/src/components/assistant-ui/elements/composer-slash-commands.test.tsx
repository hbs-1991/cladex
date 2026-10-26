import { render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  ComposerCommandItem, ComposerMenu, matchSlash, slashQuery, useSlashMatches, type ComposerCommand,
} from "./composer-slash-commands";

const commands: ComposerCommand[] = [
  { name: "compact", description: "Compact the context" },
  { name: "context" },
  { name: "wiki-ingest", description: "File a source" },
];

it("matches the start of each name, case-insensitively, only while a command is being typed", () => {
  expect(slashQuery("/wi")).toBe("wi");
  expect(slashQuery("/")).toBe("");
  expect(slashQuery("hello /co")).toBeNull();
  expect(slashQuery("/compact now")).toBeNull();
  expect(matchSlash("", commands)).toEqual([]);
  expect(matchSlash("/", commands)).toHaveLength(3);
  expect(matchSlash("/Co", commands).map((c) => c.name)).toEqual(["compact", "context"]);
  expect(matchSlash("/compact now", commands)).toEqual([]);
  expect(matchSlash("/w", undefined)).toEqual([]);
});

it("useSlashMatches follows the draft", () => {
  const { result, rerender } = renderHook(({ v }) => useSlashMatches(v, commands), { initialProps: { v: "/w" } });
  expect(result.current.map((c) => c.name)).toEqual(["wiki-ingest"]);
  rerender({ v: "/x" });
  expect(result.current).toEqual([]);
});

it("renders the rows with name, description and the hint on the active one; a click reports the row", async () => {
  const onClick = vi.fn();
  render(
    <ComposerMenu open aria-label="Commands">
      <ComposerCommandItem command={commands[0]!} active onClick={onClick} />
      <ComposerCommandItem command={commands[1]!} active={false} />
    </ComposerMenu>,
  );
  expect(screen.getByRole("listbox", { name: "Commands" })).toHaveAttribute("data-open", "true");
  expect(screen.getByText("/compact")).toBeInTheDocument();
  expect(screen.getByText("Compact the context")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /^\/compact/ })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("option", { name: "/context" })).not.toHaveAttribute("data-active");
  expect(screen.getByText("↵")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("option", { name: /^\/compact/ }));
  expect(onClick).toHaveBeenCalledTimes(1);
});

it("closed, the menu holds no rows", () => {
  const { container } = render(
    <ComposerMenu open={false}>
      <ComposerCommandItem command={commands[0]!} active={false} />
    </ComposerMenu>,
  );
  const menu = container.querySelector('[data-slot="composer-menu"]')!;
  expect(menu).not.toHaveAttribute("data-open");
  expect(menu).toHaveAttribute("aria-hidden", "true");
  expect(menu.querySelector('[data-slot="composer-menu-item"]')).toBeNull();
});
