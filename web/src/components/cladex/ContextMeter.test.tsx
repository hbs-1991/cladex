import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ContextUsageContext } from "@/runtime/context-usage";
import { ContextMeter } from "./ContextMeter";

const figure = { model: "m", window: 200_000, used: 65_600, input: 600, cacheRead: 60_000, cacheCreation: 5_000 };

it("renders nothing while the window's fill is unknown", () => {
  const { container } = render(
    <ContextUsageContext.Provider value={null}><ContextMeter /></ContextUsageContext.Provider>,
  );
  expect(container).toBeEmptyDOMElement();
});

it("shows the share of the window on the rail, and the tokens behind it", () => {
  render(<ContextUsageContext.Provider value={figure}><ContextMeter /></ContextUsageContext.Provider>);
  expect(screen.getByRole("button", { name: "Context window usage" })).toHaveTextContent("32.8%");
  expect(screen.getByText("Cached").parentElement).toHaveTextContent("60k");
  expect(screen.getByText("New input").parentElement).toHaveTextContent("600");
  expect(screen.getByText("Total").parentElement).toHaveTextContent("65.6k / 200k");
});
