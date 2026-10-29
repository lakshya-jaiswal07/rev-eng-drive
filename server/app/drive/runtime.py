"""The in-car voice agent loop.

One ``DriveRuntime`` per car (websocket). Its frames are
stage/token/message/filler/spec/tool/metric/checkpoint, plus
``drive`` (vehicle and route state) and ``drive_event`` (what BACKSPACE did).

What it demonstrates:

* Stay responsive: every request is acknowledged within milliseconds, without
  claiming anything is done ("Checking the route to Jaipur..."), and questions
  are answered even while a route is still being computed.
* Work asynchronously: route planning runs as background work, and starts
  speculatively from *partial* speech, before the user finishes. It is never
  awaited in a way that blocks the next utterance.
* Recover cleanly: the destination, stops and preferences are BACKSPACE facts
  and the route is a WorkItem depending on them. A change mid-flight marks
  the route stale, a late result from the stale attempt is discarded, the
  route is recomputed with the new arguments, and starting navigation goes
  through BACKSPACE's at-most-once commit guard - it never happens twice for
  the same route, and never for an abandoned destination.
* Barge-in: the user can cut in at any point; what they actually heard is
  checkpointed and "go on" resumes from there instead of restarting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from ..backspace import BackspaceCore, DependencyKind, WorkItem, WorkStatus
from ..backspace.work_lifecycle import IllegalWorkTransitionError, StaleExecutionError, complete_work, start_work
from ..schemas import (
    CheckpointFrame,
    FillerFrame,
    MessageFrame,
    MetricFrame,
    ServerFrame,
    SpecFrame,
    Stage,
    StageFrame,
    TokenFrame,
    ToolFrame,
    now_ms,
)
from ..speech_control import is_hold
from .geo import Place, Route, StopSpec, describe_duration, describe_stop, gazetteer, km_between, plan_route
from .understanding import Understanding, matches_removal, understand

_log = logging.getLogger(__name__)

# Spoken when a turn fails unexpectedly; the trip so far is kept as it was.
ERROR_REPLY = "Sorry, something went wrong on my side. Could you say that again?"
Emit = Callable[[ServerFrame], Awaitable[None]]


# -- how stops are said --------------------------------------------------------
_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _stop_phrase(spec: str) -> str:
    """One requested stop in a few words: "a fuel stop", "a snack break", "a stop at Lonavla"."""
    s = StopSpec.parse(spec)
    if s.kind == "place":
        return f"a stop at {s.label}"
    if s.kind == "custom":
        what = f"a stop for {s.label}"
    elif s.label.startswith(("a ", "an ")):
        what = s.label
    else:
        what = f"{'an' if s.label[:1].lower() in 'aeiou' else 'a'} {s.label} stop"
    return f"{what} at {s.at.name}" if s.at else what


_STOP_NOUN = {"snacks": "snack", "food": "meal", "rest": "rest", "atm": "ATM", "charging": "charging"}


def _removed_phrase(removal: str, count: int = 1) -> str:
    """What a removal key ("coffee", "@<place_id>", "custom:flowers") dropped, as words."""
    plural = "s" if count > 1 else ""
    if removal.startswith("@"):
        place = gazetteer().by_id(removal[1:])
        return f"the stop{plural} at {place.name}" if place else "that stop"
    label = removal[len("custom:"):] if removal.startswith("custom:") else _STOP_NOUN.get(removal, removal)
    return f"the {label} stop{plural}"


def _route_stop_phrase(stop: dict[str, Any]) -> str:
    if stop["kind"] == "place":
        return f"a stop at {stop['near']}"
    where = "at" if stop.get("at_named_place") else "near"
    return f"a stop for {stop['label']} {where} {stop['near']}"


def _stop_lines(stops: list[dict[str, Any]]) -> list[str]:
    """The route's stops as sentences, with repeats said once: "two stops for a snack break, near A and B"."""
    places = [s["near"] for s in stops if s["kind"] == "place"]
    lines = [f"I've added a stop at {_join(places)}."] if places else []
    groups: dict[tuple[str, bool], list[str]] = {}
    for s in stops:
        if s["kind"] != "place":
            groups.setdefault((s["label"], bool(s.get("at_named_place"))), []).append(s["near"])
    for (label, named), nears in groups.items():
        where = "at" if named else "near"
        if len(nears) == 1:
            lines.append(f"I've added a stop for {label} {where} {nears[0]}.")
        else:
            many = _COUNT_WORDS.get(len(nears), str(len(nears)))
            lines.append(f"I've added {many} stops for {label}, {where} {_join(nears)}.")
    return lines


