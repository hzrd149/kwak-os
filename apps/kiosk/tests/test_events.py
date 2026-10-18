import queue
import unittest

from kiosk import Kiosk
from support import settle

ADDRESS = "35129:" + "a" * 64 + ":game"
GAME = dict(address=ADDRESS, name="Evented", available=True)


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False
    def __iter__(self):
        while True:
            event = self.events.get(timeout=5)
            if event is None:
                return
            yield event
    def close(self):
        self.closed = True
        self.events.put(None)


class Runtime:
    def __init__(self):
        self.events_queue = queue.Queue()
        self.streams = []
        self.listings = 0
        self.window_polls = 0
    def listing(self, command, *args):
        self.listings += 1
        return [GAME]
    def windows(self):
        self.window_polls += 1
        return []
    def request(self, *args):
        return {}
    def events(self, types):
        assert "window.opened" in types
        stream = Stream(self.events_queue)
        self.streams.append(stream)
        return stream


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_update_running_state_and_coalesce_reloads(self):
        runtime = Runtime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            runtime.events_queue.put({"type": "window.opened", "address": ADDRESS, "window_id": "w1", "name": "Evented"})
            for _ in range(20):
                await pilot.pause(0.02)
                if ADDRESS in app.running:
                    break
            self.assertEqual(app.running[ADDRESS][0]["window_id"], "w1")
            self.assertTrue(app.events_healthy)
            self.assertTrue(app.activity.last_played(ADDRESS))
            polls = runtime.window_polls
            app.poll_tick()
            await pilot.pause()
            self.assertEqual(runtime.window_polls, polls)  # Polling relaxed while events flow.
            runtime.events_queue.put({"type": "window.closed", "address": ADDRESS, "window_id": "w1"})
            await pilot.pause(0.1)
            self.assertNotIn(ADDRESS, app.running)
            runtime.events_queue.put({"type": "updates.available", "items": [
                {"address": ADDRESS, "available_version": {"event_id": "f" * 64}}]})
            await pilot.pause(0.1)
            self.assertEqual(app.installed[ADDRESS]["update_available"]["event_id"], "f" * 64)
            listings = runtime.listings
            for kind in ("napplet.installed", "napplet.updated", "napplet.uninstalled"):
                runtime.events_queue.put({"type": kind, "address": ADDRESS})
            await pilot.pause(0.5)
            await settle(pilot)
            self.assertEqual(runtime.listings - listings, 1)
        self.assertTrue(runtime.streams[0].closed)

    async def test_stream_end_falls_back_to_polling_and_reconnects(self):
        runtime = Runtime()
        app = Kiosk(runtime)
        async with app.run_test(size=(100, 36)) as pilot:
            runtime.events_queue.put({"type": "sync.completed", "result": "ok"})
            await pilot.pause(0.1)
            self.assertTrue(app.events_healthy)
            runtime.events_queue.put(None)  # The service ended the stream.
            await pilot.pause(0.2)
            self.assertFalse(app.events_healthy)
            polls = runtime.window_polls
            app.poll_tick()
            await settle(pilot)
            self.assertGreater(runtime.window_polls, polls)
            for _ in range(40):
                await pilot.pause(0.05)
                if len(runtime.streams) > 1:
                    break
            self.assertEqual(len(runtime.streams), 2)


if __name__ == "__main__":
    unittest.main()
