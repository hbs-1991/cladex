"use client";

import { AuiIf, ComposerPrimitive, useAuiState } from "@assistant-ui/react";
import { MicIcon, SquareIcon } from "lucide-react";
import { type ComponentProps, useEffect, useState } from "react";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button"; // cladex: the row's buttons carry tooltips
import { mono, ShimmerLabel } from "./surfaces";
import { useT } from "@/i18n"; // cladex: labels come from the dictionary
import { cn } from "@/lib/utils";

const BARS = Array.from({ length: 14 }, (_, i) => i);

function barHeight(bar: number, tick: number): number {
  return 5 + Math.abs(Math.sin(bar * 1.35 + tick * 0.55)) * 13;
}

/**
 * The waveform that stands in for the mic while a person talks: a pulsing
 * dot, fourteen bars rippling from a sine wave seeded by the bar's index and
 * the elapsed seconds, and a mono `0:SS` timer. Once `recording` drops the
 * bars go flat and the timer becomes a "Transcribing" shimmer — there is no
 * third, blank state, so mounting it always means a capture is happening or
 * has just finished.
 */
export function ComposerVoice({
  recording,
  seconds,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children"> & {
  recording: boolean;
  seconds: number;
}) {
  const t = useT(); // cladex
  return (
    <div
      data-slot="composer-voice"
      data-recording={recording || undefined}
      role="status" // cladex: the state is announced, the bars are decoration
      aria-label={recording ? t("chat.listening") : t("chat.transcribing")}
      // cladex: min-w-0 + overflow-hidden — on a narrow rail the bars clip rather than push the buttons out
      className={cn("flex min-h-11 min-w-0 items-center gap-3 overflow-hidden ps-3", className)}
      {...props}
    >
      {recording && (
        <span
          aria-hidden
          // cladex: the one running mark — the signal dot on --cl-motion-loop (DESIGN.md §5)
          className="aui-composer-voice-dot size-1.5 shrink-0 animate-thinking rounded-full bg-signal"
        />
      )}
      <div className="aui-composer-voice-bars flex h-6 min-w-0 items-center gap-[3px] overflow-hidden" aria-hidden>
        {BARS.map((bar) => (
          <span
            key={bar}
            className={cn(
              "aui-composer-voice-bar w-0.5 shrink-0 rounded-full transition-[height,background-color] duration-150 motion-reduce:transition-none",
              recording ? "bg-foreground/50" : "bg-foreground/25",
            )}
            style={{ height: recording ? barHeight(bar, seconds * 10) : 3 }}
          />
        ))}
      </div>
      {recording ? (
        <span className={cn(mono, "aui-composer-voice-timer text-foreground/40 shrink-0 tabular-nums")}>
          0:{String(seconds).padStart(2, "0")}
        </span>
      ) : (
        <ShimmerLabel className="aui-composer-voice-label text-foreground/55 relative min-w-0 truncate text-[13px]">
          {t("chat.transcribing")}
        </ShimmerLabel>
      )}
    </div>
  );
}

/**
 * The mic, or — while `active` — the filled stop square. Renders a
 * `TooltipIconButton` so it forwards its ref and reads like the rest of the
 * composer's action row.
 */
export function ComposerVoiceButton({
  active,
  className,
  ...props
}: Omit<ComponentProps<typeof TooltipIconButton>, "children" | "tooltip"> & {
  active: boolean;
}) {
  const t = useT(); // cladex
  return (
    <TooltipIconButton
      type="button"
      tooltip={active ? t("chat.stop") : t("chat.dictate")}
      side="bottom"
      aria-label={active ? t("chat.stopDictation") : t("chat.startDictation")}
      data-slot="composer-voice-button"
      // cladex: primary fill while active, like the composer's own cancel button
      variant={active ? "default" : "ghost"}
      size="icon"
      className={cn(
        "aui-composer-voice-button size-8 rounded-full",
        !active && "text-muted-foreground hover:text-foreground",
        className,
      )}
      {...props}
    >
      {active ? (
        <SquareIcon className="size-3 fill-current" />
      ) : (
        <MicIcon className="size-4" />
      )}
    </TooltipIconButton>
  );
}

/**
 * Dictation wired to the runtime: the mic starts a session through the
 * configured `DictationAdapter`; while one is active the waveform and the stop
 * button take its place. The runtime has no "stopping" state — `s.composer.dictation`
 * stays non-null until the adapter's `stop()` settles and the final transcript
 * lands — so the settling phase is a local flag raised by the stop button and
 * cleared when the session ends. The timer counts only while the adapter
 * reports `running`, not while the browser is still asking for the microphone.
 */
export function ComposerVoiceControl({ className }: { className?: string }) {
  const dictation = useAuiState((s) => s.composer.dictation);
  const active = dictation != null;
  const running = dictation?.status.type === "running";
  const [stopping, setStopping] = useState(false);
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (active) return;
    setStopping(false);
    setSeconds(0);
  }, [active]);

  const recording = active && !stopping;

  useEffect(() => {
    if (!recording || !running) return undefined;
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [recording, running]);

  return (
    <AuiIf condition={(s) => s.thread.capabilities.dictation}>
      <AuiIf condition={(s) => s.composer.dictation == null}>
        <ComposerPrimitive.Dictate asChild>
          <ComposerVoiceButton active={false} className={className} />
        </ComposerPrimitive.Dictate>
      </AuiIf>
      <AuiIf condition={(s) => s.composer.dictation != null}>
        <ComposerVoice
          recording={recording}
          seconds={seconds}
          className="min-h-0 ps-0"
        />
        <ComposerPrimitive.StopDictation asChild>
          <ComposerVoiceButton
            active
            className={className}
            onClick={() => setStopping(true)}
          />
        </ComposerPrimitive.StopDictation>
      </AuiIf>
    </AuiIf>
  );
}
