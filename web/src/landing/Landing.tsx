import { useEffect, useRef, useState } from "react";
import {
  animate,
  motion,
  useInView,
  useMotionValue,
  useReducedMotion,
  useTransform,
  type MotionValue,
} from "motion/react";
import {
  ArrowRight,
  ChevronDown,
  Github,
  Hand,
  MapPin,
  MessageSquare,
  Mic,
  Navigation,
  ShieldCheck,
  Undo2,
  Volume2,
  Zap,
} from "lucide-react";
import { Logo } from "~/components/Logo";
import { useAuth } from "~/lib/auth";
import { cn } from "~/lib/utils";
import { Car } from "./Car";
import dashboard from "./assets/dashboard.webp";

type Go = (hash: string) => void;

const KICKER = "text-xs font-semibold uppercase tracking-[0.18em] text-accent";
const REPO = "https://github.com/lakshya-jaiswal07/rev-eng-drive";

/**
 * Public landing page. It is its own scroll container (the app shell never
 * scrolls), so every scroll animation reads from `scroller`.
 */
export function Landing({ go }: { go: Go }) {
  const scroller = useRef<HTMLDivElement | null>(null);
  // Children subscribe to scrolling in their own effects, which run before this
  // element's ref is set; rendering them only once it exists keeps them from
  // silently falling back to the window, which never scrolls here.
  const [mounted, setMounted] = useState(false);
  const attach = (node: HTMLDivElement | null) => {
    scroller.current = node;
    if (node && !mounted) setMounted(true);
  };
  const [scrolled, setScrolled] = useState(false);

  return (
    <div
      ref={attach}
      onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 24)}
      className="relative h-full overflow-y-auto overflow-x-hidden scroll-smooth"
    >
      <Nav go={go} scrolled={scrolled} />
      {mounted ? (
        <>
          <Drive scroller={scroller} go={go} />
          <Features />
          <HowItWorks scroller={scroller} />
          <Questions />
          <Numbers />
          <Closing go={go} />
        </>
      ) : null}
    </div>
  );
}

/**
 * How far `target` has travelled through the scroll container, 0 to 1: 0 when
 * its top reaches `startAt` (a fraction of the container's height from the
 * top), 1 when its bottom reaches `endAt`. With (0, 1) this is the classic
 * pinned-section progress: top at the top, bottom at the bottom.
 */
function useScrollProgress(
  scroller: React.RefObject<HTMLDivElement | null>,
  target: React.RefObject<HTMLElement | null>,
  startAt: number,
  endAt: number,
): MotionValue<number> {
  const progress = useMotionValue(0);
  useEffect(() => {
    const box = scroller.current;
    const el = target.current;
    if (!box || !el) return;
    const update = () => {
      const view = box.clientHeight;
      const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top;
      const from = startAt * view;
      const to = endAt * view - el.offsetHeight;
      const span = from - to;
      progress.set(span > 0 ? Math.min(1, Math.max(0, (from - top) / span)) : 0);
    };
    update();
    box.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      box.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [scroller, target, startAt, endAt, progress]);
  return progress;
}

/* ── navigation ───────────────────────────────────────────────────────────── */

function Nav({ go, scrolled }: { go: Go; scrolled: boolean }) {
  const { status } = useAuth();
  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-40 transition-colors duration-300",
        scrolled ? "border-b border-line/60 bg-background/75 backdrop-blur-md" : "border-b border-transparent",
      )}
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      <nav
        aria-label="Main"
        className="mx-auto flex h-16 max-w-6xl items-center gap-6"
        style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}
      >
        <a href="#" className="flex items-center gap-2.5" aria-label="RouteVoice home">
          <Logo size={30} />
          <span className="display text-xl leading-none">RouteVoice</span>
        </a>
        <div className="hidden items-center gap-5 text-sm text-muted md:flex">
          <a className="hover:text-foreground" href="#features">What it does</a>
          <a className="hover:text-foreground" href="#how">How it works</a>
          <a className="hover:text-foreground" href="#questions">Maps vs RouteVoice</a>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {status === "user" ? (
            <button onClick={() => go("drive")} className={pill("solid")}>
              Open RouteVoice <ArrowRight size={15} />
            </button>
          ) : (
            <>
              <button onClick={() => go("login")} className={pill("ghost")}>Log in</button>
              <button onClick={() => go("signup")} className={pill("solid")}>Sign up</button>
            </>
          )}
        </div>
      </nav>
    </header>
  );
}

