import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { WikiGraphWire } from "@/api/types";
import { useStore } from "@/store";
import { halfExtent, layout } from "@/lib/memoryGraph";
import { MemoryGraph } from "./MemoryGraph";

const page = (path: string, over: Partial<WikiGraphWire["pages"][number]> = {}) => ({
  path, ns: path.includes("/") ? path.split("/")[0] : "", title: path.split("/").pop() ?? path,
  summary: "", tags: [], updated: "", links: [], in_index: true, ...over,
});

// `content` is the point: it is not one of WikiStore._NAMESPACES, the agent made
// the folder, and the graph has to draw a hub for it anyway.
const graph: WikiGraphWire = {
  pages: [
    page("user/nutrition", { title: "Nutrition targets", links: ["user/biometrics", "topics/gone"] }),
    page("user/biometrics", { title: "Biometrics" }),
    page("content/linkedin", { title: "LinkedIn voice", in_index: false }),
  ],
  namespaces: ["content", "user"],
  missing: ["topics/gone"],
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  localStorage.clear();
  // jsdom lays nothing out; a drag needs a box to map pixels into graph units.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 1000, height: 1000, right: 1000, bottom: 1000, x: 0, y: 0, toJSON: () => ({}) } as DOMRect,
  );
});
afterEach(() => { vi.restoreAllMocks(); });

const translate = (el: Element) => {
  const m = /translate\(([-\d.e]+) ([-\d.e]+)\)/.exec(el.getAttribute("transform") ?? "");
  return m ? [Number(m[1]), Number(m[2])] : null;
};
const dragBy = (el: Element, dx: number, dy: number) => {
  const svg = screen.getByRole("img");
  fireEvent.pointerDown(el, { clientX: 500, clientY: 500, button: 0, pointerId: 1 });
  fireEvent.pointerMove(svg, { clientX: 500 + dx / 2, clientY: 500 + dy / 2, pointerId: 1 });
  fireEvent.pointerMove(svg, { clientX: 500 + dx, clientY: 500 + dy, pointerId: 1 });
  fireEvent.pointerUp(svg, { clientX: 500 + dx, clientY: 500 + dy, pointerId: 1 });
};

it("draws one hub per namespace, including one the seed never created", () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const hubs = screen.getAllByTestId("wiki-hub");
  expect(hubs).toHaveLength(2);
  expect(hubs.map((h) => h.querySelector("title")?.textContent)).toEqual(["content", "user"]);
});

it("puts the CladeX mark at the centre", () => {
  const { container } = render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const centre = screen.getByTestId("wiki-centre");
  expect(centre.querySelector("image")?.getAttribute("href")).toMatch(/cladex-mark/);
  expect(container.querySelectorAll("image")).toHaveLength(1);
});

it("draws every page as its own node", () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  expect(screen.getAllByTestId("wiki-page")).toHaveLength(3);
  expect(screen.getByRole("button", { name: "Nutrition targets" })).toBeInTheDocument();
});

it("calls onSelect with the page path when a node is clicked", async () => {
  const onSelect = vi.fn();
  render(<MemoryGraph graph={graph} onSelect={onSelect} />);
  await userEvent.click(screen.getByRole("button", { name: "Nutrition targets" }));
  expect(onSelect).toHaveBeenCalledWith("user/nutrition");
});

it("renders a ghost for a wikilink with no page behind it", () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const ghosts = screen.getAllByTestId("wiki-ghost");
  expect(ghosts).toHaveLength(1);
  expect(ghosts[0].querySelector("title")?.textContent).toContain("topics/gone");
  // The edge that points at it is dashed; every other edge is solid.
  const dashed = layout(graph).edges.filter((e) => e.dashed);
  expect(dashed.map((e) => [e.a, e.b])).toEqual([["user/nutrition", "topics/gone"]]);
});

it("flags a page the agent wrote but never listed in index.md", () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const node = screen.getByRole("button", { name: "LinkedIn voice" });
  expect(node.textContent).toContain("Written but not listed in index.md");
});

it("lays the same wiki out the same way twice", () => {
  const a = layout(graph), b = layout(graph);
  expect(a.nodes).toEqual(b.nodes);
  // Centre -> hub for each namespace, hub -> page for each page, plus the links.
  expect(a.edges.filter((e) => e.a === "centre")).toHaveLength(2);
  expect(a.edges.filter((e) => e.kind === "link")).toHaveLength(2);
});

// The shape of a real wiki that used to draw as a pile: two namespaces holding
// most of the pages, a few holding one or two.
const crowded: WikiGraphWire = {
  pages: [
    ...Array.from({ length: 22 }, (_, i) => page(`env/note-${i}`, { title: `Environment note number ${i}` })),
    ...Array.from({ length: 20 }, (_, i) => page(`projects/p-${i}`, { title: `Project page ${i} with a long title` })),
    ...Array.from({ length: 10 }, (_, i) => page(`user/u-${i}`, { title: `User fact ${i}` })),
    ...Array.from({ length: 5 }, (_, i) => page(`topics/t-${i}`, { title: `Topic ${i}` })),
    page("content/linkedin"), page("shared/x"), page("raw/a"), page("raw/b"), page("index"),
  ],
  namespaces: ["", "content", "env", "projects", "raw", "shared", "topics", "user"],
  missing: [],
};

it("lays out a crowded wiki without any two nodes overlapping", () => {
  const { nodes } = layout(crowded);
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const [aw, ah] = halfExtent(a), [bw, bh] = halfExtent(b);
      const apart = Math.abs(a.x - b.x) >= aw + bw || Math.abs(a.y - b.y) >= ah + bh;
      expect(apart, `${a.id} overlaps ${b.id}`).toBe(true);
    }
  }
});

