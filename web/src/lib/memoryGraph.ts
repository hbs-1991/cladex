/** Where every node of the memory graph sits.
 *
 * Arithmetic, not rendering: the layout is a pure function of the wire, so the
 * picture can be reasoned about (and tested) without a DOM, and the same wiki
 * draws the same picture twice — position comes from the index of a namespace in
 * `graph.namespaces` and the index of a page inside it, never from insertion
 * order or the filesystem's mood.
 *
 * Two passes. The first seats everything radially: each namespace gets a slice of
 * the circle proportional to how many pages it holds (so `projects` with twenty
 * pages does not share a sliver with `index` and its one), and a slice too narrow
 * for its pills spills onto further rings. The second pushes apart any two boxes
 * that still overlap, until none do. Both are deterministic, so "the same picture
 * twice" survives — the owner's own drags live on top of this, in the component.
 *
 * It lives beside the component rather than inside it so the component file
 * exports components only (fast refresh) — and so the constants below have one
 * home.
 */

import { Folder, Globe, Hash, Inbox, PenLine, User, type LucideIcon } from "lucide-react";
import type { WikiGraphWire, WikiPageWire } from "@/api/types";

const HUB_R = 170;            // the namespace ring
const PAGE_R = 330;           // the first page ring
const RING_GAP = 44;          // how far out the next ring sits when a slice is full
const GHOST_STEP = 90;        // a link with no page behind it hangs this far past its page
const PILL_H = 22;
/** Label size at type scale 1, in graph units. A phone draws at a larger scale:
 * the whole graph never fits a phone legibly, so there the type grows and the
 * graph opens zoomed in rather than squeezed. */
export const FONT = 10;
const GAP = 8;                // the least air between two boxes once relaxed
const MIN_WEIGHT = 2;         // a one-page namespace still gets room for its hub
const RELAX_ROUNDS = 300;
const LABEL_MAX = 22;

/** The window the graph opens at when there is nothing to fit. */
export const BASE = { x: -520, y: -470, w: 1040, h: 940 };
export type View = typeof BASE;
export type Point = [number, number];

/** The hub glyphs. Anything the agent invented falls through to a folder — the
 * map is a courtesy for the namespaces we know, never a filter on the ones we draw. */
export const ICONS: Record<string, LucideIcon> = {
  user: User, projects: Folder, topics: Hash, env: Globe, content: PenLine, raw: Inbox,
};

export type GraphNode = {
  kind: "hub" | "page" | "ghost";
  id: string;
  x: number; y: number;
  label: string;
  ns: string;
  page?: WikiPageWire;
};
export type GraphEdge = {
  kind: "hub" | "link";
  a: string; b: string;          // node ids ("centre" for the mark); positions are looked up at draw time
  dashed?: boolean;
};

export const CENTRE = "centre";
export const truncate = (s: string) => (s.length > LABEL_MAX ? `${s.slice(0, LABEL_MAX - 1)}…` : s);
export const pillWidth = (label: string, type = 1) => Math.max(54, truncate(label).length * 6.6 + 20) * type;
export const pillHeight = (type = 1) => PILL_H * type;

/** The box a node occupies, as half-extents around its centre. A hub is its tile
 * plus the label under it. */
export function halfExtent(n: Pick<GraphNode, "kind" | "label">, type = 1): Point {
  if (n.kind === "hub") return [Math.max(26, truncate(n.label).length * 3.3 + 6) * type, 34 * type];
  return [pillWidth(n.label, type) / 2, pillHeight(type) / 2];
}
/** The mark and its glow: an obstacle nothing may sit on. */
const CENTRE_HALF: Point = [48, 48];

/** Everything the SVG draws, from the wire. Exported because it is the part worth
 * testing without a DOM: positions are arithmetic, not rendering. */
