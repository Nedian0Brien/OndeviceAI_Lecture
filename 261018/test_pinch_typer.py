"""pinch_typer 검증. 카메라·MediaPipe 없이 표준 라이브러리만으로 실행된다."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from cheonjiin import CheonjiinIME
from pinch_typer import (FingerPinch, HandSignal, Kalman1D, Orientation, PinchTyper, hand_signal, palm_facing,
                         pinch_ratios, replay)


FIXTURES = Path(__file__).resolve().with_name("fixtures") / "pinch-typer-keys.json"
DT = 1 / 30
OPEN = (0.6, 0.6, 0.65, 0.7)


def sig(ratios=OPEN, pose="open", facing=0.8, curled=(False,) * 4, anchor=(0.5, 0.5)):
    return HandSignal(tuple(ratios), tuple(curled), pose, facing, tuple(anchor))


def pinch_ratios_for(finger, value=0.08):
    ratios = list(OPEN)
    ratios[finger] = value
    return tuple(ratios)


class Runner:
    """프레임 묶음을 30fps로 흘려 넣고 이벤트를 모은다."""

    def __init__(self, typer=None):
        self.typer = typer or PinchTyper()
        self.now = 0.0
        self.events = []

    def frames(self, count, left=None, right=None):
        for _ in range(count):
            self.now += DT
            self.events += self.typer.update({"Left": left, "Right": right}, self.now)
        return self

    def ready(self, left_facing=0.8, right_facing=0.8):
        return self.frames(10, sig(facing=left_facing), sig(facing=right_facing))

    def kinds(self, kind):
        return [event for event in self.events if event[0] == kind]


class FixtureTest(unittest.TestCase):
    def test_key_fixtures(self):
        data = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["cases"]), 10)
        for case in data["cases"]:
            with self.subTest(case["name"]):
                self.assertEqual(replay(case["steps"]).ime.text, case["text"])

    def test_cancel_restores_previous_text(self):
        typer = replay([["tap", "Right", 0], ["tap", "Left", 0], ["tap", "Left", 1],
                        ["tap", "Right", 1], ["tap", "Right", 1], ["fist", "Right"]])
        self.assertEqual(typer.ime.text, "가")
        self.assertIsNone(typer.pending)

    def test_put_symbol_undo(self):
        ime = CheonjiinIME()
        ime.put_symbol(".")
        ime.put_symbol(",", replace=True)
        self.assertEqual(ime.text, ",")
        ime.backspace()
        self.assertEqual(ime.text, ".")
        ime.backspace()
        self.assertEqual(ime.text, "")


class FingerPinchTest(unittest.TestCase):
    def test_counts_on_release(self):
        pinch, now, events = FingerPinch(), 0.0, []
        for ratios in [OPEN] * 3 + [pinch_ratios_for(1)] * 4 + [OPEN] * 2:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [1])

    def test_needs_open_hand_first(self):
        pinch, now = FingerPinch(), 0.0
        results = []
        for ratios in [pinch_ratios_for(0)] * 4 + [OPEN] * 2:
            now += DT
            results.append(pinch.update(sig(ratios), now))
        self.assertEqual([r for r in results if r], [])

    def test_ambiguous_between_fingers_is_ignored(self):
        pinch, now, results = FingerPinch(), 0.0, []
        for ratios in [OPEN] * 2 + [(0.6, 0.15, 0.17, 0.7)] * 4 + [OPEN] * 2:
            now += DT
            results.append(pinch.update(sig(ratios), now))
        self.assertEqual([r for r in results if r], [])

    def test_neighbor_also_close(self):
        # 중지를 꼬집으면 약지 끝마디도 가까워진다. 거리 차이(margin)만 넘으면 중지로 센다.
        pinch, now, events = FingerPinch(), 0.0, []
        for ratios in [OPEN] * 2 + [(0.45, 0.10, 0.16, 0.5)] * 4 + [OPEN] * 2:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [1])

    def test_fist_motion_spoils_contact(self):
        pinch, now, results = FingerPinch(), 0.0, []
        frames = [sig()] * 2 + [sig(pinch_ratios_for(0), pose="closing", curled=(False, True, True, False))] * 3 \
            + [sig()] * 2
        for frame in frames:
            now += DT
            results.append(pinch.update(frame, now))
        self.assertEqual([r for r in results if r], [])

    def test_rapid_taps_without_full_release_count_each(self):
        # 2026-10-22 로그(오른손 약지): 꼬집기 사이에 0.33~0.37까지만 벌려도 각각 1회다.
        pinch, now, events = FingerPinch(), 0.0, []
        values = [0.6] * 3 + [0.13] * 4 + [0.33] * 3 + [0.04] * 4 + [0.34] * 3 + [0.08] * 4 + [0.37] * 3 + [0.6] * 2
        for value in values:
            now += DT
            result = pinch.update(sig((0.6, 0.6, value, 0.7)), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2, 2, 2])

    def test_single_frame_spike_is_not_a_release(self):
        # 같은 로그(왼손 약지): 0.17 → 0.58(한 프레임) → 0.18은 한 번의 꼬집기다.
        pinch, now, events = FingerPinch(), 0.0, []
        for value in [0.6] * 3 + [0.17] * 3 + [0.58] + [0.18] * 4 + [0.6] * 3:
            now += DT
            result = pinch.update(sig((0.6, 0.6, value, 0.7)), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2])

    def test_jitter_inside_a_held_pinch_is_not_a_release(self):
        pinch, now, events = FingerPinch(), 0.0, []
        for value in [0.6] * 3 + [0.04, 0.17, 0.05, 0.16, 0.06, 0.2, 0.05] + [0.6] * 3:
            now += DT
            result = pinch.update(sig((0.6, 0.6, value, 0.7)), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2])

    def test_lost_hand_drops_contact(self):
        pinch, now = FingerPinch(), 0.0
        for frame in [sig()] * 2 + [sig(pinch_ratios_for(2))] * 3 + [None] + [sig()] * 3:
            now += DT
            self.assertIsNone(pinch.update(frame, now))


class OrientationTest(unittest.TestCase):
    def test_dwell_and_hysteresis(self):
        orientation, now = Orientation(dwell=0.15), 0.0
        states = []
        for facing in [0.8] * 6 + [0.2] * 2 + [-0.8] * 3 + [-0.8] * 4:
            now += DT
            states.append(orientation.update(facing, now))
        self.assertEqual(states[0], "turning")
        self.assertEqual(states[5], "palm")
        self.assertEqual(states[6], "palm")  # 0.2 > 0.25/2: 히스테리시스로 유지
        self.assertEqual(states[8], "palm")  # 손등이 dwell만큼 이어지기 전에는 앞 방향을 유지
        self.assertEqual(states[-1], "back")

    def test_single_frame_glitch_keeps_state(self):
        orientation, now = Orientation(dwell=0.15), 0.0
        states = []
        for facing in [0.6] * 6 + [-0.23] + [0.6] * 3:
            now += DT
            states.append(orientation.update(facing, now))
        self.assertEqual(states[5:], ["palm"] * 5)


class GeometryTest(unittest.TestCase):
    @staticmethod
    def points(index_x, pinky_x):
        p = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
        p[0] = SimpleNamespace(x=0.5, y=0.8, z=0.0)
        p[5] = SimpleNamespace(x=index_x, y=0.6, z=0.0)
        p[17] = SimpleNamespace(x=pinky_x, y=0.62, z=0.0)
        return p

    def test_palm_facing_signs(self):
        # 1인칭(반전 없음): 오른손 손바닥이 눈을 향하면 엄지(검지 뿌리)가 보는 사람 기준 오른쪽에 있다.
        self.assertGreater(palm_facing(self.points(0.56, 0.44), "Right"), 0.25)
        self.assertLess(palm_facing(self.points(0.44, 0.56), "Right"), -0.25)
        self.assertGreater(palm_facing(self.points(0.44, 0.56), "Left"), 0.25)
        self.assertLess(palm_facing(self.points(0.56, 0.44), "Left"), -0.25)
        # 좌우 반전 영상에서는 부호가 뒤집힌다.
        self.assertLess(palm_facing(self.points(0.56, 0.44), "Right", mirrored=True), -0.25)

    def test_ratios_and_signal(self):
        world = [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]
        world[0] = SimpleNamespace(x=0.0, y=0.0, z=0.0)
        world[9] = SimpleNamespace(x=0.0, y=-0.1, z=0.0)
        # 엄지 끝이 검지 끝마디(7→8) 선분 가운데에서 1cm 옆에 있다: 손끝 점까지가 아니라 선분까지 잰다.
        world[4] = SimpleNamespace(x=0.01, y=-0.08, z=0.0)
        world[8] = SimpleNamespace(x=0.0, y=-0.09, z=0.0)
        ratios = pinch_ratios(world)
        self.assertAlmostEqual(ratios[0], 0.1, places=6)
        signal = hand_signal("Right", self.points(0.56, 0.44), world)
        self.assertEqual(len(signal.curled), 4)
        self.assertGreater(signal.facing, 0)


class KeyboardFrameTest(unittest.TestCase):
    def tap(self, run, hand, finger, frames=4):
        pinched = sig(pinch_ratios_for(finger))
        left, right = (pinched, sig()) if hand == "Left" else (sig(), pinched)
        run.frames(frames, left, right).frames(3, sig(), sig())

    def test_typing_from_frames(self):
        run = Runner().ready()
        for hand, finger in (("Right", 0), ("Left", 0), ("Left", 1)):
            self.tap(run, hand, finger)
        self.assertEqual(run.typer.ime.text, "가")

    def test_turning_hand_is_ignored(self):
        run = Runner().ready()
        run.frames(4, sig(pinch_ratios_for(0), facing=0.1), sig()).frames(3, sig(facing=0.1), sig())
        self.assertEqual(run.typer.ime.text, "")

    def test_fist_space_and_delete(self):
        run = Runner().ready()
        self.tap(run, "Right", 0)
        self.tap(run, "Left", 0)
        fist = sig((0.3, 0.32, 0.4, 0.45), pose="fist", curled=(True,) * 4)
        run.frames(8, fist, sig()).frames(3, sig(), sig())  # 왼손 주먹: 후보 없음 → 공백
        self.assertEqual(run.typer.ime.text, "기 ")
        run.frames(8, sig(), fist).frames(3, sig(), sig())  # 오른손 주먹: 지움
        self.assertEqual(run.typer.ime.text, "기")
        self.assertEqual(run.kinds("input")[-1], ("input", " "))

    def test_mode_switch_commits_pending(self):
        run = Runner().ready()
        self.tap(run, "Right", 0)
        self.assertIsNotNone(run.typer.pending)
        run.frames(8, sig(), sig(facing=-0.8))
        self.assertEqual(run.typer.mode, "mouse")
        self.assertIsNone(run.typer.pending)
        # 마우스 모드에서는 꼬집어도 입력하지 않는다.
        run.frames(4, sig(pinch_ratios_for(0), facing=-0.8), sig(facing=-0.8)).frames(3, sig(facing=-0.8),
                                                                                       sig(facing=-0.8))
        self.assertEqual(run.typer.ime.text, "ㄱ")


class MouseFrameTest(unittest.TestCase):
    def mouse_ready(self):
        run = Runner().ready(-0.8, -0.8)
        self.assertEqual(run.typer.mode, "mouse")
        run.events.clear()
        return run

    def test_cursor_moves_while_pinched(self):
        run = self.mouse_ready()
        start = list(run.typer.cursor)
        for i in range(20):
            run.frames(1, sig(facing=-0.8), sig(pinch_ratios_for(0), facing=-0.8, anchor=(0.5 + 0.005 * i, 0.5)))
        self.assertGreater(run.typer.cursor[0], start[0] + 0.05)
        self.assertAlmostEqual(run.typer.cursor[1], start[1], places=3)
        moved = list(run.typer.cursor)
        # 놓고 손을 되돌려도(클러치) 커서는 그대로다.
        run.frames(5, sig(facing=-0.8), sig(facing=-0.8, anchor=(0.4, 0.5)))
        self.assertEqual(run.typer.cursor, moved)

    def test_left_click_right_click_and_drag(self):
        run = self.mouse_ready()
        tap = sig(pinch_ratios_for(0), facing=-0.8)
        idle = sig(facing=-0.8)
        run.frames(3, tap, idle).frames(15, idle, idle)
        self.assertEqual(run.kinds("click"), [("click", "left")])
        run.events.clear()
        run.frames(3, tap, idle).frames(3, idle, idle).frames(3, tap, idle).frames(15, idle, idle)
        self.assertEqual(run.kinds("click"), [("click", "right")])
        run.events.clear()
        run.frames(12, tap, idle).frames(3, idle, idle)
        self.assertEqual([event[0] for event in run.events], ["down", "up"])


class KalmanTest(unittest.TestCase):
    def test_converges_and_smooths(self):
        kalman = Kalman1D()
        kalman.reset(0.0, 0.0)
        now, value = 0.0, 0.0
        for i in range(60):
            now += DT
            value = kalman.update(0.2 + (0.004 if i % 2 else -0.004), now)
        self.assertAlmostEqual(value, 0.2, delta=0.01)


class AppHelpersTest(unittest.TestCase):
    """pinch_cheonjiin의 카메라 없는 부분: 손 배정, 버튼 판정, OS 마우스(B)."""

    def test_assign_hands_by_position(self):
        from pinch_cheonjiin import assign_hands

        def hand(x):
            return [SimpleNamespace(x=x, y=0.5, z=0.0)] * 21
        # 라벨이 서로 다르면 화면 위치와 상관없이 라벨을 따른다(카메라가 손 맞은편에 있는 배치).
        hands = assign_hands([("Left", hand(0.8), "w-left", 0.9), ("Right", hand(0.2), "w-right", 0.9)])
        self.assertEqual(hands["Left"][1], "w-left")
        self.assertEqual(hands["Right"][1], "w-right")
        # 두 손 모두 Right로 불리면 화면 왼쪽 손을 왼손으로 정하고, 방향 계산용 원래 라벨은 남긴다.
        hands = assign_hands([("Right", hand(0.7), "w-right", 0.9), ("Right", hand(0.2), "w-left", 0.6)])
        self.assertEqual(hands["Left"][1:], ("w-left", "Right"))
        self.assertEqual(hands["Right"][1:], ("w-right", "Right"))
        self.assertEqual(list(assign_hands([("Left", hand(0.8), "w", 0.9)])), ["Left"])

    def test_facing_follows_mediapipe_label_not_position(self):
        """카메라가 손 맞은편에 있어 오른손이 화면 왼쪽에 보여도 손바닥이면 손바닥이다."""
        from pinch_cheonjiin import assign_hands, build_signals

        def palm_up(center, thumb_right):
            d = 1 if thumb_right else -1
            img = [SimpleNamespace(x=center, y=0.5, z=0.0) for _ in range(21)]
            img[0] = SimpleNamespace(x=center, y=0.8, z=0.0)
            img[5] = SimpleNamespace(x=center + 0.06 * d, y=0.6, z=0.0)
            img[17] = SimpleNamespace(x=center - 0.06 * d, y=0.62, z=0.0)
            world = [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]
            return img, world

        # 1인칭 기준 오른손 손바닥(엄지가 오른쪽) 모양이 화면 왼쪽에, 왼손 손바닥이 오른쪽에 있다.
        right_img, right_world = palm_up(0.3, thumb_right=True)
        left_img, left_world = palm_up(0.7, thumb_right=False)
        hands = assign_hands([("Right", right_img, right_world, 0.9), ("Left", left_img, left_world, 0.9)])
        signals = build_signals(hands, 640, 480, mirrored=False)
        self.assertGreater(signals["Right"].facing, 0.25)
        self.assertGreater(signals["Left"].facing, 0.25)

    def test_button_hit(self):
        from pinch_cheonjiin import button_rects, hit, menu_rects

        rects = button_rects(640, 480)
        x1, y1, x2, y2 = rects[0][2]
        self.assertEqual(hit(rects, ((x1 + x2) / 2, (y1 + y2) / 2)), "save")
        self.assertIsNone(hit(rects, (10, 470)))
        menu = menu_rects((630, 470), 640, 480)  # 화면 밖으로 나가지 않게 안쪽으로 당긴다
        self.assertTrue(all(b[2] <= 640 and b[3] <= 480 for _, _, b in menu))

    def test_os_mouse_with_fake_pynput(self):
        import sys
        import types
        from pinch_cheonjiin import OsMouse

        calls = []

        class Controller:
            def move(self, dx, dy):
                calls.append(("move", dx, dy))

            def click(self, button):
                calls.append(("click", button))

            def press(self, button):
                calls.append(("press", button))

            def release(self, button):
                calls.append(("release", button))

        fake = types.ModuleType("pynput.mouse")
        fake.Controller = Controller
        fake.Button = SimpleNamespace(left="L", right="R")
        package = types.ModuleType("pynput")
        package.mouse = fake
        saved = {name: sys.modules.get(name) for name in ("pynput", "pynput.mouse")}
        sys.modules.update({"pynput": package, "pynput.mouse": fake})
        try:
            mouse = OsMouse(1000)
            for event in (("move", 0.0004, 0.0), ("move", 0.0004, 0.0), ("move", 0.0004, -0.002),
                          ("click", "left"), ("click", "right"), ("down", None), ("up", None)):
                mouse.handle(event)
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        # 1px보다 작은 이동은 모았다가 넘긴다.
        self.assertEqual(calls[0], ("move", 1, -2))
        self.assertEqual(calls[1:], [("click", "L"), ("click", "R"), ("press", "L"), ("release", "L")])

    def test_os_mouse_without_pynput(self):
        import builtins
        from pinch_cheonjiin import OsMouse

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pynput"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            with self.assertRaisesRegex(RuntimeError, "pynput"):
                OsMouse(1000)
        finally:
            builtins.__import__ = real_import


class CameraSizeTests(unittest.TestCase):
    def test_resolution_argument(self):
        import argparse
        from pinch_cheonjiin import parse_resolution

        self.assertEqual(parse_resolution("auto"), "auto")
        self.assertEqual(parse_resolution("1280x720"), (1280, 720))
        self.assertEqual(parse_resolution("1920X1080"), (1920, 1080))
        for bad in ("720p", "100x50", "x"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_resolution(bad)

    def test_auto_keeps_jetson_at_640x480(self):
        from pinch_cheonjiin import capture_size

        self.assertEqual(capture_size("auto", "linux"), (640, 480))
        self.assertEqual(capture_size("auto", "darwin"), (1280, 720))
        self.assertEqual(capture_size((1920, 1080), "linux"), (1920, 1080))

    def test_detection_frame_is_capped_and_keeps_aspect(self):
        import numpy as np
        import cv2
        from pinch_cheonjiin import detection_frame

        small = np.zeros((480, 640, 3), np.uint8)
        self.assertIs(detection_frame(cv2, small, 640), small)
        self.assertEqual(detection_frame(cv2, np.zeros((720, 1280, 3), np.uint8), 640).shape, (360, 640, 3))
        self.assertEqual(detection_frame(cv2, np.zeros((1080, 1920, 3), np.uint8), 640).shape, (360, 640, 3))

    def test_window_size(self):
        from pinch_cheonjiin import window_size

        self.assertEqual(window_size(640, 480), (960, 720))
        self.assertEqual(window_size(1280, 720), (1280, 720))
        self.assertEqual(window_size(1920, 1080), (1440, 810))
        self.assertEqual(window_size(1920, 1440), (1200, 900))


if __name__ == "__main__":
    unittest.main()
