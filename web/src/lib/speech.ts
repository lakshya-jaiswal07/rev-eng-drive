/**
 * Real voice for the assistant and the in-car agent, with the browser's own
 * speech engines (no API key, no quota): SpeechRecognition for the user,
 * speechSynthesis for the agent. Chrome and Edge support both.
 */

type Recognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
  onspeechstart: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};

function recognitionClass(): (new () => Recognition) | null {
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const voiceInputSupported = () => recognitionClass() !== null;
export const voiceOutputSupported = () => typeof window !== "undefined" && "speechSynthesis" in window;

/* --------------------------------------------------------------- speaker */

interface Queued {
  turnId: string;
  text: string;
  offset: number; // where this sentence starts in the turn's full reply
}

/**
 * Speaks a reply sentence by sentence as its tokens arrive, and knows exactly
 * how far the listener got - the server checkpoints from that, so "go on"
 * resumes from what was actually heard, not from what was merely streamed.
 */
export class Speaker {
  private queue: Queued[] = [];
  private current: (Queued & { boundary: number }) | null = null;
  private finished: Record<string, number> = {};
  private enabled = voiceOutputSupported();
  /** The last sentence spoken to the end - its echo can still reach the microphone. */
  lastText = "";
  onSpeaking: (speaking: boolean) => void = () => {};
  /** What is actually read out for a sentence (e.g. without citation markers). */
  toSpeech: (text: string) => string = (text) => text;

  get speaking() {
    return this.current !== null;
  }

  get currentText() {
    return this.current?.text ?? "";
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled && voiceOutputSupported();
    if (!this.enabled) this.cancel();
  }

  enqueue(turnId: string, text: string, offset: number) {
    if (!this.enabled || !text.trim()) return;
    this.queue.push({ turnId, text, offset });
    if (!this.current) this.next();
  }

  /** Characters of this turn's reply the listener has heard so far. */
  heard(turnId: string): number {
    if (this.current?.turnId === turnId) return this.current.offset + this.current.boundary;
    return this.finished[turnId] ?? 0;
  }

  /** Stop talking now (barge-in). Returns what the listener heard of the reply being spoken. */
  cancel(): { turnId: string; heard: number } | null {
    const current = this.current;
    const result = current ? { turnId: current.turnId, heard: current.offset + current.boundary } : null;
    this.queue = [];
    this.current = null;
    if (voiceOutputSupported()) window.speechSynthesis.cancel();
    this.onSpeaking(false);
    return result;
  }

  private next() {
    const item = this.queue.shift();
    if (!item) {
      this.current = null;
      this.onSpeaking(false);
      return;
    }
    const current = { ...item, boundary: 0 };
    this.current = current;
    this.onSpeaking(true);
    const spoken = this.toSpeech(item.text);
    const utterance = new SpeechSynthesisUtterance(spoken || item.text);
    utterance.lang = "en-IN";
    utterance.rate = 1.05;
    // Boundaries index the spoken text; map them back onto the original.
    const scale = spoken ? item.text.length / spoken.length : 1;
    utterance.onboundary = (event) => {
      if (this.current === current) current.boundary = Math.min(item.text.length, Math.round(event.charIndex * scale));
    };
    const done = () => {
      clearTimeout(watchdog);
      if (this.current !== current) return; // cancelled: cancel() already reported progress
      this.finished[item.turnId] = item.offset + item.text.length;
      this.lastText = item.text;
      this.next();
    };
    utterance.onend = done;
    utterance.onerror = done;
    // Chrome can drop an utterance's end event (or never start it), which would
    // leave the agent "speaking" forever and everything after it unspoken.
    const watchdog = setTimeout(done, 4000 + (spoken || item.text).length * 120);
    // ...and it can garbage-collect an utterance mid-speech unless it is referenced.
    this.utterance = utterance;
    if (window.speechSynthesis.paused) window.speechSynthesis.resume();
    window.speechSynthesis.speak(utterance);
  }

  /** Held only so the browser cannot garbage-collect it mid-sentence. */
  utterance: SpeechSynthesisUtterance | null = null;
}

/* -------------------------------------------------------------- listener */

export interface ListenerHandlers {
  onPartial: (text: string) => void;
  onFinal: (text: string) => void;
  onState: (listening: boolean, error?: string) => void;
}

/** Hands-free, continuous recognition: interim results stream as partials. */
export class Listener {
  private recognition: Recognition | null = null;
  private enabled = false;
  private lastPartialAt = 0;
  private pendingPartial: string | null = null;
  private pendingTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private handlers: ListenerHandlers) {}

  get listening() {
    return this.enabled;
  }

  start() {
    const Recognition = recognitionClass();
    if (!Recognition) {
      this.handlers.onState(false, "This browser has no speech recognition - use Chrome or Edge, or type.");
      return;
    }
    this.enabled = true;
    const rec = new Recognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-IN";
    rec.onresult = (event: any) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const text: string = result[0]?.transcript ?? "";
        if (result.isFinal) {
          this.clearPending(); // the final result supersedes any interim still waiting
          if (text.trim()) this.handlers.onFinal(text.trim());
        } else {
          interim += text;
        }
      }
      if (interim.trim()) this.partial(interim.trim());
    };
    rec.onerror = (event: any) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        this.enabled = false;
        this.handlers.onState(false, "Microphone access was blocked. Allow it in the address bar, then try again.");
      } else if (event.error === "network") {
        this.handlers.onState(this.enabled, "Speech recognition needs an internet connection in this browser.");
      }
      // "no-speech" / "aborted": the end handler restarts listening
    };
    rec.onend = () => {
      // Chrome ends a recognition session after a pause; keep listening hands-free.
      if (this.enabled) setTimeout(() => this.enabled && this.restart(rec), 150);
    };
    this.recognition = rec;
    this.restart(rec);
    this.handlers.onState(true);
  }

  /**
   * Throttle interim results to one per 120 ms, but never drop the latest: the
   * newest text is always delivered when the window ends. A dropped interim
   * result could be the driver cutting in right after an echo fragment.
   */
  private partial(text: string) {
    const wait = 120 - (performance.now() - this.lastPartialAt);
    if (wait <= 0) {
      this.lastPartialAt = performance.now();
      this.pendingPartial = null;
      this.handlers.onPartial(text);
      return;
    }
    this.pendingPartial = text;
    if (this.pendingTimer) return;
    this.pendingTimer = setTimeout(() => {
      this.pendingTimer = null;
      const latest = this.pendingPartial;
      if (latest && this.enabled) {
        this.lastPartialAt = performance.now();
        this.pendingPartial = null;
        this.handlers.onPartial(latest);
      }
    }, wait);
  }

  private clearPending() {
    if (this.pendingTimer) clearTimeout(this.pendingTimer);
    this.pendingTimer = null;
    this.pendingPartial = null;
  }

  stop() {
    this.enabled = false;
    this.clearPending();
    this.recognition?.abort();
    this.recognition = null;
    this.handlers.onState(false);
  }

  private restart(rec: Recognition) {
    try {
      rec.start();
    } catch {
      /* already started */
    }
  }
}

