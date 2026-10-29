# RouteVoice

[![CI](https://github.com/lakshya-jaiswal07/rev-eng-drive/actions/workflows/ci.yml/badge.svg)](https://github.com/lakshya-jaiswal07/rev-eng-drive/actions/workflows/ci.yml)

**A voice assistant for setting up a car trip that you can talk over, correct mid-sentence, and that never starts navigation twice.**

Built for **ACE Club - Reverse Engineering**, domain **Transportation and Mobility**.

| | |
|---|---|
| Live app | <https://routevoice.onrender.com> (Chrome or Edge; free hosting, so the first visit after a quiet spell takes about a minute to wake up) |
| Closed-source app studied | **Google Maps**: voice trip setup with the built-in assistant (Google Assistant / Gemini in Maps) |
| Feature recreated | Hands-free trip setup by voice: say a destination, add stops, ask the ETA, start guidance |
| Built from | Scratch. No Google code, APIs or SDKs; Maps was studied only from the outside, as a user |
| Runs | No API keys, in Chrome or Edge. The server works offline; the map tiles (OpenStreetMap) and Chrome's speech recognition need internet |

**Team MATAR PANEER**, M S Ramaiah Institute of Technology

| Member |
|---|
| Lakshya Jaiswal |
| P Nishant |
| Lucky Preetham Raj B R |
| Joanna Mariya James |

How each evaluation criterion is checked: [docs/VALIDATION.md](docs/VALIDATION.md).
Latest scenario results: [docs/measurements.md](docs/measurements.md).

---

## 1. The feature we studied

In Google Maps you can set up a trip without touching the screen: say where you
want to go, add a stop, ask how long it will take, start navigation. We studied
how that conversation behaves when the person speaking does what real drivers do:
change their mind halfway through a sentence, cut in while the assistant is still
talking, or repeat themselves.

### Reverse-engineering analysis

We asked Google Maps' voice assistant and RouteVoice the same four things, in
the same words and the same order, with the screen recorder running.

| # | What we did | What Google Maps did | What RouteVoice did |
|---|---|---|---|
| 1 | Said *"Take me to Agra"*, then, while it was still answering, cut in with *"Take me to Jaipur"* | **Ignored the interruption.** It kept answering for Agra and did not act on Jaipur. | **Stopped talking and rerouted:** *"Okay, not Agra any more. Heading to Jaipur, Rajasthan: 292 kilometres, about 5 hours via Alwar."* |
| 2 | Asked *"How long will it take to get there?"*, said *"Hold on"* while it answered, then later *"Go on"* | **Ignored "hold on"** and carried on speaking. | **Paused at once and saved a checkpoint** at the exact word it was cut off. *"Go on"* resumed from that word, not from the start. |
| 3 | Said *"Add a fuel stop on the way"* | Added the stop. | Added the stop by voice, recomputed the route and moved guidance to the updated route once: *"Updated the route to Jaipur… I've added a stop for fuel near…"* |
| 4 | Asked *"Navigate to Agra"* again for the trip already under way | **Restarted navigation and dropped the fuel stop** and the other changes made along the way. | **Kept the trip:** *"We're already on the way to Agra, Uttar Pradesh, about 3 hours 56 minutes to go."* Navigation did not restart, and the fuel stop stayed. |

**What this tells us about how Maps' assistant works.** It takes strict turns:
while it is speaking it is not listening for the driver, so an interruption (a new
destination or "hold on") is simply lost. And each request is handled as a fresh
command rather than as part of an ongoing trip, so asking for the current
destination again starts a new trip from scratch and the stops added on the way
are lost. RouteVoice keeps listening while it speaks, and keeps the trip (destination,
stops, what has already been said) as state that a new request updates instead of
replacing.

*Doing test 2 yourself:* the ETA answer is one short sentence, so say "Hold on"
within the first couple of seconds of it speaking. RouteVoice resumes from the
words you had not yet heard (the browser reports how much was spoken aloud).

---

## 2. What we built

A from-scratch rebuild of voice trip setup, plus the behaviour we wanted when
someone interrupts or corrects themselves.

| Capability | How RouteVoice does it |
|---|---|
| **Understands self-corrections** | Rule-based understanding of fillers, false starts and *"actually / no wait / instead / not X, Y"*. *"Agra... actually no, Jaipur"* acts only on Jaipur. |
| **Starts before you finish** | Route planning starts from partial speech, before the sentence ends. If the guess turns out wrong it is thrown away, so it never adds delay. |
| **Discards stale routes** | Destination, origin, stops and preferences are tracked facts, and the route depends on them. Change one while a route is still being planned, and the late result is marked stale and dropped, then recomputed. |
| **Never starts guidance twice** | Starting navigation is an at-most-once commit, so asking twice starts it once. |
| **Stops when you cut in, resumes where you left off** | Talking over it stops its voice at once (its own echo is ignored). The browser reports how many characters you actually heard, and "go on" continues from exactly there instead of restarting. |
| **Never silent, never premature** | Every request gets a spoken acknowledgement right away ("Checking the route to Jaipur..."), never a premature "done". You can ask questions while a route is still planning. |
| **Made for India** | Every Indian city and town of 1,000+ people (GeoNames), with old and alternate names (Bombay, Poona, Bangalore, Cochin) and near-miss spellings. Saved places: *"my office is in Noida"*, then *"take me to the office"*. |

The screen shows these as live numbers: **First response** (heard -> spoken
acknowledgement), **Planned early** (route work started before you finished),
**Stops talking** (cut-in -> speech stopped) and **Resumed** (after how many
interruptions). Use them for the before/after comparison.

### Before / after

| Measure | Google Maps (observed) | RouteVoice (measured) |
|---|---|---|
| New destination spoken over the answer | ignored | acted on: rerouted to Jaipur |
| "Hold on" while it speaks | ignored, kept talking | stops at once (0.19 ms server side; the "Stops talking" counter shows the browser's time) |
| "Go on" afterwards | nothing to resume | resumes from the exact word it was cut off |
| Asking for the current destination again | restarts navigation, drops the fuel stop | keeps the trip and the stop; navigation not restarted |

---

## 3. How it works

```mermaid
flowchart LR
    M[mic: browser SpeechRecognition<br/>interim + final] -->|partial| U[understanding<br/>disfluency + self-correction]
    M -->|final| U
    U -->|partial: speculative plan| P[route planner<br/>offline India gazetteer]
    U -->|facts: destination, stops, prefs| B{BACKSPACE}
    B -->|stale -> recompute, late result dropped| P
    P --> B
    B -->|at-most-once commit| N[start guidance]
    U --> R[reply] --> S[speaker: browser speechSynthesis<br/>sentence by sentence]
    M -. cut-in: stop + heard offset .-> S
```

| Part | Where |
|---|---|
| Understanding (fillers, corrections, stops, saved places, questions) | `server/app/drive/understanding.py` |
| Places and routes (gazetteer, distance and time estimates, "via" towns) | `server/app/drive/geo.py`, `server/app/drive/stops.py` |
| Turn runtime (acknowledge, plan in the background, interrupt, resume) | `server/app/drive/runtime.py` |
| BACKSPACE: facts, dependent work, stale detection, at-most-once commit | `server/app/backspace/` |
| Websocket `/ws/drive` and the web server | `server/app/main.py` |
| UI: mic, speaker, barge-in, trip card, map | `web/src/drive/` |

Stack: Python 3.11+, FastAPI, asyncio · React, Vite, Tailwind, Leaflet ·
speech in and out through the browser's own engines (Web Speech API).

---

## 4. Run it

No keys and nothing to pay for. You need Python 3.11+ and Node 20+.

```bash
make setup   # backend venv + frontend packages
make api     # backend on :8000
make web     # frontend on :5174 (second terminal)
```

<details>
<summary>Without make (Windows PowerShell)</summary>

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

```powershell
cd web
npm install
npm run dev
```
</details>

Open <http://localhost:5174> in **Chrome or Edge**, tap the mic and allow it
once. Then try:

- *"Take me to, um, Agra... actually no, Jaipur"*
- talk over the reply, then say *"go on"*
- *"Stop for fuel and, uh, a quick coffee on the way"*
- *"How long will it take?"*

The **Try it** buttons replay scripted, disfluent requests through the same path
as the microphone: change of mind mid-sentence, switching while it is still
planning, adding stops, cutting in then "go on", asking twice and starting once,
asking while it plans.

**Docker** (one port, frontend served by the backend):

```bash
docker build -t routevoice .
docker run --rm -p 8000:8000 routevoice
```

Open <http://localhost:8000>.

**Settings** (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `DRIVE_START_CITY` | Delhi | where the car is (or say *"I'm in Pune"*) |
| `DRIVE_ROUTE_LATENCY_MS` | 1200 | simulated routing-service delay, so the background planning is visible |
| `DRIVE_TOKEN_DELAY_MS` | 70 | pace of the streamed reply |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | unset | optional: an OpenAI-compatible model (e.g. Groq) for questions that are not about driving. Without one the assistant says *"I'm your driving assistant"*. |
| `AUTH_REQUIRED` | 1 | the assistant needs an account; `0` opens it for local development |
| `AUTH_DB_PATH` | `server/data/users.sqlite3` | where accounts are stored |

**Accounts:** the landing page at `/` is public; the assistant (`#drive`) needs an
account. Passwords are hashed with scrypt and a per-user salt; the browser holds a
random session token in an HttpOnly cookie, and the server stores only its SHA-256.
On a host without a persistent disk (Render's free plan), accounts reset whenever
the service restarts or redeploys.

**Tests:**

```bash
make test      # 459 tests: gazetteer, understanding, runtime guarantees, the /ws/drive socket, BACKSPACE, accounts
make measure   # the evaluation scenarios end to end, 5 runs each -> docs/measurements.md
```

Both run on every push (GitHub Actions). What each test proves, criterion by
criterion: [docs/VALIDATION.md](docs/VALIDATION.md).

---

## 5. Honest limits

- Places are city and town level, not street addresses.
- Distances and times are estimated from coordinates, not from live traffic or a road network.
- Stops are "fuel near <town>", not named businesses.
- The routing service's delay is simulated (`DRIVE_ROUTE_LATENCY_MS`) so the asynchronous behaviour is visible.
- No turn-by-turn guidance: this rebuilds the voice trip-setup conversation, not navigation itself.
- Chrome's speech recognition runs in Google's cloud (free, no key). Use headphones so it doesn't hear its own voice.
- Understanding is rule-based: it is predictable and offline, but it can miss phrasings it hasn't seen.

---

## Credits

- Place data: [GeoNames](https://www.geonames.org), CC BY 4.0 (`server/app/drive/data/`).
- Map tiles in the UI: OpenStreetMap contributors, via Leaflet.
- License: MIT (see `LICENSE`).