function pill(kind: "solid" | "ghost" | "outline", big = false) {
  return cn(
    "inline-flex items-center justify-center gap-2 rounded-[var(--radius-pill)] font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    big ? "h-12 px-6 text-[15px]" : "h-9 px-4 text-sm",
    kind === "solid" && "bg-accent text-accent-foreground shadow-[var(--shadow-soft)] hover:brightness-110",
    kind === "ghost" && "text-foreground hover:bg-subtle",
    kind === "outline" && "border border-line text-foreground hover:bg-subtle",
  );
}

/* ── the drive: car crosses the screen, then the dashboard rises ──────────── */

const SAYS: { from: number; to: number; who: "driver" | "agent"; text: string; struck?: string }[] = [
  // one bubble at a time: each fades out before the next fades in
  { from: 0.04, to: 0.18, who: "driver", text: "Take me to, um, Agra…" },
  { from: 0.19, to: 0.32, who: "driver", text: "…actually no, Jaipur.", struck: "Agra" },
  { from: 0.33, to: 0.5, who: "agent", text: "Got it, Jaipur. 292 km, about 5 hours via Alwar." },
];

function Drive({ scroller, go }: { scroller: React.RefObject<HTMLDivElement | null>; go: Go }) {
  const track = useRef<HTMLDivElement>(null);
  const still = useReducedMotion() ?? false;
  const p = useScrollProgress(scroller, track, 0, 1);

  // the car crosses from just off-screen left (one car-length, whatever the
  // screen size) to just off-screen right
  const carX = useTransform(p, (v) => {
    if (still) return "35vw";
    const t = Math.min(1, v / 0.62);
    return `calc(${(-105 * (1 - t)).toFixed(2)}% + ${(t * 104).toFixed(2)}vw)`;
  });
  const wheelTurn = useTransform(p, [0, 0.62], still ? [0, 0] : [0, 1440]);
  const bob = useTransform(p, (v) => (still ? 0 : Math.sin(v * 90) * 1.5));
  const skylineX = useTransform(p, [0, 1], still ? ["0%", "0%"] : ["0%", "-18%"]);
  const laneX = useTransform(p, [0, 1], still ? ["0px", "0px"] : ["0px", "-900px"]);
  const headline = useTransform(p, [0, 0.12, 0.5, 0.6], [1, 1, 1, 0]);
  const headlineY = useTransform(p, [0, 0.6], still ? [0, 0] : [0, -60]);
  const dim = useTransform(p, [0.55, 0.75], still ? [0, 0] : [0, 0.82]);
  const dashY = useTransform(p, [0.58, 0.86], still ? ["0vh", "0vh"] : ["70vh", "0vh"]);
  const dashTilt = useTransform(p, [0.58, 0.86], still ? [0, 0] : [38, 0]);
  const dashScale = useTransform(p, [0.58, 0.86], still ? [1, 1] : [0.82, 1]);
  // without motion the dashboard is not layered over the scene; it follows it (below)
  const dashOpacity = useTransform(p, [0.56, 0.7], still ? [0, 0] : [0, 1]);
  const hint = useTransform(p, [0, 0.06], [1, 0]);

  return (
    <section ref={track} aria-label="RouteVoice: a car voice assistant you can talk over" className={still ? "relative" : "relative h-[420vh]"}>
      <div className="sticky top-0 h-[100svh] overflow-hidden">
        {/* night sky and city */}
        <div aria-hidden className="absolute inset-0 bg-[linear-gradient(180deg,oklch(0.14_0.03_265)_0%,oklch(0.1_0.012_270)_55%,var(--background)_100%)]" />
        <Stars />
        <motion.div aria-hidden style={{ x: skylineX }} className="absolute inset-x-[-10%] bottom-[30%] h-[26%]">
          <Skyline />
        </motion.div>

        {/* road */}
        <div aria-hidden className="absolute inset-x-0 bottom-0 h-[30%] bg-[oklch(0.13_0.004_270)]">
          <div className="absolute inset-x-0 top-0 h-[3px] bg-[oklch(0.3_0.01_270)]" />
          <motion.div
            className="absolute inset-x-[-50%] top-[46%] h-[6px] opacity-70"
            style={{
              x: laneX,
              backgroundImage: "repeating-linear-gradient(90deg, oklch(0.88 0.12 90) 0 70px, transparent 70px 140px)",
            }}
          />
          <div className="absolute inset-x-0 bottom-[14%] h-[2px] bg-[oklch(0.3_0.01_270)]" />
        </div>

        {/* headline */}
        <motion.div
          style={{ opacity: headline, y: headlineY }}
          className="absolute inset-x-0 top-[17%] mx-auto max-w-4xl text-center"
        >
          <div style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}>
            <p className={KICKER}>Reverse-engineered from Google Maps voice navigation</p>
            <h1 className="display mt-4 text-[clamp(2.6rem,7.5vw,5.6rem)] leading-[0.95] [text-wrap:balance]">
              Talk over your car. <span className="text-accent">It keeps up.</span>
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-muted sm:text-base">
              Change your mind mid-sentence, cut in while it speaks, ask twice. RouteVoice plans the route you meant and starts
              navigation exactly once.
            </p>
          </div>
        </motion.div>

        {/* the car and what is being said */}
        {/* on phones the bubbles stay mid-screen; wider screens let them ride with the car */}
        <div className="absolute inset-x-4 bottom-[38%] flex justify-center sm:hidden">
          {SAYS.map((s) => (
            <Bubble key={s.text} p={p} {...s} still={still} centered />
          ))}
        </div>
        <motion.div style={{ x: carX, y: bob }} className="absolute bottom-[19%] left-0 w-[clamp(200px,34vw,420px)]">
          <div className="hidden sm:block">
            {SAYS.map((s) => (
              <Bubble key={s.text} p={p} {...s} still={still} />
            ))}
          </div>
          <Car wheelTurn={wheelTurn} className="w-full drop-shadow-[0_18px_30px_rgba(0,0,0,0.5)]" />
        </motion.div>

        <motion.div style={{ opacity: hint }} className="absolute inset-x-0 bottom-6 flex flex-col items-center gap-1 text-xs text-muted">
          <span>Scroll to drive</span>
          <ChevronDown size={16} className={still ? "" : "animate-bounce"} />
        </motion.div>

        {/* dashboard */}
        <motion.div aria-hidden style={{ opacity: dim }} className="pointer-events-none absolute inset-0 bg-background" />
        <motion.div
          style={{ y: dashY, rotateX: dashTilt, scale: dashScale, opacity: dashOpacity, transformPerspective: 1400 }}
          className="absolute inset-0 flex items-center justify-center pt-16"
        >
          {still ? null : <Dashboard go={go} />}
        </motion.div>
      </div>
      {still ? (
        <div className="flex justify-center py-20">
          <Dashboard go={go} />
        </div>
      ) : null}
    </section>
  );
}