class DriveStateFrame(ServerFrame):
    t: str = "drive"
    state: dict[str, Any] = Field(default_factory=dict)


class DriveEventFrame(ServerFrame):
    t: str = "drive_event"
    kind: str
    text: str


@dataclass
class _Speculation:
    key: tuple[Any, ...]
    task: asyncio.Task[Route]
    place: Place | None = None  # the guessed destination, shown on the map while the user speaks
    started: float = field(default_factory=now_ms)


@dataclass
class _Spoken:
    turn_id: str
    text: str


class DriveRuntime:
    def __init__(
        self,
        emit: Emit,
        *,
        route_latency_ms: int | None = None,
        token_delay_ms: int | None = None,
        start_city: str | None = None,
        provider: Any = None,
    ) -> None:
        self.emit = emit
        self.route_latency = (route_latency_ms if route_latency_ms is not None
                              else int(os.getenv("DRIVE_ROUTE_LATENCY_MS", "1200"))) / 1000.0
        self.token_delay = (token_delay_ms if token_delay_ms is not None
                            else int(os.getenv("DRIVE_TOKEN_DELAY_MS", "70"))) / 1000.0
        self.provider = provider  # optional LLM for questions outside driving
        self.core = BackspaceCore()
        gz = gazetteer()
        # The car's position (a stand-in for GPS): configurable, and the user can
        # change it by voice ("I'm in Pune").
        self.origin: Place = gz.lookup(start_city or os.getenv("DRIVE_START_CITY", "Delhi")) or gz.places[0]
        self.destination: Place | None = None
        self.stops: list[str] = []
        self.avoid_highways = False
        self.saved: dict[str, Place] = {}
        self.route_work: WorkItem | None = None
        self.route: Route | None = None
        self.navigation: dict[str, Any] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._spec: _Speculation | None = None
        self._last_spoken: _Spoken | None = None
        self._checkpoint: dict[str, Any] | None = None
        self._streaming: asyncio.Task[None] | None = None
        self._route_task: asyncio.Task[Route | None] | None = None
        self._stream_progress = 0
        self._turn = 0
        self._dropped: dict[str, int] = {}  # per removal in the current turn: how many stops it dropped
        # Every fact the route depends on exists from the start, so the first
        # change to any of them (adding a stop, avoiding highways) is a CHANGE
        # that invalidates the planned route - not a silent first observation.
        for key, value in (("origin", self.origin.place_id), ("destination", None), ("stops", ""),
                           ("avoid_highways", False)):
            self.core.assert_fact(key, value, source="system", turn_id="start")

    # -- public API (called by the websocket handler) -----------------------
    async def start(self) -> None:
        await self._state()
        await self.emit(StageFrame(stage=Stage.IDLE, detail=f"Ready. You're in {self.origin.label}. Where to?"))

    async def on_partial(self, text: str) -> None:
        """Start planning while the user is still talking (never acts on it)."""
        u = understand(text)
        target = self._resolve_destination(u)
        if target is None:
            return
        origin = self._resolve_origin(u) or self.origin
        if origin.place_id == target.place_id:
            return
        key = self._key(origin, target, self._next_stops(u), self._next_avoid(u))
        if self._spec and self._spec.key == key:
            return
        await self._discard_spec("the utterance moved on")
        await self.emit(SpecFrame(status="started", query=f"route to {target.label}"))
        self._spec = _Speculation(key, asyncio.ensure_future(self._compute(origin, target, key[2], key[3])), place=target)
        await self._state()

    async def on_final(self, text: str, modality: str = "voice") -> None:
        """A finished utterance. Supersedes whatever turn was still running."""
        text = text.strip()
        if not text:
            return
        if is_hold(text) and self._streaming is not None and not self._streaming.done():
            await self.interrupt()  # "stop" said over the agent is the barge-in itself
        await self._cancel_turn()
        self._turn += 1
        turn_id = f"drive-{self._turn}-{uuid.uuid4().hex[:4]}"
        await self.emit(MessageFrame(turn_id=turn_id, role="user", content=text, modality=modality))
        self._turn_task = asyncio.ensure_future(self._run_turn(text, turn_id))

    async def interrupt(self, heard_chars: int | None = None) -> None:
        """Barge-in. Stops speaking; keeps what was not yet heard for "go on"."""
        started = time.perf_counter()  # high resolution: now_ms() ticks in ~16 ms steps on Windows
        streaming = self._streaming is not None and not self._streaming.done()
        if streaming:
            self._streaming.cancel()
            try:
                await self._streaming
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        spoken = self._last_spoken
        if spoken is None:
            await self.emit(StageFrame(stage=Stage.IDLE, detail="Listening."))
            return
        heard = heard_chars if heard_chars is not None else (self._stream_progress if streaming else len(spoken.text))
        heard = max(0, min(heard, len(spoken.text)))
        remaining = spoken.text[heard:].strip()
        await self.emit(MetricFrame(name="time_to_yield", value=round((time.perf_counter() - started) * 1000, 2),
                                    note="barge-in -> speech stopped (server side)"))
        if remaining:
            self._checkpoint = {"turn_id": spoken.turn_id, "said": spoken.text[:heard], "remaining": remaining}
            await self.emit(CheckpointFrame(
                checkpoint_id=uuid.uuid4().hex[:8], turn_id=spoken.turn_id,
                summary=f"{len(spoken.text[:heard].split())} words heard, {len(remaining.split())} kept for 'go on'",
                kept_tokens=len(spoken.text[:heard].split()),
            ))
            await self.emit(StageFrame(stage=Stage.INTERRUPTED, turn_id=spoken.turn_id, detail="Stopped talking. Say 'go on' to hear the rest."))
        else:
            await self.emit(StageFrame(stage=Stage.IDLE, detail="Listening."))

    async def shutdown(self) -> None:
        await self._cancel_turn()
        await self._discard_spec(None)

    # -- the turn --------------------------------------------------------------
    async def _run_turn(self, text: str, turn_id: str) -> None:
        started = time.perf_counter()
        try:
            u = understand(text)
            if u.hold:
                # The barge-in already stopped the speech; keep the unheard rest for "go on".
                if self._checkpoint:
                    await self.emit(StageFrame(stage=Stage.INTERRUPTED, turn_id=turn_id,
                                               detail="Waiting. Say 'go on' to hear the rest."))
                else:
                    await self.emit(StageFrame(stage=Stage.IDLE, turn_id=turn_id, detail="Listening."))
                return
            if u.resume and self._checkpoint:
                await self._resume(turn_id)
                return
            if u.resume and not u.is_navigation:
                await self._speak(turn_id, "That was everything. Where to next?")
                await self.emit(StageFrame(stage=Stage.IDLE, turn_id=turn_id, detail="Listening."))
                return
            self._checkpoint = None  # a new request replaces an unfinished answer
            reply = await self._handle(u, turn_id, started)
            if reply:
                await self._speak(turn_id, reply)
            await self.emit(StageFrame(stage=Stage.IDLE, turn_id=turn_id, detail="Listening."))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _log.exception("drive turn %s failed", turn_id)
            # Never go silent on the driver: say so, keep the trip as it was, keep listening.
            try:
                await self._speak(turn_id, ERROR_REPLY)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _log.exception("drive turn %s: the apology itself failed", turn_id)
            await self.emit(StageFrame(stage=Stage.IDLE, turn_id=turn_id, detail=f"Recovered from an error - {type(exc).__name__}: {exc}"[:200]))

    async def _handle(self, u: Understanding, turn_id: str, started: float) -> str:
        notes: list[str] = []

        if u.save_label:
            return await self._save(u, turn_id)
        if u.greeting and not u.is_navigation:
            return ("Hi! Tell me where you're headed - any city or town in India. You can change your mind "
                    "mid-sentence, add stops like fuel or a meal, and ask how long it'll take.")
        if u.repeat and self._last_spoken:
            return self._last_spoken.text
        if u.cancel and not u.destination:
            return await self._cancel_navigation(turn_id)

        new_origin = self._resolve_origin(u)
        target = self._resolve_destination(u)
        if target is None and u.destination is None and not u.unresolved and self.destination is None:
            # "stop at Lonavala" before any trip: that town is where we're going
            named = [s for s in u.add_stops if s.startswith("place@")]
            place = StopSpec.parse(named[-1]).at if named else None
            if place is not None:
                target = place
                u.add_stops = [s for s in u.add_stops if s != named[-1]]
        if target is None and (u.destination is not None or u.unresolved):
            # The destination is unknown, but a "from" city the user gave still counts.
            moved = ""
            if new_origin and new_origin.place_id != self.origin.place_id:
                self.origin = new_origin
                await self._facts("origin", new_origin.place_id, turn_id)
                await self._state()
                moved = f"Starting from {new_origin.label}. "
            if u.destination is not None:  # "take me to the office" with no office saved
                label = u.destination.saved_label or u.destination.text
                return f"{moved}I don't know where your {label} is yet. Say 'my {label} is in' followed by the city."
            return (f"{moved}I couldn't find {u.unresolved} on my India map. Which city or town is it in, or near? "
                    "I route between cities and towns, not street addresses.")
        for gone in u.abandoned:
            if gone.place and (target is None or gone.place.place_id != target.place_id):
                await self._event("correction", f"Heard '{gone.text}', then a correction - acting only on the final destination.")

        changes = u.add_stops or u.remove_stops or u.avoid_highways is not None or new_origin or target
        if not changes and not u.is_question:
            return await self._general(u)

        if changes:
            ack = self._ack(target, u)
            await self.emit(FillerFrame(turn_id=turn_id, text=ack))
            await self.emit(MetricFrame(name="ack_latency", value=round((time.perf_counter() - started) * 1000, 2),
                                        note="request heard -> first spoken acknowledgement"))
            await self.emit(StageFrame(stage=Stage.PLANNING, turn_id=turn_id, detail=ack))

        previous_dest = self.destination
        if new_origin and new_origin.place_id != self.origin.place_id:
            self.origin = new_origin
            await self._facts("origin", new_origin.place_id, turn_id)
            notes.append(f"Starting from {new_origin.label}.")
        new_trip = False
        if target:
            if target.place_id == self.origin.place_id:
                return f"You're already in {target.label}."
            # Stops belong to a trip: a correction ("sorry, make it Ajmer") keeps
            # them, a fresh destination request starts clean.
            new_trip = (previous_dest is not None and previous_dest.place_id != target.place_id
                        and not u.corrected and bool(self.stops))
            self.destination = target
            await self._facts("destination", target.place_id, turn_id)
        stops = [s for s in u.add_stops] if new_trip else self._next_stops(u)
        if new_trip:
            notes.append("New trip, so I've cleared the earlier stops.")
        # how many stops each removal drops, so the reply can say "the snack stops"
        self._dropped = {r: sum(1 for s in self.stops if matches_removal(s, [r])) for r in u.remove_stops}
        if stops != self.stops:
            self.stops = stops
            await self._facts("stops", ",".join(sorted(stops)), turn_id)
        if u.avoid_highways is not None and u.avoid_highways != self.avoid_highways:
            self.avoid_highways = u.avoid_highways
            await self._facts("avoid_highways", self.avoid_highways, turn_id)

        if self.destination is None:
            if u.is_question:
                return "We don't have a destination yet. Where would you like to go?"
            return " ".join(notes + ["Noted. Where would you like to go?"])

        route = await self._ensure_route(turn_id)
        if route is None:  # superseded while computing: a newer turn owns the answer
            return ""
        # Guide the current route unless it is already the one being guided - even
        # when this turn is only a question: the turn that asked for the route may
        # have been superseded before it could start guidance.
        guided = self.navigation is not None and self.route_work is not None             and self.navigation.get("attempt") == self.route_work.execution_attempt
        started_nav = await self._navigate(turn_id) if (changes or not guided) else False

        if u.is_question and not (target or u.add_stops or u.remove_stops or u.avoid_highways is not None):
            return self._answer_question(u)
        changed_destination = previous_dest is not None and target is not None and previous_dest.place_id != target.place_id
        return " ".join(notes + [self._describe(route, changed_from=previous_dest if changed_destination else None,
                                                started=started_nav, u=u)])

    # -- route work (BACKSPACE) -------------------------------------------------
    async def _ensure_route(self, turn_id: str) -> Route | None:
        work = self.route_work
        if work is not None and work.status is WorkStatus.VALID and self.route is not None:
            return self.route  # nothing it depends on changed
        if work is not None and work.status is WorkStatus.RUNNING and self._route_task is not None:
            # the same request is still being planned (nothing it depends on changed): wait for it
            return await asyncio.shield(self._route_task)
        if work is None:
            work = self.route_work = self.core.register_work(WorkItem(kind="route", turn_id=turn_id))
        attempt = start_work(work)  # from PENDING, or from STALE after a change
        for key in ("origin", "destination", "stops", "avoid_highways"):
            fact = self.core.get_fact(key)
            if fact is not None:
                self.core.register_dependency(DependencyKind.FACT_TO_WORK, fact.fact_id, work.work_id)
        origin, dest = self.origin, self.destination
        assert dest is not None
        key = self._key(origin, dest, tuple(self.stops), self.avoid_highways)
        args = {"from": origin.label, "to": dest.label, "stops": list(self.stops), "avoid_highways": self.avoid_highways}
        await self.emit(ToolFrame(call_id=f"{work.work_id}:{attempt}", name="plan_route", status="requested", args=args))
        await self.emit(StageFrame(stage=Stage.TOOLING, turn_id=turn_id, detail=f"Planning the route to {dest.label}"))
        await self._state()
        task = self._route_task = asyncio.ensure_future(self._finish_route(work, attempt, origin, dest, key))
        route = await asyncio.shield(task)  # a newer turn may cancel us, never the route work itself
        await self._state()
        return route

    async def _finish_route(self, work: WorkItem, attempt: int, origin: Place, dest: Place, key: tuple[Any, ...]) -> Route | None:
        started = now_ms()
        spec = self._spec
        if spec is not None and spec.key == key:
            self._spec = None  # adopted: a later partial must not cancel work this attempt now owns
            route = await spec.task
            saved = min(self.route_latency * 1000, started - spec.started)
            await self.emit(SpecFrame(status="hit", query=f"route to {dest.label}", saved_ms=round(max(saved, 0), 1)))
            await self.emit(MetricFrame(name="speculation_saved", value=round(max(saved, 0), 1), note="route planning started before you finished speaking"))
        else:
            if spec is not None:
                await self._discard_spec("the final request differed from the partial guess")
            route = await self._compute(origin, dest, key[2], key[3])
        try:
            complete_work(work, attempt, route.to_dict())
        except (StaleExecutionError, IllegalWorkTransitionError):
            await self._event("late_discarded", f"Discarded a late route to {dest.label} - the request changed while it was being planned.")
            return None
        self.route = route
        await self.emit(ToolFrame(call_id=f"{work.work_id}:{attempt}", name="plan_route", status="ok",
                                  args={"to": dest.label}, latency_ms=round(now_ms() - started, 1)))
        return route

    async def _compute(self, origin: Place, dest: Place, stops: tuple[str, ...], avoid: bool) -> Route:
        await asyncio.sleep(self.route_latency)  # a routing service's latency; the plan itself is instant
        return plan_route(origin, dest, stops, avoid)

    async def _navigate(self, turn_id: str) -> bool:
        """Start (or re-start) guidance for the current route - at most once per route attempt."""
        work = self.route_work
        if work is None or work.status is not WorkStatus.VALID or self.route is None:
            return False
        result = self.core.commit_action(work.work_id, work.execution_attempt, {"to": self.route.destination.label})
        action_id = result.commit.action_id
        if result.already_committed:
            await self._event("nav_duplicate", f"Navigation for this route is already running (action {action_id}) - not started twice.")
            return False
        previous = self.navigation
        self.navigation = {"action_id": action_id, "destination": self.route.destination.label, "attempt": work.execution_attempt}
        await self.emit(ToolFrame(call_id=action_id, name="start_navigation", status="ok", args={"to": self.route.destination.label}))
        if previous and previous["destination"] == self.route.destination.label:
            await self._event("route_updated", f"Route to {self.route.destination.label} updated - guidance restarted on the new route (action {action_id}).")
        elif previous:
            await self._event("rerouted", f"Rerouted: guidance to {previous['destination']} replaced by {self.route.destination.label} (action {action_id}).")
        else:
            await self._event("nav_started", f"Navigation started to {self.route.destination.label} (action {action_id}, committed once).")
        await self._state()
        return True

    async def _cancel_navigation(self, turn_id: str) -> str:
        if self.navigation is None and self.destination is None:
            return "There's no trip to cancel."
        dest = self.navigation["destination"] if self.navigation else self.destination.label  # type: ignore[union-attr]
        self.navigation = None
        self.destination = None
        self.route = None
        await self._facts("destination", None, turn_id)
        await self._event("cancelled", f"Navigation to {dest} cancelled.")
        await self._state()
        return f"Okay, navigation to {dest} is cancelled."

    # -- facts and speculation --------------------------------------------------
    async def _facts(self, key: str, value: Any, turn_id: str) -> None:
        update = self.core.assert_fact(key, value, source="user", turn_id=turn_id)
        if update.changeset is None:
            return
        inv = self.core.invalidate(update.changeset)
        if self.route_work is not None and self.route_work.work_id in inv.invalidated_work_ids:
            if key == "destination" and value is None:
                await self._event("stale", "Trip cancelled, so BACKSPACE marked the planned route stale - it will not be used.")
                return
            what = {"origin": "the starting point", "destination": "the destination", "stops": "the stops",
                    "avoid_highways": "the route preference"}[key]
            await self._event("stale", f"{what.capitalize()} changed, so BACKSPACE marked the planned route stale - it will be recomputed, not reused.")

    def _key(self, origin: Place, dest: Place, stops: tuple[str, ...] | list[str], avoid: bool) -> tuple[Any, ...]:
        return (origin.place_id, dest.place_id, tuple(sorted(stops)), bool(avoid))

    def _next_stops(self, u: Understanding) -> list[str]:
        """The trip's stops after this request.

        Asking again for a stop the trip already has changes nothing ("stop for
        fuel" twice is still one fuel stop - ask twice, act once); a count asks
        for that many ("two snack stops"), and "another" adds on top.
        """
        stops = [s for s in self.stops if not matches_removal(s, u.remove_stops)]
        for spec in dict.fromkeys(u.add_stops):
            wanted = u.add_stops.count(spec)
            have = stops.count(spec)
            stops += [spec] * (wanted if spec in u.more_stops else max(0, wanted - have))
        return stops

    def _next_avoid(self, u: Understanding) -> bool:
        return self.avoid_highways if u.avoid_highways is None else u.avoid_highways

    async def _discard_spec(self, reason: str | None) -> None:
        spec, self._spec = self._spec, None
        if spec is None:
            return
        spec.task.cancel()
        if reason:
            await self.emit(SpecFrame(status="discarded", query=reason))
        await self._state()  # the map drops the guess

    def _resolve_destination(self, u: Understanding) -> Place | None:
        m = u.destination
        if m is None:
            return None
        if m.saved_label:
            return self.saved.get(m.saved_label)
        return m.place

    def _resolve_origin(self, u: Understanding) -> Place | None:
        m = u.origin
        if m is None:
            return None
        return self.saved.get(m.saved_label) if m.saved_label else m.place

    # -- replies ------------------------------------------------------------------
    def _ack(self, target: Place | None, u: Understanding) -> str:
        if target and self.destination and target.place_id != self.destination.place_id:
            return f"Okay, changing to {target.name}..."
        if target:
            return f"Checking the route to {target.name}..."
        if u.add_stops:
            return f"Finding {_stop_phrase(u.add_stops[0])} on the way..."
        return "One moment, updating the route..."

    def _describe(self, route: Route, *, changed_from: Place | None, started: bool, u: Understanding) -> str:
        parts = []
        if changed_from is not None:
            parts.append(f"Okay, not {changed_from.name} any more.")
        elif u.abandoned and route.destination:
            parts.append(f"Got it, {route.destination.name}.")
        via = f" via {route.via}" if route.via else ""
        same_trip = changed_from is None and not u.destination and (u.add_stops or u.remove_stops or u.avoid_highways is not None)
        lead = "Updated the route to" if same_trip else "Heading to"
        parts.append(f"{lead} {route.destination.label}: {route.distance_km:g} kilometres, about "
                     f"{describe_duration(route.eta_min)}{via}.")
        parts += _stop_lines(route.stops)
        floating = [s for s in route.stops if not s.get("at_named_place")]
        if u.timed_stops and len(floating) > 1:
            parts.append("I don't know when you're setting off, so I've spaced them out along the way.")
        if u.remove_stops:
            parts.append(f"Dropped {_join([_removed_phrase(r, self._dropped.get(r, 1)) for r in u.remove_stops])}.")
        if u.avoid_highways is True:
            parts.append("Avoiding highways.")
        if not started and self.navigation and self.navigation.get("destination") == route.destination.label and changed_from is None \
                and not (u.add_stops or u.remove_stops or u.avoid_highways is not None):
            parts = [f"We're already on the way to {route.destination.label}, about {describe_duration(route.eta_min)} to go."]
        return " ".join(parts)

    def _answer_question(self, u: Understanding) -> str:
        r = self.route
        if r is None:
            return "Still working out the route - one moment."
        if u.distance:
            return f"{r.destination.label} is about {r.distance_km:g} kilometres by road from {r.origin.name}."
        if u.eta:
            return f"About {describe_duration(r.eta_min)} to {r.destination.label}."
        stops = f", with {_join([_route_stop_phrase(s) for s in r.stops])}" if r.stops else ""
        return f"We're going from {r.origin.name} to {r.destination.label}{stops}, arriving in about {describe_duration(r.eta_min)}."

    async def _save(self, u: Understanding, turn_id: str) -> str:
        label = u.save_label or "place"
        place = u.save_place.place if u.save_place and u.save_place.place else self.destination
        if place is None:
            return f"There's no destination to save as {label} yet."
        self.saved[label] = place
        await self._event("saved", f"Saved {place.label} as '{label}' for this session.")
        await self._state()
        return f"Saved {place.label} as your {label}."

    async def _general(self, u: Understanding) -> str:
        """Anything that is not about the drive: the configured LLM, else an honest offline reply."""
        if u.is_question and self.route is None:
            return "We don't have a destination yet. Where would you like to go?"
        if re.fullmatch(r"(ok|okay|thanks|thank you|thank you so much|cool|great|nice|alright|all right|fine|got it|perfect)", u.text):
            return "You're welcome." if u.text.startswith("thank") else "Okay."
        if len(u.text.split()) <= 2:
            # a fragment the recogniser caught ("going"): ask, don't lecture
            return "Sorry, I didn't catch that. Where would you like to go?"
        provider = self.provider
        if provider is not None and getattr(provider, "name", "") != "local-deterministic":
            from ..providers.base import GenerationRequest

            request = GenerationRequest(
                goal="In-car voice assistant",
                utterance=u.text,
                constraints=["Answer in one or two short spoken sentences.",
                             f"You are an in-car assistant; the car is in {self.origin.label}."],
            )
            chunks = [c async for c in provider.stream(request)]
            answer = re.sub(r"\s+", " ", "".join(chunks)).strip()
            if answer:
                return answer
        return ("I'm your driving assistant: I can plan routes between cities and towns across India, add stops for "
                "fuel, charging or a meal, change the destination any time, and tell you how long it'll take.")

    # -- speaking ------------------------------------------------------------------
    async def _speak(self, turn_id: str, text: str, status: str = "complete") -> None:
        self._last_spoken = _Spoken(turn_id, text)
        await self.emit(StageFrame(stage=Stage.RESPONDING, turn_id=turn_id, detail="Speaking - cut in any time"))
        self._streaming = asyncio.ensure_future(self._stream(turn_id, text))
        try:
            await self._streaming
            await self.emit(MessageFrame(turn_id=turn_id, role="agent", content=text, modality="voice", status=status))  # type: ignore[arg-type]
        except asyncio.CancelledError:
            await self.emit(MessageFrame(turn_id=turn_id, role="agent", content=text[: self._stream_progress],
                                         modality="voice", status="interrupted"))
            raise

    async def _stream(self, turn_id: str, text: str) -> None:
        self._stream_progress = 0
        for word in re.findall(r"\S+\s*", text):
            if self.token_delay:
                await asyncio.sleep(self.token_delay)
            await self.emit(TokenFrame(turn_id=turn_id, text=word))
            self._stream_progress += len(word)

    async def _resume(self, turn_id: str) -> None:
        cp, self._checkpoint = self._checkpoint, None
        assert cp is not None
        await self.emit(StageFrame(stage=Stage.RECOVERING, turn_id=turn_id, detail="Picking up where I stopped"))
        await self.emit(MetricFrame(name="recovered_tokens", value=float(len(cp["said"].split())), unit="tokens",
                                    note="words already heard, not repeated"))
        await self._speak(turn_id, cp["remaining"], status="resumed")
        await self.emit(StageFrame(stage=Stage.IDLE, turn_id=turn_id, detail="Listening."))

    async def _cancel_turn(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _event(self, kind: str, text: str) -> None:
        await self.emit(DriveEventFrame(kind=kind, text=text))

    async def _state(self) -> None:
        work = self.route_work
        await self.emit(DriveStateFrame(state={
            "origin": self.origin.to_dict(),
            "destination": self.destination.to_dict() if self.destination else None,
            "stops": list(self.stops),
            "avoid_highways": self.avoid_highways,
            "route": self.route.to_dict() if self.route and work is not None and work.status is WorkStatus.VALID else None,
            "route_status": work.status.value if work is not None else "none",
            "route_attempt": work.execution_attempt if work is not None else 0,
            "navigation": dict(self.navigation) if self.navigation else None,
            "saved": {label: place.label for label, place in self.saved.items()},
            "straight_km": round(km_between(self.origin, self.destination), 1) if self.destination else None,
            # Planned early from partial speech, not yet acted on (never navigated to).
            "guess": self._spec.place.to_dict() if self._spec and self._spec.place else None,
        }))
