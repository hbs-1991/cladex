/** Read-aloud adapter: the server synthesises the audio, the browser only plays it. */

import type { SpeechSynthesisAdapter } from "@assistant-ui/react";
import { api } from "@/api/client";

export class ServerSpeechAdapter implements SpeechSynthesisAdapter {
  speak(text: string): SpeechSynthesisAdapter.Utterance {
    const subs = new Set<() => void>();
    let status: SpeechSynthesisAdapter.Status = { type: "starting" };
    let audio: HTMLAudioElement | null = null;
    const notify = () => subs.forEach((cb) => cb());
    const finish = (reason: "finished" | "cancelled" | "error", error?: unknown) => {
      if (status.type === "ended") return;
      status = { type: "ended", reason, error };
      notify();
    };
    api.voice
      .speak(text)
      .then((blob) => {
        if (status.type === "ended") return;
        audio = new Audio(URL.createObjectURL(blob));
        status = { type: "running" };
        notify();
        audio.onended = () => finish("finished");
        audio.onerror = (e) => finish("error", e);
        audio.play().catch((e: unknown) => finish("error", e));
      })
      .catch((e: unknown) => finish("error", e));
    return {
      get status() {
        return status;
      },
      cancel: () => {
        audio?.pause();
        finish("cancelled");
      },
      subscribe: (cb) => {
        subs.add(cb);
        return () => subs.delete(cb);
      },
    };
  }
}
