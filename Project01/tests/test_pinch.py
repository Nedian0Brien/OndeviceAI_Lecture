"""손가락 하나를 골라 꼬집었다 뗐는지 보는 판정기(pinch.FingerPinch)."""

import unittest

from pinch import FingerPinch
from tests.helpers import DT, OPEN, pinched, sig


def taps(frames):
    """신호 목록을 30fps로 넣고 입력된 손가락 목록을 돌려준다."""
    pinch, now, fingers = FingerPinch(), 0.0, []
    for frame in frames:
        now += DT
        tap = pinch.update(frame if frame is None or hasattr(frame, "ratios") else sig(frame), now)
        if tap:
            fingers.append(tap[0])
    return fingers


class FingerPinchTest(unittest.TestCase):
    def test_counts_on_release(self):
        self.assertEqual(taps([OPEN] * 3 + [pinched(1)] * 4 + [OPEN] * 2), [1])

    def test_needs_open_hand_first(self):
        self.assertEqual(taps([pinched(0)] * 4 + [OPEN] * 2), [])

    def test_ambiguous_between_fingers_is_ignored(self):
        self.assertEqual(taps([OPEN] * 2 + [(0.6, 0.15, 0.17, 0.7)] * 4 + [OPEN] * 2), [])

    def test_neighbor_also_close(self):
        # 중지를 꼬집으면 약지 끝마디도 가까워진다. 거리 차이(PINCH_MARGIN)만 넘으면 중지로 센다.
        self.assertEqual(taps([OPEN] * 2 + [(0.45, 0.10, 0.16, 0.5)] * 4 + [OPEN] * 2), [1])

    def test_fist_ignores_pinch_distance(self):
        # 주먹을 쥐면 엄지가 손가락 위에 놓여 거리가 낮아도 다가옴 0이고 판정하지 않는다. 손을 펴면 다시 꼬집을 수 있다.
        pinch, now = FingerPinch(), 0.0
        fist = sig((0.10, 0.12, 0.3, 0.4), pose="fist", curled=(True,) * 4)
        found = []
        for frame in [sig()] * 10 + [fist] * 12 + [sig()] * 3 + [sig(pinched(0))] * 4 + [sig()] * 3:
            now += DT
            tap = pinch.update(frame, now)
            if frame is fist:
                self.assertEqual((pinch.progress(frame.ratios), pinch.active), ([0.0] * 4, None))
            if tap:
                found.append(tap[0])
        self.assertEqual(found, [0])

    def test_fist_motion_spoils_contact(self):
        # 주먹을 쥐는 도중: 꼬집힌 것처럼 보이는 검지 말고 나머지 셋이 모두 굽는다.
        closing = sig(pinched(0), pose="closing", curled=(False, True, True, True))
        self.assertEqual(taps([sig()] * 2 + [closing] * 3 + [sig()] * 2), [])

    def test_rapid_taps_without_full_release_count_each(self):
        # 2026-10-22 기록(오른손 약지): 꼬집기 사이에 0.33~0.37까지만 벌려도 각각 1회다.
        values = [0.6] * 3 + [0.13] * 4 + [0.33] * 3 + [0.04] * 4 + [0.34] * 3 + [0.08] * 4 + [0.37] * 3 + [0.6] * 2
        self.assertEqual(taps([(0.6, 0.6, v, 0.7) for v in values]), [2, 2, 2])

    def test_single_frame_spike_is_not_a_release(self):
        values = [0.6] * 3 + [0.17] * 3 + [0.58] + [0.18] * 4 + [0.6] * 3
        self.assertEqual(taps([(0.6, 0.6, v, 0.7) for v in values]), [2])

    def test_index_resting_close_is_not_a_pinch(self):
        # 쉬는 자세에서 검지가 0.15~0.25로 가까워도 꼬집기가 아니다(기준값보다 다가오지 않음). 중지를 꼬집으면 중지다.
        rest, middle = (0.2, 0.6, 0.65, 0.7), (0.21, 0.04, 0.4, 0.6)
        self.assertEqual(taps([rest] * 20 + [middle] * 4 + [rest] * 3), [1])

    def test_shallow_pass_hands_over_to_closer_finger(self):
        # 중지로 가는 길에 검지가 0.25까지 다가왔다가 벌어진다. 검지가 아니라 중지다.
        frames = [(0.51, 0.76, 0.86, 0.99)] * 5 + [(0.27, 0.51, 0.68, 0.83), (0.25, 0.36, 0.51, 0.64),
                                                   (0.27, 0.23, 0.39, 0.54), (0.38, 0.12, 0.36, 0.52),
                                                   (0.39, 0.06, 0.35, 0.50), (0.4, 0.05, 0.36, 0.5)] \
            + [(0.5, 0.7, 0.8, 0.9)] * 3
        self.assertEqual(taps(frames), [1])

    def test_lost_hand_drops_contact(self):
        self.assertEqual(taps([sig()] * 2 + [sig(pinched(2))] * 3 + [None] + [sig()] * 3), [])

    def test_progress_and_release_progress(self):
        pinch, now = FingerPinch(), 0.0
        for _ in range(10):
            now += DT
            pinch.update(sig((0.2, 0.6, 0.65, 0.7)), now)
        progress = pinch.progress((0.2, 0.35, 0.65, 0.7))
        self.assertEqual(progress[0], 0.0)  # 원래 가까운 검지는 다가오지 않았다
        self.assertAlmostEqual(progress[1], 1.0)  # 중지는 기준값 0.6에서 PINCH_DROP 이상 다가왔다
        pinch = FingerPinch()
        for _ in range(3):
            pinch.update(sig(), 0.0)
        pinch.update(sig(pinched(0, 0.05)), 0.1)
        self.assertEqual(pinch.active, 0)
        self.assertAlmostEqual(pinch.release_progress(pinched(0, 0.11)), 0.5)  # 떼기 기준(+0.12)의 절반
        self.assertEqual(pinch.release_progress(pinched(0, 0.3)), 1.0)


if __name__ == "__main__":
    unittest.main()
