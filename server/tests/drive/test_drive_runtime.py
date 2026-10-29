"""The in-car voice agent (use-case extension): responsiveness, async work and
clean recovery, asserted on the frames a client actually receives."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from app.drive.runtime import DriveRuntime
from app.main import app
from fastapi.testclient import TestClient


class Car:
    def __init__(self, route_latency_ms: int = 60, token_delay_ms: int = 0, start: str = "Delhi") -> None:
        self.frames: list[dict[str, Any]] = []

        async def emit(frame: Any) -> None:
            self.frames.append(json.loads(frame.model_dump_json()))

        self.rt = DriveRuntime(emit, route_latency_ms=route_latency_ms, token_delay_ms=token_delay_ms, start_city=start)

    async def say(self, text: str, *, partials: bool = False) -> list[dict[str, Any]]:
        mark = len(self.frames)
        if partials:
            words = text.split()
            for i in range(2, len(words) + 1):  # a recogniser sends the full text as a partial first
                await self.rt.on_partial(" ".join(words[:i]))
                await asyncio.sleep(0.01)
        await self.rt.on_final(text)
        await self.rt._turn_task
        return self.frames[mark:]

    def of(self, frames: list[dict[str, Any]], t: str, **match: Any) -> list[dict[str, Any]]:
        return [f for f in frames if f["t"] == t and all(f.get(k) == v for k, v in match.items())]

    def reply(self, frames: list[dict[str, Any]]) -> str:
        msgs = [f for f in frames if f["t"] == "message" and f["role"] == "agent"]
        return msgs[-1]["content"] if msgs else ""


async def test_acknowledges_before_the_route_is_ready_and_never_claims_done_early() -> None:
    car = Car(route_latency_ms=150)
    frames = await car.say("take me to Jaipur")
    kinds = [f["t"] if f["t"] != "tool" else f"tool:{f['name']}:{f['status']}" for f in frames]
    ack, route_ok = kinds.index("filler"), kinds.index("tool:plan_route:ok")
    assert ack < route_ok  # spoken acknowledgement while the route is still being planned
    assert "Checking the route" in car.of(frames, "filler")[0]["text"]
    assert car.of(frames, "metric", name="ack_latency")[0]["value"] < 100
    nav = kinds.index("tool:start_navigation:ok")
    assert route_ok < nav  # guidance only starts once the route exists
    assert car.reply(frames).startswith("Heading to Jaipur, Rajasthan")


async def test_self_correction_acts_only_on_the_final_destination() -> None:
    car = Car()
    frames = await car.say("take me to, um, Agra - actually no, Jaipur", partials=True)
    planned = [f["args"]["to"] for f in car.of(frames, "tool", name="plan_route", status="requested")]
    assert planned == ["Jaipur, Rajasthan"]  # Agra was never planned for real, only guessed while speaking
    started = car.of(frames, "tool", name="start_navigation")
    assert [f["args"]["to"] for f in started] == ["Jaipur, Rajasthan"]
    assert car.of(frames, "drive_event", kind="correction")
    specs = [f["status"] for f in car.of(frames, "spec")]
    assert "discarded" in specs and "hit" in specs  # the Agra guess was dropped, the Jaipur one reused


async def test_speculation_starts_planning_from_partial_speech() -> None:
    car = Car(route_latency_ms=120)
    frames = await car.say("please take me to Jaipur right now", partials=True)
    hit = car.of(frames, "spec", status="hit")
    assert hit and hit[0]["saved_ms"] > 0
    assert car.of(frames, "metric", name="speculation_saved")[0]["value"] > 0


async def test_change_while_planning_discards_the_late_stale_result() -> None:
    car = Car(route_latency_ms=200)
    mark = len(car.frames)
    await car.rt.on_final("take me to Udaipur")
    await asyncio.sleep(0.05)  # Udaipur is still being planned...
    await car.rt.on_final("sorry, make it Ajmer")
    await car.rt._turn_task
    await asyncio.sleep(0.3)  # let the superseded Udaipur work finish late
    frames = car.frames[mark:]
    assert car.of(frames, "drive_event", kind="stale")
    assert car.of(frames, "drive_event", kind="late_discarded")
    assert [f["args"]["to"] for f in car.of(frames, "tool", name="start_navigation")] == ["Ajmer, Rajasthan"]
    assert car.rt.navigation["destination"] == "Ajmer, Rajasthan"


async def test_navigation_is_never_started_twice_for_the_same_route() -> None:
    car = Car()
    await car.say("take me to Jaipur")
    frames = await car.say("go to Jaipur")
    assert not car.of(frames, "tool", name="start_navigation")
    assert car.of(frames, "drive_event", kind="nav_duplicate")
    assert "already on the way" in car.reply(frames)
    commits = [a for a in car.rt.core.snapshot()["actions"]]
    assert len(commits) == 1


async def test_changes_to_stops_or_preferences_invalidate_and_recompute_the_route() -> None:
    car = Car(start="Mumbai")
    first = await car.say("take me to Pune")
    eta = car.rt.route.eta_min
    frames = await car.say("stop for fuel on the way")
    assert car.of(frames, "drive_event", kind="stale")
    assert car.rt.route.stops and car.rt.route.eta_min > eta
    assert car.rt.route_work.execution_attempt == 2
    frames = await car.say("and avoid highways")
    assert car.of(frames, "drive_event", kind="stale") and car.rt.route.avoid_highways
    assert car.of(first, "drive_event", kind="nav_started")
    assert car.of(frames, "drive_event", kind="route_updated")


async def test_questions_are_answered_from_the_current_route() -> None:
    car = Car()
    await car.say("take me to Jaipur")
    assert car.reply(await car.say("how long will it take")).startswith("About 5 hours")
    assert "kilometres" in car.reply(await car.say("how far is it"))
    assert "Delhi to Jaipur" in car.reply(await car.say("where are we going"))


async def test_barge_in_checkpoints_what_was_heard_and_go_on_resumes_the_rest() -> None:
    car = Car(token_delay_ms=20)
    await car.rt.on_final("hello")
    await asyncio.sleep(0.12)
    await car.rt.interrupt()
    await asyncio.sleep(0.02)
    checkpoint = car.of(car.frames, "checkpoint")
    assert checkpoint and car.of(car.frames, "stage", stage="interrupted")
    interrupted = car.of(car.frames, "message", role="agent", status="interrupted")[0]["content"]
    car.rt.token_delay = 0
    frames = await car.say("go on")
    resumed = car.of(frames, "message", role="agent", status="resumed")[0]["content"]
    assert resumed and not resumed.startswith(interrupted.strip())  # continues, does not restart
    assert car.of(frames, "metric", name="recovered_tokens")


async def test_interrupt_uses_what_the_listener_actually_heard() -> None:
    car = Car()
    await car.say("hello")
    spoken = car.rt._last_spoken.text
    await car.rt.interrupt(heard_chars=10)  # the browser's speech had only reached character 10
    frames = await car.say("go on")
    assert car.of(frames, "message", role="agent", status="resumed")[0]["content"] == spoken[10:].strip()


async def test_saved_places_unknown_places_cancel_and_off_topic() -> None:
    car = Car()
    assert "don't know where your office is" in car.reply(await car.say("take me to the office"))
    assert car.reply(await car.say("my office is in Noida")) == "Saved Noida, Uttar Pradesh as your office."
    assert car.reply(await car.say("take me to the office")).startswith("Heading to Noida")
    assert "couldn't find zzyzx" in car.reply(await car.say("take me to Zzyzx"))
    assert "cancelled" in car.reply(await car.say("cancel the trip"))
    assert car.rt.navigation is None
    assert "driving assistant" in car.reply(await car.say("what's the weather like"))


async def test_origin_by_voice_and_already_there() -> None:
    car = Car()
    frames = await car.say("I'm in Pune, take me to Mumbai")
    assert car.rt.origin.name == "Pune" and "Starting from Pune" in car.reply(frames)
    assert car.reply(await car.say("take me to Pune")) == "You're already in Pune, Maharashtra."


async def test_off_topic_questions_use_a_configured_llm() -> None:
    class FakeLLM:
        name = "openai-compatible"

        async def stream(self, request: Any):
            yield "It looks sunny "
            yield "today."

    car = Car()
    car.rt.provider = FakeLLM()
    assert car.reply(await car.say("what's the weather like")) == "It looks sunny today."


def test_drive_websocket_end_to_end() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/drive") as ws:
            ready = ws.receive_json()
            assert ready["t"] == "ready" and ready["mode"] == "drive"
            frames = []
            ws.send_json({"t": "partial", "text": "take me to"})
            ws.send_json({"t": "partial", "text": "take me to Jaipur"})
            ws.send_json({"t": "final", "text": "take me to Jaipur"})
            for _ in range(500):
                f = ws.receive_json()
                frames.append(f)
                if f["t"] == "message" and f["role"] == "agent":
                    break
            assert any(f["t"] == "filler" for f in frames)
            assert any(f["t"] == "tool" and f["name"] == "start_navigation" for f in frames)
            assert frames[-1]["content"].startswith("Heading to Jaipur")
            states = [f for f in frames if f["t"] == "drive"]
            assert states[-1]["state"]["navigation"]["destination"] == "Jaipur, Rajasthan"


async def test_a_later_partial_cannot_cancel_an_early_plan_a_turn_already_adopted() -> None:
    car = Car(route_latency_ms=150)
    await car.rt.on_partial("take me to Udaipur")  # early plan starts
    await car.rt.on_final("take me to Udaipur")  # the turn adopts it
    await asyncio.sleep(0.02)
    await car.rt.on_partial("no wait Ajmer")  # the driver starts correcting mid-planning
    await car.rt._turn_task  # must finish cleanly, not with CancelledError
    assert car.rt.route is not None
    await car.rt.on_final("no wait Ajmer")
    await car.rt._turn_task
    assert car.rt.navigation["destination"] == "Ajmer, Rajasthan"


async def test_stops_survive_a_correction_but_not_a_new_trip() -> None:
    car = Car(start="Mumbai")
    await car.say("take me to Pune and stop for fuel")
    assert car.rt.stops == ["fuel"]
    await car.say("sorry, make it Nashik")  # a correction: same trip, stops kept
    assert car.rt.stops == ["fuel"] and car.rt.route.stops
    frames = await car.say("take me to Goa")  # a new trip
    assert car.rt.stops == [] and not car.rt.route.stops
    assert "cleared the earlier stops" in car.reply(frames)


async def test_a_question_that_supersedes_a_planning_turn_still_gets_guidance_started() -> None:
    car = Car(route_latency_ms=150)
    await car.say("take me to Jaipur")
    mark = len(car.frames)
    await car.rt.on_final("take me to Mumbai")
    await asyncio.sleep(0.02)  # still planning Mumbai...
    await car.rt.on_final("how long will it take")  # ...when the question supersedes that turn
    await car.rt._turn_task
    frames = car.frames[mark:]
    assert car.reply(frames).startswith("About") and "Mumbai" in car.reply(frames)
    assert [f["args"]["to"] for f in car.of(frames, "tool", name="start_navigation")] == ["Mumbai, Maharashtra"]
    assert car.rt.navigation["destination"] == "Mumbai, Maharashtra"


@pytest.mark.parametrize("hold", ["hold on", "stop", "wait a second"])
async def test_hold_on_pauses_without_a_reply_and_go_on_resumes(hold: str) -> None:
    car = Car(token_delay_ms=20)
    await car.rt.on_final("hello")
    await asyncio.sleep(0.12)
    await car.rt.interrupt()  # the browser stopped speaking on the partial "hold on"
    interrupted = car.of(car.frames, "message", role="agent", status="interrupted")[0]["content"]
    frames = await car.say(hold)
    assert not car.of(frames, "message", role="agent")  # stays quiet: no repeated answer
    assert car.of(frames, "stage", stage="interrupted")
    car.rt.token_delay = 0
    frames = await car.say("go on")
    resumed = car.of(frames, "message", role="agent", status="resumed")[0]["content"]
    assert resumed and not resumed.startswith(interrupted.strip())


async def test_a_typed_stop_while_speaking_interrupts_and_keeps_the_rest() -> None:
    car = Car(token_delay_ms=20)
    await car.rt.on_final("hello")
    await asyncio.sleep(0.12)
    frames = await car.say("stop")
    assert car.of(frames, "checkpoint") and not car.of(frames, "message", role="agent", status="complete")
    car.rt.token_delay = 0
    assert car.of(await car.say("go on"), "message", role="agent", status="resumed")


async def test_a_misheard_fragment_gets_a_short_question_not_the_help_text() -> None:
    car = Car()
    reply = car.reply(await car.say("going"))
    assert reply == "Sorry, I didn't catch that. Where would you like to go?"
    assert car.reply(await car.say("thank you")) == "You're welcome."
    assert car.reply(await car.say("okay")) == "Okay."
    assert car.reply(await car.say("go on")) == "That was everything. Where to next?"


async def test_a_from_city_counts_even_when_the_destination_is_unknown() -> None:
    car = Car()
    reply = car.reply(await car.say("I want to go from Bangalore to Zzyzx"))
    assert reply.startswith("Starting from Bengaluru") and "couldn't find zzyzx" in reply
    assert car.rt.origin.name == "Bengaluru"
    assert car.of(car.frames, "drive")[-1]["state"]["origin"]["name"] == "Bengaluru"
    # the next request routes from there
    assert "Bengaluru" in car.reply(await car.say("take me to Mysore")) or car.rt.route.origin.name == "Bengaluru"


async def test_map_state_shows_the_early_guess_and_drops_it_on_a_correction() -> None:
    """The route map draws what the agent is guessing while the driver speaks - and only that."""
    car = Car()
    frames = await car.say("take me to, um, Agra - actually no, Jaipur", partials=True)
    guesses = [f["state"]["guess"] for f in car.of(frames, "drive") if f["state"].get("guess")]
    names = [g["name"] for g in guesses]
    assert "Agra" in names and "Jaipur" in names  # Agra guessed first, then replaced by Jaipur
    assert names.index("Agra") < names.index("Jaipur")
    assert all({"lat", "lon"} <= set(g) for g in guesses)  # every guess can be placed on the map
    final = car.of(frames, "drive")[-1]["state"]
    assert final["guess"] is None  # once acted on, it is the route, no longer a guess
    assert final["route"]["destination"]["name"] == "Jaipur"


async def test_route_stops_carry_coordinates_for_the_map() -> None:
    car = Car()
    await car.say("take me to Jaipur")
    frames = await car.say("stop for fuel on the way")
    stops = car.of(frames, "drive")[-1]["state"]["route"]["stops"]
    assert stops and stops[0]["kind"] == "fuel"
    assert isinstance(stops[0]["lat"], float) and isinstance(stops[0]["lon"], float)


async def test_the_reported_trip_gets_two_snack_stops_on_the_map() -> None:
    """The request that produced a Pune -> Mumbai route with no stops at all."""
    car = Car(start="Delhi")
    frames = await car.say("I want to travel from Pune to Mumbai also add to stops for snacks "
                           "one in the morning and one in the evening")
    route = car.of(frames, "drive")[-1]["state"]["route"]
    assert (route["origin"]["name"], route["destination"]["name"]) == ("Pune", "Mumbai")
    snacks = [s for s in route["stops"] if s["kind"] == "snacks"]
    assert len(snacks) == 2 and snacks[0]["near"] != snacks[1]["near"]
    assert all(isinstance(s["lat"], float) for s in snacks)  # both drawn on the map
    reply = car.reply(frames)
    assert "two stops for a snack break" in reply
    assert "spaced them out" in reply  # honest: it does not know the departure time
    assert "Got it, Mumbai" not in reply  # nothing was corrected


async def test_asking_twice_keeps_the_count_and_another_adds_one() -> None:
    car = Car()
    await car.say("take me to Jaipur with two snack stops")
    assert car.rt.stops == ["snacks", "snacks"]
    await car.say("stop for snacks")  # already there: ask twice, act once
    assert car.rt.stops == ["snacks", "snacks"]
    await car.say("add another snack stop")
    assert car.rt.stops == ["snacks", "snacks", "snacks"]
    frames = await car.say("skip the snack stops")
    assert car.rt.stops == [] and not car.rt.route.stops
    assert "Dropped the snack stops" in car.reply(frames)


async def test_named_stops_are_passed_through_and_can_be_dropped() -> None:
    car = Car(start="Pune")
    frames = await car.say("take me to Mumbai and stop for lunch at Lonavala")
    route = car.rt.route
    assert route.destination.name == "Mumbai" and route.via == "Lonavla"
    assert [(s["kind"], s["near"]) for s in route.stops] == [("food", "Lonavla")]
    assert "a meal at Lonavla" in car.reply(frames)
    frames = await car.say("remove the Lonavala stop")
    assert car.rt.route.stops == [] and "Dropped the stop at Lonavla" in car.reply(frames)


async def test_via_keeps_the_destination() -> None:
    car = Car()
    await car.say("take me to Mumbai via Nashik")
    assert car.rt.destination.name == "Mumbai"  # used to navigate to Nashik
    assert [s["near"] for s in car.rt.route.stops] == ["Nashik"]


async def test_stop_at_a_town_with_no_trip_yet_goes_there() -> None:
    car = Car(start="Pune")
    await car.say("stop at Lonavala")
    assert car.rt.destination.name == "Lonavla" and car.rt.route.stops == []