function Bubble({
  p,
  from,
  to,
  who,
  text,
  struck,
  still,
  centered = false,
}: (typeof SAYS)[number] & { p: MotionValue<number>; still: boolean; centered?: boolean }) {
  const opacity = useTransform(p, [from, from + 0.04, to - 0.04, to], still ? [1, 1, 1, 1] : [0, 1, 1, 0]);
  const y = useTransform(p, [from, from + 0.05], still ? [0, 0] : [12, 0]);
  const agent = who === "agent";
  if (still && who === "driver") return null; // without motion, the reply alone tells the story
  return (
    <motion.div
      style={{ opacity, y }}
      className={cn(
        "absolute w-max max-w-[min(78vw,340px)] rounded-2xl px-4 py-2.5 text-[13px] leading-snug shadow-[var(--shadow-lift)] sm:text-sm",
        centered ? "bottom-0" : cn("bottom-[86%] rounded-bl-sm", agent ? "left-[2%]" : "left-[8%]"),
        agent ? "bg-accent text-accent-foreground" : "border border-line bg-elevated text-foreground",
      )}
    >
      <span className="mb-0.5 block text-[10px] font-semibold uppercase tracking-[0.12em] opacity-70">{agent ? "RouteVoice" : "Driver"}</span>
      {text}
      {struck ? <span className="ml-2 text-xs text-danger line-through">{struck}</span> : null}
    </motion.div>
  );
}

