"""꼬집기 키보드(pinch_keyboard, 방식 1) 검증: 꼬집기 판정과 키 고정, 손 역할, 겹칠 때 자판 위치 유지, 주먹 지움.

실행: python -m unittest discover -s 261018 -p 'test_*.py' -v
"""

from types import SimpleNamespace
import unittest

import numpy as np

from common.hand_keys import KEY_MAP, key_targets
from common.synthetic import AWAY, HEIGHT, WIDTH, detection, landmark_list, to_image, world_hand
from pinch_keyboard import HOLD_KEYBOARD, PinchGate, PinchKeyboard, assign_roles, pinch_cursor, pinch_ratio


def selector(cursor, pinched=False):
    """커서(엄지·검지 끝 중점)가 화면 정규화 좌표 cursor에 오는 선택 손. 자판 손과 같은 합성 손 모양."""
    world = world_hand(np.asarray(AWAY, float))
    if pinched:
        world[4] = world[8] + (0.004, 0.0, 0.0)
    image = to_image(world)
    middle = (image[4, :2] + image[8, :2]) / 2
    image[:, :2] += np.asarray(cursor) - middle
    return landmark_list(image), landmark_list(world)


def key_position(key, keyboard_image):
    """자판 손 이미지 landmark에서 키 중점의 정규화 좌표."""
    points, targets = key_targets(keyboard_image, WIDTH, HEIGHT)
    index = next(index for k, _, index, _, _ in targets if k == key)
    return points[index][0] / WIDTH, points[index][1] / HEIGHT


class PinchGateTests(unittest.TestCase):
    def test_ratio_open_and_closed(self):
        open_hand = landmark_list(world_hand(np.asarray(AWAY, float)))
        self.assertGreater(pinch_ratio(open_hand), 0.6)
        closed = world_hand(np.asarray(AWAY, float))
        closed[4] = closed[8]
        self.assertAlmostEqual(pinch_ratio(landmark_list(closed)), 0.0)

    def test_cursor_is_midpoint(self):
        points = [(0, 0)] * 21
        points[4], points[8] = (10, 20), (30, 40)
        self.assertEqual(pinch_cursor(points), (20, 30))

    def test_needs_open_first_and_fires_once(self):
        gate = PinchGate(0.3, 0.45)
        self.assertIsNone(gate.update(0.1, "1"))  # 처음부터 꼬집은 상태는 입력하지 않는다.
        gate.update(0.9, "1")
        self.assertIsNone(gate.update(0.4, "1"))
        self.assertEqual(gate.update(0.2, "1"), "1")
        self.assertIsNone(gate.update(0.1, "1"))
        gate.update(0.5, None)
        self.assertEqual(gate.update(0.2, "2"), "2")

    def test_key_is_latched_when_closing_starts(self):
        gate = PinchGate(0.3, 0.45)
        gate.update(0.9, "4")
        gate.update(0.4, "4")  # 오므리기 시작: 4 고정
        gate.update(0.35, "5")  # 오므리는 동안 커서가 옆 키로 넘어가도
        self.assertEqual(gate.update(0.2, "5"), "4")

    def test_reopening_before_contact_cancels(self):
        gate = PinchGate(0.3, 0.45)
        gate.update(0.9, "4")
        gate.update(0.4, "4")
        gate.update(0.6, "4")
        gate.update(0.4, "7")
        self.assertEqual(gate.update(0.2, "7"), "7")

    def test_no_key_under_cursor_types_nothing(self):
        gate = PinchGate(0.3, 0.45)
        gate.update(0.9, None)
        gate.update(0.4, None)
        self.assertIsNone(gate.update(0.1, "1"))


class RoleTests(unittest.TestCase):
    @staticmethod
    def hand(x):
        return [SimpleNamespace(x=x, y=0.5) for _ in range(21)], None

    def test_labels_decide_when_different(self):
        left, right = ("Left", *self.hand(0.8)), ("Right", *self.hand(0.2))
        self.assertEqual(assign_roles([right, left], "Left"), (left, right))
        self.assertEqual(assign_roles([right, left], "Right"), (right, left))

    def test_screen_side_decides_when_labels_match(self):
        a, b = ("Right", *self.hand(0.2)), ("Right", *self.hand(0.8))
        self.assertEqual(assign_roles([b, a], "Left"), (a, b))
        self.assertEqual(assign_roles([b, a], "Right"), (b, a))

    def test_single_hand(self):
        hand = ("Left", *self.hand(0.5))
        self.assertEqual(assign_roles([hand], "Left"), (hand, None))
        self.assertEqual(assign_roles([hand], "Right"), (None, hand))
        self.assertEqual(assign_roles([], "Left"), (None, None))


