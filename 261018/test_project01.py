"""python -m unittest discover -s 261018 -p 'test_*.py' -v"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from cheonjiin import CheonjiinIME, CONSONANTS, MEDIALS, TouchGate, FistGate
from project01 import HandKeyboardTracker, classify_hand_pose, contact_geometry, is_window_closed, key_diameter, key_layout


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


class HandKeyboardTests(unittest.TestCase):
    @staticmethod
    def hand(contact=False, mirrored=False, fist=False, key="1"):
        landmarks = GeometryTests.hand()
        if fist:
            for base in (5, 9, 13, 17):
                x = landmarks[base].x
                landmarks[base + 2] = SimpleNamespace(x=x, y=.68)
                landmarks[base + 3] = SimpleNamespace(x=x, y=.73)
        if mirrored:
            landmarks = [SimpleNamespace(x=1 - p.x, y=p.y) for p in landmarks]
        if contact:
            index = next(index for actual, _, index in key_layout(landmarks) if actual == key)
            landmarks[4] = SimpleNamespace(x=landmarks[index].x, y=landmarks[index].y)
        else:
            landmarks[4] = SimpleNamespace(x=0.0, y=0.0)
        return landmarks

    def test_default_and_auto_keep_both_hands_after_first_detection(self):
        for mode in ("both", "auto"):
            with self.subTest(mode=mode):
                tracker = HandKeyboardTracker(mode)
                tracker.update([("Left", self.hand())], 640, 480, .16, 0)
                views, _ = tracker.update([("Right", self.hand(mirrored=True)), ("Left", self.hand())],
                                          640, 480, .16, .11)
                self.assertEqual({v.label for v in views}, {"Left", "Right"})

    def test_right_hand_alone_can_type(self):
        tracker = HandKeyboardTracker()
        for now in (0.0, .11):
            tracker.update([("Right", self.hand(mirrored=True))], 640, 480, .16, now)
        tracker.update([("Right", self.hand(contact=True, mirrored=True))], 640, 480, .16, .2)
        _, events = tracker.update([("Right", self.hand(contact=True, mirrored=True))], 640, 480, .16, .34)
        self.assertEqual(events, [])  # 손가락에 엄지를 대기만 해서는 입력되지 않는다.
        for now in (.4, .6):
            _, events = tracker.update([("Right", self.hand(mirrored=True, fist=True))], 640, 480, .16, now)
        self.assertEqual(events, [("Right", "1")])

    def test_hands_have_independent_release_and_detection_order(self):
        tracker = HandKeyboardTracker()
        for now in (0.0, .11):
            tracker.update([("Left", self.hand()), ("Right", self.hand(mirrored=True))], 640, 480, .16, now)
        for now in (.2, .34):
            views, events = tracker.update([("Right", self.hand(mirrored=True)), ("Left", self.hand(contact=True))],
                                           640, 480, .16, now)
        self.assertEqual(events, [])
        for now in (.4, .6):
            _, events = tracker.update([("Right", self.hand(mirrored=True)), ("Left", self.hand(fist=True))],
                                       640, 480, .16, now)
        self.assertEqual(events, [("Left", "1")])
        self.assertTrue(tracker.gates["Right"].armed)
        self.assertFalse(tracker.gates["Left"].armed)
        for now in (.7, .9):
            _, events = tracker.update([("Left", self.hand(fist=True)), ("Right", self.hand(fist=True, mirrored=True))],
                                       640, 480, .16, now)
        self.assertEqual(events, [("Right", "1")])
        self.assertEqual(tracker.last_press["Left"], ("1", .6))

    def test_hand_loss_requires_release_when_it_returns(self):
        tracker = HandKeyboardTracker()
        for now in (0.0, .11):
            tracker.update([("Right", self.hand(mirrored=True))], 640, 480, .16, now)
        tracker.update([], 640, 480, .16, .2)
        for now in (.3, 1.0):
            _, events = tracker.update([("Right", self.hand(fist=True, mirrored=True))], 640, 480, .16, now)
            self.assertEqual(events, [])

    def test_explicit_hand_filter(self):
        for mode, expected in (("left", "Left"), ("right", "Right")):
            with self.subTest(mode=mode):
                tracker = HandKeyboardTracker(mode)
                views, _ = tracker.update([("Left", self.hand()), ("Right", self.hand(mirrored=True))],
                                          640, 480, .16, 0)
                self.assertEqual([v.label for v in views], [expected])

    def test_nearest_key_highlights_before_contact_without_typing(self):
        tracker = HandKeyboardTracker()
        for now in (0.0, .11, .2):
            tracker.update([("Right", self.hand())], 640, 480, .16, now)
        views, events = tracker.update([("Right", self.hand())], 640, 480, .16, .5)
        hand = views[0]
        self.assertTrue(hand.gate.armed)
        self.assertIsNone(hand.candidate)
        self.assertIsNotNone(hand.nearest)
        self.assertEqual(hand.gate.selected, hand.nearest)
        self.assertEqual(events, [])
        self.assertEqual(hand.focus[hand.nearest], 1.0)
        _, _, index, _, _ = next(t for t in hand.targets if t[0] == hand.nearest)
        self.assertGreater(key_diameter(hand.targets, hand.points, index, hand.focus[hand.nearest], 1),
                           key_diameter(hand.targets, hand.points, index, 0, 1))

    def test_duplicate_handedness_is_visible_but_cannot_type(self):
        tracker = HandKeyboardTracker()
        for now in (0.0, .11):
            tracker.update([("Left", self.hand())], 640, 480, .16, now)
        for now in (.2, .34):
            views, events = tracker.update([("Left", self.hand(contact=True)), ("Left", self.hand(mirrored=True))],
                                           640, 480, .16, now)
            self.assertEqual(len(views), 2)
            self.assertEqual(events, [])
            self.assertFalse(tracker.gates["Left"].armed)

    def test_pause_resets_both_gates_and_requires_release(self):
        tracker = HandKeyboardTracker()
        for now in (0.0, .11):
            tracker.update([("Left", self.hand()), ("Right", self.hand(mirrored=True))], 640, 480, .16, now)
        tracker.reset()
        for now in (.2, .5):
            _, events = tracker.update([("Left", self.hand(fist=True)), ("Right", self.hand(fist=True, mirrored=True))],
                                       640, 480, .16, now)
            self.assertEqual(events, [])

    def test_preview_latches_when_fist_starts_and_survives_cycle_timeout(self):
        tracker = HandKeyboardTracker()
        ime = CheonjiinIME()
        ime.press("4", 0.0)
        for now in (.1, .21, .3, .44):
            tracker.update([("Right", self.hand(contact=True, mirrored=True, key="4"))],
                           640, 480, .16, now, ime=ime)
        views, _ = tracker.update([("Right", self.hand(mirrored=True, fist=True))],
                                  640, 480, .16, .8, ime=ime)
        self.assertEqual(views[0].pending_label, "ㅋ")
        _, events = tracker.update([("Right", self.hand(mirrored=True, fist=True))],
                                   640, 480, .16, 1.0, ime=ime)
        self.assertEqual(events, [("Right", "4")])
        ime.press("4", 1.0, prepared=tracker.pending["Right"])
        self.assertEqual(ime.text, "ㅋ")


class FistTests(unittest.TestCase):
    def setUp(self):
        self.gate = FistGate()

    def select(self, key="4", start=0.0):
        for delta in (0.0, .11, .2, .34):
            self.assertIsNone(self.gate.update(key, "open", start + delta))
        self.assertEqual(self.gate.selected, key)

    def test_starting_with_fist_does_not_type(self):
        for now in (0.0, .5, 2.0):
            self.assertIsNone(self.gate.update("4", "fist", now))
        self.assertIsNone(self.gate.selected)

    def test_selecting_and_holding_open_never_types(self):
        self.select()
        self.assertIsNone(self.gate.update("4", "open", 10.0))
        self.assertTrue(self.gate.armed)

    def test_selection_is_frozen_during_closing(self):
        self.select("4")
        self.gate.update("9", "closing", .4)
        self.assertEqual(self.gate.selected, "4")
        self.gate.update("0", "fist", .5)
        self.assertEqual(self.gate.update("BACK", "fist", .7), "4")

    def test_held_fist_fires_once_until_opened_again(self):
        self.select()
        self.gate.update("1", "fist", .4)
        self.assertEqual(self.gate.update("1", "fist", .6), "4")
        self.assertIsNone(self.gate.update("1", "fist", 5.0))
        self.assertIsNone(self.gate.update("1", "closing", 6.0))
        self.select("2", start=7.0)
        self.gate.update("9", "fist", 7.4)
        self.assertEqual(self.gate.update("9", "fist", 7.6), "2")

    def test_fist_must_be_continuous(self):
        self.select()
        self.gate.update("4", "fist", .4)
        self.gate.update("4", "closing", .5)
        self.gate.update("4", "fist", .6)
        self.assertIsNone(self.gate.update("4", "fist", .7))
        self.assertEqual(self.gate.update("4", "fist", .8), "4")

    def test_loss_forgets_selection_and_requires_open_hand(self):
        self.select()
        self.gate.lost()
        self.assertIsNone(self.gate.update("4", "fist", 2.0))
        self.assertIsNone(self.gate.update("4", "fist", 3.0))
        self.assertIsNone(self.gate.selected)

    def test_changing_key_before_stable_selection_does_not_commit_old_key(self):
        self.select("4")
        self.gate.update("5", "open", .4)
        self.assertIsNone(self.gate.selected)
        self.gate.update("5", "fist", .5)
        self.assertIsNone(self.gate.update("5", "fist", 1.0))

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

    def test_prepared_character_does_not_change_after_other_hand_edits(self):
        ime = CheonjiinIME()
        ime.press("4", 0.0)
        prepared = ime.prepare_key("4", .3)
        ime.press("SPACE", .4)
        ime.press("4", 1.0, prepared=prepared)
        self.assertEqual(ime.text, "ㄱ ㅋ")
        ime.backspace()
        self.assertEqual(ime.text, "ㄱ ")


class PoseTests(unittest.TestCase):
    def test_open_and_fist_for_both_hands_and_camera_sizes(self):
        for mirrored in (False, True):
            for width, height in ((640, 480), (1280, 720)):
                with self.subTest(mirrored=mirrored, size=(width, height)):
                    self.assertEqual(classify_hand_pose(HandKeyboardTests.hand(mirrored=mirrored), width, height), "open")
                    self.assertEqual(classify_hand_pose(HandKeyboardTests.hand(mirrored=mirrored, fist=True), width, height), "fist")

    def test_partial_curl_is_not_a_fist(self):
        opened = HandKeyboardTests.hand()
        closed = HandKeyboardTests.hand(fist=True)
        for base in (5, 9, 13):
            opened[base:base + 4] = closed[base:base + 4]
        self.assertEqual(classify_hand_pose(opened, 640, 480), "closing")

    def test_degenerate_points_do_not_look_like_a_fist(self):
        points = [SimpleNamespace(x=.5, y=.5) for _ in range(21)]
        self.assertEqual(classify_hand_pose(points, 640, 480), "closing")


class GeometryTests(unittest.TestCase):
    @staticmethod
    def hand():
        points = [SimpleNamespace(x=0.1, y=0.9) for _ in range(21)]
        for base, x in ((5, 0.3), (9, 0.5), (13, 0.7), (17, 0.9)):
            for offset in range(4):
                points[base + offset] = SimpleNamespace(x=x, y=0.75 - offset * 0.15)
        points[4] = SimpleNamespace(x=points[8].x, y=points[8].y)
        return points

    def test_thumb_at_top_left_vowel_is_key_one(self):
        _, targets, key, released = contact_geometry(self.hand(), 640, 480, 0.16)
        self.assertEqual(len(targets), 12)
        self.assertEqual(key, "1")
        self.assertFalse(released)

    def test_uniform_image_scaling_keeps_same_key(self):
        for scale in (0.5, 1, 2):
            _, _, key, _ = contact_geometry(self.hand(), 640 * scale, 480 * scale, 0.16)
            self.assertEqual(key, "1")

    def test_all_twelve_targets(self):
        for key, _, index in key_layout(self.hand()):
            with self.subTest(key=key):
                points = self.hand()
                points[4] = SimpleNamespace(x=points[index].x, y=points[index].y)
                _, _, found, _ = contact_geometry(points, 640, 480, 0.16)
                self.assertEqual(found, key)

    def test_vowels_are_top_row_for_both_hands(self):
        for mirrored in (False, True):
            with self.subTest(mirrored=mirrored):
                points = self.hand()
                if mirrored:
                    points = [SimpleNamespace(x=1 - p.x, y=p.y) for p in points]
                layout = key_layout(points)
                vowels = layout[:3]
                self.assertEqual([label for _, label, _ in vowels], ["ㅣ", "ㆍ", "ㅡ"])
                self.assertEqual([index % 4 for _, _, index in vowels], [0, 0, 0])
                xs = [points[index].x for _, _, index in vowels]
                self.assertEqual(xs, sorted(xs))
                self.assertLess(max(points[index].y for _, _, index in vowels),
                                min(points[index].y for _, _, index in layout[3:]))

    def test_standard_four_by_three_layout_and_contact_on_both_hands(self):
        expected = (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"), ("SPACE", "0", "BACK"))
        for mirrored in (False, True):
            points = self.hand()
            if mirrored:
                points = [SimpleNamespace(x=1 - p.x, y=p.y) for p in points]
            layout = key_layout(points)
            self.assertEqual(len({index for _, _, index in layout}), 12)
            for row_index, row in enumerate(expected):
                keys = layout[row_index * 3:row_index * 3 + 3]
                self.assertEqual(tuple(key for key, _, _ in keys), row)
                xs = [points[index].x for _, _, index in keys]
                self.assertEqual(xs, sorted(xs))
            for key, _, index in layout:
                with self.subTest(mirrored=mirrored, key=key):
                    points[4] = SimpleNamespace(x=points[index].x, y=points[index].y)
                    _, _, found, _ = contact_geometry(points, 640, 480, .16)
                    self.assertEqual(found, key)

    def test_fingertip_radius_uses_same_finger_neighbor(self):
        points = self.hand()
        # 다른 손가락의 MCP가 손끝에 가까워져도 손끝 반경에는 영향을 주지 않는다.
        points[9] = SimpleNamespace(x=points[8].x + .001, y=points[8].y)
        _, targets, _, _ = contact_geometry(points, 640, 480, .16)
        target = next(target for target in targets if target[2] == 8)
        self.assertGreater(target[3], 10)

    def test_far_thumb_releases(self):
        points = self.hand()
        points[4] = SimpleNamespace(x=0.0, y=0.0)
        _, _, key, released = contact_geometry(points, 640, 480, 0.16)
        self.assertIsNone(key)
        self.assertTrue(released)

    def test_tiny_hand_is_not_an_input(self):
        _, targets, key, released = contact_geometry(self.hand(), 20, 20, 0.16)
        self.assertEqual(targets, [])
        self.assertIsNone(key)
        self.assertFalse(released)


if __name__ == "__main__":
    unittest.main()
