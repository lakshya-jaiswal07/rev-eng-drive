import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Car, LogOut, Mic, MicOff, Navigation, Send, Square, Volume2, VolumeX, Wifi, WifiOff } from "lucide-react";
import { Logo } from "~/components/Logo";
import { ThemeToggle } from "~/components/ThemeToggle";
import { Badge, Button, Panel, SectionLabel } from "~/components/ui/primitives";
import { useAuth } from "~/lib/auth";
import { cn } from "~/lib/utils";
import { useDrive, type ScriptStep } from "./store";

// The map library is downloaded separately, so the first screen loads fast.
const RouteMap = lazy(() => import("./RouteMap").then((m) => ({ default: m.RouteMap })));
import type { RouteInfo } from "./types";

/** One-click demos, spoken word by word through the same path as the microphone. */
const SCRIPTS: { id: string; title: string; hint: string; steps: ScriptStep[] }[] = [
  {
    id: "correction",
    title: "Change of mind mid-sentence",
    hint: "Starts planning Agra while you speak, drops it, acts only on Jaipur",
    steps: [{ say: "take me to, um, Agra... actually no, Jaipur" }],
  },
  {
    id: "mid-planning",
    title: "Switch while it's still planning",
    hint: "The stale Udaipur route is discarded, guidance starts once for Ajmer",
    steps: [{ say: "take me to Udaipur" }, { say: "no wait, Ajmer", msPerWord: 60 }],
  },
  {
    id: "stops",
    title: "Add stops on the way",
    hint: "The route goes stale and is recomputed with the new stops",
    steps: [{ say: "stop for fuel and, uh, a quick coffee on the way" }],
  },
  {
    id: "barge-in",
    title: "Cut in, then 'go on'",
    hint: "Speech stops at once; 'go on' resumes from what you heard",
    steps: [{ say: "where are we going" }, { wait: 1600 }, { interrupt: true }, { wait: 900 }, { say: "go on" }],
  },
  {
    id: "no-double",
    title: "Ask twice, start once",
    hint: "A repeated request never starts navigation a second time",
    steps: [{ say: "go to Jaipur" }],
  },
  {
    id: "ask-while-planning",
    title: "Ask while it's planning",
    hint: "The question is answered without blocking or losing the route",
    steps: [{ say: "take me to Mumbai" }, { wait: 150 }, { say: "how long will it take" }],
  },
];

function duration(min: number) {
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m < 5 ? `${h} h` : `${h} h ${m} min`;
}

function RouteLine({ route }: { route: RouteInfo }) {
  const stopLabel = (s: RouteInfo["stops"][number]) =>
    s.kind === "place" ? `Stop at ${s.near}` : `${s.label[0].toUpperCase()}${s.label.slice(1)} ${s.at_named_place ? "at" : "near"} ${s.near}`;
  const nodes = [
    { label: route.origin.name, kind: "start" },
    ...[...route.stops].sort((a, b) => a.km_from_start - b.km_from_start).map((s) => ({ label: stopLabel(s), kind: "stop" })),
    { label: route.destination.name, kind: "end" },
  ];
  return (
    <div className="relative mt-3 pl-5" aria-label="Route">
      <div className="absolute bottom-2 left-[9px] top-2 w-px bg-line" />
      {nodes.map((n, i) => (
        <div key={i} className="relative flex items-center gap-3 py-1.5">
          <span
            className={cn(
              "absolute -left-5 h-3 w-3 rounded-full border-2",
              n.kind === "start" && "border-info bg-panel",
              n.kind === "stop" && "border-warn bg-warn/40",
              n.kind === "end" && "border-live bg-live",
            )}
          />
          <span className={cn("text-sm", n.kind === "stop" ? "text-muted" : "font-medium text-foreground")}>{n.label}</span>
        </div>
      ))}
    </div>
  );
}

