# RouteVoice scenario measurements

RouteVoice, 5 run(s) per scenario; routing service 1200 ms, speaking pace 70 ms per word. Numbers are medians.

| # | Scenario | Result | What RouteVoice did | Measured |
|---|---|---|---|---|
| 1 | Correction mid-sentence: "Agra... actually no, Jaipur" | 5/5 pass | navigated to Jaipur, Rajasthan (routes planned: Jaipur, Rajasthan) | first response (ms): 0.92; planned early (ms saved): 266.0 |
| 2 | Talking over it while it speaks | 5/5 pass | stopped mid-sentence; 4 words heard, 2 kept for 'go on' | time to stop (ms, server): 0.19 |
| 3 | "Go on" after the interruption | 5/5 pass | continued from the unheard part, no restart | words not repeated: 4.0 |
| 4 | Adding a fuel stop during a trip | 5/5 pass | stops 0 -> 1; old route marked stale, recomputed, guidance moved to the new route once | first response (ms): 0.82 |
| 5 | Asking the ETA while the route is still loading | 5/5 pass | answered "About 24 hours 15 minutes to Mumbai, Maharashtra."; guidance started once, for Mumbai | question -> full answer spoken (s): 1.77 |
| 6 | "Start navigation" twice | 5/5 pass | guidance started 1 time(s); second request recognised as a duplicate | navigation starts: 1.0 |
| 7 | Changing destination while it is still planning (extra) | 5/5 pass | late Udaipur route discarded; guidance started for Ajmer, Rajasthan | stale routes used: 0.0 |