function Stars() {
  // fixed positions, so the sky looks the same on every visit
  const stars = Array.from({ length: 42 }, (_, i) => ({
    x: (i * 37.7) % 100,
    y: (i * 53.3) % 62,
    r: i % 5 === 0 ? 1.6 : 1,
    o: 0.25 + ((i * 7) % 10) / 20,
  }));
  return (
    <svg aria-hidden className="absolute inset-0 h-full w-full" preserveAspectRatio="none">
      {stars.map((s, i) => (
        <circle key={i} cx={`${s.x}%`} cy={`${s.y}%`} r={s.r} fill="white" opacity={s.o} />
      ))}
    </svg>
  );
}

function Skyline() {
  // a skyline loosely after a north Indian city at night: towers, a dome, a minaret
  return (
    <svg viewBox="0 0 1200 200" preserveAspectRatio="none" className="h-full w-full">
      <path
        fill="oklch(0.17 0.012 268)"
        d="M0 200 V120 h40 v-30 h30 v50 h25 V70 h45 v130 h20 V100 h30 V60 h12 V40 h6 v20 h12 v140 h30 V110 c0-30 60-30 60 0 V200 h20 V80 h40 v120 h25 V95 h35 v-25 h30 v130 h20 V55 h8 V30 h4 v25 h8 v145 h30 V120 h50 V85 h40 v115 h25 V100 h30 c0-28 56-28 56 0 v100 h20 V65 h45 v135 h30 V110 h40 V75 h35 v125 h40 V95 h40 v105 h35 V120 h40 V200 Z"
      />
      {Array.from({ length: 60 }, (_, i) => (
        <rect key={i} x={20 + ((i * 97) % 1160)} y={90 + ((i * 41) % 90)} width="4" height="5" fill="oklch(0.85 0.12 85)" opacity={0.35 + (i % 3) * 0.2} />
      ))}
    </svg>
  );
}

/* ── the dashboard: a car display with the login next to it ───────────────── */

