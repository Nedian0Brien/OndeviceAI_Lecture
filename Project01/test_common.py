"""공통 모듈(common/) 테스트."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from common.camera import is_window_closed
from common.gestures import FistHold
from common.pose import pose_from_image
from common.synthetic import detection


class WindowTests(unittest.TestCase):
    def test_unsupported_gtk_visibility_does_not_exit(self):
        cv2 = Mock(error=RuntimeError)
        cv2.getWindowProperty.return_value = -1.0
        self.assertFalse(is_window_closed(cv2, "Hand Cheonjiin"))

    def test_visible_window_continues_and_closed_window_exits(self):
        cv2 = Mock(error=RuntimeError)
        cv2.getWindowProperty.return_value = 1.0
        self.assertFalse(is_window_closed(cv2, "Hand Cheonjiin"))
        cv2.getWindowProperty.return_value = 0.0
        self.assertTrue(is_window_closed(cv2, "Hand Cheonjiin"))

    def test_unsupported_property_exception_does_not_exit(self):
        cv2 = Mock(error=RuntimeError)
        cv2.getWindowProperty.side_effect = RuntimeError("unsupported backend")
        self.assertFalse(is_window_closed(cv2, "Hand Cheonjiin"))

class FistHoldTests(unittest.TestCase):
    def test_hold_fires_once_until_opened(self):
        gate = FistHold(hold=0.18)
        self.assertFalse(gate.update("open", 0.0))
        self.assertFalse(gate.update("fist", 0.1))
        self.assertTrue(gate.update("fist", 0.3))
        self.assertFalse(gate.update("fist", 5.0))
        self.assertFalse(gate.update("closing", 6.0))
        gate.update("open", 6.1)
        gate.update("fist", 6.2)
        self.assertTrue(gate.update("fist", 6.4))

    def test_starting_with_fist_and_loss_need_open_hand(self):
        gate = FistHold()
        self.assertFalse(gate.update("fist", 0.0))
        self.assertFalse(gate.update("fist", 1.0))
        gate.update("open", 1.1)
        gate.lost()
        self.assertFalse(gate.update("fist", 2.0))
        self.assertFalse(gate.update("fist", 3.0))

    def test_fist_must_be_continuous_and_reports_progress(self):
        gate = FistHold(hold=0.2)
        gate.update("open", 0.0)
        gate.update("fist", 0.1)
        gate.update("fist", 0.2)
        self.assertAlmostEqual(gate.progress, 0.5)
        gate.update("closing", 0.25)
        self.assertEqual(gate.progress, 0.0)
        gate.update("fist", 0.3)
        self.assertFalse(gate.update("fist", 0.45))
        self.assertTrue(gate.update("fist", 0.5))

class PoseTests(unittest.TestCase):
    def test_open_and_fist_for_both_hands_and_camera_sizes(self):
        for mirrored in (False, True):
            for width, height in ((640, 480), (1280, 720)):
                with self.subTest(mirrored=mirrored, size=(width, height)):
                    self.assertEqual(pose_from_image(detection(mirrored=mirrored)[0], width, height), "open")
                    self.assertEqual(pose_from_image(detection(mirrored=mirrored, fist=True)[0], width, height), "fist")

    def test_partial_curl_is_not_a_fist(self):
        opened = detection()[0]
        closed = detection(fist=True)[0]
        for base in (5, 9, 13):
            opened[base:base + 4] = closed[base:base + 4]
        self.assertEqual(pose_from_image(opened, 640, 480), "closing")

    def test_degenerate_points_do_not_look_like_a_fist(self):
        points = [SimpleNamespace(x=.5, y=.5) for _ in range(21)]
        self.assertEqual(pose_from_image(points, 640, 480), "closing")

class DrawingTests(unittest.TestCase):
    def test_rounded_rectangle_survives_fractional_near_circle_bounds(self):
        import random
        from PIL import Image, ImageDraw
        from common.hud import rounded_rectangle

        draw = ImageDraw.Draw(Image.new("RGBA", (1280, 720)))
        # Pillow 12.3에서 ValueError가 나던 실제 좌표 (project01 1280x720 손끝 라벨)
        rounded_rectangle(draw, (400.791, 559.157, 429.791, 586.779), 13.81097210380915,
                          fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)
        rng = random.Random(0)
        for _ in range(2000):
            x, y, height = rng.uniform(0, 1200), rng.uniform(0, 650), rng.uniform(12, 40)
            bounds = (x, y, x + height + rng.uniform(0, 3), y + height)
            rounded_rectangle(draw, bounds, height / 2, fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)

    def test_blur_under_changes_only_masked_area(self):
        from PIL import Image, ImageDraw
        from common.hud import blur_under

        rng = np.random.default_rng(0)
        picture = Image.fromarray(rng.integers(0, 255, (120, 160, 4), dtype=np.uint8), "RGBA")
        before = np.asarray(picture).copy()
        mask = Image.new("L", picture.size)
        ImageDraw.Draw(mask).rectangle((40, 30, 80, 60), fill=255)
        after = np.asarray(blur_under(picture, mask))
        self.assertTrue((after[:30] == before[:30]).all())
        self.assertTrue((after[:, 90:] == before[:, 90:]).all())
        self.assertFalse((after[35:55, 45:75] == before[35:55, 45:75]).all())
        self.assertIs(blur_under(picture, Image.new("L", picture.size)), picture)

class HandednessTests(unittest.TestCase):
    """MediaPipe Tasks는 입력 영상에 보이는 실제 손을 라벨로 낸다. 반전한 영상에서만 라벨을 뒤집는다."""

    @staticmethod
    def fake_result(label):
        point = SimpleNamespace(x=0.5, y=0.5, z=0.0)
        return SimpleNamespace(hand_landmarks=[[point] * 21], hand_world_landmarks=[[point] * 21],
                               handedness=[[SimpleNamespace(category_name=label, score=0.9)]])

    def test_label_kept_for_unmirrored_and_swapped_for_mirrored(self):
        from common.hands import detections_from_result

        for raw, mirrored, expected in (("Right", False, "Right"), ("Left", False, "Left"),
                                        ("Right", True, "Left"), ("Left", True, "Right")):
            with self.subTest(raw=raw, mirrored=mirrored):
                detection = detections_from_result(self.fake_result(raw), mirrored)[0]
                self.assertEqual(detection.label, expected)
                self.assertEqual(detection[0], expected)  # 튜플처럼도 쓸 수 있다

    def test_real_landmarks_give_true_hand_and_facing(self):
        import json
        from pathlib import Path
        from common.pose import palm_facing

        data = json.loads((Path(__file__).resolve().with_name("fixtures") / "handedness.json").read_text(encoding="utf-8"))
        for case in data["cases"]:
            with self.subTest(source=case["source"]):
                self.assertEqual(case["mediapipe_label"], case["true_hand"])
                landmarks = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in case["landmarks"]]
                facing = palm_facing(landmarks, case["true_hand"], False, case["width"], case["height"])
                self.assertEqual("palm" if facing > 0.25 else "back" if facing < -0.25 else "turning",
                                 case["true_facing"])
                # 같은 손을 좌우 반전한 영상: 좌표 x 반전 + 반전 표시. 방향은 같아야 한다.
                flipped = [SimpleNamespace(x=1 - p.x, y=p.y, z=p.z) for p in landmarks]
                self.assertAlmostEqual(palm_facing(flipped, case["true_hand"], True, case["width"], case["height"]),
                                       facing, places=6)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
