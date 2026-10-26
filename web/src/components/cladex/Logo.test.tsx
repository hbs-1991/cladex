import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { Logo, Mark } from "./Logo";

test("the wordmark uses a prefiltered alpha mask that follows the theme", () => {
  const { container } = render(<Logo width={144} />);
  const logo = screen.getByRole("img", { name: "CladeX" });
  expect(logo).toHaveStyle({ width: "144px", height: "38px" });
  expect(container.querySelector("svg")).not.toBeInTheDocument();
  const wordmark = container.querySelector<HTMLElement>(".bg-current")!;
  expect(wordmark).toBeInTheDocument();
  expect(wordmark).toHaveClass("bg-current");
  // jsdom drops image-set() from mask-image; inspect React's emitted CSS and
  // verify the actual mask rendering in the browser.
  const markup = renderToStaticMarkup(<Logo width={144} />);
  expect(markup).toContain("mask-image:image-set(");
  expect(markup).toContain("cladex-wordmark-144.png");
  expect(markup).toContain("cladex-wordmark-288.png");
  expect(markup).toContain("cladex-wordmark-432.png");
  expect(markup).toContain("3x");
  const mark = container.querySelector("img")!;
  expect(mark).toHaveAttribute("alt", "");
  expect(mark).toHaveAttribute("sizes", "49px");
  expect(mark.getAttribute("srcset")).toContain(" 98w");
});

test("login and empty-thread marks request their own display sizes", () => {
  const { container } = render(<><Logo width={240} /><Mark width={64} /></>);
  expect(screen.getByRole("img", { name: "CladeX" })).toHaveStyle({ width: "240px", height: "64px" });
  const markup = renderToStaticMarkup(<Logo width={240} />);
  expect(markup).toContain("cladex-wordmark-240.png");
  expect(markup).toContain("cladex-wordmark-480.png");
  expect(markup).toContain("cladex-wordmark-720.png");
  const marks = container.querySelectorAll("img");
  expect(marks).toHaveLength(2);
  expect(marks[0]).toHaveAttribute("sizes", "82px");
  expect(marks[1]).toHaveAttribute("sizes", "64px");
  expect(marks[0].getAttribute("srcset")).toContain(" 164w");
  expect(marks[1].getAttribute("srcset")).toContain(" 128w");
});

test("the mark alone is decorative", () => {
  render(<Mark />);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("each complete logo has one accessible name without duplicate title ids", () => {
  const { container } = render(<><Logo /><Logo /></>);
  expect(screen.getAllByRole("img", { name: "CladeX" })).toHaveLength(2);
  expect(container.querySelectorAll("[id]")).toHaveLength(0);
});
