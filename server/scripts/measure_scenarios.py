"""Run the six evaluation scenarios against RouteVoice and record what it did.

The same scenarios the team records on Google Maps, driven through the same
public calls the websocket uses (``on_partial`` / ``on_final`` / ``interrupt``),
at the real defaults: a 1.2 s routing service and a 70 ms speaking pace.
Timings are server side; how fast the browser's own voice stops is read off
the "Stops talking" counter in the UI.

    python scripts/measure_scenarios.py                 # print a markdown table
    python scripts/measure_scenarios.py --runs 5 --out ../docs/measurements.md

Exits non-zero if any scenario's expected behaviour fails, so CI catches a
regression in the behaviour itself, not only in the unit tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.drive.runtime import DriveRuntime  # noqa: E402

ROUTE_LATENCY_MS = 1200
TOKEN_DELAY_MS = 70


class Car:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

        async def emit(frame: Any) -> None:
            self.frames.append(json.loads(frame.model_dump_json()))

        self.rt = DriveRuntime(emit, route_latency_ms=ROUTE_LATENCY_MS, token_delay_ms=TOKEN_DELAY_MS, start_city="Delhi")

    async def say(self, text: str, *, partials: bool = False) -> list[dict[str, Any]]:
        mark = len(self.frames)
        if partials:
            words = text.split()
            for i in range(2, len(words) + 1):
                await self.rt.on_partial(" ".join(words[:i]))
                await asyncio.sleep(0.25)  # roughly a speaking pace between words
        await self.rt.on_final(text)
        await self.rt._turn_task
        return self.frames[mark:]

    async def until_speaking(self, mark: int, timeout: float = 10.0) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if any(f["t"] == "stage" and f.get("stage") == "responding" for f in self.frames[mark:]):
                await asyncio.sleep(0.4)  # a few words in
                return
            await asyncio.sleep(0.01)
        raise TimeoutError("the agent never started speaking")

    def of(self, frames: list[dict[str, Any]], t: str, **match: Any) -> list[dict[str, Any]]:
        return [f for f in frames if f["t"] == t and all(f.get(k) == v for k, v in match.items())]

    def metric(self, frames: list[dict[str, Any]], name: str) -> float | None:
        values = [f["value"] for f in self.of(frames, "metric", name=name)]
        return values[-1] if values else None

    def reply(self, frames: list[dict[str, Any]]) -> str:
        msgs = [f for f in frames if f["t"] == "message" and f["role"] == "agent"]
        return msgs[-1]["content"] if msgs else ""

    def navigation_starts(self, frames: list[dict[str, Any]]) -> list[str]:
        return [f["args"]["to"] for f in self.of(frames, "tool", name="start_navigation", status="ok")]


# Each scenario returns (passed, what happened, numbers).
Result = tuple[bool, str, dict[str, float]]


async def s1_correction() -> Result:
    car = Car()
    frames = await car.say("take me to, um, Agra... actually no, Jaipur", partials=True)
    planned = [f["args"]["to"] for f in car.of(frames, "tool", name="plan_route", status="requested")]
    started = car.navigation_starts(frames)
    ok = started == ["Jaipur, Rajasthan"] and car.rt.navigation["destination"] == "Jaipur, Rajasthan"
    return ok, f"navigated to {', '.join(started) or 'nothing'} (routes planned: {', '.join(planned)})", {
        "first response (ms)": car.metric(frames, "ack_latency") or 0.0,
        "planned early (ms saved)": car.metric(frames, "speculation_saved") or 0.0,
    }


async def s2_barge_in() -> tuple[Result, Car]:
    car = Car()
    await car.say("take me to Jaipur")
    mark = len(car.frames)
    await car.rt.on_final("how long will it take")
    await car.until_speaking(mark)
    await car.rt.interrupt()
    await asyncio.sleep(0.05)
    frames = car.frames[mark:]
    stopped = bool(car.of(frames, "message", role="agent", status="interrupted"))
    checkpoint = car.of(frames, "checkpoint")
    ok = stopped and bool(checkpoint)
    return (ok, f"stopped mid-sentence; {checkpoint[0]['summary'] if checkpoint else 'nothing kept'}", {
        "time to stop (ms, server)": car.metric(frames, "time_to_yield") or 0.0,
    }), car


async def s3_go_on(car: Car) -> Result:
    interrupted = car.of(car.frames, "message", role="agent", status="interrupted")[-1]["content"]
    frames = await car.say("go on")
    resumed = car.of(frames, "message", role="agent", status="resumed")
    text = resumed[0]["content"] if resumed else ""
    ok = bool(text) and not text.startswith(interrupted.strip())
    return ok, ("continued from the unheard part, no restart" if ok else "restarted or said nothing"), {
        "words not repeated": car.metric(frames, "recovered_tokens") or 0.0,
    }


async def s4_add_stop() -> Result:
    car = Car()
    await car.say("take me to Jaipur")
    before = len(car.rt.route.stops) if car.rt.route else 0
    frames = await car.say("add a fuel stop on the way")
    after = len(car.rt.route.stops) if car.rt.route else 0
    recomputed = bool(car.of(frames, "drive_event", kind="stale"))
    rerouted = bool(car.of(frames, "drive_event", kind="route_updated"))
    ok = after == before + 1 and recomputed and rerouted and len(car.navigation_starts(frames)) == 1
    return ok, f"stops {before} -> {after}; old route marked stale, recomputed, guidance moved to the new route once", {
        "first response (ms)": car.metric(frames, "ack_latency") or 0.0,
    }


async def s5_question_while_planning() -> Result:
    car = Car()
    await car.say("take me to Jaipur")
    mark = len(car.frames)
    await car.rt.on_final("take me to Mumbai")
    await asyncio.sleep(0.05)  # Mumbai is still being planned
    asked = time.perf_counter()
    await car.rt.on_final("how long will it take")
    await car.rt._turn_task
    frames = car.frames[mark:]
    answer = car.reply(frames)
    ok = answer.startswith("About") and "Mumbai" in answer and car.navigation_starts(frames) == ["Mumbai, Maharashtra"]
    return ok, f'answered "{answer[:70]}"; guidance started once, for Mumbai', {
        "question -> full answer spoken (s)": round(time.perf_counter() - asked, 2),
    }


async def s6_start_twice() -> Result:
    car = Car()
    first = await car.say("go to Jaipur")
    second = await car.say("go to Jaipur")
    starts = car.navigation_starts(first) + car.navigation_starts(second)
    duplicate = bool(car.of(second, "drive_event", kind="nav_duplicate"))
    ok = starts == ["Jaipur, Rajasthan"] and duplicate
    return ok, f"guidance started {len(starts)} time(s); second request recognised as a duplicate", {
        "navigation starts": float(len(starts)),
    }


async def s7_change_while_planning() -> Result:
    car = Car()
    mark = len(car.frames)
    await car.rt.on_final("take me to Udaipur")
    await asyncio.sleep(0.2)  # Udaipur is still being planned
    await car.rt.on_final("no wait, Ajmer")
    await car.rt._turn_task
    await asyncio.sleep(ROUTE_LATENCY_MS / 1000 + 0.3)  # let the superseded Udaipur work finish late
    frames = car.frames[mark:]
    discarded = bool(car.of(frames, "drive_event", kind="late_discarded"))
    starts = car.navigation_starts(frames)
    ok = starts == ["Ajmer, Rajasthan"] and discarded
    return ok, f"late Udaipur route discarded; guidance started for {', '.join(starts) or 'nothing'}", {
        "stale routes used": 0.0 if ok else 1.0,
    }


SCENARIOS: list[tuple[str, str]] = [
    ("1", "Correction mid-sentence: \"Agra... actually no, Jaipur\""),
    ("2", "Talking over it while it speaks"),
    ("3", "\"Go on\" after the interruption"),
    ("4", "Adding a fuel stop during a trip"),
    ("5", "Asking the ETA while the route is still loading"),
    ("6", "\"Start navigation\" twice"),
    ("7", "Changing destination while it is still planning (extra)"),
]


async def run_once() -> dict[str, Result]:
    results: dict[str, Result] = {"1": await s1_correction()}
    results["2"], car = await s2_barge_in()
    results["3"] = await s3_go_on(car)
    await car.rt.shutdown()
    single: list[tuple[str, Callable[[], Awaitable[Result]]]] = [
        ("4", s4_add_stop), ("5", s5_question_while_planning), ("6", s6_start_twice), ("7", s7_change_while_planning),
    ]
    for key, scenario in single:
        results[key] = await scenario()
    return results


def summarise(runs: list[dict[str, Result]]) -> tuple[str, bool]:
    lines = [
        f"RouteVoice, {len(runs)} run(s) per scenario; routing service {ROUTE_LATENCY_MS} ms, speaking pace {TOKEN_DELAY_MS} ms per word. "
        "Numbers are medians.",
        "",
        "| # | Scenario | Result | What RouteVoice did | Measured |",
        "|---|---|---|---|---|",
    ]
    all_ok = True
    for key, title in SCENARIOS:
        passed = sum(1 for r in runs if r[key][0])
        all_ok &= passed == len(runs)
        numbers: dict[str, list[float]] = {}
        for r in runs:
            for name, value in r[key][2].items():
                numbers.setdefault(name, []).append(value)
        measured = "; ".join(f"{name}: {round(statistics.median(vals), 2)}" for name, vals in numbers.items())
        lines.append(f"| {key} | {title} | {passed}/{len(runs)} pass | {runs[-1][key][1]} | {measured} |")
    return "\n".join(lines), all_ok


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, help="also write the table to this markdown file")
    args = parser.parse_args()
    runs = [await run_once() for _ in range(args.runs)]
    table, ok = summarise(runs)
    print(table)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(f"# RouteVoice scenario measurements\n\n{table}\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
