# Performance investigation and verification

## Reproduce and measure

Separate launcher cold start, entering discovery, rapid typing, card selection, scrolling, settings round trip, and idle polls. Record terminal dimensions, package versions, catalog count, image backend, cached/cold artwork, and daemon availability. Exercise realistic and larger fake catalogs without mutating real accounts.

Instrument elapsed time and operation counts around model filtering, DOM reconciliation, details requests, image fetch/decode, and polling. Measure event-loop heartbeat delays separately from backend elapsed time. Profile CPU with Python `cProfile`/`pstats`; use allocation tracing only when needed. Keep instrumentation outside `render()` and disable noisy per-frame logs for timing runs. These are general Python techniques, not a Textual profiler API.

Textual's optional development tools offer a console and dev-mode logs. Run `textual console` in a second terminal and `textual run --dev ...` for debugging; verify tools are installed first, since the packaged runtime may not include them. [Devtools](https://textual.textualize.io/guide/devtools/).

## Diagnose before optimizing

| Symptom | Evidence to collect | Candidate change |
| --- | --- | --- |
| Keystrokes delayed during requests | Handler duration, heartbeat stalls | Return immediately and use bounded worker |
| Discovery stalls even without artwork | Mount/remove counts, widget count, layout profile | Reuse keyed cards; paginate/bound tree |
| Selection gets expensive with catalog size | Full-tree queries/class changes per key | Retain selected widget; change old/new only |
| Idle CPU or repeated service calls | Poll counts, overlaps, unchanged redraws | Serialize/coalesce polls; compare snapshots |
| Artwork causes resource spikes | Active loads, bytes/pixels, duplicate URLs | Bound concurrent loads; reuse cache; lazy viewport loads |
| Filtering rebuilds while typing | Rebuilds versus keystrokes, stale results | Debounce/coalesce work while keeping input responsive |

These candidates require measurement. Do not introduce virtualization, caching layers, or debounce delays if the bottleneck is elsewhere. Keep cleanup/lifetime handling and error reporting when optimizing.

Repo-specific audit points: avoid clearing an `OptionList` and adding options one at a time on every refresh; use supported bulk updates for membership changes and targeted prompt updates otherwise. Avoid duplicate selection work from focus plus click notifications. A four-times-per-second full-card scan and an image subtree mounted for every missing-artwork card can cost CPU without fetching a single image. Reproduce these costs before choosing a replacement.

## Tests that protect user experience

Use `async with app.run_test(size=(80, 28)) as pilot` for headless operation; drive `pilot.press`, `pilot.click`, and `pilot.pause`. Repeat critical layout flows at 88×33 and 120×40. Assert focus, selected game, scroll continuity, action reachability, exact runtime arguments, and error recovery. [Testing](https://textual.textualize.io/guide/testing/).

Use controlled delayed fake backends to prove an old details result cannot overwrite a new selection and typing remains possible while I/O waits. Use operation-count assertions for unchanged refreshes and bounded loads instead of tight wall-clock thresholds that fail on slow CI.

Headless tests validate behavior and cell layout, not terminal graphics protocol, VM GL drivers, or perceived frame pacing. Manually inspect the actual terminal for image rendering and VM text size. Describe untested environments honestly.

## Completion evidence

Report the reproduced trigger, measured cause, changes, relevant tests, before/after results under the same workload, and remaining limits. Keep asynchronous cancellation, stale-result correctness, and widget lifetime checks in the regression coverage.

## Reconciliation lifetime

Use latest-wins cancellation for replaceable read requests, not for a sequence
of DOM removals and mounts. Cancelling `remove_children()`/`mount()` midway can
leave an empty or partial tree with a misleading cached signature. Keep desired
and fully applied snapshots separate, serialize reconciliation in one worker,
coalesce newer requests, and publish the applied signature only after the DOM
transaction finishes. Queue focus requests until the latest selection is
mounted, and respect the owning active screen before moving focus. Test this
with a gated mount and a newer status/selection request while it waits.