it("moves a dragged page, keeps it there, and does not open it", () => {
  const onSelect = vi.fn();
  const { unmount } = render(<MemoryGraph graph={graph} onSelect={onSelect} />);
  const node = screen.getByRole("button", { name: "Nutrition targets" });
  const before = translate(node)!;
  dragBy(node, 100, 40);
  fireEvent.click(node);
  const after = translate(node)!;
  expect(after[0]).toBeGreaterThan(before[0]);
  expect(after[1]).toBeGreaterThan(before[1]);
  expect(onSelect).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem("cladex.memory.moved")!)).toHaveProperty(["user/nutrition"]);

  // A remount (a reload, a return to the page) finds the node where it was left.
  unmount();
  render(<MemoryGraph graph={graph} onSelect={onSelect} />);
  expect(translate(screen.getByRole("button", { name: "Nutrition targets" }))).toEqual(after);
});

it("drags a hub together with its pages", () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const hub = screen.getAllByTestId("wiki-hub").find((h) => h.querySelector("title")?.textContent === "user")!;
  const pages = ["Nutrition targets", "Biometrics"].map((name) => screen.getByRole("button", { name }));
  const other = screen.getByRole("button", { name: "LinkedIn voice" });
  const start = [hub, ...pages, other].map((el) => translate(el)!);
  dragBy(hub, 80, 0);
  const end = [hub, ...pages, other].map((el) => translate(el)!);
  const shift = end[0][0] - start[0][0];
  expect(shift).toBeGreaterThan(0);
  expect(end[1][0] - start[1][0]).toBeCloseTo(shift);
  expect(end[2][0] - start[2][0]).toBeCloseTo(shift);
  expect(end[3]).toEqual(start[3]);   // another namespace's page stays put
});

it("puts moved nodes back where the layout had them", async () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Put the nodes back" })).toBeNull();
  const node = screen.getByRole("button", { name: "Biometrics" });
  const before = translate(node);
  dragBy(node, -60, 0);
  expect(translate(node)).not.toEqual(before);
  await userEvent.click(screen.getByRole("button", { name: "Put the nodes back" }));
  expect(translate(node)).toEqual(before);
  expect(localStorage.getItem("cladex.memory.moved")).toBeNull();
});

it("a click without travel still opens the page", () => {
  const onSelect = vi.fn();
  render(<MemoryGraph graph={graph} onSelect={onSelect} />);
  const node = screen.getByRole("button", { name: "Biometrics" });
  const before = translate(node);
  fireEvent.pointerDown(node, { clientX: 10, clientY: 10, button: 0 });
  fireEvent.pointerUp(screen.getByRole("img"), { clientX: 11, clientY: 11 });
  fireEvent.click(node);
  expect(onSelect).toHaveBeenCalledWith("user/biometrics");
  expect(translate(node)).toEqual(before);
});

const viewBox = () => screen.getByRole("img").getAttribute("viewBox")!.split(" ").map(Number);

it("zooms in and out with the buttons, about the middle of the screen", async () => {
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const [x, y, w, h] = viewBox();
  await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  const [x1, y1, w1, h1] = viewBox();
  expect(w1).toBeLessThan(w);
  expect(x1 + w1 / 2).toBeCloseTo(x + w / 2);
  expect(y1 + h1 / 2).toBeCloseTo(y + h / 2);
  await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
  expect(viewBox()[2]).toBeCloseTo(w);
});

it("pinches to zoom and does not open or move the page under a finger", () => {
  const onSelect = vi.fn();
  render(<MemoryGraph graph={graph} onSelect={onSelect} />);
  const svg = screen.getByRole("img");
  const node = screen.getByRole("button", { name: "Biometrics" });
  const before = translate(node);
  const w0 = viewBox()[2];
  fireEvent.pointerDown(node, { clientX: 450, clientY: 500, button: 0, pointerId: 1 });
  fireEvent.pointerDown(svg, { clientX: 550, clientY: 500, button: 0, pointerId: 2 });
  fireEvent.pointerMove(svg, { clientX: 400, clientY: 500, pointerId: 1 });
  fireEvent.pointerMove(svg, { clientX: 600, clientY: 500, pointerId: 2 });
  expect(viewBox()[2]).toBeCloseTo(w0 / 2);        // fingers twice as far apart: twice as close
  fireEvent.pointerUp(svg, { clientX: 600, clientY: 500, pointerId: 2 });
  fireEvent.pointerMove(svg, { clientX: 300, clientY: 300, pointerId: 1 });   // the finger left behind does not pan
  expect(viewBox()[2]).toBeCloseTo(w0 / 2);
  fireEvent.pointerUp(svg, { clientX: 300, clientY: 300, pointerId: 1 });
  fireEvent.click(node);
  expect(onSelect).not.toHaveBeenCalled();
  expect(translate(node)).toEqual(before);
});

it("on a phone draws bigger type and opens zoomed in around the centre", () => {
  const desktop = render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const wide = viewBox()[2];
  desktop.unmount();
  vi.spyOn(window, "matchMedia").mockImplementation((q: string) => ({
    matches: q.includes("max-width"), media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList);
  vi.spyOn(window, "innerWidth", "get").mockReturnValue(390);
  render(<MemoryGraph graph={graph} onSelect={vi.fn()} />);
  const [x, , w] = viewBox();
  expect(w).toBe(390);
  expect(x).toBe(-195);
  expect(w).toBeLessThan(wide);
  expect(screen.getByRole("button", { name: "Biometrics" }).getAttribute("transform")).toContain("scale(1.3)");
});
