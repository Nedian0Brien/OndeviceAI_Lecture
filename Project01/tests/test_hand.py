"""hand.py의 손 검출 라벨, 손 배정, 자세, 손 방향, 핀치 거리 테스트"""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from hand import (Detection, assign_hands, detect_hands, detection_frame, hand_signal, palm_facing, palm_roll,
                  pinch_ratios, pose_from_image)
from tests.helpers import detection


def flat_points(index_x, pinky_x):
    """손목, 검지 뿌리, 소지 뿌리만 정한 영상 관절을 만드는 함수"""
    p = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
    p[0] = SimpleNamespace(x=0.5, y=0.8, z=0.0)
    p[5] = SimpleNamespace(x=index_x, y=0.6, z=0.0)
    p[17] = SimpleNamespace(x=pinky_x, y=0.62, z=0.0)
    return p


class DetectionTest(unittest.TestCase):
    def test_label_kept_for_unmirrored_and_swapped_for_mirrored(self):
        point = SimpleNamespace(x=0.5, y=0.5, z=0.0)
        for raw, mirrored, expected in (("Right", False, "Right"), ("Left", False, "Left"),
                                        ("Right", True, "Left"), ("Left", True, "Right")):
            result = SimpleNamespace(hand_landmarks=[[point] * 21], hand_world_landmarks=[[point] * 21],
                                     handedness=[[SimpleNamespace(category_name=raw)]])
            detector = SimpleNamespace(detect_for_video=lambda image, timestamp, result=result: result)
            with self.subTest(raw=raw, mirrored=mirrored):
                frame = np.zeros((4, 4, 3), np.uint8)
                self.assertEqual(detect_hands(detector, frame, 0, mirrored)[0].label, expected)

    def test_real_landmarks_give_true_hand_and_facing(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "handedness.json").read_text(encoding="utf-8"))
        for case in data["cases"]:
            with self.subTest(source=case["source"]):
                self.assertEqual(case["mediapipe_label"], case["true_hand"])
                landmarks = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in case["landmarks"]]
                facing = palm_facing(landmarks, case["true_hand"], False, case["width"], case["height"])
                self.assertEqual("palm" if facing > 0.25 else "back" if facing < -0.25 else "turning",
                                 case["true_facing"])
                # 같은 손을 좌우 반전한 영상에서도 손 방향은 같아야 함
                flipped = [SimpleNamespace(x=1 - p.x, y=p.y, z=p.z) for p in landmarks]
                self.assertAlmostEqual(palm_facing(flipped, case["true_hand"], True, case["width"], case["height"]),
                                       facing, places=6)

    def test_detection_frame_is_capped_and_keeps_aspect(self):
        small = np.zeros((480, 640, 3), np.uint8)
        self.assertIs(detection_frame(small, 640), small)
        self.assertEqual(detection_frame(np.zeros((720, 1280, 3), np.uint8), 640).shape, (360, 640, 3))

    def test_assign_hands_by_label_then_position(self):
        def hand(label, x, world):
            return Detection(label, [SimpleNamespace(x=x, y=0.5, z=0.0)] * 21, world)

        hands = assign_hands([hand("Left", 0.8, "w-left"), hand("Right", 0.2, "w-right")])
        self.assertEqual((hands["Left"].world, hands["Right"].world), ("w-left", "w-right"))
        # 두 손이 모두 Right로 불리면 화면 왼쪽 손을 왼손으로 정하고 원래 라벨은 남김
        hands = assign_hands([hand("Right", 0.7, "w-right"), hand("Right", 0.2, "w-left")])
        self.assertEqual((hands["Left"].world, hands["Left"].label), ("w-left", "Right"))

    def test_facing_follows_mediapipe_label_not_position(self):
        # 카메라가 손 맞은편에 있어 오른손이 화면 왼쪽에 보여도 손바닥이면 손바닥임
        def palm_up(center, thumb_right):
            d = 1 if thumb_right else -1
            image = flat_points(center + 0.06 * d, center - 0.06 * d)
            image[0] = SimpleNamespace(x=center, y=0.8, z=0.0)
            return image, [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]

        hands = assign_hands([Detection("Right", *palm_up(0.3, True)), Detection("Left", *palm_up(0.7, False))])
        for hand in hands.values():
            self.assertGreater(hand_signal(hand.label, hand.landmarks, hand.world, False, 640, 480).facing, 0.25)


class PoseTest(unittest.TestCase):
    def test_open_and_fist_for_both_hands_and_camera_sizes(self):
        for mirrored in (False, True):
            for width, height in ((640, 480), (1280, 720)):
                with self.subTest(mirrored=mirrored, size=(width, height)):
                    self.assertEqual(pose_from_image(detection(mirrored=mirrored)[0], width, height), "open")
                    self.assertEqual(pose_from_image(detection(fist=True, mirrored=mirrored)[0], width, height),
                                     "fist")

    def test_partial_curl_is_not_a_fist(self):
        opened, closed = detection()[0], detection(fist=True)[0]
        for base in (5, 9, 13):
            opened[base:base + 4] = closed[base:base + 4]
        self.assertEqual(pose_from_image(opened, 640, 480), "closing")

    def test_degenerate_points_do_not_look_like_a_fist(self):
        points = [SimpleNamespace(x=.5, y=.5) for _ in range(21)]
        self.assertEqual(pose_from_image(points, 640, 480), "closing")


class GeometryTest(unittest.TestCase):
    def test_palm_facing_signs(self):
        # 반전하지 않은 영상에서 오른손 손바닥이 카메라를 향하면 검지 뿌리가 보는 사람 기준 오른쪽에 있음
        self.assertGreater(palm_facing(flat_points(0.56, 0.44), "Right"), 0.25)
        self.assertLess(palm_facing(flat_points(0.44, 0.56), "Right"), -0.25)
        self.assertGreater(palm_facing(flat_points(0.44, 0.56), "Left"), 0.25)
        self.assertLess(palm_facing(flat_points(0.56, 0.44), "Right", mirrored=True), -0.25)

    def test_palm_roll_signs(self):
        p = flat_points(0.56, 0.44)
        p[5], p[17] = SimpleNamespace(x=0.56, y=0.6, z=-0.12), SimpleNamespace(x=0.44, y=0.6, z=0.0)
        self.assertAlmostEqual(palm_roll(p), 45.0, places=6)
        p[5] = SimpleNamespace(x=0.56, y=0.6, z=0.12)
        self.assertAlmostEqual(palm_roll(p), -45.0, places=6)

    def test_pinch_ratio_measures_to_fingertip_segment(self):
        world = [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]
        world[0] = SimpleNamespace(x=0.0, y=0.0, z=0.0)
        world[9] = SimpleNamespace(x=0.0, y=-0.1, z=0.0)
        # 엄지 끝이 검지 끝마디 선분 가운데에서 1cm 옆에 있으면 손끝 점이 아니라 선분까지 잼
        world[4] = SimpleNamespace(x=0.01, y=-0.08, z=0.0)
        world[8] = SimpleNamespace(x=0.0, y=-0.09, z=0.0)
        self.assertAlmostEqual(pinch_ratios(world)[0], 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
