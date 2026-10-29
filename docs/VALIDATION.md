# Validation

How RouteVoice is checked against each evaluation criterion, and where the
evidence lives. Everything marked **automated** runs on every push (GitHub
Actions, `.github/workflows/ci.yml`) and can be re-run locally:

```bash
cd server
python -m pytest tests -q                        # 455 tests
python scripts/measure_scenarios.py --runs 5     # the evaluation scenarios, end to end
```

## Summary

| # | Criterion | Evidence | How it is checked |
|---|---|---|---|
| 1 | Functional Correctness | the evaluation scenarios pass 5/5 runs; 112 Drive tests | automated |
| 2 | Reverse Engineering Accuracy | four tests run on Google Maps and on RouteVoice, with what each did (below and README section 1) | manual, screen-recorded on a phone |
| 3 | Improvement & Practical Value | side-by-side table below: same test, Maps vs RouteVoice | manual (Maps) + automated (RouteVoice) |
| 4 | Reliability & Error Handling | failure-path tests listed below | automated |
| 5 | Interaction & Usability | hands-on checklist below | manual, in Chrome or Edge |
| 6 | Testing & Validation | this document, CI, 455 tests, scenario runner | automated |

---

## 1. Functional Correctness

The feature recreated - voice trip setup - works end to end. Seven scenarios,
covering the four Google Maps tests below and three more, are run against
RouteVoice through the same calls the websocket uses, at the real defaults (a
1.2 s routing service, a 70 ms speaking pace). Latest results:
[measurements.md](measurements.md).

| # | Scenario | RouteVoice result (5 runs) |
|---|---|---|
| 1 | Correction mid-sentence: *"Agra... actually no, Jaipur"* | 5/5: navigates to Jaipur only |
| 2 | Talking over it while it speaks | 5/5: stops mid-sentence, keeps the unheard part |
| 3 | *"Go on"* after the interruption | 5/5: continues from the unheard part, no restart |
| 4 | Adding a fuel stop during a trip | 5/5: stop added, route recomputed, guidance moved to the new route once |
| 5 | Asking the ETA while the route is still loading | 5/5: answered for the new destination |
| 6 | *"Start navigation"* twice | 5/5: guidance starts once |
| 7 | Changing destination while still planning | 5/5: the late, outdated route is discarded |

Unit coverage of the feature (`server/tests/drive/`):

| Area | Tests |
|---|---|
| Understanding disfluent speech | `test_final_destination_after_disfluency_and_self_correction`, `test_abandoned_destination_is_reported_not_acted_on`, `test_stops_added_dropped_and_corrected`, `test_route_preferences`, `test_saved_places`, `test_questions_and_commands` |
| Places across India | `test_gazetteer_covers_india_not_one_city`, `test_lookup_by_name_alternate_and_near_miss`, `test_ambiguous_names_prefer_the_larger_place_unless_a_state_is_given`, `test_town_names_that_are_ordinary_words_are_not_destinations` |
| Routes | `test_intercity_route_is_realistic`, `test_stops_add_time_and_sit_near_a_real_town`, `test_avoiding_highways_is_slower_and_routes_are_deterministic` |
| End to end over the socket | `test_drive_websocket_end_to_end` |

## 2. Reverse Engineering Accuracy

The team asked Google Maps' voice assistant four things, in the same words and
order, with the screen recorder running, then asked RouteVoice the same.

| # | What we did | What Google Maps did |
|---|---|---|
| 1 | *"Take me to Agra"*, then cut in with *"Take me to Jaipur"* while it answered | Ignored the interruption; kept answering for Agra |
| 2 | *"How long will it take to get there?"*, *"Hold on"* while it answered, then *"Go on"* | Ignored "hold on" and kept speaking |
| 3 | *"Add a fuel stop on the way"* | Added the stop |
| 4 | *"Navigate to Agra"* again, for the trip already under way | Restarted navigation and dropped the fuel stop and the other changes made on the way |

What the recordings show about how Maps' assistant works:

- **It takes strict turns.** While it is speaking it is not listening, so a new
  destination or "hold on" spoken over it is lost (tests 1 and 2).
- **Each request is a fresh command.** Asking for the current destination again
  starts a new trip rather than updating the one under way, so stops added on the
  way are discarded (test 4).

