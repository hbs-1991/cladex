"use client";

import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";
import { paper } from "./surfaces";

const DOTS = Array.from({ length: 64 }, (_, i) => i);

// cladex: upstream also ships a "1024 × 1024" chip and a regenerate button. The
// size is not ours to claim — gpt-image-2 takes the aspect ratio in words, not in
// flags — and the button has no handler in the source and nothing to call here:
// the picture is drawn by a skill running in the container, not by the browser.
export function ImageGeneration({
  prompt,
  generating,
  label,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children" | "prompt" | "generating"> & {
  prompt: string;
  generating: boolean;
  /** cladex: shown while generating when the prompt could not be read off the command. */
  label: string;
}) {
  return (
    <div
      data-slot="image-generation"
      className={cn("flex w-52 flex-col gap-2.5", className)}

      {...props}
    >
      <div
        className={cn(
          paper,
          "relative aspect-square w-full overflow-hidden rounded-2xl",
        )}
      >
        <div
          className="absolute inset-0 grid grid-cols-8 place-items-center p-6"
          aria-hidden
        >
          {DOTS.map((dot) => {
            const row = Math.floor(dot / 8);
            const col = dot % 8;
            return (
              <span
                key={dot}
                className={cn(
                  "bg-foreground/20 size-1 rounded-full transition-opacity duration-500",
                  generating
                    ? "animate-pulse motion-reduce:animate-none"
                    : "opacity-0",
                )}
                style={{ animationDelay: `${(row + col) * 90}ms` }}
              />
            );
          })}
        </div>
        <div
          aria-hidden
          className={cn(
            "absolute inset-0 transition-[opacity,filter] duration-1000 ease-out motion-reduce:transition-none",
            generating ? "opacity-0 blur-xl" : "blur-0 opacity-100",
          )}
          style={{
            background:
              "radial-gradient(120% 90% at 20% 100%, oklch(0.45 0.09 265) 0%, transparent 55%), radial-gradient(110% 80% at 85% 90%, oklch(0.62 0.1 300 / 0.8) 0%, transparent 60%), radial-gradient(130% 100% at 60% 0%, oklch(0.88 0.06 60) 0%, oklch(0.74 0.09 25 / 0.9) 45%, transparent 75%), linear-gradient(to top, oklch(0.35 0.06 275), oklch(0.82 0.07 50))",
          }}
        />
      </div>
      <div className="flex items-center justify-between gap-2">
        {/* cladex: upstream shimmers the word "Generating" here. The prompt says more
            during the wait, and it stays still: the dot grid above is this element's
            motion, and the step-group header is already shimmering above it. */}
        <p className="text-foreground/45 min-w-0 flex-1 truncate text-xs">
          {generating ? prompt || label : prompt}
        </p>
      </div>
    </div>
  );
}
