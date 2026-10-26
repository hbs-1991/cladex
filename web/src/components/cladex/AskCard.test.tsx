import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AskCard } from "./AskCard";

it("shows the question with one button per option and reports the pick once", async () => {
  const addResult = vi.fn();
  render(<AskCard args={{ question: "Which?", options: ["A", "B"] }} result={undefined} addResult={addResult} />);
  expect(screen.getByText("Which?")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "B" }));
  expect(addResult).toHaveBeenCalledWith({ answer: "B" });
  expect(screen.getByRole("button", { name: "B" })).toBeDisabled();
});

it("renders the settled answer", () => {
  render(<AskCard args={{ question: "Which?", options: ["A", "B"] }} result={{ answer: "A", source: "user" }} addResult={vi.fn()} />);
  expect(screen.getByText(/Chosen: A/)).toBeInTheDocument();
});

it("renders a timeout", () => {
  render(<AskCard args={{ question: "Which?", options: ["A"] }} result={{ answer: null, source: "timeout" }} addResult={vi.fn()} />);
  expect(screen.getByText(/Time to answer ran out/)).toBeInTheDocument();
});
