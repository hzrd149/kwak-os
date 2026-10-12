#!/usr/bin/env python3
"""Read-only synthetic launcher benchmark; run with the kiosk's Python environment.

PYTHONPATH=apps/kiosk:apps/settings python tests/launcher-performance.py --profile
Pilot injects waits; phase durations are workload comparisons, not key latency.
"""
import argparse
import asyncio
import cProfile
import json
import pstats
import time

from discovery import Discovery
from kiosk import Kiosk


class Runtime:
    def __init__(self, count):
        self.games = [dict(address=str(i), name=f"Game {i}", available=True)
                      for i in range(count)]

    def listing(self, *args):
        return self.games

    def request(self, *args):
        return {"items": []}


async def benchmark(count):
    app = Kiosk(Runtime(count))
    selection_updates = 0
    original = app.update_selection
    def counted():
        nonlocal selection_updates
        selection_updates += 1
        return original()
    app.update_selection = counted
    phases = {}
    heartbeat_lags = []
    phase_lags = {}
    phase = "open_catalog"
    async def heartbeat():
        while True:
            deadline = time.monotonic() + 0.02
            await asyncio.sleep(0.02)
            lag = max(0, time.monotonic() - deadline)
            heartbeat_lags.append(lag)
            phase_lags.setdefault(phase, []).append(lag)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        task = asyncio.create_task(heartbeat())
        try:
            start = time.monotonic()
            await pilot.click("#store")
            await app.workers.wait_for_complete()
            await pilot.pause()
            phases["open_catalog"] = dict(seconds=time.monotonic()-start,
                                           selection_updates=selection_updates)
            selection_updates = 0
            phase = "unchanged_refresh_5"
            start = time.monotonic()
            for _ in range(5):
                app.render_games()
                await pilot.pause()
            phases["unchanged_refresh_5"] = dict(seconds=time.monotonic()-start,
                                                 selection_updates=selection_updates)
            selection_updates = 0
            phase = "navigation_15"
            start = time.monotonic()
            await pilot.press(*(["right"]*15))
            await pilot.pause()
            phases["navigation_15"] = dict(seconds=time.monotonic()-start,
                                           selection_updates=selection_updates)
            selection_updates = 0
            phase = "scroll_end_home_4"
            start = time.monotonic()
            await pilot.press("end", "home", "end", "home")
            await pilot.pause()
            phases["scroll_end_home_4"] = dict(seconds=time.monotonic()-start,
                                              selection_updates=selection_updates)
            browser = app.query_one(Discovery)
            widgets = len(browser.query("*"))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    for name, values in phase_lags.items():
        ordered = sorted(values)
        phases[name]["heartbeat_ms"] = dict(max=1000*max(ordered),
                                            p95=1000*ordered[min(len(ordered)-1, int(len(ordered)*.95))])
    lags = sorted(heartbeat_lags)
    return dict(games=count, terminal=[120,40], phases=phases,
                discovery_descendants=widgets,
                heartbeat_ms=dict(max=1000*max(lags, default=0),
                                  p95=1000*lags[min(len(lags)-1, int(len(lags)*.95))] if lags else 0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=110)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    profiler = cProfile.Profile() if args.profile else None
    if profiler:
        profiler.enable()
    result = asyncio.run(benchmark(args.games))
    if profiler:
        profiler.disable()
    print(json.dumps(result, indent=2))
    if profiler:
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(20)
