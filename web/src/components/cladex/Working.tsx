/**
 * The one mark that says "this is still going": a signal dot pulsing on
 * `--cl-motion-loop`, the tempo every looping animation in the app shares
 * (DESIGN.md §5). It replaced three different idioms — an opacity pulse here, a
 * shimmering label there, a bullet on Tailwind's own two-second pulse at the end
 * of the message — that used to run at once and at three different speeds.
 *
 * Give it a `label` only where nothing else names what is running: with a label
 * it announces itself, without one it is decoration next to text that already
 * says so.
 */

import { cn } from "@/lib/utils";

export function Working({ label, className }: { label?: string; className?: string }) {
  return (
    <span
      {...(label ? { role: "status", "aria-label": label } : { "aria-hidden": true })}
      className={cn("size-1.5 shrink-0 animate-thinking rounded-full bg-signal", className)}
    />
  );
}