function Dashboard({ go }: { go: Go }) {
  const { status, user } = useAuth();
  return (
    <div
      className="grid w-full max-w-6xl items-center gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]"
      style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}
    >
      {/* the infotainment screen */}
      <div className="relative hidden rounded-[26px] border border-line/80 bg-[oklch(0.07_0.003_270)] p-3 shadow-[0_40px_120px_-20px_rgba(0,0,0,0.8)] sm:block">
        <div className="absolute left-1/2 top-1.5 h-1 w-16 -translate-x-1/2 rounded-full bg-line" />
        <img
          src={dashboard}
          alt="RouteVoice planning a trip from Delhi to Jaipur with coffee and fuel stops, after the driver corrected Agra to Jaipur mid-sentence"
          className="w-full rounded-[18px]"
          width={1600}
          height={1000}
          loading="lazy"
        />
        <Chip className="left-[-10px] top-[18%]" icon={<Hand size={13} />} label="Stops talking" value="0.19 ms" />
        <Chip className="right-[-10px] top-[44%]" icon={<Zap size={13} />} label="Planned early" value="266 ms" />
        <Chip className="bottom-[10%] left-[22%]" icon={<Navigation size={13} />} label="Navigation started" value="once" />
      </div>

      {/* login */}
      <div className="rounded-[var(--radius-panel)] border border-line/80 bg-panel/95 p-7 shadow-[var(--shadow-lift)] backdrop-blur">
        <p className={KICKER}>Your co-driver is ready</p>
        <h2 className="display mt-3 text-[2.1rem] leading-[1.02] [text-wrap:balance]">
          {status === "user" ? `Welcome back, ${user?.name.split(" ")[0] ?? "driver"}.` : "Where are we going today?"}
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Plan a trip to any city or town in India by voice. Correct yourself, add stops, cut in whenever you like.
        </p>
        <div className="mt-6 flex flex-col gap-2.5">
          {status === "user" ? (
            <button onClick={() => go("drive")} className={pill("solid", true)}>
              Start driving <ArrowRight size={16} />
            </button>
          ) : (
            <>
              <button onClick={() => go("signup")} className={pill("solid", true)}>
                Create a free account <ArrowRight size={16} />
              </button>
              <button onClick={() => go("login")} className={pill("outline", true)}>
                I already have an account
              </button>
            </>
          )}
        </div>
        <ul className="mt-6 grid gap-2 border-t border-line/70 pt-5 text-[13px] text-muted">
          <li className="flex items-center gap-2"><Mic size={14} className="text-accent" /> Works hands-free in Chrome or Edge</li>
          <li className="flex items-center gap-2"><MapPin size={14} className="text-accent" /> Every Indian town of 1,000+ people</li>
          <li className="flex items-center gap-2"><ShieldCheck size={14} className="text-accent" /> No API keys, no tracking, free</li>
        </ul>
      </div>
    </div>
  );
}

function Chip({ className, icon, label, value }: { className: string; icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className={cn("absolute flex items-center gap-2.5 rounded-xl border border-line bg-elevated/95 px-3 py-2 shadow-[var(--shadow-lift)] backdrop-blur", className)}>
      <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent">{icon}</span>
      <span className="leading-tight">
        <span className="block text-[10px] uppercase tracking-[0.1em] text-muted">{label}</span>
        <span className="font-mono text-sm font-semibold">{value}</span>
      </span>
    </div>
  );
}

/* ── information sections ─────────────────────────────────────────────────── */

function Section({ id, eyebrow, title, intro, children }: { id: string; eyebrow: string; title: string; intro?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="relative scroll-mt-20 border-t border-line/50 bg-background">
      <div
        className="mx-auto max-w-6xl py-24"
        style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}
      >
        <Reveal>
          <p className={KICKER}>{eyebrow}</p>
          <h2 className="display mt-3 max-w-3xl text-[clamp(2rem,4.6vw,3.3rem)] leading-[1.02] [text-wrap:balance]">{title}</h2>
          {intro ? <p className="mt-4 max-w-2xl leading-relaxed text-muted">{intro}</p> : null}
        </Reveal>
        <div className="mt-12">{children}</div>
      </div>
    </section>
  );
}

function Reveal({ children, delay = 0, className }: { children: React.ReactNode; delay?: number; className?: string }) {
  const still = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={still ? false : { opacity: 0, y: 28 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.25 }}
      transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}

const FEATURES = [
  { icon: Undo2, title: "Acts on what you meant", body: "Fillers, false starts and “actually / no wait / not X, Y” are understood. Only the final destination is used.", example: "“Agra… actually no, Jaipur” → Jaipur" },
  { icon: Hand, title: "Stops the instant you cut in", body: "Talk over it and the voice stops at once. “Go on” continues from the next word you hadn't heard, not from the top.", example: "0.19 ms to stop, server side" },
  { icon: ShieldCheck, title: "Never stale, never twice", body: "A route built on a destination you've since changed is thrown away. Starting navigation can only happen once.", example: "Ask twice → one start" },
  { icon: Zap, title: "Starts before you finish", body: "Route planning begins on partial speech, so the answer is ready sooner. A wrong early guess is dropped at no cost.", example: "266 ms head start" },
];