/**
 * Echo guard: with speakers (not headphones) the microphone also hears the
 * agent. Recognised text that is mostly the agent's own current sentence is
 * its echo, not the driver cutting in.
 */
export function isEcho(heard: string, speaking: string): boolean {
  const words = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  const h = words(heard);
  if (!speaking || h.length === 0) return false;
  const said = new Set(words(speaking));
  const overlap = h.filter((w) => said.has(w)).length / h.length;
  return overlap >= 0.6;
}

/* ------------------------------------------------------- control phrases */

const normalise = (text: string) =>
  text.toLowerCase().replace(/’/g, "'").replace(/[^a-z' ]+/g, " ").replace(/\s+/g, " ").trim();

const HOLD =
  /^(?:ok(?:ay)? |no |please |hey )?(?:hold on|hang on|hold it|wait|wait up|stop|stop talking|stop it|stop there|pause|quiet|be quiet|silence|sh+|shush|shut up|enough|that'?s enough|one (?:sec|second|moment|minute)|just a (?:sec|second|moment|minute)|give me a (?:sec|second|moment|minute))(?: (?:a )?(?:sec|second|moment|minute|bit))?(?: please)?$/;
const RESUME =
  /^(?:ok(?:ay)? |yes |yeah |sure |please )?(?:go on|continue|carry on|keep going|go ahead|you were saying|finish that|and then|resume)\b/;

/** "Hold on", "stop": pause the agent - never a request to answer. Mirrors server/app/speech_control.py. */
export const isHold = (text: string) => HOLD.test(normalise(text));
/** "Go on", "continue": carry on from what was actually heard. */
export const isResume = (text: string) => RESUME.test(normalise(text));
