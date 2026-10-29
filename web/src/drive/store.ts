import { create } from "zustand";
import { DriveSocket } from "./socket";
import { isEcho, Listener, Speaker, voiceInputSupported, voiceOutputSupported } from "~/lib/speech";
import type { DriveFrame, DriveState } from "./types";

export interface DriveMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  status: "complete" | "interrupted" | "resumed";
  modality: string;
}

export interface DriveLogEntry {
  id: number;
  kind: string;
  text: string;
  at: number;
}

export interface DriveMetrics {
  ack: number | null;
  speculationSaved: number | null;
  serverYield: number | null;
  voiceYield: number | null;
  recovered: number;
  interruptions: number;
}

export type ScriptStep = { say: string; msPerWord?: number } | { wait: number } | { interrupt: true };

interface DriveStore {
  status: "connecting" | "open" | "closed";
  provider: string;
  drive: DriveState | null;
  stage: string;
  stageDetail: string;
  messages: DriveMessage[];
  live: { turnId: string; text: string } | null;
  ack: string | null;
  log: DriveLogEntry[];
  metrics: DriveMetrics;
  interim: string;
  listening: boolean;
  speaking: boolean;
  voiceOut: boolean;
  voiceError: string | null;
  scriptRunning: boolean;
  connect: () => void;
  disconnect: () => void;
  say: (text: string, modality?: "voice" | "text") => void;
  partial: (text: string) => void;
  bargeIn: () => void;
  toggleListening: () => void;
  setVoiceOut: (on: boolean) => void;
  runScript: (steps: ScriptStep[]) => Promise<void>;
  stopScript: () => void;
}

const LOG_CAP = 80;
let socket: DriveSocket | null = null;
let listener: Listener | null = null;
const speaker = new Speaker();
let logSeq = 0;
let lastReplyTurn: string | null = null;
let recentlySpoken = { text: "", until: 0 }; // echo guard just after the agent stops
let scriptAbort = false;
const replyText: Record<string, string> = {};
const enqueuedUpTo: Record<string, number> = {};

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Queue every sentence of a reply that has fully arrived; `final` flushes the rest. */
function speakArrived(turnId: string, final: boolean) {
  const text = replyText[turnId] ?? "";
  let from = enqueuedUpTo[turnId] ?? 0;
  const boundary = /[.!?](\s+|$)/g;
  boundary.lastIndex = from;
  let match: RegExpExecArray | null;
  while ((match = boundary.exec(text))) {
    const end = match.index + match[0].length;
    if (!final && end === text.length && !/\s$/.test(match[0])) break; // may still be mid-number ("5.")
    speaker.enqueue(turnId, text.slice(from, end), from);
    from = end;
  }
  if (final && from < text.length) {
    speaker.enqueue(turnId, text.slice(from), from);
    from = text.length;
  }
  enqueuedUpTo[turnId] = from;
}

