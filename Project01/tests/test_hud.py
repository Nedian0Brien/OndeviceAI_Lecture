"""hud.py의 애니메이션 계산과 화면 그리기 테스트"""

import random
import unittest

import numpy as np
from PIL import Image, ImageDraw

from hud import (CYCLE_ANIM, ECHO_HOLD, ECHO_IN, ECHO_OUT, SPREAD_CLOSE, SPREAD_HOLD, SPREAD_OPEN, cycle_motion,
                 draw_hud, echo_look, echo_view, finger_path_point, load_fonts, rounded_rectangle, spread_motion)
from keyboard import PinchTyper
from tests.helpers import DT


class HudMotionTest(unittest.TestCase):
    def test_path_runs_from_button_to_pip(self):
        points = [(0.0, 0.0)] * 21
        for i, y in zip((5, 6, 7, 8), (100.0, 70.0, 50.0, 30.0)):  # 검지가 위로 곧게 뻗음
            points[i] = (10.0, y)
        start = (10.0, 10.0)
        self.assertEqual(finger_path_point(points, 0, start, 0.0), start)
        self.assertEqual(finger_path_point(points, 0, start, 0.5), (10.0, 40.0))
        self.assertEqual(finger_path_point(points, 0, start, 1.0), (10.0, 70.0))

    def test_spread_folds_on_fist_and_unfolds_after_hold(self):
        spreads = {}
        self.assertEqual(spread_motion(spreads, "Left", False, 0.0), 0.0)
        self.assertEqual(spread_motion(spreads, "Left", False, SPREAD_OPEN), 1.0)
        t = SPREAD_OPEN
        for k in range(1, 7):  # 주먹과 주먹 아님이 번갈아 읽혀도 접힘
            t += SPREAD_CLOSE / 4
            value = spread_motion(spreads, "Left", k % 2 == 1, t)
        self.assertEqual(value, 0.0)
        opened, values = t, []
        while t < opened + SPREAD_HOLD + SPREAD_OPEN + 0.1:
            t += DT
            values.append((t - opened, spread_motion(spreads, "Left", False, t)))
        self.assertTrue(all(v == 0.0 for dt, v in values if dt < SPREAD_HOLD - DT))
        self.assertEqual(values[-1][1], 1.0)

    def test_echo_appears_then_fades(self):
        self.assertAlmostEqual(echo_look(ECHO_IN / 2)[0], 0.5)
        self.assertEqual(echo_look((ECHO_IN + ECHO_HOLD) / 2), (1.0, 1.0))
        self.assertIsNone(echo_look(ECHO_OUT))
        typer, ui = PinchTyper(), {}
        self.assertIsNone(echo_view(ui, typer, 0.0))
        typer.press_finger("Right", 0)
        self.assertEqual(echo_view(ui, typer, 10.0)[:2], ("Right", "ㄱ"))  # 화면이 처음 본 시각부터 잼
        self.assertIsNone(echo_view(ui, typer, 10.0 + ECHO_OUT + 0.01))

    def test_motion_only_when_queue_turns(self):
        cycles = {}
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㄱ", "ㅋ", "ㄲ"), 0.0), (1.0, None))
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0), (0.0, ("ㄱ", "ㅋ", "ㄲ")))
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0 + CYCLE_ANIM), (1.0, None))
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㄱ", "ㅋ", "ㄲ"), 2.0), (1.0, None))


class DrawingTest(unittest.TestCase):
    def test_rounded_rectangle_survives_fractional_near_circle_bounds(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (1280, 720)))
        # Pillow 12.3에서 ValueError가 나던 실제 좌표(1280x720 손끝 라벨)
        rounded_rectangle(draw, (400.791, 559.157, 429.791, 586.779), 13.81097210380915,
                          fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)
        rng = random.Random(0)
        for _ in range(2000):
            x, y, height = rng.uniform(0, 1200), rng.uniform(0, 650), rng.uniform(12, 40)
            bounds = (x, y, x + height + rng.uniform(0, 3), y + height)
            rounded_rectangle(draw, bounds, height / 2, fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)

    def test_draw_hud_runs_on_blank_frame(self):
        typer = PinchTyper()
        typer.press_finger("Right", 0)
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
