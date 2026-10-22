"""python -m unittest discover -s 261018 -p 'test_*.py' -v"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from cheonjiin import CheonjiinIME, CONSONANTS, MEDIALS, TouchGate, FistHold
from project01 import (KEY_MAP, KEY_OF_SEGMENT, HandKeyboardTracker, classify_hand_pose, is_window_closed,
                       key_diameter, key_targets)
from test_tap import AWAY, HEIGHT, WIDTH, landmark_list, middle, tap, world_hand


class IMETests(unittest.TestCase):
    def setUp(self):
        self.ime = CheonjiinIME()
        self.now = 0.0

    def type(self, keys, step=0.25):
        for key in keys:
            self.ime.press(key, self.now)
            self.now += step

    def consonant(self, char):
        for key, group in CONSONANTS.items():
            if char in group:
                # 같은 그룹의 독립 자음을 입력할 때는 순환 대기 시간을 넘긴다.
                self.now += 1.0
                self.type(key * (group.index(char) + 1))
                return
        self.fail("지원되지 않는 테스트 자음")

    def test_all_21_vowels(self):
        # 아래아를 포함하는 입력 중간 상태뿐 아니라 모든 현대 한글 모음 확인.
        examples = {
            "1": "ㅣ", "3": "ㅡ", "12": "ㅏ", "122": "ㅑ", "21": "ㅓ",
            "221": "ㅕ", "23": "ㅗ", "223": "ㅛ", "32": "ㅜ", "322": "ㅠ",
            "121": "ㅐ", "1221": "ㅒ", "211": "ㅔ", "2211": "ㅖ",
            "2312": "ㅘ", "23121": "ㅙ", "231": "ㅚ", "3221": "ㅝ",
            "32211": "ㅞ", "321": "ㅟ", "31": "ㅢ",
        }
        self.assertEqual(set(examples.values()), set(MEDIALS))
        for keys, expected in examples.items():
            with self.subTest(keys=keys):
                self.setUp()
                self.type(keys)
                self.assertEqual(self.ime.text, expected)

    def test_greeting(self):
        for keys in ("0125", "52210", "8812", "8211", "0223"):
            self.type(keys)
            self.now += 1.0
        self.assertEqual(self.ime.text, "안녕하세요")

    def test_consonant_cycles(self):
        for key, group in CONSONANTS.items():
            with self.subTest(key=key):
                self.setUp()
                for char in group + group[0]:
                    self.type(key)
                    self.assertEqual(self.ime.text, char)

    def test_timeout_separates_same_consonant(self):
        self.type("5")
        self.now += 1.0
        self.type("5")
        self.assertEqual(self.ime.text, "ㄴㄴ")

    def test_final_moves_to_next_initial(self):
        self.type("412512")  # ㄱㅏㄴㅏ
        self.assertEqual(self.ime.text, "가나")

    def test_compound_final_and_split(self):
        self.consonant("ㄷ")
        self.type("12")
        self.consonant("ㄹ")
        self.consonant("ㄱ")
        self.assertEqual(self.ime.text, "닭")
        self.type("12")
        self.assertEqual(self.ime.text, "달가")

    def test_all_compound_final_splits(self):
        pairs = ("ㄱㅅ", "ㄴㅈ", "ㄴㅎ", "ㄹㄱ", "ㄹㅁ", "ㄹㅂ", "ㄹㅅ", "ㄹㅌ", "ㄹㅍ", "ㄹㅎ", "ㅂㅅ")
        expected = ("각사", "간자", "간하", "갈가", "갈마", "갈바", "갈사", "갈타", "갈파", "갈하", "갑사")
        for pair, word in zip(pairs, expected):
            with self.subTest(pair=pair):
                self.setUp()
                self.consonant("ㄱ")
                self.type("12")
                self.consonant(pair[0])
                self.consonant(pair[1])
                self.type("12")
                self.assertEqual(self.ime.text, word)

    def test_tense_initial_and_compound_medial(self):
        self.type("44423121")
        self.assertEqual(self.ime.text, "꽤")

    def test_cycling_final_can_form_next_initial(self):
        self.type("412666")  # ㄱㅏ + ㄷ→ㅌ→ㄸ (ㄸ은 받침으로 쓸 수 없다)
        self.assertEqual(self.ime.text, "가ㄸ")
        self.type("12")
        self.assertEqual(self.ime.text, "가따")

    def test_backspace_undoes_each_stroke(self):
        self.type("412")
        self.assertEqual(self.ime.text, "가")
        for expected in ("기", "ㄱ", "", ""):
            self.ime.backspace()
            self.assertEqual(self.ime.text, expected)

    def test_backspace_restores_precycle_consonant(self):
        self.type("444")
        self.assertEqual(self.ime.text, "ㄲ")
        self.ime.backspace()
        self.assertEqual(self.ime.text, "ㅋ")

    def test_commit_space_and_newline(self):
        self.type(("4", "COMMIT", "4", "SPACE", "0", "ENTER"))
        self.assertEqual(self.ime.text, "ㄱㄱ ㅇ\n")

    def test_clear_is_undoable(self):
        self.type("412")
        self.ime.clear()
        self.assertEqual(self.ime.text, "")
        self.ime.backspace()
        self.assertEqual(self.ime.text, "가")

    def test_pending_dot_can_be_undone(self):
        self.type("02")
        self.assertEqual(self.ime.text, "ㅇㆍ")
        self.type("1")
        self.assertEqual(self.ime.text, "어")
        self.ime.backspace()
        self.assertEqual(self.ime.text, "ㅇㆍ")


class TouchTests(unittest.TestCase):
    def setUp(self):
        self.gate = TouchGate(dwell=0.12, release=0.10)

    def arm(self, now=0.0):
        self.gate.update(None, True, now)
        self.gate.update(None, True, now + 0.11)
        self.assertTrue(self.gate.armed)

    def test_start_with_contact_does_not_press(self):
        self.assertIsNone(self.gate.update("1", False, 0.0))
        self.assertIsNone(self.gate.update("1", False, 10.0))

    def test_hold_fires_once_and_releases_before_next_press(self):
        self.arm()
        self.assertIsNone(self.gate.update("1", False, 0.2))
        self.assertEqual(self.gate.update("1", False, 0.33), "1")
        self.assertIsNone(self.gate.update("1", False, 5.0))
        self.assertIsNone(self.gate.update("2", False, 5.1))
        self.arm(5.2)
        self.gate.update("1", False, 5.4)
        self.assertEqual(self.gate.update("1", False, 5.54), "1")

    def test_candidate_jitter_restarts_dwell(self):
        self.arm()
        self.gate.update("1", False, 0.2)
        self.assertIsNone(self.gate.update("2", False, 0.3))
        self.assertIsNone(self.gate.update("2", False, 0.4))
        self.assertEqual(self.gate.update("2", False, 0.43), "2")

    def test_lost_hand_never_counts_as_release(self):
        self.arm()
        self.gate.update("1", False, 0.2)
        self.gate.lost()
        self.assertIsNone(self.gate.update("1", False, 10.0))
        self.assertIsNone(self.gate.update("1", False, 20.0))
        self.arm(21.0)

    def test_release_must_be_continuous(self):
        self.gate.update(None, True, 0.0)
        self.gate.update(None, False, 0.08)
        self.gate.update(None, True, 0.15)
        self.assertFalse(self.gate.armed)
        self.gate.update(None, True, 0.26)
        self.assertTrue(self.gate.armed)


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


def detection(thumb=AWAY, fist=False, mirrored=False):
    """test_tap의 합성 손을 (이미지 landmark, world landmark)로 만든다."""
    world = world_hand(np.asarray(thumb, float))
    if fist:
        for base in (5, 9, 13, 17):
            mcp = world[base].copy()
            world[base + 1] = mcp + (0.0, -0.03, 0.0)
            world[base + 2] = mcp + (0.0, -0.02, -0.02)
            world[base + 3] = mcp + (0.0, 0.005, -0.015)
    image = np.column_stack([0.5 + world[:, 0] * 4 * HEIGHT / WIDTH, 0.5 + world[:, 1] * 4, world[:, 2] * 4])
    if mirrored:
        image[:, 0] = 1 - image[:, 0]
    return landmark_list(image), landmark_list(world)


class HandKeyboardTests(unittest.TestCase):
    STEP = 1 / 30

    def feed(self, tracker, frames, start=0.0, enabled=True):
        """frames: 프레임마다 [(라벨, thumb 또는 dict(thumb=, fist=))]. 이벤트를 모아 돌려준다."""
        events = []
        for index, hands in enumerate(frames):
            detections = []
            for label, spec in hands:
                spec = spec if isinstance(spec, dict) else {"thumb": spec}
                image, world = detection(**spec)
                detections.append((label, image, world))
            _, found = tracker.update(detections, WIDTH, HEIGHT, start + index * self.STEP, enabled=enabled)
            events.extend(found)
        return events

    def test_layout_maps_every_segment_once(self):
        self.assertEqual(len(KEY_MAP), 12)
        self.assertEqual(KEY_OF_SEGMENT["I1"], "1")
        self.assertEqual(KEY_OF_SEGMENT["P2"], "SPACE")
        self.assertEqual(len(set(KEY_OF_SEGMENT.values())), 12)

    def test_each_segment_types_its_key_once(self):
        for segment, key, _ in KEY_MAP:
            with self.subTest(segment=segment):
                tracker = HandKeyboardTracker()
                frames = [[("Right", AWAY)]] * 3 + [[("Right", thumb)] for thumb in tap(segment)]
                self.assertEqual(self.feed(tracker, frames), [("Right", key)])

    def test_hovering_thumb_does_not_type(self):
        tracker = HandKeyboardTracker()
        frames = [[("Right", AWAY)]] * 3 + [[("Right", thumb)] for thumb in tap("M1", frames_down=30, lift=0.03)]
        self.assertEqual(self.feed(tracker, frames), [])

    def test_fist_deletes_once_until_hand_opens(self):
        tracker = HandKeyboardTracker()
        fist = {"thumb": AWAY, "fist": True}
        frames = [[("Right", AWAY)]] * 3 + [[("Right", fist)]] * 30
        self.assertEqual(self.feed(tracker, frames), [("Right", "BACK")])
        frames = [[("Right", AWAY)]] * 3 + [[("Right", fist)]] * 8
        self.assertEqual(self.feed(tracker, frames, start=2.0), [("Right", "BACK")])

    def test_starting_with_fist_does_not_delete(self):
        tracker = HandKeyboardTracker()
        frames = [[("Right", {"thumb": AWAY, "fist": True})]] * 30
        self.assertEqual(self.feed(tracker, frames), [])

    def test_thumb_on_segment_during_fist_does_not_type(self):
        tracker = HandKeyboardTracker()
        frames = ([[("Right", AWAY)]] * 3 + [[("Right", {"thumb": middle("I2"), "fist": True})]] * 10
                  + [[("Right", middle("I2"))]] * 5)
        # 주먹으로 지움 한 번. 주먹을 풀었을 때 엄지가 마디에 닿아 있어도, 떼기 전에는 입력하지 않는다.
        self.assertEqual(self.feed(tracker, frames), [("Right", "BACK")])

    def test_hands_type_independently_in_any_detection_order(self):
        tracker = HandKeyboardTracker()
        frames = [[("Left", AWAY), ("Right", AWAY)]] * 3
        frames += [[("Right", AWAY), ("Left", thumb)] for thumb in tap("I1")]
        frames += [[("Left", thumb), ("Right", thumb)] for thumb in tap("R1")]
        self.assertEqual(self.feed(tracker, frames), [("Left", "1"), ("Left", "3"), ("Right", "3")])
        self.assertEqual(tracker.last_press["Right"][0], "3")

    def test_hand_loss_requires_release_when_it_returns(self):
        tracker = HandKeyboardTracker()
        approach = tap("M2")[:7]
        frames = [[("Right", AWAY)]] * 3 + [[("Right", thumb)] for thumb in approach] + [[]]
        frames += [[("Right", middle("M2"))]] * 10
        self.assertEqual(self.feed(tracker, frames), [])

    def test_explicit_hand_filter(self):
        for mode, expected in (("left", "Left"), ("right", "Right")):
            with self.subTest(mode=mode):
                tracker = HandKeyboardTracker(mode)
                image, world = detection()
                views, _ = tracker.update([("Left", image, world), ("Right", image, world)], WIDTH, HEIGHT, 0)
                self.assertEqual([v.label for v in views], [expected])

    def test_duplicate_handedness_is_visible_but_cannot_type(self):
        tracker = HandKeyboardTracker()
        frames = [[("Left", AWAY)]] * 3 + [[("Left", thumb), ("Left", AWAY)] for thumb in tap("I1")]
        self.assertEqual(self.feed(tracker, frames), [])
        image, world = detection()
        views, _ = tracker.update([("Left", image, world), ("Left", image, world)], WIDTH, HEIGHT, 5.0)
        self.assertEqual(len(views), 2)

    def test_pause_resets_and_requires_release(self):
        tracker = HandKeyboardTracker()
        self.feed(tracker, [[("Right", AWAY)]] * 3)
        tracker.reset()
        frames = [[("Right", middle("I1"))]] * 10
        self.assertEqual(self.feed(tracker, frames, start=1.0), [])
        self.assertEqual(self.feed(tracker, frames, start=2.0, enabled=False), [])

    def test_tap_threshold_can_be_changed(self):
        tracker = HandKeyboardTracker()
        hover = [[("Right", AWAY)]] * 3 + [[("Right", thumb)] for thumb in tap("M1", lift=0.015)]
        self.assertEqual(self.feed(tracker, hover), [])
        tracker.set_tap(0.02, 0.03)
        self.assertEqual(self.feed(tracker, hover, start=2.0), [("Right", "2")])

    def test_nearest_key_is_highlighted_without_typing(self):
        tracker = HandKeyboardTracker()
        frames = [[("Right", AWAY)]] * 3 + [[("Right", middle("R3", 0.03))]] * 20
        self.assertEqual(self.feed(tracker, frames), [])
        image, world = detection(middle("R3", 0.03))
        views, _ = tracker.update([("Right", image, world)], WIDTH, HEIGHT, 5.0)
        hand = views[0]
        self.assertEqual(hand.nearest, "9")
        self.assertGreater(hand.focus["9"], 0.99)
        _, _, index, _, _ = next(t for t in hand.targets if t[0] == "9")
        self.assertGreater(key_diameter(hand.targets, hand.points, index, hand.focus["9"], 1),
                           key_diameter(hand.targets, hand.points, index, 0, 1))


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


class PreviewTests(unittest.TestCase):
    def test_jamo_preview_follows_consonant_cycle_without_editing(self):
        ime = CheonjiinIME()
        self.assertEqual(ime.preview_label("4", 0.0), "ㄱ")
        self.assertEqual(ime.text, "")
        ime.press("4", 0.0)
        self.assertEqual(ime.preview_label("4", .5), "ㅋ")
        self.assertEqual(ime.text, "ㄱ")
        self.assertEqual(ime.preview_label("4", 2.0), "ㄱ")
        self.assertEqual(ime.preview_label("2", .5), "ㆍ")
        self.assertEqual(ime.preview_label("BACK", .5), "지움")
        self.assertEqual(ime.preview_label("COMMIT", .5), "확정")

    def test_commit_key_separates_same_consonant(self):
        ime = CheonjiinIME()
        ime.press("4", 0.0)
        ime.press("COMMIT", 0.1)
        ime.press("4", 0.2)
        self.assertEqual(ime.text, "ㄱㄱ")


class PoseTests(unittest.TestCase):
    def test_open_and_fist_for_both_hands_and_camera_sizes(self):
        for mirrored in (False, True):
            for width, height in ((640, 480), (1280, 720)):
                with self.subTest(mirrored=mirrored, size=(width, height)):
                    self.assertEqual(classify_hand_pose(detection(mirrored=mirrored)[0], width, height), "open")
                    self.assertEqual(classify_hand_pose(detection(mirrored=mirrored, fist=True)[0], width, height), "fist")

    def test_partial_curl_is_not_a_fist(self):
        opened = detection()[0]
        closed = detection(fist=True)[0]
        for base in (5, 9, 13):
            opened[base:base + 4] = closed[base:base + 4]
        self.assertEqual(classify_hand_pose(opened, 640, 480), "closing")

    def test_degenerate_points_do_not_look_like_a_fist(self):
        points = [SimpleNamespace(x=.5, y=.5) for _ in range(21)]
        self.assertEqual(classify_hand_pose(points, 640, 480), "closing")


class GeometryTests(unittest.TestCase):
    def test_keys_sit_on_segment_midpoints(self):
        image, _ = detection()
        points, targets = key_targets(image, WIDTH, HEIGHT)
        self.assertEqual(len(points), 21 + 12)
        self.assertEqual([key for key, _, _, _, _ in targets], [key for _, key, _ in KEY_MAP])
        # I1(끝마디)은 DIP(7)와 TIP(8)의 중점이다.
        _, _, index, _, _ = targets[0]
        self.assertAlmostEqual(points[index][0], (points[7][0] + points[8][0]) / 2)
        self.assertAlmostEqual(points[index][1], (points[7][1] + points[8][1]) / 2)

    def test_nearest_target_follows_thumb(self):
        for segment, key, _ in KEY_MAP:
            with self.subTest(segment=segment):
                _, targets = key_targets(detection(middle(segment))[0], WIDTH, HEIGHT)
                self.assertEqual(min(targets, key=lambda target: target[4])[0], key)

    def test_tiny_hand_is_not_an_input(self):
        _, targets = key_targets(detection()[0], 20, 20)
        self.assertEqual(targets, [])


class DrawingTests(unittest.TestCase):
    def test_rounded_rectangle_survives_fractional_near_circle_bounds(self):
        import random
        from PIL import Image, ImageDraw
        from project01 import rounded_rectangle

        draw = ImageDraw.Draw(Image.new("RGBA", (1280, 720)))
        # Pillow 12.3에서 ValueError가 나던 실제 좌표 (pinch_cheonjiin 1280x720 손끝 라벨)
        rounded_rectangle(draw, (400.791, 559.157, 429.791, 586.779), 13.81097210380915,
                          fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)
        rng = random.Random(0)
        for _ in range(2000):
            x, y, height = rng.uniform(0, 1200), rng.uniform(0, 650), rng.uniform(12, 40)
            bounds = (x, y, x + height + rng.uniform(0, 3), y + height)
            rounded_rectangle(draw, bounds, height / 2, fill=(1, 1, 1, 1), outline=(2, 2, 2, 2), width=2)

    def test_blur_under_changes_only_masked_area(self):
        from PIL import Image, ImageDraw
        from project01 import blur_under

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
    def fake(label):
        point = SimpleNamespace(x=0.5, y=0.5, z=0.0)
        result = SimpleNamespace(hand_landmarks=[[point] * 21], hand_world_landmarks=[[point] * 21],
                                 handedness=[[SimpleNamespace(category_name=label, score=0.9)]])
        detector = SimpleNamespace(detect_for_video=lambda image, timestamp: result)
        cv2 = SimpleNamespace(cvtColor=lambda frame, code: frame, COLOR_BGR2RGB=None)
        mp = SimpleNamespace(Image=lambda image_format, data: None, ImageFormat=SimpleNamespace(SRGB=None))
        return cv2, mp, detector

    def test_label_kept_for_unmirrored_and_swapped_for_mirrored(self):
        from project01 import detect_hands

        for raw, mirrored, expected in (("Right", False, "Right"), ("Left", False, "Left"),
                                        ("Right", True, "Left"), ("Left", True, "Right")):
            with self.subTest(raw=raw, mirrored=mirrored):
                cv2, mp, detector = self.fake(raw)
                self.assertEqual(detect_hands(cv2, mp, detector, None, 1, mirrored)[0][0], expected)

    def test_real_landmarks_give_true_hand_and_facing(self):
        import json
        from pathlib import Path
        from pinch_typer import palm_facing

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
