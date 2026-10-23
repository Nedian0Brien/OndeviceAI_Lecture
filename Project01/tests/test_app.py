"""카메라 앱(project01.py)과 화면(hud.py)의 카메라 없이 도는 부분."""

from types import SimpleNamespace
import unittest

from hud import (CYCLE_ANIM, ECHO_HOLD, ECHO_IN, ECHO_OUT, SPREAD_CLOSE, SPREAD_HOLD, SPREAD_OPEN, cycle_motion,
                 echo_look, echo_view, finger_path_point, spread_motion)
from tests.helpers import DT
from typer import PinchTyper


class HudMotionTest(unittest.TestCase):
    def test_path_runs_from_button_to_pip(self):
        points = [(0.0, 0.0)] * 21
        for i, y in zip((5, 6, 7, 8), (100.0, 70.0, 50.0, 30.0)):  # 검지 MCP~TIP이 위로 곧게
            points[i] = (10.0, y)
        start = (10.0, 10.0)
        self.assertEqual(finger_path_point(points, 0, start, 0.0), start)
        self.assertEqual(finger_path_point(points, 0, start, 0.5), (10.0, 40.0))
        self.assertEqual(finger_path_point(points, 0, start, 1.0), (10.0, 70.0))  # PIP

    def test_spread_folds_on_fist_and_unfolds_after_hold(self):
        spreads = {}
        self.assertEqual(spread_motion(spreads, "Left", False, 0.0), 0.0)  # 처음 보이는 손은 0에서
        self.assertEqual(spread_motion(spreads, "Left", False, SPREAD_OPEN), 1.0)
        t = SPREAD_OPEN
        for k in range(1, 7):  # 주먹 / 주먹 아님이 번갈아 읽혀도 접힌다
            t += SPREAD_CLOSE / 4
            value = spread_motion(spreads, "Left", k % 2 == 1, t)
        self.assertEqual(value, 0.0)
        opened, values = t, []
        while t < opened + SPREAD_HOLD + SPREAD_OPEN + 0.1:
            t += DT
            values.append((t - opened, spread_motion(spreads, "Left", False, t)))
        self.assertTrue(all(v == 0.0 for dt, v in values if dt < SPREAD_HOLD - DT))  # 편 직후는 접힌 채
        self.assertEqual(values[-1][1], 1.0)

    def test_echo_appears_then_fades(self):
        self.assertAlmostEqual(echo_look(ECHO_IN / 2)[0], 0.5)
        self.assertEqual(echo_look((ECHO_IN + ECHO_HOLD) / 2), (1.0, 1.0))
        self.assertIsNone(echo_look(ECHO_OUT))
        typer, ui = PinchTyper(), {}
        self.assertIsNone(echo_view(ui, typer, 0.0))
        typer.press_finger("Right", 0, 0.0)
        self.assertEqual(echo_view(ui, typer, 10.0)[:2], ("Right", "ㄱ"))  # 화면이 처음 본 시각부터 잰다
        self.assertIsNone(echo_view(ui, typer, 10.0 + ECHO_OUT + 0.01))

    def test_motion_only_when_queue_turns(self):
        cycles = {}
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㄱ", "ㅋ", "ㄲ"), 0.0), (1.0, None))
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0), (0.0, ("ㄱ", "ㅋ", "ㄲ")))
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0 + CYCLE_ANIM), (1.0, None))
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㄱ", "ㅋ", "ㄲ"), 2.0), (1.0, None))  # 확정: 바로


class AppTest(unittest.TestCase):
    def test_facing_follows_mediapipe_label_not_position(self):
        # 카메라가 손 맞은편에 있어 오른손이 화면 왼쪽에 보여도 손바닥이면 손바닥이다.
        from common.hands import Detection, assign_hands
        from project01 import build_signals

        def palm_up(center, thumb_right):
            d = 1 if thumb_right else -1
            image = [SimpleNamespace(x=center, y=0.5, z=0.0) for _ in range(21)]
            image[0] = SimpleNamespace(x=center, y=0.8, z=0.0)
            image[5] = SimpleNamespace(x=center + 0.06 * d, y=0.6, z=0.0)
            image[17] = SimpleNamespace(x=center - 0.06 * d, y=0.62, z=0.0)
            return image, [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]

        hands = assign_hands([Detection("Right", *palm_up(0.3, True), 0.9), Detection("Left", *palm_up(0.7, False), 0.9)])
        signals = build_signals(hands, 640, 480, mirrored=False)
        self.assertGreater(signals["Right"].facing, 0.25)
        self.assertGreater(signals["Left"].facing, 0.25)

    def test_word_view(self):
        from project01 import word_view
        from tools.word_test import WordTest

        test = WordTest(words=("바다",), rounds=1)
        self.assertEqual(word_view(test, test.prompt(0.0), None)["title"], "「바다」 입력")
        self.assertEqual(word_view(test, None, ("collect", 0, "바다", "done"))["tone"], "ok")
        self.assertIsNone(word_view(test, None, None))

    def test_draw_hud_runs_on_blank_frame(self):
        # 손이 없는 빈 프레임에서도 단어 안내·자판 지도·도움말을 그린다.
        import numpy as np
        from common.canvas import load_fonts
        from hud import draw_hud

        typer = PinchTyper()
        typer.press_finger("Right", 0, 0.0)
        ui = {"paused": False, "show_help": False, "show_map": True, "status": "", "notice_since": None,
              "word": {"tone": "ok", "title": "완료 · 바다", "detail": "1 / 60"}}
        frame = np.zeros((480, 640, 3), np.uint8)
        for show_help in (False, True):
            ui["show_help"] = show_help
            view = draw_hud(frame, typer, {}, ui, load_fonts(), 30.0)
            self.assertEqual(view.shape, frame.shape)
            self.assertGreater(int(view.max()), 0)


if __name__ == "__main__":
    unittest.main()