export const useDrive = create<DriveStore>((set, get) => {
  const log = (kind: string, text: string) =>
    set((s) => ({ log: [...s.log, { id: logSeq++, kind, text, at: Date.now() }].slice(-LOG_CAP) }));

  const onFrame = (frame: DriveFrame) => {
    switch (frame.t) {
      case "ready":
        set({ provider: frame.provider });
        break;
      case "drive":
        set({ drive: frame.state });
        break;
      case "drive_event":
        log(frame.kind, frame.text);
        break;
      case "stage":
        set({ stage: frame.stage, stageDetail: frame.detail });
        if (frame.stage === "interrupted") log("interrupted", frame.detail);
        break;
      case "filler":
        set({ ack: frame.text });
        speaker.enqueue(`ack:${frame.turn_id}`, frame.text, 0);
        break;
      case "token": {
        replyText[frame.turn_id] = (replyText[frame.turn_id] ?? "") + frame.text;
        lastReplyTurn = frame.turn_id;
        set({ live: { turnId: frame.turn_id, text: replyText[frame.turn_id] } });
        speakArrived(frame.turn_id, false);
        break;
      }
      case "message": {
        if (frame.role === "agent") {
          replyText[frame.turn_id] = frame.content;
          lastReplyTurn = frame.turn_id;
          if (frame.status !== "interrupted") speakArrived(frame.turn_id, true);
        }
        set((s) => {
          const message: DriveMessage = {
            id: `${frame.turn_id}:${frame.role}:${s.messages.length}`,
            role: frame.role === "agent" ? "agent" : "user",
            text: frame.content,
            status: frame.status,
            modality: frame.modality,
          };
          return {
            live: frame.role === "agent" ? null : s.live,
            ack: frame.role === "agent" ? null : s.ack,
            messages: [...s.messages, message].slice(-60),
          };
        });
        break;
      }
      case "spec":
        if (frame.status === "started") log("spec", `Started planning the ${frame.query} while you were still speaking`);
        else if (frame.status === "hit") log("spec", `Reused the early plan for the ${frame.query} (saved ${Math.round(frame.saved_ms)} ms)`);
        else log("spec", `Dropped an early guess: ${frame.query}`);
        break;
      case "tool":
        if (frame.name === "plan_route" && frame.status === "requested") log("tool", `plan_route -> ${String(frame.args.to ?? "")}`);
        break;
      case "checkpoint":
        log("checkpoint", `Checkpoint: ${frame.summary}`);
        break;
      case "metric":
        set((s) => {
          const m = { ...s.metrics };
          if (frame.name === "ack_latency") m.ack = frame.value;
          if (frame.name === "speculation_saved") m.speculationSaved = frame.value;
          if (frame.name === "time_to_yield") m.serverYield = frame.value;
          if (frame.name === "recovered_tokens") m.recovered += 1;
          return { metrics: m };
        });
        break;
      case "error":
        log("error", frame.message);
        break;
    }
  };

  speaker.onSpeaking = (speaking) => {
    if (!speaking) recentlySpoken = { text: speaker.lastText, until: Date.now() + 1500 };
    set({ speaking });
  };

  const echo = (text: string) =>
    isEcho(text, speaker.currentText) || (Date.now() < recentlySpoken.until && isEcho(text, recentlySpoken.text));

  return {
    status: "connecting",
    provider: "",
    drive: null,
    stage: "idle",
    stageDetail: "",
    messages: [],
    live: null,
    ack: null,
    log: [],
    metrics: { ack: null, speculationSaved: null, serverYield: null, voiceYield: null, recovered: 0, interruptions: 0 },
    interim: "",
    listening: false,
    speaking: false,
    voiceOut: voiceOutputSupported(),
    voiceError: voiceInputSupported() ? null : "This browser has no speech recognition - use Chrome or Edge, or type below.",
    scriptRunning: false,

    connect: () => {
      if (socket) return;
      socket = new DriveSocket(onFrame, (status) => set({ status }));
      socket.connect();
    },

    disconnect: () => {
      scriptAbort = true;
      listener?.stop();
      speaker.cancel();
      socket?.close();
      socket = null;
    },

    partial: (text) => {
      if (speaker.speaking && !echo(text)) get().bargeIn(); // the driver is talking over the agent
      set({ interim: text });
      socket?.send({ t: "partial", text });
    },

    say: (text, modality = "text") => {
      const clean = text.trim();
      set({ interim: "" });
      if (!clean) return;
      if (speaker.speaking) get().bargeIn();
      socket?.send({ t: "final", text: clean, modality });
    },

    bargeIn: () => {
      const started = performance.now();
      const stopped = speaker.cancel();
      const voiceYield = performance.now() - started;
      // How much of the latest reply the driver actually heard: from the sentence
      // being spoken when they cut in, else from what had finished playing.
      let heard: number | undefined;
      if (get().voiceOut && lastReplyTurn) {
        heard = stopped?.turnId === lastReplyTurn ? stopped.heard : speaker.heard(lastReplyTurn);
      }
      socket?.send({ t: "interrupt", heard_chars: heard });
      if (stopped) {
        recentlySpoken = { text: "", until: 0 };
        set((s) => ({ metrics: { ...s.metrics, voiceYield, interruptions: s.metrics.interruptions + 1 } }));
      }
    },

    toggleListening: () => {
      if (!listener) {
        listener = new Listener({
          onPartial: (text) => {
            if (!echo(text)) get().partial(text);
          },
          onFinal: (text) => {
            if (!echo(text)) get().say(text, "voice");
            else set({ interim: "" });
          },
          onState: (listening, error) => set({ listening, voiceError: error ?? null }),
        });
      }
      if (listener.listening) listener.stop();
      else listener.start();
    },

    setVoiceOut: (on) => {
      speaker.setEnabled(on);
      set({ voiceOut: on && voiceOutputSupported() });
    },

    runScript: async (steps) => {
      scriptAbort = false;
      set({ scriptRunning: true });
      try {
        for (const step of steps) {
          if (scriptAbort) break;
          if ("wait" in step) {
            await sleep(step.wait);
          } else if ("interrupt" in step) {
            get().bargeIn();
          } else {
            // spoken word by word, through the same partial -> final path as the microphone
            const words = step.say.split(/\s+/);
            let spoken = "";
            for (const word of words) {
              if (scriptAbort) break;
              spoken = spoken ? `${spoken} ${word}` : word;
              get().partial(spoken);
              await sleep(step.msPerWord ?? 90 + Math.random() * 80);
            }
            if (!scriptAbort) {
              await sleep(200);
              get().say(spoken, "voice");
            }
          }
        }
      } finally {
        set({ scriptRunning: false });
      }
    },

    stopScript: () => {
      scriptAbort = true;
    },
  };
});
