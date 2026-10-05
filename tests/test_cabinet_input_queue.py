"""Bounded cabinet input ingress and tick-aligned edge delivery."""
import queue
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cabinet_controls import CabinetEventQueue, KEY_IDS
from m68k_database_bridge import (
    TouchPacketStream, consume_cabinet_button_events, advance_button_pulses,
    publish_cabinet_buttons,
)
from test_cabinet_controls import MemoryRsp
from event_log_viewer import BridgeLogParser


def button(name="menu", down=True):
    return {"type": "button", "name": name, "down": down}


def touch(x, down=True):
    return {"type": "touch", "x": x, "y": 200, "down": down}


class CabinetInputQueueTests(unittest.TestCase):
    def test_full_queue_reserves_button_release(self):
        events = CabinetEventQueue(4)
        events.put_nowait(button())
        for _ in range(2):
            events.put_nowait({"type": "coin", "cents": 100})
        with self.assertRaises(queue.Full):
            events.put_nowait({"type": "coin", "cents": 100})
        events.put_nowait(button(down=False))
        self.assertEqual(events.qsize(), 4)
        self.assertEqual([events.get_nowait()["type"] for _ in range(4)],
                         ["button", "coin", "coin", "button"])

    def test_full_queue_reserves_touch_release(self):
        events = CabinetEventQueue(4)
        events.put_nowait(touch(100))
        for _ in range(2):
            events.put_nowait({"type": "coin", "cents": 100})
        with self.assertRaises(queue.Full):
            events.put_nowait(touch(200))
        events.put_nowait(touch(200, False))
        self.assertEqual(events.qsize(), 4)
        self.assertFalse(events.queue[-1]["down"])

    def test_movements_coalesce_without_overwriting_first_down(self):
        events = CabinetEventQueue(8)
        events.put_nowait(touch(0))
        for index in range(1, 10000):
            events.put_nowait(touch(index % 800))
        events.put_nowait(touch(399, False))
        self.assertEqual(events.qsize(), 3)
        self.assertEqual(events.get_nowait(), touch(0))
        self.assertEqual(events.get_nowait(), touch(399))
        self.assertEqual(events.get_nowait(), touch(399, False))

    def test_coalescing_never_crosses_release_press_boundary(self):
        events = CabinetEventQueue(12)
        for event in (touch(100), touch(100, False), touch(200), touch(210), touch(220)):
            events.put_nowait(event)
        self.assertEqual(list(events.queue),
                         [touch(100), touch(100, False), touch(200), touch(220)])

    def test_repeated_button_level_is_not_an_extra_click(self):
        events = CabinetEventQueue()
        for event in (button(), button(), button(down=False), button(down=False)):
            events.put_nowait(event)
        self.assertEqual(list(events.queue), [button(), button(down=False)])

    def test_other_devices_not_blocked_by_button_backlog(self):
        events = CabinetEventQueue()
        events.put_nowait(button())
        events.put_nowait({"type": "coin", "cents": 100})
        self.assertEqual(events.pop_types({"coin"})["type"], "coin")
        self.assertEqual(events.get_nowait(), button())

    def test_busy_button_does_not_allow_release_to_overtake_next_press(self):
        events = CabinetEventQueue()
        for event in (button(), button(down=False), button("start")):
            events.put_nowait(event)
        seen = set()
        self.assertEqual(events.pop_button({"menu"}, seen), button("start"))
        with self.assertRaises(queue.Empty):
            events.pop_button({"menu"}, seen)
        self.assertEqual(list(events.queue), [button(), button(down=False)])

    def test_rapid_clicks_publish_separate_press_release_cycles(self):
        events = CabinetEventQueue()
        for _ in range(3):
            events.put_nowait(button())
            events.put_nowait(button(down=False))
        rsp = MemoryRsp()
        pressed, edges, released, pulses = set(), set(), set(KEY_IDS), {}
        down_ticks, up_ticks, idle_ticks = [], [], []
        for tick in range(100):
            consumed = consume_cabinet_button_events(events, pressed, edges, released, pulses)
            for event in consumed:
                (down_ticks if event["down"] else up_ticks).append(tick)
            publish_cabinet_buttons(rsp, pressed, edges, released)
            if "menu" not in pressed:
                idle_ticks.append(tick)
            advance_button_pulses(pressed, edges, released, pulses)
        self.assertEqual(len(down_ticks), 3)
        self.assertEqual(len(up_ticks), 3)
        for first, second in zip(down_ticks, down_ticks[1:]):
            self.assertTrue(any(first < idle < second for idle in idle_ticks))
        self.assertTrue(events.empty())
        self.assertFalse(pressed)
        self.assertFalse(pulses)

    def test_touch_packets_stay_bounded_and_release_is_last(self):
        stream = TouchPacketStream()
        for index in range(10000):
            stream.request(index % 800, 200, True)
            self.assertLessEqual(len(stream.packets), 3)
        stream.request(399, 200, False)
        self.assertEqual(len(stream.packets), 4)
        self.assertFalse(stream.packets[-1][2])
        self.assertEqual(stream.packets[0][0], 0)

    def test_touch_contact_backpressure_does_not_exceed_limit(self):
        stream = TouchPacketStream()
        while stream.can_accept_contact():
            stream.request(100, 200, True)
            stream.request(100, 200, False)
        self.assertLessEqual(len(stream.packets), stream.MAX_PACKETS)
        with self.assertRaises(queue.Full):
            stream.request(200, 200, True)
        for _ in range(4):
            stream.packets.popleft()
        self.assertTrue(stream.can_accept_contact())

    def test_door_log_distinguishes_queued_from_applied(self):
        parser = BridgeLogParser()
        queued = parser.feed("DB_CABINET_DOOR_QUEUED door=open")
        applied = parser.feed("DB_CABINET_INPUT_STATE door=open mask=10 board_input=40 source=board-strobe")
        self.assertIn("vorgemerkt", queued[0].title)
        self.assertIn("offen", applied[0].title)

    def test_button_log_distinguishes_queued_from_applied(self):
        parser = BridgeLogParser()
        queued = parser.feed("DB_CABINET_BUTTON_QUEUED name=menu down=True queued=2")
        applied = parser.feed("DB_CABINET_BUTTON name=menu key_id=2 down=True source=board-scan queued=1")
        self.assertIn("vorgemerkt", queued[0].title)
        self.assertIn("betätigt", applied[0].title)


if __name__ == "__main__":
    unittest.main()