function Features() {
  return (
    <Section
      id="features"
      eyebrow="What it does"
      title="Same feature as Google Maps' voice trip setup. Four behaviours changed."
      intro="We studied how Maps' assistant handles a trip by voice, rebuilt that feature from scratch, and changed how it copes with the way people actually talk while driving."
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {FEATURES.map((f, i) => (
          <Reveal key={f.title} delay={i * 0.08}>
            <article className="group h-full rounded-[var(--radius-card)] border border-line/70 bg-panel p-6 transition-colors hover:border-accent/50">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent-soft text-accent transition-transform group-hover:-rotate-6">
                <f.icon size={20} />
              </span>
              <h3 className="mt-5 text-lg font-semibold">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{f.body}</p>
              <p className="mt-4 inline-block rounded-md bg-subtle px-2.5 py-1 font-mono text-xs text-foreground/80">{f.example}</p>
            </article>
          </Reveal>
        ))}
      </div>
    </Section>
  );
}

const STEPS = [
  { icon: Mic, title: "You speak", body: "The browser's own recogniser streams partial words as well as the final sentence." },
  { icon: MessageSquare, title: "It understands", body: "Fillers, false starts and corrections are resolved; only the final intent is kept." },
  { icon: ShieldCheck, title: "Facts, not guesses", body: "Destination, origin and stops are stored as facts. A route built on an old fact is thrown away." },
  { icon: Navigation, title: "It plans and commits once", body: "Routes cover every Indian town of 1,000+ people. Guidance starts at most once." },
  { icon: Volume2, title: "It speaks, and stops", body: "Replies are spoken sentence by sentence; a cut-in stops the voice and remembers what you heard." },
];

function HowItWorks({ scroller }: { scroller: React.RefObject<HTMLDivElement | null> }) {
  const list = useRef<HTMLOListElement>(null);
  const line = useScrollProgress(scroller, list, 0.75, 0.55);
  return (
    <Section id="how" eyebrow="How it works" title="From your voice to the road, in five steps" intro="Everything is built from scratch in Python and React. No Google code, APIs or SDKs.">
      <ol ref={list} className="relative grid gap-10 pl-14">
        <div aria-hidden className="absolute bottom-2 left-[19px] top-2 w-[2px] bg-line/60" />
        <motion.div aria-hidden style={{ scaleY: line }} className="absolute bottom-2 left-[19px] top-2 w-[2px] origin-top bg-accent" />
        {STEPS.map((s, i) => (
          <Reveal key={s.title} delay={0.05}>
            <li className="relative">
              <span className="absolute -left-14 flex h-10 w-10 items-center justify-center rounded-full border border-line bg-panel text-accent">
                <s.icon size={18} />
              </span>
              <p className="font-mono text-xs text-muted">Step {i + 1}</p>
              <h3 className="mt-1 text-lg font-semibold">{s.title}</h3>
              <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-muted">{s.body}</p>
            </li>
          </Reveal>
        ))}
      </ol>
    </Section>
  );
}

const QUESTIONS = [
  { q: "“Take me to Agra… actually no, Jaipur.”", tests: "A correction inside one sentence", ours: "Acts on Jaipur only" },
  { q: "“How long will it take?” … “Hold on.” … “Go on.”", tests: "Talking over the reply, then resuming", ours: "Stops at once; continues, never restarts" },
  { q: "“Add a fuel stop on the way.”", tests: "Changing a trip that has started", ours: "Added by voice; guidance moves once" },
  { q: "“Navigate to Jaipur.” × 2", tests: "Repeating a request with a side effect", ours: "Navigation starts once" },
];

function Questions() {
  return (
    <Section
      id="questions"
      eyebrow="How we tested it"
      title="Four questions, asked to Google Maps and to RouteVoice"
      intro="Each question breaks strict turn-taking in one way. We asked both apps the same words and compared what they did."
    >
      <div className="grid gap-4 md:grid-cols-2">
        {QUESTIONS.map((x, i) => (
          <Reveal key={x.q} delay={i * 0.08}>
            <article className="h-full rounded-[var(--radius-card)] border border-line/70 bg-panel p-6">
              <p className="font-mono text-xs text-accent">Q{i + 1}</p>
              <p className="display mt-2 text-2xl leading-snug">{x.q}</p>
              <p className="mt-3 text-sm text-muted">{x.tests}</p>
              <p className="mt-4 flex items-center gap-2 border-t border-line/60 pt-4 text-sm">
                <span className="rounded-md bg-live-soft px-2 py-0.5 text-xs font-semibold text-live">RouteVoice</span>
                {x.ours}
              </p>
            </article>
          </Reveal>
        ))}
      </div>
    </Section>
  );
}

