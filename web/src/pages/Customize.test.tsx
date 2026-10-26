import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { expect, it } from "vitest";
import { Customize } from "./Customize";

it("shows three tabs and marks the current one", () => {
  render(
    <MemoryRouter initialEntries={["/customize/plugins"]}>
      <Routes><Route path="/customize" element={<Customize />}><Route path="plugins" element={<p>the plugins tab</p>} /></Route></Routes>
    </MemoryRouter>,
  );
  expect(screen.getByRole("heading", { name: "Customize" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Connectors" })).toHaveAttribute("href", "/customize/connectors");
  expect(screen.getByRole("link", { name: "Skills" })).toHaveAttribute("href", "/customize/skills");
  expect(screen.getByRole("link", { name: "Plugins" })).toHaveAttribute("aria-current", "page");
  // The tab renders whatever the route gives it: there is no placeholder left.
  expect(screen.getByText("the plugins tab")).toBeInTheDocument();
  expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
});
