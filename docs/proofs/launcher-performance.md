# Launcher performance investigation

Measured 2026-10-12 against the current local worktree, Python 3.13, Textual
8.2.6, synthetic 110-game catalog, 120×40 headless terminal. The catalog size
matches the live daemon's catalog. These synthetic records have no artwork URLs,
matching the metadata currently exposed by the live daemon; image network and
pixel-renderer performance are outside this measurement.

## Causes and fixes

- Every render cleared the entire OptionList and appended games individually,
  invalidating its caches repeatedly. The list now uses one bulk insertion when
  its address/name/status signature changes, and remains untouched otherwise.
  Selection and scroll are restored with highlight events suppressed during the
  transaction, preventing intermediate selection events and detail requests.
- Each card focus rendered the details twice. Focus now emits one choice event;
  the OptionList highlight drives the detail update. A click that changes focus
  uses the same path. Unchanged detail state skips widget updates.
- Selecting a card walked every card and reapplied selected styling. Cached
  address/index lookups now change only the previous and current card. Keyboard
  movement also uses those lookups rather than querying all cards each time.
- Every no-artwork card mounted an unused TerminalImage and its placeholder and
  renderer. Cards now mount these three widgets only when metadata supplies
  artwork. Artwork metadata appearing later mounts the widget without replacing
  the card; disappearing metadata returns to the fallback icon.
- Artwork visibility scanned the tree four times per second at idle. Scroll,
  resize, and catalog changes now request a coalesced check after layout. Image
  cache reads/downloads remain threaded, and only visible artwork is requested.
- Background workers queried the currently active screen, crashing when a modal
  or settings screen hid the launcher. Worker queries now target the stored
  launcher screen. Settings input messages cannot trigger catalog filtering.

## Comparative profile

The same initial workload opened the catalog, rendered five unchanged refreshes,
and pressed Right 15 times, using cProfile. These timings include profiling
and Pilot's intentional waits; they are useful for comparing the whole workload,
**not measurements of individual key latency**. The initial development harness
is now represented by `tests/launcher-performance.py`, which additionally covers
End/Home scrolling and event-loop heartbeat lag.

| Profile measurement | Before | After |
| --- | ---: | ---: |
| Python function calls, whole run | 19,555,127 | 8,115,073 |
| Profiler total elapsed, whole run | 11.963 s | 5.793 s |
| Open 110-card catalog | 3.607 s | 2.044 s |
| Five unchanged refreshes | 1.558 s | 0.516 s |
| 15 keyboard moves | 6.432 s | 2.886 s |
| Selection updates for 15 moves | 30 | 15 |
| Selection updates for five refreshes | 10 | 5 |

The no-artwork discovery tree fell from 882 to 552 descendant widgets: three
unused image widgets removed per card. Whole-run function calls fell about 59%.

## Unprofiled measurement

A separate final run of the repository benchmark script produced:

| Workload | Elapsed including Pilot waits | Heartbeat lag p95 | Maximum lag |
| --- | ---: | ---: | ---: |
| Open catalog | 0.901 s | 228.2 ms | 228.2 ms |
| Five unchanged refreshes | 0.293 s | 14.0 ms | 14.0 ms |
| 15 keyboard moves | 1.875 s | 31.3 ms | 93.6 ms |
| End/Home four times | 0.582 s | 28.2 ms | 33.5 ms |

Heartbeat is a 20 ms sleep; reported lag is delay beyond that deadline. Catalog
mount remains the largest stall, and there is no initial heartbeat baseline,
so these lag figures establish a future comparison rather than an improvement
claim. All catalog cards are still mounted; this change does not virtualize the
catalog. Larger catalogs and terminals with actual image graphics should be
measured separately before choosing a viewport-based widget implementation.

## Reproduction and regression coverage

```sh
KIOSK_PYTHON=$(nix eval --raw .#kwak-kiosk.python.outPath)/bin/python3
PYTHONPATH=apps/kiosk:apps/settings "$KIOSK_PYTHON" tests/launcher-performance.py
PYTHONPATH=apps/kiosk:apps/settings "$KIOSK_PYTHON" tests/launcher-performance.py --profile
PYTHONPATH=apps/kiosk:apps/settings "$KIOSK_PYTHON" -m unittest discover -s apps/kiosk/tests -p 'test_performance.py'
```

The four new regression tests use deterministic operation counts and state
assertions, avoiding machine-dependent speed thresholds. They cover unchanged
refreshes preserving widgets/selection with no list invalidation or extra detail
request, selection styling touching only the old/new card, late artwork and
status changes without card replacement, and refresh completion below a modal.
Focused launcher and discovery suites also passed. Package validation passed
65 kiosk tests; the separate settings suite passed 25 tests. Read-only live
kwakore checks passed at 88×33, 100×32, 120×40, and 160×50, including discovery,
search, library details, settings, and the 80×28 overlay. Installed records and
running windows remained unchanged. Graphical VM frame pacing was not measured.
