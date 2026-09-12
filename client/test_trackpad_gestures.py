"""GestureEngine sticky-drag linger: idle timeout, not wall-clock from start."""

from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import trackpad_config
import trackpad_sink
from trackpad_sink import (
    GestureEngine,
    INERTIA_DECAY,
    INERTIA_DECAY_VREF,
    INERTIA_FIRM_V,
    INERTIA_FRICTION,
    INERTIA_KAPPA,
    INERTIA_SOFT_V,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    coast_decay_for_speed,
    coast_step,
)


def _c(slot: int, x: int, y: int) -> tuple[int, int, int, int, int, int, int]:
    return (slot, slot + 1, x, y, 20, 10, 10)


def _triple(x: int = 100, y: int = 200) -> list[tuple[int, int, int, int, int, int, int]]:
    return [_c(0, x, y), _c(1, x + 20, y), _c(2, x + 40, y)]


def _quad(x: int = 100, y: int = 200) -> list[tuple[int, int, int, int, int, int, int]]:
    return _triple(x, y) + [_c(3, x + 60, y)]


class StickyDragTests(unittest.TestCase):
    def setUp(self) -> None:
        self.btns: list[int] = []
        self.moves: list[tuple[int, int]] = []
        self.wheels: list[dict] = []
        self.ctrls: list[bool] = []
        self.cfg = trackpad_config.TrackpadConfig(drag_idle_ms=400, three_finger_drag=True)
        self.patches = [
            patch.object(trackpad_sink, "mouse_btn", side_effect=self.btns.append),
            patch.object(trackpad_sink, "mouse_move", side_effect=lambda dx, dy: self.moves.append((dx, dy))),
            patch.object(trackpad_sink, "mouse_wheel", side_effect=lambda **kw: self.wheels.append(kw)),
            patch.object(trackpad_sink, "key_ctrl", side_effect=self.ctrls.append),
            patch.object(trackpad_sink, "key_chord"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        trackpad_sink.OLE_PARK = False
        self.addCleanup(lambda: setattr(trackpad_sink, "OLE_PARK", True))
        self.engine = GestureEngine(self.cfg)

    def _feed(self, contacts, now: float, buttons: int = 0) -> None:
        self.engine.feed(buttons, contacts, now=now)

    def _arm_drag3(self, t0: float = 1.0) -> float:
        self._feed(_triple(100, 200), t0)
        t = t0
        for step in range(1, 8):
            t = t0 + step * 0.01
            self._feed(_triple(100 + step * 12, 200), t)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.engine.mode, "drag3")
        return t

    def test_flicker_three_to_two_keeps_left_down(self) -> None:
        t = self._arm_drag3()
        downs = self.btns.count(MOUSEEVENTF_LEFTDOWN)
        self._feed(_triple(184, 200)[:2], t + 0.01)
        self._feed(
            [_c(0, 196, 200), _c(1, 216, 200)],
            t + 0.03,
        )
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), downs)

    def test_lift_past_idle_releases(self) -> None:
        t = self._arm_drag3()
        self._feed([], t + 0.01)
        self.assertTrue(self.engine.left_down)
        self._feed([], t + 0.20)
        self.assertTrue(self.engine.left_down)
        self._feed([], t + 0.50)
        self.assertFalse(self.engine.left_down)
        self.assertFalse(self.engine.sticky)
        self.assertIn(MOUSEEVENTF_LEFTUP, self.btns)

    def test_lift_then_resume_before_idle(self) -> None:
        t = self._arm_drag3()
        self._feed([], t + 0.01)
        self._feed(_triple(220, 210), t + 0.20)
        self._feed(_triple(240, 210), t + 0.22)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)

    def test_stationary_three_finger_hold_does_not_expire(self) -> None:
        t = self._arm_drag3()
        held = _triple(184, 200)
        for step in range(1, 12):
            self._feed(held, t + step * 0.2)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)

    def test_two_finger_only_stays_scroll(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        self._feed([_c(0, 104, 204), _c(1, 124, 204)], 1.02)
        self.assertFalse(self.engine.left_down)
        self.assertFalse(self.engine.sticky)
        self.assertEqual(self.engine.mode, "scroll")

    def test_third_finger_during_scroll_starts_mark(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        self._feed([_c(0, 100, 210), _c(1, 120, 210)], 1.02)
        self.assertEqual(self.engine.mode, "scroll")
        self._feed(_triple(100, 210), 1.03)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.engine.mode, "drag3")
        self._feed(pair, 1.04)
        self.assertTrue(self.engine.left_down)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)

    def test_fourth_finger_does_not_release_mark(self) -> None:
        self._feed(_triple(100, 200), 1.0)
        downs = self.btns.count(MOUSEEVENTF_LEFTDOWN)
        self._feed(_quad(100, 200), 1.01)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), downs)

    def test_rel_ignored_during_drag3(self) -> None:
        self._feed(_triple(100, 200), 1.0)
        self.moves.clear()
        self.engine.feed(0, _triple(112, 200), rel=(20, 0, 0, 0), now=1.02)
        self.assertTrue(self.moves)
        self.assertFalse(any(dx == 20 for dx, _dy in self.moves))

    def test_three_finger_stroke_does_not_park_ole(self) -> None:
        parks: list[int] = []
        unparks: list[int] = []
        p1 = patch.object(trackpad_sink, "park_ole_drag", side_effect=lambda: parks.append(1))
        p2 = patch.object(trackpad_sink, "unpark_ole_drag", side_effect=lambda: unparks.append(1))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self._feed(_triple(100, 200), 1.0)
        self._feed(_triple(112, 200), 1.01)
        self._feed(_triple(124, 200), 1.02)
        self._feed(_triple(124, 200)[:2], 1.03)
        self.assertEqual(len(parks), 0)
        self._feed([], 1.04)
        self._feed([], 1.50)
        self.assertFalse(self.engine.left_down)
        self.assertEqual(len(parks), 0)

    def test_one_finger_flicker_during_drag3_keeps_mark(self) -> None:
        t = self._arm_drag3()
        downs = self.btns.count(MOUSEEVENTF_LEFTDOWN)
        self._feed([_c(0, 184, 200)], t + 0.01)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self._feed(_triple(184, 200), t + 0.03)
        self.assertTrue(self.engine.left_down)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), downs)

    def test_spread_chord_flicker_does_not_teleport(self) -> None:
        wide = [_c(0, 100, 200), _c(1, 2000, 200), _c(2, 4000, 200)]
        self._feed(wide, 1.0)
        self._feed([_c(0, 112, 200), _c(1, 2012, 200), _c(2, 4012, 200)], 1.02)
        self.assertTrue(self.engine.sticky)
        self.moves.clear()
        self._feed([_c(0, 112, 200), _c(1, 2012, 200)], 1.03)
        self.assertTrue(self.engine.left_down)
        self.assertEqual(self.moves, [])
        self.moves.clear()
        self._feed([_c(0, 124, 200), _c(1, 2024, 200)], 1.04)
        self.assertTrue(self.moves)
        self.assertTrue(all(math.hypot(dx, dy) < 20 for dx, dy in self.moves))

    def test_one_finger_flicker_does_not_rebase_to_lone_contact(self) -> None:
        wide = [_c(0, 100, 200), _c(1, 2000, 200), _c(2, 4000, 200)]
        self._feed(wide, 1.0)
        self._feed([_c(0, 112, 200), _c(1, 2012, 200), _c(2, 4012, 200)], 1.02)
        self.moves.clear()
        self._feed([_c(0, 112, 200)], 1.03)
        self._feed([_c(0, 112, 200)], 1.04)
        self._feed([_c(0, 124, 200), _c(1, 2024, 200), _c(2, 4024, 200)], 1.05)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertTrue(all(math.hypot(dx, dy) < 20 for dx, dy in self.moves))

    def test_sustained_one_finger_after_drag3_ends_mark(self) -> None:
        t = self._arm_drag3()
        self._feed([_c(0, 184, 200)], t + 0.01)
        self.assertTrue(self.engine.left_down)
        self._feed([_c(0, 184, 200)], t + 0.10)
        self.assertFalse(self.engine.left_down)
        self.assertFalse(self.engine.sticky)
        self.assertEqual(self.engine.mode, "pointer")

    def test_three_finger_lift_does_not_swipe(self) -> None:
        chords: list[list[int]] = []
        with patch.object(trackpad_sink, "key_chord", side_effect=lambda vks: chords.append(list(vks))):
            self.cfg.swipe_pages = "back-forward"
            self.engine.cfg.swipe_pages = "back-forward"
            self._feed(_triple(100, 200), 1.0)
            t = 1.0
            for step in range(1, 10):
                t = 1.0 + step * 0.01
                self._feed(_triple(100 + step * 40, 200), t)
            self._feed([], t + 0.50)
        self.assertEqual(chords, [])

    def test_three_finger_land_then_two_keeps_mark(self) -> None:
        self._feed(_triple(100, 200), 1.0)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.engine.mode, "drag3")
        self._feed(_triple(100, 200)[:2], 1.01)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)

    def test_one_two_three_held_then_two_is_one_stroke(self) -> None:
        self._feed([_c(0, 100, 200)], 1.0)
        self._feed([_c(0, 100, 200), _c(1, 120, 200)], 1.01)
        self._feed(_triple(100, 200), 1.02)
        self._feed(_triple(112, 200), 1.03)
        self._feed(_triple(124, 200), 1.04)
        self._feed(_triple(124, 200)[:2], 1.05)
        self.assertTrue(self.engine.left_down)
        self.assertTrue(self.engine.sticky)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), 1)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)

    def test_tiny_steps_arm_sticky_then_flicker_keeps_mark(self) -> None:
        self._feed(_triple(100, 200), 1.0)
        t = 1.0
        for step in range(1, 20):
            t = 1.0 + step * 0.01
            self._feed(_triple(100 + step, 200), t)
        self.assertTrue(self.engine.sticky)
        downs = self.btns.count(MOUSEEVENTF_LEFTDOWN)
        self._feed(_triple(119, 200)[:2], t + 0.01)
        self._feed([_c(0, 122, 200), _c(1, 142, 200)], t + 0.03)
        self.assertTrue(self.engine.left_down)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), downs)

    def test_drag3_dwell_arms_sticky_without_big_jumps(self) -> None:
        held = _triple(100, 200)
        self._feed(held, 1.0)
        self._feed(held, 1.01)
        self._feed(held, 1.02)
        self._feed(held, 1.03)
        self.assertTrue(self.engine.sticky)
        self._feed(held[:2], 1.04)
        self.assertTrue(self.engine.left_down)
        self.assertEqual(self.engine.mode, "drag3")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 0)

    def test_one_finger_click_and_drag_is_not_a_mark(self) -> None:
        self.cfg.click_drag = True
        self.engine.cfg.click_drag = True
        self._feed([_c(0, 100, 200)], 1.0, buttons=1)
        t = 1.0
        for step in range(1, 8):
            t = 1.0 + step * 0.01
            self._feed([_c(0, 100 + step * 12, 200)], t, buttons=1)
        self.assertFalse(self.engine.left_down)
        self.assertFalse(self.engine.sticky)
        self.assertEqual(self.engine.mode, "pointer")
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), 1)
        self.assertEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 1)
        self._feed([_c(0, 184, 200), _c(1, 204, 200)], t + 0.01, buttons=1)
        self.assertFalse(self.engine.left_down)
        self.assertNotEqual(self.engine.mode, "clickdrag")

    def test_one_finger_move_after_mark_ends_drag(self) -> None:
        t = self._arm_drag3()
        self._feed([], t + 0.01)
        self.assertTrue(self.engine.sticky)
        self._feed([_c(0, 180, 200)], t + 0.04)
        self.assertTrue(self.engine.linger_tap)
        self._feed([_c(0, 260, 200)], t + 0.06)
        self._feed([_c(0, 340, 200)], t + 0.08)
        self.assertFalse(self.engine.sticky)
        self.assertFalse(self.engine.left_down)
        self.assertEqual(self.engine.mode, "pointer")

    def test_scroll_small_steps_emit_fine_ticks(self) -> None:
        pair0 = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair0, 1.0)
        t = 1.0
        for step in range(1, 16):
            t = 1.0 + step * 0.008
            y = 200 + step * 3
            self._feed([_c(0, 100, y), _c(1, 120, y)], t)
        self.assertGreaterEqual(len(self.wheels), 3)
        verts = [w.get("vertical") or 0 for w in self.wheels]
        self.assertTrue(any(abs(v) and abs(v) < 120 for v in verts))
        self.assertFalse(self.engine.left_down)

    def _flick_scroll(self, t0: float = 1.0) -> float:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, t0)
        t = t0
        for step in range(1, 8):
            t = t0 + step * 0.008
            y = 200 + step * 28
            self._feed([_c(0, 100, y), _c(1, 120, y)], t)
        return t

    def _soft_flick_scroll(self, t0: float = 1.0) -> float:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, t0)
        t = t0
        for step in range(1, 14):
            t = t0 + step * 0.008
            y = 200 + step
            self._feed([_c(0, 100, y), _c(1, 120, y)], t)
        return t

    def test_fast_flick_coasts_after_lift(self) -> None:
        t = self._flick_scroll()
        before = len(self.wheels)
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        self.assertAlmostEqual(abs(self.engine.coast_vy), INERTIA_FIRM_V, places=4)
        self.assertAlmostEqual(
            self.engine.inertia_decay,
            coast_decay_for_speed(INERTIA_FIRM_V),
            places=4,
        )
        self._feed([], t + 0.03)
        self._feed([], t + 0.06)
        self.assertGreater(len(self.wheels), before)

    def test_slow_lift_does_not_coast(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        self._feed([_c(0, 100, 202), _c(1, 120, 202)], 1.04)
        self._feed([_c(0, 100, 204), _c(1, 120, 204)], 1.08)
        self._feed([], 1.10)
        self.assertFalse(self.engine.coasting)

    def test_touch_cancels_coast(self) -> None:
        t = self._flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        self._feed([_c(0, 100, 400)], t + 0.02)
        self.assertFalse(self.engine.coasting)
        after = len(self.wheels)
        self._feed([], t + 0.05)
        self._feed([], t + 0.08)
        self.assertEqual(len(self.wheels), after)

    def _wheel_abs(self, key: str) -> int:
        return sum(abs(w.get(key) or 0) for w in self.wheels)

    def test_gentle_flick_coasts(self) -> None:
        t = self._soft_flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        self.assertAlmostEqual(abs(self.engine.coast_vy), INERTIA_SOFT_V, places=4)
        self.assertGreater(self._wheel_abs("vertical"), 0)
        clock = t + 0.008
        while clock < t + 0.40:
            clock += 0.008
            self._feed([], clock)
        self.assertTrue(self.engine.coasting)
        soft_ticks = [abs(w.get("vertical") or 0) for w in self.wheels]
        self.assertTrue(all(tick <= 8 for tick in soft_ticks))
        while clock < t + 1.30:
            clock += 0.008
            self._feed([], clock)
        self.assertFalse(self.engine.coasting)

    def test_flick_glide_is_long_and_modest(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        t = 1.0
        for step in range(1, 8):
            t = 1.0 + step * 0.008
            y = 200 + step * 10
            self._feed([_c(0, 100, y), _c(1, 120, y)], t)
        live = self._wheel_abs("vertical")
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        self.assertAlmostEqual(abs(self.engine.coast_vy), INERTIA_FIRM_V, places=4)
        coast_start = len(self.wheels)
        clock = t + 0.008
        while clock < t + 0.70:
            clock += 0.008
            self._feed([], clock)
        self.assertTrue(self.engine.coasting)
        coast_ticks = [abs(w.get("vertical") or 0) for w in self.wheels[coast_start:]]
        self.assertTrue(coast_ticks)
        self.assertTrue(all(tick <= 8 for tick in coast_ticks))
        for prev, nxt in zip(coast_ticks, coast_ticks[1:]):
            self.assertLessEqual(nxt, prev + 1)
        mid = len(self.wheels)
        while clock < t + 2.00:
            clock += 0.008
            self._feed([], clock)
        self.assertFalse(self.engine.coasting)
        second_half = [abs(w.get("vertical") or 0) for w in self.wheels[mid:]]
        self.assertTrue(any(tick > 0 for tick in second_half))
        coast = self._wheel_abs("vertical") - live
        self.assertGreater(coast, 0)

    def test_slam_flick_early_ticks_stay_modest(self) -> None:
        t = self._flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        coast_start = len(self.wheels)
        clock = t + 0.008
        while clock < t + 0.108:
            clock += 0.008
            self._feed([], clock)
        early = [abs(w.get("vertical") or 0) for w in self.wheels[coast_start:]]
        self.assertTrue(early)
        self.assertTrue(all(tick <= 8 for tick in early))
        self.assertTrue(all(tick <= 5 for tick in early))
        self.assertTrue(self.engine.coasting)

    def test_vertical_flick_locks_axis(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        t = 1.0
        for step in range(1, 8):
            t = 1.0 + step * 0.008
            self._feed([_c(0, 100 + step, 200 + step * 28), _c(1, 120 + step, 200 + step * 28)], t)
        horiz_before = self._wheel_abs("horizontal")
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        for step in range(1, 8):
            self._feed([], t + 0.008 + step * 0.008)
        self.assertEqual(self._wheel_abs("horizontal"), horiz_before)
        self.assertGreater(self._wheel_abs("vertical"), 0)

    def test_late_empty_frame_does_not_dump_wheel(self) -> None:
        t = self._flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        before = self._wheel_abs("vertical")
        self._feed([], t + 0.208)
        dumped = self._wheel_abs("vertical") - before
        self.assertGreater(dumped, 0)
        self.assertLess(dumped, 24)

    def test_tap_survives_rel_jitter(self) -> None:
        self.engine.feed(0, [_c(0, 100, 200)], rel=(3, 1, 0, 0), now=1.0)
        self.engine.feed(0, [_c(0, 100, 200)], rel=(2, 0, 0, 0), now=1.05)
        self.engine.feed(0, [], rel=(0, 0, 0, 0), now=1.08)
        self.assertIn(MOUSEEVENTF_LEFTDOWN, self.btns)
        self.assertIn(MOUSEEVENTF_LEFTUP, self.btns)

    def test_two_finger_tap_does_not_click(self) -> None:
        self.cfg.secondary = "two-finger"
        self.engine.cfg.secondary = "two-finger"
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        self._feed(pair, 1.04)
        self._feed([], 1.06)
        self.assertNotIn(MOUSEEVENTF_RIGHTDOWN, self.btns)
        self.assertNotIn(MOUSEEVENTF_LEFTDOWN, self.btns)
        self.assertFalse(self.engine.left_down)

    def test_two_finger_physical_click_is_right(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0, buttons=1)
        self._feed([_c(0, 100, 212), _c(1, 120, 212)], 1.02, buttons=1)
        self._feed([], 1.04, buttons=1)
        self.assertEqual(self.btns.count(MOUSEEVENTF_RIGHTDOWN), 1)
        self.assertEqual(self.btns.count(MOUSEEVENTF_RIGHTUP), 1)
        self.assertNotIn(MOUSEEVENTF_LEFTDOWN, self.btns)
        self.assertFalse(self.engine.left_down)
        self.assertEqual(self.engine.mode, "")

    def test_two_finger_click_while_scrolling_is_right(self) -> None:
        pair = [_c(0, 100, 200), _c(1, 120, 200)]
        self._feed(pair, 1.0)
        self._feed([_c(0, 100, 220), _c(1, 120, 220)], 1.02)
        self.assertEqual(self.engine.mode, "scroll")
        self._feed([_c(0, 100, 230), _c(1, 120, 230)], 1.04, buttons=1)
        self.assertEqual(self.btns.count(MOUSEEVENTF_RIGHTDOWN), 1)
        self.assertEqual(self.engine.mode, "scroll")
        self.assertNotIn(MOUSEEVENTF_LEFTDOWN, self.btns)

    def test_tap_survives_one_frame_second_finger(self) -> None:
        self._feed([_c(0, 100, 200)], 1.0)
        self._feed([_c(0, 100, 200), _c(1, 118, 202)], 1.02)
        self._feed([_c(0, 100, 200)], 1.03)
        self._feed([], 1.05)
        self.assertIn(MOUSEEVENTF_LEFTDOWN, self.btns)
        self.assertIn(MOUSEEVENTF_LEFTUP, self.btns)
        self.assertNotIn(MOUSEEVENTF_RIGHTDOWN, self.btns)

    def test_tap_during_linger_ends_mark(self) -> None:
        t = self._arm_drag3()
        self._feed([], t + 0.01)
        self.assertTrue(self.engine.sticky)
        self._feed([_c(0, 180, 200)], t + 0.04)
        self._feed([], t + 0.08)
        self.assertFalse(self.engine.sticky)
        self.assertFalse(self.engine.left_down)
        self.assertGreaterEqual(self.btns.count(MOUSEEVENTF_LEFTUP), 1)
        self.assertGreaterEqual(self.btns.count(MOUSEEVENTF_LEFTDOWN), 2)

    def test_firm_coast_last_400ms_emits(self) -> None:
        t = self._flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        clock = t + 0.008
        frames: list[tuple[float, int, float, bool]] = []
        while clock < t + 2.50:
            speed = math.hypot(self.engine.coast_vx, self.engine.coast_vy)
            before = self._wheel_abs("vertical")
            clock += 0.008
            self._feed([], clock)
            emitted = self._wheel_abs("vertical") - before
            frames.append((clock, emitted, speed, self.engine.coasting))
            if not self.engine.coasting:
                break
        self.assertFalse(self.engine.coasting)
        rest_at = frames[-1][0]
        last_400 = [emitted for ts, emitted, _, _ in frames if ts >= rest_at - 0.40]
        self.assertTrue(last_400)
        self.assertTrue(any(tick > 0 for tick in last_400))
        empty_run = 0
        max_empty = 0
        for _, emitted, speed, _coasting in frames:
            # Below ~7 units/s an 8 ms frame adds <1 integer tick. That
            # quantization is not the ice; stall above it would be.
            if speed > 8.0:
                if emitted == 0:
                    empty_run += 1
                    max_empty = max(max_empty, empty_run)
                else:
                    empty_run = 0
            else:
                empty_run = 0
        self.assertLess(max_empty, 5)

    def test_two_finger_spread_scrolls_not_pinch(self) -> None:
        self.cfg.pinch_zoom = True
        self.engine.cfg.pinch_zoom = True
        self._feed([_c(0, 100, 200), _c(1, 120, 200)], 1.0)
        t = 1.0
        for step in range(1, 12):
            t = 1.0 + step * 0.008
            gap = 20 + step * 20
            y = 200 + step * 8
            self._feed([_c(0, 100, y), _c(1, 100 + gap, y)], t)
        self.assertNotEqual(self.engine.mode, "pinch")
        self.assertEqual(self.engine.mode, "scroll")
        self.assertFalse(self.engine.ctrl_down)
        self.assertNotIn(True, self.ctrls)
        self.assertGreater(self._wheel_abs("vertical"), 0)
        self.assertTrue(all(abs(w.get("vertical") or 0) < 120 for w in self.wheels))

    def test_flick_force_slider_changes_firm_snap(self) -> None:
        self.cfg.flick_force = 10
        self.engine._apply_cfg()
        t = self._flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        self.assertGreater(self.engine.inertia_firm_v, INERTIA_FIRM_V)
        self.assertAlmostEqual(abs(self.engine.coast_vy), self.engine.inertia_firm_v, places=4)
        self.assertAlmostEqual(
            self.engine.inertia_decay,
            coast_decay_for_speed(self.engine.inertia_firm_v, friction=self.engine.inertia_friction),
            places=4,
        )

    def test_flick_friction_slider_changes_decay_not_snap(self) -> None:
        self.cfg.flick_friction = 10
        self.engine._apply_cfg()
        t = self._flick_scroll()
        self._feed([], t + 0.008)
        self.assertTrue(self.engine.coasting)
        self.assertAlmostEqual(abs(self.engine.coast_vy), INERTIA_FIRM_V, places=4)
        self.assertGreater(self.engine.inertia_friction, INERTIA_FRICTION)
        self.assertGreater(
            self.engine.inertia_decay,
            coast_decay_for_speed(INERTIA_FIRM_V, friction=INERTIA_FRICTION),
        )


class ConfigClampTests(unittest.TestCase):
    def test_clamp_forces_pinch_zoom_off(self) -> None:
        cfg = trackpad_config._clamp(trackpad_config.TrackpadConfig(pinch_zoom=True))
        self.assertFalse(cfg.pinch_zoom)

    def test_clamp_forces_two_finger_secondary(self) -> None:
        cfg = trackpad_config._clamp(trackpad_config.TrackpadConfig(secondary="off"))
        self.assertEqual(cfg.secondary, "two-finger")

    def test_clamp_forces_three_finger_drag_on(self) -> None:
        cfg = trackpad_config._clamp(trackpad_config.TrackpadConfig(three_finger_drag=False))
        self.assertTrue(cfg.three_finger_drag)

    def test_flick_sliders_map_physics(self) -> None:
        cfg = trackpad_config.TrackpadConfig()
        self.assertAlmostEqual(cfg.firm_v(), INERTIA_FIRM_V)
        self.assertAlmostEqual(cfg.table_friction(), INERTIA_FRICTION)
        cfg.flick_force = 10
        self.assertGreater(cfg.firm_v(), INERTIA_FIRM_V)
        cfg.flick_friction = 10
        self.assertGreater(cfg.table_friction(), INERTIA_FRICTION)
        cfg = trackpad_config._clamp(trackpad_config.TrackpadConfig(flick_force=99, flick_friction=-3))
        self.assertEqual(cfg.flick_force, 10)
        self.assertEqual(cfg.flick_friction, 0)


class CoastStepTests(unittest.TestCase):
    def test_decay_scales_inverse_with_throw(self) -> None:
        k_ref = coast_decay_for_speed(INERTIA_DECAY_VREF)
        self.assertAlmostEqual(k_ref, INERTIA_DECAY, places=6)
        k_firm = coast_decay_for_speed(INERTIA_FIRM_V)
        k_soft = coast_decay_for_speed(INERTIA_SOFT_V)
        self.assertAlmostEqual(k_firm * INERTIA_FIRM_V, INERTIA_KAPPA * INERTIA_FRICTION, places=4)
        self.assertAlmostEqual(k_soft * INERTIA_SOFT_V, INERTIA_KAPPA * INERTIA_FRICTION, places=4)
        self.assertLess(k_firm, k_ref)

    def test_decay_follows_friction(self) -> None:
        k_lo = coast_decay_for_speed(INERTIA_FIRM_V, friction=20.0)
        k_hi = coast_decay_for_speed(INERTIA_FIRM_V, friction=32.0)
        self.assertGreater(k_hi, k_lo)
        self.assertAlmostEqual(k_hi * INERTIA_FIRM_V, INERTIA_KAPPA * 32.0, places=4)

    def test_each_frame_slows(self) -> None:
        speed = INERTIA_FIRM_V
        decay = coast_decay_for_speed(speed)
        dt = 0.008
        for _ in range(400):
            v_new, dist = coast_step(speed, dt, decay=decay)
            self.assertLess(v_new, speed)
            self.assertGreater(dist, 0.0)
            if v_new <= 0.0:
                return
            speed = v_new
        self.fail("firm coast did not rest")

    def test_soft_rests_sooner_than_firm(self) -> None:
        def rest_time(v0: float) -> float:
            speed = v0
            decay = coast_decay_for_speed(v0)
            elapsed = 0.0
            dt = 0.008
            for _ in range(400):
                v_new, _dist = coast_step(speed, dt, decay=decay)
                elapsed += dt
                if v_new <= 0.0:
                    return elapsed
                speed = v_new
            self.fail("did not rest")
            return elapsed

        self.assertLess(rest_time(INERTIA_SOFT_V), rest_time(INERTIA_FIRM_V))

    def test_overshoot_frame_zeroes_with_last_dist(self) -> None:
        v_new, dist = coast_step(1.0, 0.10)
        self.assertEqual(v_new, 0.0)
        self.assertGreater(dist, 0.0)


if __name__ == "__main__":
    unittest.main()