function TripCard() {
  const drive = useDrive((s) => s.drive);
  const route = drive?.route;
  const status = drive?.route_status ?? "none";
  const tone = status === "valid" ? "live" : status === "running" ? "info" : status === "stale" ? "warn" : "neutral";
  const statusLabel =
    { valid: "route ready", running: "planning...", stale: "stale - replanning", pending: "pending", none: "no route" }[status] ?? status;
  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>Trip</SectionLabel>
        <Badge tone={tone as never}>{statusLabel}</Badge>
      </div>
      <p className="mt-2 text-xs text-muted">
        You're in <span className="font-medium text-foreground">{drive?.origin.label ?? "..."}</span>
      </p>
      {route ? (
        <>
          <h3 className="display mt-3 text-2xl leading-tight">{route.destination.label}</h3>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted">
            <span>{route.distance_km} km</span>
            <span>{duration(route.eta_min)}</span>
            {route.via ? <span>via {route.via}</span> : null}
            {route.avoid_highways ? <span>no highways</span> : null}
          </div>
          <RouteLine route={route} />
        </>
      ) : (
        <p className="mt-4 text-sm text-muted">
          {drive?.destination ? `Planning the route to ${drive.destination.label}...` : "No destination yet. Say where you're going."}
        </p>
      )}
      <div className="mt-4 flex items-center gap-2 border-t border-line/60 pt-3 text-xs">
        <Navigation size={13} className={drive?.navigation ? "text-live" : "text-muted"} />
        {drive?.navigation ? (
          <span>
            Guidance active to <span className="font-medium">{drive.navigation.destination}</span>
            <span className="ml-1 font-mono text-[10.5px] text-muted">({drive.navigation.action_id})</span>
          </span>
        ) : (
          <span className="text-muted">Guidance off</span>
        )}
      </div>
      {drive && Object.keys(drive.saved).length ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {Object.entries(drive.saved).map(([label, place]) => (
            <Badge key={label} tone="info">
              {label}: {place}
            </Badge>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}

function Metrics() {
  const m = useDrive((s) => s.metrics);
  const fmt = (v: number | null) => (v === null ? "--" : v < 1 ? "<1 ms" : `${Math.round(v)} ms`);
  const tiles = [
    { label: "First response", value: fmt(m.ack), note: "heard -> spoken acknowledgement" },
    { label: "Planned early", value: fmt(m.speculationSaved), note: "route work started before you finished" },
    { label: "Stops talking", value: fmt(m.voiceYield), note: "cut-in -> speech stopped" },
    { label: "Resumed", value: String(m.recovered), note: `after ${m.interruptions} interruption(s)` },
  ];
  return (
    <div className="grid grid-cols-2 gap-2">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-[var(--radius-item)] border border-line/60 bg-subtle/40 p-3">
          <div className="eyebrow">{t.label}</div>
          <div className="mt-1 font-mono text-lg">{t.value}</div>
          <div className="text-[10.5px] leading-tight text-muted">{t.note}</div>
        </div>
      ))}
    </div>
  );
}

const LOG_TONE: Record<string, string> = {
  correction: "text-accent", stale: "text-warn", late_discarded: "text-warn", nav_started: "text-live",
  rerouted: "text-live", route_updated: "text-live", nav_duplicate: "text-info", cancelled: "text-danger",
  spec: "text-info", checkpoint: "text-accent", interrupted: "text-accent", saved: "text-info", tool: "text-muted",
};

function ActivityLog() {
  const log = useDrive((s) => s.log);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" }); // may return a Promise: never the cleanup
  }, [log.length]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto pr-1" aria-label="What the agent did">
      {log.length === 0 ? <p className="text-xs text-muted">Nothing yet.</p> : null}
      {log.map((e) => (
        <div key={e.id} className="border-b border-line/40 py-1.5 text-xs leading-snug">
          <span className={cn("mr-1.5 font-mono text-[10px] uppercase", LOG_TONE[e.kind] ?? "text-muted")}>{e.kind.replace("_", " ")}</span>
          <span>{e.text}</span>
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}

function Conversation() {
  const { messages, live, ack, interim, speaking } = useDrive();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" }); // may return a Promise: never the cleanup
  }, [messages.length, live?.text, interim]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" aria-label="Conversation">
      {messages.length === 0 && !live ? (
        <div className="mx-auto mt-10 max-w-md text-center">
          <Car className="mx-auto text-muted" size={28} />
          <h3 className="display mt-3 text-2xl">Where are we going?</h3>
          <p className="mt-2 text-sm text-muted">
            Any city or town in India. Change your mind mid-sentence, add stops, ask how long it'll take - and cut in
            whenever you like.
          </p>
        </div>
      ) : null}
      <div className="flex flex-col gap-3">
        {messages.map((m) => (
          <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                m.role === "user" ? "bg-accent text-accent-foreground" : "border border-line/60 bg-subtle/50",
              )}
            >
              {m.text}
              {m.status !== "complete" ? (
                <Badge tone={m.status === "interrupted" ? "accent" : "live"} className="ml-2 align-middle">
                  {m.status === "interrupted" ? "cut short" : "resumed"}
                </Badge>
              ) : null}
            </div>
          </div>
        ))}
        {ack && !live ? <p className="text-xs italic text-muted" data-testid="ack">{ack}</p> : null}
        {live ? (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl border border-live/40 bg-live-soft/40 px-4 py-2.5 text-sm" data-testid="live">
              {live.text}
              <span className="ml-1 animate-pulse">|</span>
            </div>
          </div>
        ) : null}
        {interim ? (
          <p className="self-end text-sm italic text-muted" data-testid="interim">
            {interim}...
          </p>
        ) : null}
        {speaking ? <p className="text-[11px] text-live">speaking - just talk to cut in</p> : null}
      </div>
      <div ref={end} />
    </div>
  );
}