export function layout(graph: WikiGraphWire, type = 1): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const namespaces = graph.namespaces;
  const at = (angle: number, r: number): Point => [Math.cos(angle) * r, Math.sin(angle) * r];
  const pagesOf = new Map(namespaces.map((ns) => [ns, graph.pages.filter((p) => p.ns === ns)]));

  // Slices proportional to page count, going round from the top; the first slice
  // is centred on the top so a one-namespace wiki still hangs straight down.
  const weights = namespaces.map((ns) => Math.max(MIN_WEIGHT, pagesOf.get(ns)?.length ?? 0));
  const total = weights.reduce((s, w) => s + w, 0) || 1;
  let cursor = -Math.PI / 2 - (Math.PI * (weights[0] ?? 0)) / total;
  const pageAngle = new Map<string, number>();

  namespaces.forEach((ns, i) => {
    const span = (2 * Math.PI * weights[i]) / total;
    const mid = cursor + span / 2;
    cursor += span;
    const [hx, hy] = at(mid, HUB_R);
    const hub: GraphNode = { kind: "hub", id: `ns:${ns}`, x: hx, y: hy, label: ns, ns };
    nodes.push(hub);
    edges.push({ kind: "hub", a: CENTRE, b: hub.id });

    const pages = pagesOf.get(ns) ?? [];
    if (pages.length === 0) return;
    // A little of the slice stays empty on both sides so neighbours do not touch.
    const usable = pages.length > 1 ? span * 0.9 : 0;
    // Along the ring a pill needs its height where the ring runs vertically (the
    // sides) and its width where it runs horizontally (top and bottom).
    const meanW = pages.reduce((s, p) => s + pillWidth(p.title || p.path, type), 0) / pages.length;
    const need = Math.abs(Math.sin(mid)) * (meanW + GAP) + Math.abs(Math.cos(mid)) * (pillHeight(type) + GAP);
    let placed = 0, ring = 0;
    while (placed < pages.length) {
      const r = PAGE_R + ring * RING_GAP * type;
      const room = Math.max(1, Math.floor((usable * r) / need) + 1);
      const here = pages.slice(placed, placed + room);
      here.forEach((p, j) => {
        const t = here.length === 1 ? 0.5 : j / (here.length - 1);
        const angle = usable === 0 ? mid : mid - usable / 2 + usable * t;
        const [x, y] = at(angle, r);
        pageAngle.set(p.path, angle);
        nodes.push({ kind: "page", id: p.path, x, y, label: p.title || p.path, ns, page: p });
        edges.push({ kind: "hub", a: hub.id, b: p.path });
      });
      placed += here.length;
      ring += 1;
    }
  });

  const known = new Set(graph.pages.map((p) => p.path));
  const missing = new Set(graph.missing);
  const ghosts = new Set<string>();
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const p of graph.pages) {
    const from = byId.get(p.path);
    if (!from) continue;
    for (const target of p.links) {
      if (known.has(target) || !missing.has(target) || ghosts.has(target)) continue;
      // A ghost hangs just beyond the page that wanted it, so the gap reads as
      // "this page points at nothing" rather than as a stray node.
      const [x, y] = at((pageAngle.get(p.path) ?? 0) + 0.04, Math.hypot(from.x, from.y) + GHOST_STEP);
      nodes.push({ kind: "ghost", id: target, x, y, label: target, ns: p.ns });
      ghosts.add(target);
    }
  }

  for (const p of graph.pages) {
    if (!byId.has(p.path)) continue;
    for (const target of p.links) {
      if (target === p.path || !(known.has(target) || ghosts.has(target))) continue;
      edges.push({ kind: "link", a: p.path, b: target, dashed: ghosts.has(target) });
    }
  }

  relax(nodes, type);
  return { nodes, edges };
}

/** Push overlapping boxes apart along the axis they overlap least on, half each,
 * until nothing overlaps. The mark at the centre is a fixed obstacle. */
function relax(nodes: GraphNode[], type: number) {
  const half = nodes.map((n) => halfExtent(n, type));
  for (let round = 0; round < RELAX_ROUNDS; round++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      // The centre never moves, so whoever sits on it takes the whole push.
      const cx = CENTRE_HALF[0] + half[i][0] + GAP - Math.abs(a.x);
      const cy = CENTRE_HALF[1] + half[i][1] + GAP - Math.abs(a.y);
      if (cx > 0 && cy > 0) {
        if (cx < cy) a.x += (a.x >= 0 ? 1 : -1) * cx; else a.y += (a.y >= 0 ? 1 : -1) * cy;
        moved = true;
      }
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const ox = half[i][0] + half[j][0] + GAP - Math.abs(dx);
        const oy = half[i][1] + half[j][1] + GAP - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        // Ties (two boxes on one spot) break outward, away from the centre, so the
        // result never depends on which of the two came first.
        if (ox < oy) {
          const s = dx !== 0 ? Math.sign(dx) : Math.sign(a.x + b.x) || 1;
          a.x -= (s * ox) / 2; b.x += (s * ox) / 2;
        } else {
          const s = dy !== 0 ? Math.sign(dy) : Math.sign(a.y + b.y) || 1;
          a.y -= (s * oy) / 2; b.y += (s * oy) / 2;
        }
      }
    }
    if (!moved) return;
  }
}

/** A window around every node with some air: the first paint and the reset. */
export function fitView(nodes: GraphNode[], at: (n: GraphNode) => Point = (n) => [n.x, n.y], type = 1): View {
  if (nodes.length === 0) return BASE;
  let x0 = -CENTRE_HALF[0], y0 = -CENTRE_HALF[1], x1 = CENTRE_HALF[0], y1 = CENTRE_HALF[1];
  for (const n of nodes) {
    const [x, y] = at(n);
    const [hw, hh] = halfExtent(n, type);
    x0 = Math.min(x0, x - hw); x1 = Math.max(x1, x + hw);
    y0 = Math.min(y0, y - hh); y1 = Math.max(y1, y + hh);
  }
  const pad = 40;
  return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };
}

/** The svg keeps the default `preserveAspectRatio="xMidYMid meet"`: the view is
 * scaled to fit and centred, so a unit is the same number of pixels on both axes
 * and one axis has margins. This is that mapping, client pixels to graph units. */
export function clientToGraph(rect: { left: number; top: number; width: number; height: number }, v: View, cx: number, cy: number): Point {
  const scale = Math.min(rect.width / v.w, rect.height / v.h);
  const ox = (rect.width - v.w * scale) / 2, oy = (rect.height - v.h * scale) / 2;
  return [v.x + (cx - rect.left - ox) / scale, v.y + (cy - rect.top - oy) / scale];
}

/** The inverse: a `w`×`h` window that puts graph point `anchor` under client
 * point (`cx`, `cy`). Every zoom is this — the wheel, a pinch, the buttons. */
export function viewAround(rect: { left: number; top: number; width: number; height: number }, w: number, h: number, anchor: Point, cx: number, cy: number): View {
  const scale = Math.min(rect.width / w, rect.height / h);
  const ox = (rect.width - w * scale) / 2, oy = (rect.height - h * scale) / 2;
  return { x: anchor[0] - (cx - rect.left - ox) / scale, y: anchor[1] - (cy - rect.top - oy) / scale, w, h };
}
