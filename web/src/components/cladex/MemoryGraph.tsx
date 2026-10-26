/** The wiki as a brain: a glowing centre, one hub per namespace on an inner ring,
 * every page as a pill further out, thin lines between them.
 *
 * Pure SVG and no new dependency. The starting layout is deterministic (see
 * `lib/memoryGraph.ts`), so the same wiki draws the same picture twice. Nothing
 * here decides *which* namespaces exist: the server scans the directory and this
 * component draws whatever came back, including folders the agent invented.
 *
 * Every node can be dragged. A page moves alone; a hub carries its whole
 * namespace with it. What the owner moved is kept per browser (localStorage, a
 * convenience — the page draws fine without it) as absolute positions by node id,
 * so a refetch or a new page leaves the arrangement alone, and "put the nodes
 * back" drops it.
 *
 * Pan and zoom are a `viewBox`, not a CSS transform, so strokes keep their width
 * and the whole thing stays crisp at any scale.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Folder, LayoutGrid, Maximize, Minus, Plus } from "lucide-react";
import mark64 from "@/assets/cladex-mark-64.png";
import type { WikiGraphWire } from "@/api/types";
import { useT } from "@/i18n";
import {
  CENTRE, ICONS, clientToGraph, fitView, layout, pillWidth, truncate, viewAround,
  type GraphEdge, type GraphNode, type Point, type View,
} from "@/lib/memoryGraph";
import { cn } from "@/lib/utils";

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
/** Pointer travel (px) below which a press is a click, not a drag. */
const DRAG_SLOP = 5;
const MOVED_KEY = "cladex.memory.moved";
/** Below `sm` the graph is a phone's: bigger type, and it opens zoomed in. */
const PHONE = "(max-width: 639px)";
const PHONE_TYPE = 1.3;
/** One press of + or −. */
const ZOOM_STEP = 1.4;
const isPhone = () => { try { return window.matchMedia?.(PHONE).matches ?? false; } catch { return false; } };

/** A phone opens at about one graph unit per pixel around the centre — the mark
 * and the hubs, labels readable — rather than squeezing sixty pills into 390px
 * where no label can be read. Panning, pinching and "fit" reach the rest. */
function phoneView(): View {
  const w = window.innerWidth || 390;
  const h = Math.max(320, (window.innerHeight || 844) - 140);
  return { x: -w / 2, y: -h / 2, w, h };
}

type Moved = Record<string, Point>;

function readMoved(): Moved {
  try {
    const raw = JSON.parse(localStorage.getItem(MOVED_KEY) ?? "{}");
    if (!raw || typeof raw !== "object") return {};
    const out: Moved = {};
    for (const [id, p] of Object.entries(raw)) {
      if (Array.isArray(p) && p.length === 2 && p.every(Number.isFinite)) out[id] = [p[0], p[1]];
    }
    return out;
  } catch { return {}; }   // private mode, or a value some other build wrote
}
function writeMoved(m: Moved) {
  try {
    if (Object.keys(m).length === 0) localStorage.removeItem(MOVED_KEY);
    else localStorage.setItem(MOVED_KEY, JSON.stringify(m));
  } catch { /* private mode */ }
}

type Drag = {
  x: number; y: number;            // where the press started, client px
  moved: number; captured: boolean;
  view: View;                      // the view at the press: a pan is relative to it
  grab?: { ids: string[]; from: Point[]; at: Point };   // a node drag: what moves, from where
  pinch?: { dist: number; view: View; anchor: Point };   // two fingers: the spread and the point between them
  dead?: boolean;                  // a pinch lost a finger: ignore the rest of this gesture
};

type Props = {
  graph: WikiGraphWire;
  selected?: string | null;
  onSelect: (path: string) => void;
  className?: string;
};