function Numbers() {
  const stats = [
    { value: 0.19, decimals: 2, unit: " ms", label: "to stop speaking after a cut-in (server side)" },
    { value: 266, decimals: 0, unit: " ms", label: "head start: planning begins before you finish" },
    { value: 443, decimals: 0, unit: "", label: "automated tests, run on every push" },
    { value: 7, decimals: 0, unit: " / 7", label: "scenarios pass in all five runs" },
  ];
  return (
    <section className="border-t border-line/50 bg-[linear-gradient(180deg,var(--background),oklch(0.11_0.02_34))]">
      <div
        className="mx-auto grid max-w-6xl gap-10 py-20 sm:grid-cols-2 lg:grid-cols-4"
        style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}
      >
        {stats.map((s) => (
          <div key={s.label}>
            <p className="display text-[clamp(2.6rem,5vw,3.6rem)] leading-none text-accent">
              <CountUp to={s.value} decimals={s.decimals} />
              <span className="text-foreground/80">{s.unit}</span>
            </p>
            <p className="mt-3 max-w-[22ch] text-sm leading-snug text-muted">{s.label}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function CountUp({ to, decimals }: { to: number; decimals: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const seen = useInView(ref, { once: true, amount: 0.6 });
  const still = useReducedMotion();
  const [shown, setShown] = useState(still ? to : 0);
  useEffect(() => {
    if (!seen || still) return;
    const controls = animate(0, to, { duration: 1.4, ease: [0.22, 1, 0.36, 1], onUpdate: setShown });
    return () => controls.stop();
  }, [seen, still, to]);
  return <span ref={ref} className="tabular-nums">{shown.toFixed(decimals)}</span>;
}

function Closing({ go }: { go: Go }) {
  const { status } = useAuth();
  return (
    <footer className="border-t border-line/50 bg-background">
      <div
        className="mx-auto max-w-6xl py-24"
        style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}
      >
        <Reveal className="text-center">
          <h2 className="display mx-auto max-w-2xl text-[clamp(2.2rem,5vw,3.6rem)] leading-[1.02] [text-wrap:balance]">
            Say where you're going. <span className="text-accent">Change your mind.</span> It keeps up.
          </h2>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <button onClick={() => go(status === "user" ? "drive" : "signup")} className={pill("solid", true)}>
              {status === "user" ? "Start driving" : "Create a free account"} <ArrowRight size={16} />
            </button>
            <a href={REPO} target="_blank" rel="noreferrer" className={pill("outline", true)}>
              <Github size={16} /> Code on GitHub
            </a>
          </div>
        </Reveal>
        <div className="mt-20 flex flex-col gap-6 border-t border-line/60 pt-8 text-sm text-muted md:flex-row md:items-start md:justify-between">
          <div className="flex items-center gap-2.5">
            <Logo size={26} />
            <span>
              <span className="font-semibold text-foreground">RouteVoice</span> · ACE Club Reverse Engineering · Transportation and Mobility
            </span>
          </div>
          <div className="md:text-right">
            <p className="font-semibold text-foreground">Team MATAR PANEER</p>
            <p>Lakshya Jaiswal · P Nishant · Lucky Preetham Raj B R · Joanna Mariya James</p>
            <p>M S Ramaiah Institute of Technology</p>
          </div>
        </div>
        <p className="mt-6 text-xs text-muted/80">
          Place data: GeoNames (CC BY 4.0). Map tiles © OpenStreetMap contributors. Distances and times are estimates, not live traffic.
        </p>
      </div>
    </footer>
  );
}
