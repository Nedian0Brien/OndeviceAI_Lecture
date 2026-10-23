"""공통 모듈(common/) 테스트."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

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
        from common.canvas import rounded_rectangle

        draw = ImageDraw.Draw(Image.new("RGBA", (1280, 720)))
        # Pillow 12.3에서 ValueError가 나던 실제 좌표 (project01 1280x720 손끝 라벨)
        rounded_rectangle(draw, (400.791, 559.157, 429.791, 586.779), 13.81097210380915,
                          fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)
        rng = random.Random(0)
        for _ in range(2000):
            x, y, height = rng.uniform(0, 1200), rng.uniform(0, 650), rng.uniform(12, 40)
            bounds = (x, y, x + height + rng.uniform(0, 3), y + height)
            rounded_rectangle(draw, bounds, height / 2, fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)


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

        data = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "handedness.json").read_text(encoding="utf-8"))
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


class OrientationTests(unittest.TestCase):
    def test_dwell_and_hysteresis(self):
        from common.gestures import Orientation
        orientation, now, states = Orientation(dwell=0.15), 0.0, []
        for facing in [0.8] * 6 + [0.2] * 2 + [-0.8] * 7:
            now += 1 / 30
            states.append(orientation.update(facing, now))
        self.assertEqual(states[0], "turning")
        self.assertEqual(states[6], "palm")  # 0.2: 히스테리시스로 유지
        self.assertEqual(states[8], "palm")  # 손등이 dwell만큼 이어지기 전에는 앞 방향을 유지
        self.assertEqual(states[-1], "back")


class GeometryTests(unittest.TestCase):
    @staticmethod
    def points(index_x, pinky_x):
        p = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
        p[0] = SimpleNamespace(x=0.5, y=0.8, z=0.0)
        p[5] = SimpleNamespace(x=index_x, y=0.6, z=0.0)
        p[17] = SimpleNamespace(x=pinky_x, y=0.62, z=0.0)
        return p

    def test_palm_facing_signs(self):
        from common.pose import palm_facing
        # 1인칭(반전 없음): 오른손 손바닥이 눈을 향하면 엄지(검지 뿌리)가 보는 사람 기준 오른쪽에 있다.
        self.assertGreater(palm_facing(self.points(0.56, 0.44), "Right"), 0.25)
        self.assertLess(palm_facing(self.points(0.44, 0.56), "Right"), -0.25)
        self.assertGreater(palm_facing(self.points(0.44, 0.56), "Left"), 0.25)
        self.assertLess(palm_facing(self.points(0.56, 0.44), "Right", mirrored=True), -0.25)

    def test_palm_roll_signs(self):
        from common.pose import palm_roll
        # 엄지 쪽(검지 뿌리)이 카메라에 가까우면(z가 작으면) 양수, 소지 쪽이 가까우면 음수.
        p = self.points(0.56, 0.44)
        p[5], p[17] = SimpleNamespace(x=0.56, y=0.6, z=-0.12), SimpleNamespace(x=0.44, y=0.6, z=0.0)
        self.assertAlmostEqual(palm_roll(p), 45.0, places=6)
        p[5] = SimpleNamespace(x=0.56, y=0.6, z=0.12)
        self.assertAlmostEqual(palm_roll(p), -45.0, places=6)

    def test_pinch_ratio_measures_to_fingertip_segment(self):
        from hand_signal import hand_signal, pinch_ratios
        world = [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]
        world[0] = SimpleNamespace(x=0.0, y=0.0, z=0.0)
        world[9] = SimpleNamespace(x=0.0, y=-0.1, z=0.0)
        # 엄지 끝이 검지 끝마디(7→8) 선분 가운데에서 1cm 옆: 손끝 점이 아니라 선분까지 잰다.
        world[4] = SimpleNamespace(x=0.01, y=-0.08, z=0.0)
        world[8] = SimpleNamespace(x=0.0, y=-0.09, z=0.0)
        self.assertAlmostEqual(pinch_ratios(world)[0], 0.1, places=6)
        self.assertGreater(hand_signal("Right", self.points(0.56, 0.44), world).facing, 0)


class CameraTests(unittest.TestCase):
    def test_resolution_and_capture_size(self):
        import argparse
        from common.camera import capture_size, parse_resolution, window_size
        self.assertEqual(parse_resolution("1920X1080"), (1920, 1080))
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_resolution("720p")
        self.assertEqual(capture_size("auto", "linux"), (640, 480))  # Jetson
        self.assertEqual(capture_size("auto", "darwin"), (1280, 720))
        self.assertEqual(window_size(640, 480), (960, 720))

    def test_detection_frame_is_capped_and_keeps_aspect(self):
        import numpy as np
        from common.hands import detection_frame
        small = np.zeros((480, 640, 3), np.uint8)
        self.assertIs(detection_frame(small, 640), small)
        self.assertEqual(detection_frame(np.zeros((720, 1280, 3), np.uint8), 640).shape, (360, 640, 3))

    def test_assign_hands_by_label_then_position(self):
        from common.hands import assign_hands

        def hand(x):
            return [SimpleNamespace(x=x, y=0.5, z=0.0)] * 21
        hands = assign_hands([("Left", hand(0.8), "w-left", 0.9), ("Right", hand(0.2), "w-right", 0.9)])
        self.assertEqual((hands["Left"][2], hands["Right"][2]), ("w-left", "w-right"))  # 라벨이 다르면 라벨대로
        # 두 손 모두 Right로 불리면 화면 왼쪽 손을 왼손으로 정하고, 방향 계산용 원래 라벨은 남긴다.
        hands = assign_hands([("Right", hand(0.7), "w-right", 0.9), ("Right", hand(0.2), "w-left", 0.6)])
        self.assertEqual((hands["Left"][2], hands["Left"][0]), ("w-left", "Right"))


if __name__ == "__main__":
    unittest.main()