RouteVoice rebuilds the same feature (destination, stops, ETA, navigation by
voice) and changes exactly these two behaviours: it listens while it speaks, and
it keeps the trip as state that a new request updates instead of replacing.

## 3. Improvement & Practical Value

| Test | Google Maps (observed) | RouteVoice (observed and measured) |
|---|---|---|
| 1. New destination spoken over the answer | ignored | stops and reroutes: *"Okay, not Agra any more. Heading to Jaipur…"* |
| 2. "Hold on" while it speaks | ignored, kept talking | stops at once (0.19 ms server side, median of 5 runs) and saves a checkpoint |
| 2. "Go on" afterwards | nothing to resume | resumes from the exact word it was cut off, never from the top (5/5) |
| 3. Add a fuel stop | added the stop | added by voice; route recomputed; guidance moved to the new route once (5/5) |
| 4. Ask for the current destination again | restarted navigation, dropped the fuel stop | *"We're already on the way to Agra…"*: no restart, fuel stop kept |

Beyond the four tests, RouteVoice also corrects itself inside one sentence
(*"Agra… actually no, Jaipur"*), answers while a route is still loading, and
starts planning before the driver finishes speaking (266 ms head start, median).

Why it matters in a car: every one of these is a moment the driver would
otherwise look at the screen to fix - cancelling a wrong destination, waiting
for the assistant to finish, checking whether navigation started twice.

## 4. Reliability & Error Handling

| Failure | What RouteVoice does | Test |
|---|---|---|
| Unknown place | Never invents one: asks which city or town it is in or near | `test_unknown_place_is_not_invented`, `test_unknown_destination_is_flagged` |
| Misheard fragment ("going") | Asks a short question, not a lecture | `test_a_misheard_fragment_gets_a_short_question_not_the_help_text` |
| Request changes while a route is being planned | Late result discarded, route recomputed | `test_change_while_planning_discards_the_late_stale_result`, `test_a_later_partial_cannot_cancel_an_early_plan_a_turn_already_adopted` |
| Repeated "start navigation" | At-most-once commit | `test_navigation_is_never_started_twice_for_the_same_route`, BACKSPACE `test_actions.py` (17 tests) |
| Stops or preferences change | Route marked stale and recomputed, never reused | `test_changes_to_stops_or_preferences_invalidate_and_recompute_the_route` |
| Unexpected server error in a turn | Says *"Sorry, something went wrong on my side. Could you say that again?"*, keeps the trip, keeps listening | `test_an_unexpected_failure_is_spoken_and_the_trip_survives` |
| Malformed or unknown message on the socket | Ignored with an error frame; the session continues | `server/app/main.py` |
| Connection drops | Browser reconnects with exponential backoff (0.8 s up to 8 s) | `web/src/drive/socket.ts` |
| Browser without speech recognition | Says so and offers typing instead | `web/src/drive/store.ts` |
| No AI model key | Off-topic questions get an honest offline reply | `test_saved_places_unknown_places_cancel_and_off_topic` |
| Model rate-limited or failing | Retries, then a backup model if one is set | `server/app/providers/openai_compat.py` |

## 5. Interaction & Usability

Checked by hand in Chrome and Edge, with headphones:

- [ ] Mic works hands-free after allowing it once
- [ ] Talking over the reply stops it at once; it does not react to its own voice
- [ ] "Hold on" pauses, "go on" continues from where it stopped
- [ ] Typed input works as a fallback
- [ ] Map shows the route and the stops, and moves when the destination changes
- [ ] The six **Try it** buttons each play their scenario
- [ ] Counters update: First response, Planned early, Stops talking, Resumed
- [ ] Layout works on a laptop screen and a phone-width window

## 6. Testing & Validation

- **455 automated tests** (`server/tests/`): 112 for the Drive feature, 331 for
  BACKSPACE (facts, dependencies, stale detection, at-most-once commits), 12 for
  accounts (sign-up, log-in, hashed passwords, sessions, the assistant locked
  behind a login).
- **Scenario runner** (`server/scripts/measure_scenarios.py`): the evaluation
  scenarios end to end, exits non-zero if any expected behaviour fails.
- **CI**: both run on every push, plus a type-checked production build of the web app.
- **Honest limits** of what is measured: timings are server side; the browser's
  own speech engines (recognition and voice) add their own delay, which is
  shown live in the UI rather than measured here.