export function DriveView() {
  const { connect, disconnect, status, listening, toggleListening, voiceError, voiceOut, setVoiceOut, say, runScript, scriptRunning, stopScript, bargeIn, speaking, stageDetail } =
    useDrive();
  const { user, logout } = useAuth();
  const [draft, setDraft] = useState("");

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <Panel className="shrink-0">
        <header className="flex flex-wrap items-center gap-3 px-5 py-3">
          <a href="#" aria-label="RouteVoice home" className="shrink-0 rounded-[10px] focus-visible:outline-2 focus-visible:outline-accent">
            <Logo size={30} />
          </a>
          <div className="min-w-0">
            <h2 className="display text-base leading-none">RouteVoice</h2>
            <p className="eyebrow mt-1">In-car voice assistant &middot; talk over it any time</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {status === "open" ? (
              <Badge tone="live">
                <Wifi size={11} /> connected
              </Badge>
            ) : (
              <Badge tone="warn">
                <WifiOff size={11} /> {status}
              </Badge>
            )}
            <ThemeToggle />
            <Button variant="ghost" size="icon" onClick={() => setVoiceOut(!voiceOut)} aria-label={voiceOut ? "Mute the agent" : "Unmute the agent"}>
              {voiceOut ? <Volume2 size={16} /> : <VolumeX size={16} />}
            </Button>
            {user ? (
              <>
                <span
                  title={user.email}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent"
                >
                  {user.name.trim().charAt(0).toUpperCase() || "?"}
                </span>
                <Button variant="ghost" size="icon" aria-label="Log out" title="Log out" onClick={() => void logout()}>
                  <LogOut size={15} />
                </Button>
              </>
            ) : null}
          </div>
        </header>
      </Panel>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[320px_minmax(0,1fr)_320px]">
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <TripCard />
          <Panel className="p-4">
            <SectionLabel>Try it</SectionLabel>
            <div className="mt-2 flex flex-col gap-1.5">
              {SCRIPTS.map((s) => (
                <button
                  key={s.id}
                  disabled={scriptRunning || status !== "open"}
                  onClick={() => runScript(s.steps)}
                  className="rounded-[var(--radius-item)] border border-line/60 px-3 py-2 text-left hover:bg-subtle disabled:opacity-40"
                  data-script={s.id}
                >
                  <div className="text-xs font-medium">{s.title}</div>
                  <div className="text-[11px] leading-snug text-muted">{s.hint}</div>
                </button>
              ))}
              {scriptRunning ? (
                <Button size="sm" variant="outline" onClick={stopScript}>
                  <Square size={12} /> Stop demo
                </Button>
              ) : null}
            </div>
          </Panel>
        </div>

        <div className="flex min-h-0 flex-col gap-3">
        <Panel className="h-[300px] shrink-0 overflow-hidden lg:h-[44%]">
          <Suspense fallback={<div className="grid h-full place-items-center text-xs text-muted">Loading the map...</div>}>
            <RouteMap />
          </Suspense>
        </Panel>
        <Panel className="min-h-[360px] flex-1 overflow-hidden">
          <div className="flex shrink-0 items-center justify-between border-b border-line/60 px-5 py-2 text-xs text-muted">
            <span className="truncate">{stageDetail || "Ready."}</span>
            {speaking ? (
              <Button size="sm" variant="primary" onClick={bargeIn}>
                Interrupt
              </Button>
            ) : null}
          </div>
          <Conversation />
          <div className="shrink-0 border-t border-line/60 px-5 py-4">
            <div className="flex items-center gap-3">
              <button
                onClick={toggleListening}
                disabled={status !== "open"}
                aria-label={listening ? "Stop listening" : "Start listening hands-free"}
                aria-pressed={listening}
                className={cn(
                  "grid h-14 w-14 shrink-0 place-items-center rounded-full transition-all disabled:opacity-40",
                  listening ? "bg-accent text-accent-foreground shadow-[0_0_0_6px_var(--accent-soft)]" : "bg-elevated text-foreground hover:bg-line",
                )}
              >
                {listening ? <Mic size={22} /> : <MicOff size={22} />}
              </button>
              <form
                className="flex min-w-0 flex-1 items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  say(draft, "text");
                  setDraft("");
                }}
              >
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={listening ? "Listening - just talk, or type here" : "Tap the mic to talk, or type: take me to Jaipur"}
                  className="min-w-0 flex-1 rounded-[var(--radius-pill)] border border-line/70 bg-transparent px-4 py-2.5 text-sm outline-none focus:border-accent"
                  aria-label="Type a request"
                />
                <Button type="submit" variant="primary" size="icon" aria-label="Send" disabled={!draft.trim() || status !== "open"}>
                  <Send size={15} />
                </Button>
              </form>
            </div>
            <p className="mt-2 text-[11px] text-muted">
              {voiceError ?? (listening ? "Hands-free: talk any time, even while it's speaking. Headphones avoid echo." : "Voice uses your browser's speech engine (Chrome or Edge).")}
            </p>
          </div>
        </Panel>
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel className="p-4">
            <SectionLabel>Responsiveness</SectionLabel>
            <div className="mt-2">
              <Metrics />
            </div>
          </Panel>
          <Panel className="min-h-[240px] flex-1 p-4">
            <SectionLabel>What the agent did</SectionLabel>
            <div className="mt-2 flex min-h-0 flex-1 flex-col">
              <ActivityLog />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
