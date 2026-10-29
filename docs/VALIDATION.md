# Validation

How RouteVoice is checked against each evaluation criterion, and where the
evidence lives. Everything marked **automated** runs on every push (GitHub
Actions, `.github/workflows/ci.yml`) and can be re-run locally:

```bash
cd server
python -m pytest tests -q                        # 443 tests
python scripts/measure_scenarios.py --runs 5     # the evaluation scenarios, end to end
```

## Summary

| # | Criterion | Evidence | How it is checked |
|---|---|---|---|
| 1 | Functional Correctness | the evaluation scenarios pass 5/5 runs; 112 Drive tests | automated |
| 2 | Reverse Engineering Accuracy | Google Maps recordings and analysis (README section 1) | manual, recorded on a phone |
| 3 | Improvement & Practical Value | side-by-side table below: same scenario, Maps vs RouteVoice | automated (RouteVoice) + manual (Maps) |
| 4 | Reliability & Error Handling | failure-path tests listed below | automated |
| 5 | Interaction & Usability | hands-on checklist below | manual, in Chrome or Edge |
| 6 | Testing & Validation | this document, CI, 443 tests, scenario runner | automated |

---

## 1. Functional Correctness

The feature recreated - voice trip setup - works end to end. The same seven
scenarios the team recorded on Google Maps are run against RouteVoice through
the same calls the websocket uses, at the real defaults (a 1.2 s routing
service, a 70 ms speaking pace). Latest results: [measurements.md](measurements.md).

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

Recorded by the team on a phone: Google Maps' voice trip setup, the same seven
scenarios, with timings read from the screen recordings. Results and analysis
are in the README, section 1; screenshots in `docs/analysis/`.

| # | Scenario | Google Maps (observed) | Evidence |
|---|---|---|---|
| 1 | Correction mid-sentence | _fill in_ | _screenshot_ |
| 2 | Talking over it | _fill in_ | |
| 3 | "Go on" after the interruption | _fill in_ | |
| 4 | Adding a stop | _fill in_ | |
| 5 | ETA while the route loads | _fill in_ | |
| 6 | "Start navigation" twice | _fill in_ | |
| 7 | Change while planning | _fill in_ | |

Device, app version and assistant used: _fill in_.

## 3. Improvement & Practical Value

| Scenario | Google Maps (observed) | RouteVoice (measured) |
|---|---|---|
| Correction mid-sentence lands on the right place | _fill in_ | yes, 5/5 |
| Time from cut-in to speech stopping | _fill in (from video)_ | server: 0.19 ms median; browser voice: read "Stops talking" in the UI |
| After an interruption, "go on" | _fill in_ | continues from the unheard part, 5/5 |
| Adding a stop mid-trip | _fill in_ | route recomputed, guidance moved once, 5/5 |
| Asking while the route is still loading | _fill in_ | answered, 5/5 |
| "Start navigation" twice | _fill in_ | starts once, 5/5 |
| First acknowledgement after a request | _fill in_ | under 1 ms server side ("Checking the route to Jaipur...") |
| Route planning started before the sentence ended | no (one request, then one reply) _- confirm_ | 266 ms saved median, from partial speech |

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

- **443 automated tests** (`server/tests/`): 112 for the Drive feature, 331 for
  BACKSPACE (facts, dependencies, stale detection, at-most-once commits).
- **Scenario runner** (`server/scripts/measure_scenarios.py`): the evaluation
  scenarios end to end, exits non-zero if any expected behaviour fails.
- **CI**: both run on every push, plus a type-checked production build of the web app.
- **Honest limits** of what is measured: timings are server side; the browser's
  own speech engines (recognition and voice) add their own delay, which is
  shown live in the UI rather than measured here.