export function MemoryGraph({ graph, selected = null, onSelect, className }: Props) {
  const t = useT();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [phone] = useState(isPhone);
  const type = phone ? PHONE_TYPE : 1;
  const { nodes, edges } = useMemo(() => layout(graph, type), [graph, type]);
  const [moved, setMoved] = useState<Moved>(readMoved);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const pos = (id: string): Point => {
    if (id === CENTRE) return [0, 0];
    const m = moved[id];
    if (m) return m;
    const n = byId.get(id);
    return n ? [n.x, n.y] : [0, 0];
  };
  const fit = () => fitView(nodes, (n) => moved[n.id] ?? [n.x, n.y], type);
  // A refetch must not move the camera: the graph is re-read whenever the tab
  // regains focus, and snapping back every time would undo whatever the owner had
  // just zoomed in on. The fit button is the way back.
  const [view, setView] = useState<View>(() => (phone ? phoneView() : fit()));
  const viewRef = useRef(view);
  useEffect(() => { viewRef.current = view; }, [view]);
  // How far in and out a zoom may go, from the fitted window: worked out on the
  // first zoom and again after a fit or a tidy, not on every render.
  const zoomRange = useRef<[number, number] | null>(null);
  const range = (): [number, number] => {
    if (!zoomRange.current) {
      const f = fit();
      zoomRange.current = [Math.min(f.w, viewRef.current.w) / 6, Math.max(f.w, viewRef.current.w) * 3];
    }
    return zoomRange.current;
  };
  const rangeRef = useRef(range);
  useEffect(() => { rangeRef.current = range; });
  // Fingers on the svg, by pointer id: two of them make a pinch.
  const fingers = useRef(new Map<number, Point>());
  const [hover, setHover] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  // A drag must not become a click on whatever node happened to be under the finger.
  const drag = useRef<Drag | null>(null);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    // Registered by hand, not as onWheel: React attaches wheel passively, and a
    // passive listener cannot stop the page from scrolling under the zoom.
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const v = viewRef.current;
      const [lo, hi] = rangeRef.current();
      const w = clamp(v.w * Math.exp(e.deltaY * 0.0015), lo, hi);
      // The point under the cursor stays under the cursor.
      const anchor = clientToGraph(rect, v, e.clientX, e.clientY);
      setView(viewAround(rect, w, (v.h * w) / v.w, anchor, e.clientX, e.clientY));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  /** The buttons: zoom about the middle of what is on screen. */
  const zoomBy = (f: number) => {
    const v = viewRef.current;
    const [lo, hi] = range();
    const w = clamp(v.w * f, lo, hi), h = (v.h * w) / v.w;
    const cx = v.x + v.w / 2, cy = v.y + v.h / 2;
    setView({ x: cx - w / 2, y: cy - h / 2, w, h });
  };

  const active = dragging ?? hover;
  const neighbours = useMemo(() => {
    if (!active) return null;
    const near = new Set<string>([active]);
    for (const e of edges) {
      if (e.a === active) near.add(e.b);
      if (e.b === active) near.add(e.a);
    }
    return near;
  }, [active, edges]);

  const dim = (id: string) => (neighbours && !neighbours.has(id) ? 0.28 : 1);
  const edgeOpacity = (e: GraphEdge) => {
    const base = e.kind === "hub" ? 0.14 : 0.1;
    if (!active) return base;
    return e.a === active || e.b === active ? 0.55 : 0.03;
  };

  /** What a press on this node drags: the node, or for a hub its whole namespace. */
  const carried = (n: GraphNode): string[] =>
    n.kind === "hub" ? [n.id, ...nodes.filter((m) => m.kind !== "hub" && m.ns === n.ns).map((m) => m.id)] : [n.id];

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    fingers.current.set(e.pointerId, [e.clientX, e.clientY]);
    const rect0 = svgRef.current?.getBoundingClientRect();
    if (fingers.current.size === 2 && rect0?.width) {
      // A second finger turns whatever the first one started into a pinch; a node
      // it may have been dragging stays where it got to.
      const [[ax, ay], [bx, by]] = [...fingers.current.values()];
      const mx = (ax + bx) / 2, my = (ay + by) / 2;
      drag.current = {
        x: mx, y: my, moved: Infinity, captured: true, view,
        pinch: { dist: Math.hypot(ax - bx, ay - by) || 1, view, anchor: clientToGraph(rect0, view, mx, my) },
      };
      setDragging(null);
      try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* not capturable */ }
      return;
    }
    if (fingers.current.size > 2 || e.button !== 0) return;
    const d: Drag = { x: e.clientX, y: e.clientY, view, moved: 0, captured: false };
    const id = (e.target as Element).closest?.("[data-node]")?.getAttribute("data-node");
    const n = id ? byId.get(id) : undefined;
    const rect = svgRef.current?.getBoundingClientRect();
    if (n && rect?.width) {
      const ids = carried(n);
      d.grab = { ids, from: ids.map(pos), at: clientToGraph(rect, view, e.clientX, e.clientY) };
    }
    drag.current = d;
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (fingers.current.has(e.pointerId)) fingers.current.set(e.pointerId, [e.clientX, e.clientY]);
    const d = drag.current;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!d || d.dead || !rect?.width || !rect.height) return;
    if (d.pinch) {
      if (fingers.current.size < 2) return;
      const [[ax, ay], [bx, by]] = [...fingers.current.values()];
      const { dist, view: v0, anchor } = d.pinch;
      const [lo, hi] = range();
      const w = clamp((v0.w * dist) / (Math.hypot(ax - bx, ay - by) || 1), lo, hi);
      // The point that was between the fingers stays between them.
      setView(viewAround(rect, w, (v0.h * w) / v0.w, anchor, (ax + bx) / 2, (ay + by) / 2));
      return;
    }
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    d.moved = Math.max(d.moved, Math.abs(dx) + Math.abs(dy));
    if (d.moved < DRAG_SLOP) return;
    // Capture only once this is a drag. Capturing on pointerdown would retarget
    // the pointerup — and the click that follows it — at the svg, so a plain tap
    // on a page would never reach that page's handler. try/catch, not a bare call:
    // jsdom has no pointer capture at all, and a real browser throws when the id
    // is already gone. Neither should break a drag.
    if (!d.captured) {
      d.captured = true;
      try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* not capturable */ }
      if (d.grab) setDragging(d.grab.ids[0]);
    }
    if (d.grab) {
      const { ids, from, at } = d.grab;
      const [gx, gy] = clientToGraph(rect, d.view, e.clientX, e.clientY);
      setMoved((m) => {
        const next = { ...m };
        ids.forEach((id, i) => { next[id] = [from[i][0] + gx - at[0], from[i][1] + gy - at[1]]; });
        return next;
      });
      return;
    }
    const scale = Math.min(rect.width / d.view.w, rect.height / d.view.h);
    setView({ ...d.view, x: d.view.x - dx / scale, y: d.view.y - dy / scale });
  };
  const endDrag = (e: React.PointerEvent<SVGSVGElement>) => {
    try { e.currentTarget.releasePointerCapture?.(e.pointerId); } catch { /* never captured */ }
    fingers.current.delete(e.pointerId);
    if (drag.current?.pinch || drag.current?.dead) {
      // Lifting one of two fingers must not turn the other into a pan that jumps.
      if (fingers.current.size > 0) { drag.current = { ...drag.current, pinch: undefined, dead: true }; return; }
      window.setTimeout(() => { drag.current = null; }, 0);
      return;
    }
    if (drag.current?.grab && drag.current.moved >= DRAG_SLOP) {
      // Saved once per drag, not per frame, and only for nodes this wiki still has.
      setMoved((m) => {
        const kept = Object.fromEntries(Object.entries(m).filter(([id]) => byId.has(id)));
        writeMoved(kept);
        return kept;
      });
    }
    setDragging(null);
    // Kept until the click that follows has been handled, then cleared by it.
    window.setTimeout(() => { drag.current = null; }, 0);
  };
  const click = (path: string) => { if ((drag.current?.moved ?? 0) < DRAG_SLOP) onSelect(path); };

  const tidy = () => {
    setMoved({});
    writeMoved({});
    zoomRange.current = null;
    setView(fitView(nodes, undefined, type));
  };
  const refit = () => {
    zoomRange.current = null;
    setView(fit());
  };
  const anyMoved = nodes.some((n) => moved[n.id]);

  const TOOL = "rounded-md border border-border bg-background/70 p-1.5 text-muted-foreground backdrop-blur hover:bg-accent hover:text-accent-foreground";

  return (
    <div className={cn("relative h-full w-full", className)}>
      <div className="absolute right-2 top-2 z-10 flex gap-1.5">
        {anyMoved && (
          <button type="button" onClick={tidy} aria-label={t("memory.tidy")} title={t("memory.tidy")} className={TOOL}>
            <LayoutGrid className="size-4" strokeWidth={1.75} />
          </button>
        )}
        <button type="button" onClick={() => zoomBy(1 / ZOOM_STEP)} aria-label={t("memory.zoomIn")} title={t("memory.zoomIn")} className={TOOL}>
          <Plus className="size-4" strokeWidth={1.75} />
        </button>
        <button type="button" onClick={() => zoomBy(ZOOM_STEP)} aria-label={t("memory.zoomOut")} title={t("memory.zoomOut")} className={TOOL}>
          <Minus className="size-4" strokeWidth={1.75} />
        </button>
        <button type="button" onClick={refit} aria-label={t("memory.reset")} title={t("memory.reset")} className={TOOL}>
          <Maximize className="size-4" strokeWidth={1.75} />
        </button>
      </div>
      <p className="pointer-events-none absolute bottom-2 left-3 z-10 hidden text-xs text-muted-foreground sm:block">
        {t("memory.hint")}
      </p>
      <svg ref={svgRef} role="img" aria-label={t("memory.graph")}
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        className={cn("block h-full w-full touch-none select-none text-foreground",
          dragging ? "cursor-grabbing" : "cursor-grab active:cursor-grabbing")}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={endDrag} onPointerCancel={endDrag}>
        <defs>
          <filter id="cl-memory-glow" x="-200%" y="-200%" width="500%" height="500%">
            <feGaussianBlur stdDeviation="16" />
          </filter>
        </defs>

        <g>
          {edges.map((e, i) => {
            const [x1, y1] = pos(e.a), [x2, y2] = pos(e.b);
            return (
              <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="currentColor" strokeWidth={1} opacity={edgeOpacity(e)}
                strokeDasharray={e.dashed ? "4 4" : undefined} />
            );
          })}
        </g>

        {/* The centre: the CladeX mark over a blurred signal-coloured disc for the glow. */}
        <g className="text-signal" data-testid="wiki-centre">
          <circle cx={0} cy={0} r={34} fill="currentColor" opacity={0.3} filter="url(#cl-memory-glow)" />
          <image href={mark64} x={-24} y={-24} width={48} height={48} />
        </g>

        {nodes.map((n) => {
          const [x, y] = pos(n.id);
          if (n.kind === "hub") {
            const Icon = ICONS[n.ns] ?? Folder;
            const label = n.ns || t("memory.rootNs");
            return (
              <g key={n.id} data-testid="wiki-hub" data-node={n.id} opacity={dim(n.id)}
                transform={`translate(${x} ${y}) scale(${type})`}
                onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)}>
                <title>{label}</title>
                <rect x={-15} y={-15} width={30} height={30} rx={9}
                  className={cn("fill-muted stroke-border", dragging === n.id && "stroke-signal")} strokeWidth={1} />
                <Icon x={-9} y={-9} width={18} height={18} strokeWidth={1.75}
                  className="pointer-events-none text-muted-foreground" />
                <text y={30} textAnchor="middle" className="pointer-events-none fill-muted-foreground text-[10px]">{label}</text>
              </g>
            );
          }
          const w = pillWidth(n.label);
          if (n.kind === "ghost") {
            return (
              <g key={`ghost:${n.id}`} data-testid="wiki-ghost" data-node={n.id} opacity={dim(n.id) * 0.7}
                transform={`translate(${x} ${y}) scale(${type})`}>
                <title>{t("memory.missingLink", { path: n.id })}</title>
                <rect x={-w / 2} y={-11} width={w} height={22} rx={11}
                  className="fill-background" stroke="currentColor" strokeWidth={1} strokeDasharray="3 3" strokeOpacity={0.35} />
                <text y={4} textAnchor="middle" className="pointer-events-none fill-muted-foreground text-[10px]">{truncate(n.label)}</text>
              </g>
            );
          }
          const on = selected === n.id;
          return (
            <g key={n.id} data-testid="wiki-page" data-node={n.id} role="button" tabIndex={0} aria-label={n.label}
              opacity={dim(n.id)} transform={`translate(${x} ${y}) scale(${type})`}
              className={cn("outline-none", dragging === n.id ? "cursor-grabbing" : "cursor-pointer")}
              onClick={() => click(n.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n.id); } }}
              onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)}
              onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}>
              <title>{n.page?.summary ? `${n.label} — ${n.page.summary}` : n.label}</title>
              <rect x={-w / 2} y={-11} width={w} height={22} rx={11}
                className={cn("fill-muted stroke-border", (on || dragging === n.id) && "stroke-signal", on && "fill-selected")}
                strokeWidth={on || dragging === n.id ? 1.5 : 1} />
              <text y={4} textAnchor="middle"
                className={cn("pointer-events-none fill-foreground text-[10px]", on && "fill-selected-foreground")}>
                {truncate(n.label)}
              </text>
              {n.page && !n.page.in_index && (
                <g>
                  <title>{t("memory.notInIndex")}</title>
                  <circle cx={w / 2 - 3} cy={-9} r={3} className="fill-warning-foreground" />
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
