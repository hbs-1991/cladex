import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ServerDictationAdapter } from "./dictation";

const transcribe = vi.fn();
vi.mock("@/api/client", () => ({ api: { voice: { transcribe: (...a: unknown[]) => transcribe(...a) } } }));

/** A MediaRecorder that, like the real one, delivers `stop` on a later task. */
class FakeRecorder {
  static last: FakeRecorder | null = null;
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstart: (() => void) | null = null;
  onstop: (() => void) | null = null;
  stream: unknown;
  constructor(stream: unknown) { this.stream = stream; FakeRecorder.last = this; }
  start() { this.state = "recording"; this.onstart?.(); }
  stop() {
    this.state = "inactive";
    setTimeout(() => {
      this.ondataavailable?.({ data: new Blob(["x"]) });
      this.onstop?.();
    }, 0);
  }
}

const tracks = [{ stop: vi.fn() }];
const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  transcribe.mockReset();
  tracks[0].stop.mockReset();
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => tracks }) },
  });
});
afterEach(() => vi.unstubAllGlobals());

it("stop() settles only after the transcript has reached the listeners", async () => {
  let resolveText: (v: { text: string }) => void = () => {};
  transcribe.mockReturnValue(new Promise((r) => { resolveText = r; }));
  const session = new ServerDictationAdapter().listen();
  const speech = vi.fn();
  const ended = vi.fn();
  session.onSpeech(speech);
  session.onSpeechEnd(ended);
  await tick();
  expect(session.status).toEqual({ type: "running" });

  let stopped = false;
  const stopping = session.stop().then(() => { stopped = true; });
  await tick();
  expect(stopped).toBe(false); // the recorder has stopped, the server has not answered
  expect(speech).not.toHaveBeenCalled();

  resolveText({ text: "hello" });
  await stopping;
  expect(speech).toHaveBeenCalledWith({ transcript: "hello", isFinal: true });
  expect(ended).toHaveBeenCalledWith({ transcript: "hello", isFinal: true });
  expect(session.status).toEqual({ type: "ended", reason: "stopped" });
  expect(tracks[0].stop).toHaveBeenCalled();
});

it("a failed transcription (no key on the server) ends the session with an error and still settles", async () => {
  transcribe.mockRejectedValue(new Error("503"));
  const session = new ServerDictationAdapter().listen();
  const speech = vi.fn();
  session.onSpeech(speech);
  await tick();
  await session.stop();
  expect(speech).not.toHaveBeenCalled();
  expect(session.status).toEqual({ type: "ended", reason: "error" });
});

it("stopping before the microphone is granted settles at once and releases the stream", async () => {
  let grant: (v: unknown) => void = () => {};
  (navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockReturnValue(
    new Promise((r) => { grant = r; }),
  );
  const session = new ServerDictationAdapter().listen();
  await session.stop();
  grant({ getTracks: () => tracks });
  await tick();
  expect(tracks[0].stop).toHaveBeenCalled();
  expect(FakeRecorder.last?.state ?? "inactive").toBe("inactive");
});
