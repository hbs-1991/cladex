/** Dictation adapter that records with MediaRecorder and transcribes on the server. */

import type { DictationAdapter } from "@assistant-ui/react";
import { api } from "@/api/client";

export class ServerDictationAdapter implements DictationAdapter {
  disableInputDuringDictation = true;

  listen(): DictationAdapter.Session {
    const start = new Set<() => void>();
    const end = new Set<(r: DictationAdapter.Result) => void>();
    const speech = new Set<(r: DictationAdapter.Result) => void>();
    let rec: MediaRecorder | null = null;
    let stream: MediaStream | null = null;
    let cancelled = false;
    let stopped = false;
    const chunks: BlobPart[] = [];

    // The runtime tears the session down — listeners included — as soon as
    // `stop()` settles, while MediaRecorder delivers its last chunk and its
    // `stop` event on a later task. `stop()` therefore resolves only once the
    // transcript has been handed to the listeners (or the attempt has failed),
    // which is also what keeps the composer in its "transcribing" state until
    // the words land.
    let settle: () => void = () => {};
    const settled = new Promise<void>((resolve) => {
      settle = resolve;
    });

    const session: DictationAdapter.Session = {
      status: { type: "starting" },
      stop: async () => {
        stopped = true;
        if (rec && rec.state !== "inactive") rec.stop();
        else settle(); // stopped before the microphone was granted
        await settled;
      },
      cancel: () => {
        cancelled = true;
        rec?.stop();
        stream?.getTracks().forEach((t) => t.stop());
        session.status = { type: "ended", reason: "cancelled" };
        settle();
      },
      onSpeechStart: (cb) => {
        start.add(cb);
        return () => start.delete(cb);
      },
      onSpeechEnd: (cb) => {
        end.add(cb);
        return () => end.delete(cb);
      },
      onSpeech: (cb) => {
        speech.add(cb);
        return () => speech.delete(cb);
      },
    };

    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled || stopped) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        rec = new MediaRecorder(stream);
        rec.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data);
        };
        rec.onstart = () => {
          session.status = { type: "running" };
          start.forEach((cb) => cb());
        };
        rec.onstop = async () => {
          stream?.getTracks().forEach((t) => t.stop());
          if (cancelled) return;
          try {
            const { text } = await api.voice.transcribe(
              new Blob(chunks, { type: rec?.mimeType || "audio/webm" }),
            );
            const result = { transcript: text, isFinal: true };
            speech.forEach((cb) => cb(result));
            end.forEach((cb) => cb(result));
            session.status = { type: "ended", reason: "stopped" };
          } catch {
            session.status = { type: "ended", reason: "error" };
          } finally {
            settle();
          }
        };
        rec.start();
      } catch {
        session.status = { type: "ended", reason: "error" };
        settle();
      }
    })();
    return session;
  }
}