class PinchKeyboardTests(unittest.TestCase):
    STEP = 1 / 30

    def setUp(self):
        self.keyboard_image, self.keyboard_world = detection()

    def frames(self, board, steps, start=0.0):
        """steps: 프레임마다 (키 또는 정규화 좌표, 꼬집음 여부) 또는 None(선택 손 없음)."""
        events = []
        for index, step in enumerate(steps):
            hands = [("Left", self.keyboard_image, self.keyboard_world)]
            if step is not None:
                where, pinched = step
                cursor = key_position(where, self.keyboard_image) if isinstance(where, str) else where
                hands.append(("Right", *selector(cursor, pinched)))
            _, found, _ = board.update(hands, WIDTH, HEIGHT, start + index * self.STEP)
            events.extend(found)
        return events

    def test_pinch_over_each_key_types_it(self):
        for _, key, _ in KEY_MAP:
            with self.subTest(key=key):
                board = PinchKeyboard()
                steps = [None] * 2 + [(key, False)] * 3 + [(key, True)] * 3 + [(key, False)] * 2
                self.assertEqual(self.frames(board, steps), [key])

    def test_open_selector_only_highlights(self):
        board = PinchKeyboard()
        self.assertEqual(self.frames(board, [None] + [("5", False)] * 10), [])
        self.assertEqual(board.highlight, "5")

    def test_pinch_away_from_keys_types_nothing(self):
        board = PinchKeyboard()
        steps = [None] + [((0.95, 0.95), False)] * 3 + [((0.95, 0.95), True)] * 3
        self.assertEqual(self.frames(board, steps), [])

    def test_keyboard_position_is_kept_while_selector_overlaps(self):
        board = PinchKeyboard()
        self.frames(board, [None])
        cached = board.cache.points
        moved_image = [SimpleNamespace(x=p.x + 0.05, y=p.y, z=p.z) for p in self.keyboard_image]
        cursor = key_position("1", self.keyboard_image)
        board.update([("Left", moved_image, self.keyboard_world), ("Right", *selector(cursor))], WIDTH, HEIGHT, 1.0)
        self.assertEqual(board.cache.points, cached)
        board.update([("Left", moved_image, self.keyboard_world)], WIDTH, HEIGHT, 1.1)
        self.assertNotEqual(board.cache.points, cached)

    def test_hidden_keyboard_uses_last_position_for_a_while(self):
        board = PinchKeyboard()
        self.frames(board, [None])
        cursor = key_position("8", self.keyboard_image)
        board.update([("Right", *selector(cursor))], WIDTH, HEIGHT, 0.5)
        _, events, _ = board.update([("Right", *selector(cursor, True))], WIDTH, HEIGHT, 0.55)
        self.assertEqual(events, ["8"])
        board.update([("Right", *selector(cursor))], WIDTH, HEIGHT, HOLD_KEYBOARD + 0.1)
        self.assertIsNone(board.cache)

    def test_keyboard_fist_deletes_once(self):
        board = PinchKeyboard()
        fist_image, fist_world = detection(fist=True)
        events = []
        for index in range(3):
            _, found, _ = board.update([("Left", self.keyboard_image, self.keyboard_world)], WIDTH, HEIGHT, index * 0.03)
            events += found
        for index in range(20):
            _, found, _ = board.update([("Left", fist_image, fist_world)], WIDTH, HEIGHT, 0.1 + index * 0.03)
            events += found
        self.assertEqual(events, ["BACK"])

    def test_pause_requires_reopen(self):
        board = PinchKeyboard()
        cursor = key_position("1", self.keyboard_image)
        self.frames(board, [None] + [("1", False)] * 2)
        board.reset()
        _, events, _ = board.update([("Left", self.keyboard_image, self.keyboard_world),
                                     ("Right", *selector(cursor, True))], WIDTH, HEIGHT, 1.0)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
