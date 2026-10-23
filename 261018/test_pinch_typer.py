"""손끝 꼬집기 입력(pinch_typer)과 앱(pinch_cheonjiin)의 카메라 없는 부분 검증.

상태 머신 테스트는 표준 라이브러리만 쓴다. CameraSizeTests는 numpy·OpenCV를 쓴다(검출 영상 축소).
실행: python -m unittest discover -s 261018 -p 'test_*.py' -v
"""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from korean_ime import CheonjiinIME
from common.gestures import Kalman1D, Orientation
from common.pose import palm_facing, palm_roll
from pinch_typer import FingerPinch, HandSignal, PinchTyper, hand_signal, pinch_ratios, replay, signal_from_log


FIXTURES = Path(__file__).resolve().with_name("fixtures") / "pinch-typer-keys.json"
DT = 1 / 30
OPEN = (0.6, 0.6, 0.65, 0.7)


def sig(ratios=OPEN, pose="open", facing=0.8, curled=(False,) * 4, roll=0.0, tips=None, size=0.0):
    return HandSignal(tuple(ratios), tuple(curled), pose, facing, roll, None, tips, size)


def vowel(run, drag=None, frames=6, right=None):
    """왼손 중지(모음 버튼) 꼬집기. drag "h"/"v"면 꼬집은 채 손 크기(100)의 0.4배만큼 가로/세로로 끈다."""
    for k in range(frames):
        step = 40.0 * k / (frames - 1) if drag else 0.0
        point = (50 + step, 50.0) if drag == "h" else (50.0, 50 + step) if drag == "v" else (50.0, 50.0)
        run.frames(1, sig(pinch_ratios_for(1), tips=(point,) * 5, size=100.0), right or sig())
    run.frames(3, sig(), right or sig())


def pinch_ratios_for(finger, value=0.08):
    ratios = list(OPEN)
    ratios[finger] = value
    return tuple(ratios)


class Runner:
    """프레임 묶음을 30fps로 흘려 넣고 이벤트를 모은다.

    손가락 고르기·떼기 규칙을 시험하는 테스트가 대부분이라 기본은 뗄 때 입력(hold=0)이다.
    누르고 유지 방식은 HoldTest에서 따로 시험한다.
    """

    def __init__(self, typer=None):
        self.typer = typer or PinchTyper(hold=0)
        self.now = 0.0
        self.events = []

    def frames(self, count, left=None, right=None):
        for _ in range(count):
            self.now += DT
            self.events += self.typer.update({"Left": left, "Right": right}, self.now)
        return self

    def ready(self, left_facing=0.8, right_facing=0.8):
        return self.frames(20, sig(facing=left_facing), sig(facing=right_facing))  # 방향 인정 0.15초 + SETTLE 0.3초

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
        typer = replay([["tap", "Right", 0], ["tap", "Left", 1, "palm", "v"], ["tap", "Left", 1],
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
    """손가락 고르기·떼기 규칙(뗄 때 입력, hold=0)."""

    def test_counts_on_release(self):
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        for ratios in [OPEN] * 3 + [pinch_ratios_for(1)] * 4 + [OPEN] * 2:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [1])

    def test_needs_open_hand_first(self):
        pinch, now = FingerPinch(hold=0), 0.0
        results = []
        for ratios in [pinch_ratios_for(0)] * 4 + [OPEN] * 2:
            now += DT
            results.append(pinch.update(sig(ratios), now))
        self.assertEqual([r for r in results if r], [])

    def test_ambiguous_between_fingers_is_ignored(self):
        pinch, now, results = FingerPinch(hold=0), 0.0, []
        for ratios in [OPEN] * 2 + [(0.6, 0.15, 0.17, 0.7)] * 4 + [OPEN] * 2:
            now += DT
            results.append(pinch.update(sig(ratios), now))
        self.assertEqual([r for r in results if r], [])

    def test_neighbor_also_close(self):
        # 중지를 꼬집으면 약지 끝마디도 가까워진다. 거리 차이(margin)만 넘으면 중지로 센다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        for ratios in [OPEN] * 2 + [(0.45, 0.10, 0.16, 0.5)] * 4 + [OPEN] * 2:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [1])

    def test_fist_motion_spoils_contact(self):
        # 주먹을 쥐는 도중: 꼬집힌 것처럼 보이는 검지 말고 나머지 셋이 모두 굽는다.
        pinch, now, results = FingerPinch(hold=0), 0.0, []
        frames = [sig()] * 2 + [sig(pinch_ratios_for(0), pose="closing", curled=(False, True, True, True))] * 3 \
            + [sig()] * 2
        for frame in frames:
            now += DT
            results.append(pinch.update(frame, now))
        self.assertEqual([r for r in results if r], [])

    def test_pinky_pinch_with_middle_and_ring_bent_counts(self):
        # 2026-10-23 기록: 소지를 꼬집으면 중지·약지가 함께 굽는다(curled 0111). 주먹이 아니므로 입력이다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        frames = [sig((0.44, 0.22, 0.25, 0.52))] * 6 \
            + [sig((0.61, 0.43, 0.14, 0.02), pose="closing", curled=(False, True, True, True))] * 5 \
            + [sig((0.44, 0.19, 0.14, 0.37))] * 3
        for frame in frames:
            now += DT
            result = pinch.update(frame, now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [3])

    def test_rapid_taps_without_full_release_count_each(self):
        # 2026-10-22 로그(오른손 약지): 꼬집기 사이에 0.33~0.37까지만 벌려도 각각 1회다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        values = [0.6] * 3 + [0.13] * 4 + [0.33] * 3 + [0.04] * 4 + [0.34] * 3 + [0.08] * 4 + [0.37] * 3 + [0.6] * 2
        for value in values:
            now += DT
            result = pinch.update(sig((0.6, 0.6, value, 0.7)), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2, 2, 2])

    def test_single_frame_spike_is_not_a_release(self):
        # 같은 로그(왼손 약지): 0.17 → 0.58(한 프레임) → 0.18은 한 번의 꼬집기다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        for value in [0.6] * 3 + [0.17] * 3 + [0.58] + [0.18] * 4 + [0.6] * 3:
            now += DT
            result = pinch.update(sig((0.6, 0.6, value, 0.7)), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2])

    def test_jitter_inside_a_held_pinch_is_not_a_release(self):
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        for value in [0.6] * 3 + [0.04, 0.17, 0.05, 0.16, 0.06, 0.2, 0.05] + [0.6] * 3:
            now += DT
            result = pinch.update(sig((0.6, 0.6, value, 0.7)), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2])

    def test_index_resting_close_is_not_a_pinch(self):
        # 2026-10-22 정답 기록: 쉬는 자세에서 검지가 0.15~0.25로 가까워도 꼬집기가 아니다. 중지를 꼬집으면 중지다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        rest, middle = (0.2, 0.6, 0.65, 0.7), (0.21, 0.04, 0.4, 0.6)
        for ratios in [rest] * 20 + [middle] * 4 + [rest] * 3:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [1])

    def test_passing_index_on_the_way_to_ring_counts_ring(self):
        # 엄지가 약지로 가는 길에 검지 옆을 먼저 지나가도(검지 0.42 → 0.22) 실제로 꼬집은 약지로 센다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        frames = [(0.42, 0.6, 0.65, 0.7)] * 5 + [(0.22, 0.45, 0.5, 0.7)] * 2 + [(0.3, 0.3, 0.12, 0.6)] \
            + [(0.4, 0.35, 0.04, 0.55)] * 3 + [(0.42, 0.6, 0.4, 0.7)] * 3
        for ratios in frames:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [2])

    def test_shallow_pass_hands_over_to_closer_finger(self):
        # 2026-10-23 기록 차례 33: 중지로 가는 길에 검지가 0.25까지 다가왔다가 벌어진다. 검지가 아니라 중지다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        frames = [(0.51, 0.76, 0.86, 0.99)] * 5 + [(0.27, 0.51, 0.68, 0.83), (0.25, 0.36, 0.51, 0.64),
                                                   (0.27, 0.23, 0.39, 0.54), (0.38, 0.12, 0.36, 0.52),
                                                   (0.39, 0.06, 0.35, 0.50), (0.4, 0.05, 0.36, 0.5)] \
            + [(0.5, 0.7, 0.8, 0.9)] * 3
        for ratios in frames:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [1])

    def test_deep_pinch_is_not_handed_over_on_the_way_back(self):
        # 소지를 깊게 꼬집은 뒤 엄지가 돌아가며 약지 옆을 지나가도 소지다.
        pinch, now, events = FingerPinch(hold=0), 0.0, []
        frames = [(0.6, 0.7, 0.75, 0.8)] * 5 + [(0.6, 0.6, 0.4, 0.05)] * 3 + [(0.6, 0.5, 0.2, 0.3)] * 2 \
            + [(0.6, 0.7, 0.75, 0.8)] * 3
        for ratios in frames:
            now += DT
            result = pinch.update(sig(ratios), now)
            if result:
                events.append(result[0])
        self.assertEqual(events, [3])

    def test_progress_ignores_finger_that_did_not_approach(self):
        pinch, now = FingerPinch(hold=0), 0.0
        for _ in range(10):
            now += DT
            pinch.update(sig((0.2, 0.6, 0.65, 0.7)), now)
        progress = pinch.progress((0.2, 0.35, 0.65, 0.7))
        self.assertEqual(progress[0], 0.0)  # 원래 가까운 검지는 다가오지 않았다
        self.assertAlmostEqual(progress[1], 1.0)  # 중지는 기준값 0.6에서 drop(0.15) 이상 다가왔다
        self.assertEqual(pinch.progress((0.2, 0.6, 0.65, 0.39))[3], 0.0)  # exit 위는 0

    def test_lost_hand_drops_contact(self):
        pinch, now = FingerPinch(hold=0), 0.0
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

    def test_finger_flex(self):
        from common.pose import finger_flex
        straight = [SimpleNamespace(x=0.0, y=-0.01 * i, z=0.0) for i in range(21)]
        for base in (5, 9, 13, 17):  # 손가락마다 손목에서 곧게 뻗은 네 점
            for k in range(4):
                straight[base + k] = SimpleNamespace(x=0.01 * base, y=-0.05 - 0.02 * k, z=0.0)
        straight[0] = SimpleNamespace(x=0.01 * 9, y=0.05, z=0.0)
        bent = list(straight)
        bent[12] = SimpleNamespace(x=0.09, y=-0.09, z=-0.02)  # 중지 끝마디만 90° 굽힘
        self.assertLess(finger_flex(straight)[1], 1.0)
        self.assertAlmostEqual(finger_flex(bent)[1] - finger_flex(straight)[1], 90.0, delta=1.0)
        self.assertAlmostEqual(finger_flex(bent)[0], finger_flex(straight)[0])

    def test_palm_roll_signs(self):
        # 엄지 쪽(검지 뿌리)이 카메라에 가까우면(z가 작으면) 양수, 소지 쪽이 가까우면 음수. 좌우 반전과 무관하다.
        p = self.points(0.56, 0.44)
        p[5], p[17] = SimpleNamespace(x=0.56, y=0.6, z=-0.12), SimpleNamespace(x=0.44, y=0.6, z=0.0)
        self.assertAlmostEqual(palm_roll(p), 45.0, places=6)
        p[5], p[17] = SimpleNamespace(x=0.56, y=0.6, z=0.12), SimpleNamespace(x=0.44, y=0.6, z=0.0)
        self.assertAlmostEqual(palm_roll(p), -45.0, places=6)
        mirrored = [SimpleNamespace(x=1 - q.x, y=q.y, z=q.z) for q in p]
        self.assertAlmostEqual(palm_roll(mirrored), -45.0, places=6)
        self.assertAlmostEqual(palm_roll(self.points(0.56, 0.44)), 0.0)

    def test_signal_from_log_computes_roll_for_old_rows(self):
        entry = {"ratios": [0.1, 0.5, 0.6, 0.7], "curled": [False] * 4, "pose": "open", "facing": 0.6,
                 "image": [[0.5, 0.5, 0.0]] * 5 + [[0.6, 0.5, 0.1]] + [[0.5, 0.5, 0.0]] * 11 + [[0.5, 0.5, 0.0]] * 4}
        self.assertAlmostEqual(signal_from_log(entry, (1.0, 1.0)).roll, -45.0, places=6)
        self.assertEqual(signal_from_log(dict(entry, roll=-12.5)).roll, -12.5)
        self.assertEqual(signal_from_log({k: v for k, v in entry.items() if k != "image"}).roll, 0.0)

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
        self.tap(run, "Right", 0)  # ㄱ
        vowel(run, "v")  # 세로 끌기 ㅣ
        vowel(run)  # 그대로 ㆍ → ㅏ
        self.assertEqual(run.typer.ime.text, "가")

    def test_vowel_drag_direction(self):
        # 모음 버튼(왼손 중지): 그대로 ㆍ, 가로 끌기 ㅡ, 세로 끌기 ㅣ(pinch_typer.DRAG_MIN).
        for drag, expected in ((None, "ㆍ"), ("h", "ㅡ"), ("v", "ㅣ")):
            run = Runner().ready()
            vowel(run, drag)
            self.assertEqual(run.kinds("input"), [("input", expected)], drag)

    def test_drag_view_while_pinching_vowel(self):
        # 모음 버튼을 꼬집는 동안 화면용 궤적과 지금 떼면 들어갈 끌기를 알 수 있다. 다른 키·접촉 없음은 None.
        run = Runner().ready()
        self.assertIsNone(run.typer.drag_view("Left"))
        for k in range(6):
            run.frames(1, sig(pinch_ratios_for(1), tips=((50.0, 50.0 + 8 * k),) * 5, size=100.0), sig())
        view = run.typer.drag_view("Left")
        self.assertEqual((view["finger"], len(view["points"]), view["drag"]), (1, 6, "v"))
        self.assertEqual(view["points"][0], (50.0, 50.0))
        run.frames(3, sig(), sig())
        self.assertIsNone(run.typer.drag_view("Left"))
        self.assertEqual(run.kinds("input"), [("input", "ㅣ")])
        run.frames(4, sig(), sig(pinch_ratios_for(0), tips=((50.0, 50.0),) * 5, size=100.0))  # 오른손 ㄱ: 모음 아님
        self.assertIsNone(run.typer.drag_view("Right"))

    def test_release_motion_is_not_drag(self):
        # 떼는 동안(떼기 기준 위 프레임)만 꼬집기 지점이 크게 움직이면 끌기로 세지 않는다(ㆍ).
        run = Runner().ready()
        still = sig(pinch_ratios_for(1), tips=((50.0, 50.0),) * 5, size=100.0)
        opening = sig(tips=((50.0, 110.0),) * 5, size=100.0)
        run.frames(5, still, sig()).frames(2, opening, sig()).frames(3, sig(), sig())
        self.assertEqual(run.kinds("input"), [("input", "ㆍ")])

    def test_turning_hand_is_ignored(self):
        run = Runner().ready()
        run.frames(4, sig(pinch_ratios_for(0), facing=0.1), sig()).frames(3, sig(facing=0.1), sig())
        self.assertEqual(run.typer.ime.text, "")

    def test_flip_while_releasing_is_ignored(self):
        # 손바닥에서 꼬집고 손등으로 뒤집으며 뗐다. Orientation은 아직 손바닥이지만(FACING_DWELL),
        # 마지막 프레임들의 손 방향 값이 손등 쪽이라 입력하지 않는다(2026-10-23 기록의 뒤집기 ㄱ).
        run = Runner().ready()
        run.frames(5, sig(), sig(pinch_ratios_for(0))).frames(2, sig(), sig(facing=-0.6))
        run.frames(8, sig(), sig())
        self.assertEqual((run.typer.ime.text, run.typer.pinches), ("", []))

    def test_one_frame_facing_jitter_keeps_tap(self):
        # 접촉 중간에 손 방향 값이 한 프레임만 반대로 튀어도 처음·끝이 손바닥이면 입력한다.
        run = Runner().ready()
        pinched = pinch_ratios_for(0)
        run.frames(3, sig(), sig(pinched)).frames(1, sig(), sig(pinched, facing=-0.4)).frames(3, sig(), sig(pinched))
        run.frames(3, sig(), sig())
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_thumb_hidden_by_roll_is_ignored(self):
        # 소지 쪽이 카메라에 가깝게 기울면(-25° 이하) 엄지가 가려 손가락 값을 믿을 수 없다: 손바닥 꼬집기를 버리고 안내한다.
        run = Runner().ready()
        tilted = sig(roll=-35.0)
        run.frames(3, sig(), tilted)
        self.assertTrue(run.typer.occluded("Right"))
        run.frames(5, sig(), sig(pinch_ratios_for(0), roll=-35.0)).frames(3, sig(), tilted)
        self.assertEqual((run.typer.ime.text, run.typer.pinches), ("", []))
        run.frames(3, sig(), sig(roll=-10.0))
        self.assertFalse(run.typer.occluded("Right"))
        run.frames(5, sig(), sig(pinch_ratios_for(0), roll=-10.0)).frames(3, sig(), sig(roll=-10.0))
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_roll_rule_skips_back_of_hand(self):
        run = Runner().ready()
        back = sig(facing=-0.8, roll=-35.0)
        run.frames(20, sig(), back)
        self.assertFalse(run.typer.occluded("Right"))
        run.frames(4, sig(), sig(pinch_ratios_for(0), facing=-0.8, roll=-35.0)).frames(3, sig(), back)
        self.assertEqual(run.typer.ime.text, " ")  # 오른손 손등 검지: 후보가 없으니 공백

    def test_index_overlap_goes_to_bent_finger(self):
        # 엄지 옆면이 검지를 눌러 검지·중지 값이 함께 내려왔다. 중지만 굽었으면 중지(ㄴ), 굽힘 정보가 없거나
        # 검지가 굽고 중지는 그만큼 더 굽지 않았으면 지금처럼 검지(ㄱ)다(pinch_typer.INDEX_TIE).
        rest_flex = (20.0, 20.0, 20.0, 20.0)
        for flex, expected in (((22.0, 60.0, 30.0, 25.0), "ㄴ"), ((30.0, 25.0, 22.0, 21.0), "ㄱ"), (None, "ㄱ")):
            run = Runner().ready()
            run.frames(10, sig(), HandSignal(OPEN, (False,) * 4, "open", 0.8, 0.0, rest_flex if flex else None))
            start = HandSignal((0.08, 0.30, 0.55, 0.7), (False,) * 4, "open", 0.8, 0.0, rest_flex if flex else None)
            both = HandSignal((0.10, 0.11, 0.5, 0.7), (False,) * 4, "open", 0.8, 0.0, flex)
            run.frames(2, sig(), start).frames(3, sig(), both)
            run.frames(3, sig(), HandSignal(OPEN, (False,) * 4, "open", 0.8, 0.0, flex))
            self.assertEqual(run.typer.ime.text, expected, flex)

    def test_shallow_repinch_counts_twice(self):
        # 눌린 채로 0.14만 벌렸다가 다시 꼬집었다(2026-10-23 사용자: 핀치를 해도 눌리지 않는다). 떼기(+0.12)와
        # 같은 손가락 다시 꼬집기(0.08 다가옴, 처음 꼬집기는 0.15)로 두 번 입력되어 ㄱ → ㅋ으로 순환한다.
        run = Runner().ready()
        pressed, half = sig(pinch_ratios_for(0, 0.05)), sig(pinch_ratios_for(0, 0.19))
        run.frames(3, sig(), pressed).frames(3, sig(), half).frames(3, sig(), pressed).frames(3, sig(), sig())
        self.assertEqual(run.kinds("input"), [("input", "ㄱ"), ("input", "ㅋ")])

    def test_no_switch_while_releasing(self):
        # 중지를 떼는 중에 엄지가 검지로 미끄러져 검지가 더 가까워져도 중지(ㄴ)로 입력한다.
        run = Runner().ready()
        run.frames(3, sig(), sig((0.6, 0.05, 0.6, 0.7))).frames(1, sig(), sig((0.6, 0.19, 0.6, 0.7)))
        run.frames(1, sig(), sig((0.02, 0.24, 0.6, 0.7))).frames(3, sig(), sig())
        self.assertEqual(run.typer.ime.text, "ㄴ")

    def test_release_progress(self):
        pinch = FingerPinch(hold=0)
        self.assertEqual(pinch.release_progress(OPEN), 0.0)
        for _ in range(3):
            pinch.update(sig(), 0.0)
        pinch.update(sig(pinch_ratios_for(0, 0.05)), 0.1)
        self.assertEqual(pinch.active, 0)
        self.assertAlmostEqual(pinch.release_progress(pinch_ratios_for(0, 0.11)), 0.5)
        self.assertEqual(pinch.release_progress(pinch_ratios_for(0, 0.3)), 1.0)

    def test_right_index_sweep_is_ignored(self):
        # 오른손 검지 지나감(pinch_typer.INDEX_REPASS): 중지(ㄴ)를 뗀 직후 엄지가 돌아가며 검지를 지나간 접촉,
        # 검지가 굽지 않은 접촉은 입력하지 않는다. 같은 동작이라도 왼손(중지 ㆍ 다음 곧바로 검지 ㅂ)은 그대로 입력한다.
        rest, bent, flat = (20.0,) * 4, (35.0, 20.0, 20.0, 20.0), (21.0, 20.0, 20.0, 20.0)

        def frames(run, hand, count, ratios, flex):
            signal = HandSignal(ratios, (False,) * 4, "open", 0.8, 0.0, flex)
            run.frames(count, signal if hand == "Left" else sig(), signal if hand == "Right" else sig())

        for hand, expected in (("Right", "ㄴ"), ("Left", "ㆍㅂ")):
            run = Runner().ready()
            frames(run, hand, 10, OPEN, rest)
            frames(run, hand, 4, (0.6, 0.05, 0.6, 0.7), rest)  # 중지 꼬집기
            frames(run, hand, 2, (0.6, 0.30, 0.6, 0.7), rest)  # 뗌
            frames(run, hand, 1, OPEN, rest)
            frames(run, hand, 4, (0.05, 0.4, 0.6, 0.7), bent)  # 0.07초 뒤 검지(굽음)
            frames(run, hand, 3, OPEN, rest)
            self.assertEqual(run.typer.ime.text, expected, hand)
        run = Runner().ready()
        frames(run, "Right", 10, OPEN, rest)
        frames(run, "Right", 4, pinch_ratios_for(0, 0.05), flat)  # 검지가 굽지 않은 채 닿음
        frames(run, "Right", 3, OPEN, rest)
        self.assertEqual(run.typer.ime.text, "")
        frames(run, "Right", 10, OPEN, rest)
        frames(run, "Right", 4, pinch_ratios_for(0, 0.05), bent)  # 검지를 굽혀 꼬집음
        frames(run, "Right", 3, OPEN, rest)
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_short_contact_is_ignored(self):
        # PINCH_MIN_CONTACT(0.10초) 미만: 엄지가 손끝을 스친 것으로 보고 버린다.
        run = Runner().ready()
        self.tap(run, "Right", 0, frames=1)  # 접촉 2프레임 간격(0.067초)
        self.assertEqual(run.typer.ime.text, "")
        self.tap(run, "Right", 0, frames=3)  # 0.133초
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_both_fists_delete_last_jamo(self):
        run = Runner().ready()
        self.tap(run, "Right", 3)
        self.tap(run, "Right", 3)  # ㅇ → ㅁ
        vowel(run, "v")  # 미
        fist = sig((0.3, 0.32, 0.4, 0.45), pose="fist", curled=(True,) * 4)
        run.frames(8, fist, sig()).frames(3, sig(), sig())  # 왼손 주먹: 지움(ㅣ)
        self.assertEqual(run.typer.ime.text, "ㅁ")
        run.frames(8, sig(), fist).frames(3, sig(), sig())  # 오른손 주먹: 지움(ㅁ째, ㅇ으로 되돌리지 않음)
        self.assertEqual(run.typer.ime.text, "")
        self.assertEqual(len(run.kinds("delete")), 2)

    def test_right_back_index_is_commit_space(self):
        # 오른손 손등 검지 = 확정/공백(후보가 있으면 확정, 없으면 공백). 손등은 손가락이 가려 나머지 꼬집기는 무시한다.
        run = Runner().ready()
        self.tap(run, "Right", 1)  # ㄴ 후보
        back = sig(facing=-0.8)
        run.frames(20, sig(), back)  # 오른손을 뒤집는다(방향 인정 + SETTLE + 쉬는 자세)
        for finger in (1, 2):  # 손등 중지·약지: 무시
            run.frames(4, sig(), sig(pinch_ratios_for(finger), facing=-0.8)).frames(3, sig(), back)
        self.assertEqual(run.typer.ime.text, "ㄴ")
        run.frames(4, sig(), sig(pinch_ratios_for(0), facing=-0.8)).frames(3, sig(), back)  # 확정
        self.assertEqual((run.kinds("commit"), run.typer.pending), ([("commit", None)], None))
        run.frames(4, sig(), sig(pinch_ratios_for(0), facing=-0.8)).frames(3, sig(), back)  # 공백
        self.assertEqual(run.typer.ime.text, "ㄴ ")

    def test_fist_held_keeps_deleting(self):
        from pinch_typer import FIST_REPEAT
        run = Runner().ready()
        for consonant in (0, 1):  # 가나: 키 입력 6번
            self.tap(run, "Right", consonant)
            vowel(run, "v")
            vowel(run)
        fist = sig((0.3, 0.32, 0.4, 0.45), pose="fist", curled=(True,) * 4)
        frames = round((0.18 + 2 * FIST_REPEAT + 0.25) / DT)  # 첫 지움 + 0.5초마다 두 번 더(세 번째 반복 전에 편다)
        run.frames(frames, sig(), fist).frames(3, sig(), sig())
        self.assertEqual(len(run.kinds("delete")), 3)
        self.assertEqual(run.typer.ime.text, "ㄱ")  # 가나 → 간 → 가 → ㄱ
        run.frames(8, fist, sig()).frames(3, sig(), sig())  # 왼손 주먹도 지움
        self.assertEqual(run.typer.ime.text, "")


class CollectTest(unittest.TestCase):
    """기록 모드(pinch_collect.Collector)와 PinchTyper.pinches."""

    def test_order_is_index_to_pinky_per_hand(self):
        from pinch_collect import Collector
        collector = Collector(reps=2)
        left = [("Left", finger, "palm") for finger in range(4)]
        right = [("Right", finger, "palm") for finger in range(4)]
        self.assertEqual(collector.order, left * 2 + right * 2)
        self.assertEqual([collector.round_of(i) for i in (0, 3, 4, 8, 15)], [1, 1, 2, 1, 2])
        self.assertEqual(Collector(reps=1, kind="edit").order, [("Left", finger, "back") for finger in range(4)])
        # 한 손만: 그 손 손바닥 검지 → 소지를 reps바퀴
        self.assertEqual(Collector(reps=2, kind="right").order, right * 2)
        self.assertEqual(Collector(reps=1, kind="left").order, left)
        self.assertEqual([Collector(reps=2, kind="right").round_of(i) for i in (0, 4, 7)], [1, 2, 2])

    def test_prompt_advances_only_on_prompted_hand_and_face_then_rests(self):
        from pinch_collect import REST, Collector
        collector = Collector(reps=1)
        number, hand, finger, face = collector.prompt(0.0)
        self.assertEqual(collector.update([("Right", finger, "palm")], 0.1), [])  # 다른 손은 넘기지 않는다
        self.assertEqual(collector.update([(hand, finger, "back")], 0.1), [])  # 다른 방향도 넘기지 않는다
        events = collector.update([(hand, (finger + 1) % 4, face)], 0.2)
        self.assertEqual(events, [("collect", 0, hand, finger, (finger + 1) % 4, face)])
        self.assertIsNone(collector.prompt(0.2 + REST / 2))  # 쉬는 중
        self.assertEqual(collector.prompt(0.2 + REST)[0], 1)

    def test_skip_redo_and_summary(self):
        from pinch_collect import REST, Collector
        collector = Collector(reps=1)
        _, hand, finger, face = collector.prompt(0.0)
        self.assertEqual(collector.skip(0.0)[0][4], "skip")
        self.assertEqual(collector.redo(1.0), [("collect", 0, hand, finger, "redo", face)])
        self.assertEqual(collector.prompt(1.0 + REST)[0], 0)
        collector.update([(hand, finger, face)], 2.0)
        self.assertIn("왼손 검지: 1 / 0 / 0", collector.summary())
        t = 3.0
        while not collector.done:
            _, hand, finger, face = collector.prompt(t)
            collector.update([(hand, finger, face)], t)
            t += 1.0
        self.assertIsNone(collector.prompt(t))
        self.assertTrue(all(collector.results[i] == item[1] for i, item in enumerate(collector.order)))

    def test_typer_reports_accepted_pinches(self):
        run = Runner().ready()
        pinched = sig(pinch_ratios_for(2))
        found = []
        for _ in range(4):
            run.frames(1, sig(), pinched)
            found += run.typer.pinches
        for _ in range(3):
            run.frames(1, sig(), sig())
            found += run.typer.pinches
        self.assertEqual(found, [("Right", 2, "palm")])

    def test_log_row_prompt_field(self):
        from pinch_cheonjiin import log_row
        typer = PinchTyper()
        self.assertNotIn("prompt", log_row(1.0, typer, {}, {}, [], 640, 480))
        self.assertIsNone(log_row(1.0, typer, {}, {}, [], 640, 480, True, None)["prompt"])
        row = log_row(1.0, typer, {}, {}, [], 640, 480, True, (3, "Left", 2, "back"))
        self.assertEqual(row["prompt"], {"id": 3, "hand": "Left", "finger": 2, "face": "back"})

    def test_collect_view(self):
        from pinch_cheonjiin import collect_view
        from pinch_collect import Collector
        collector = Collector(reps=1)
        view = collect_view(collector, (0, "Left", 2, "palm"), None)
        self.assertEqual((view["target"], view["title"]), (("Left", 2), "왼손 약지 · ㅅㅎㅆ"))
        self.assertEqual(collect_view(collector, (0, "Left", 0, "back"), None)["title"], "왼손 손등 검지 · 지움")
        self.assertEqual(collect_view(collector, None, ("collect", 0, "Left", 2, 2, "palm"))["tone"], "ok")
        self.assertEqual(collect_view(collector, None, ("collect", 0, "Left", 2, 0, "palm"))["tone"], "wrong")
        self.assertIsNone(collect_view(collector, None, None))


class HoldTest(unittest.TestCase):
    """누르고 유지(pinch_typer.HOLD_TIME): 손끝에 0.3초 머물러야 입력한다. 스치기만 한 접촉은 입력하지 않는다."""

    def test_graze_is_ignored_and_hold_fires_once(self):
        run = Runner(PinchTyper(hold=0.3)).ready()
        pressed = sig(pinch_ratios_for(0, 0.05))
        run.frames(6, sig(), pressed).frames(3, sig(), sig())  # 0.2초 닿음: 스침
        self.assertEqual(run.kinds("input"), [])
        run.frames(11, sig(), pressed)  # 0.33초 유지: 떼기 전에 입력
        self.assertEqual(run.kinds("input"), [("input", "ㄱ")])
        run.frames(20, sig(), pressed).frames(3, sig(), sig())  # 더 쥐고 있다 떼도 한 번
        self.assertEqual(run.kinds("input"), [("input", "ㄱ")])
        run.frames(11, sig(), pressed).frames(3, sig(), sig())  # 다시 누르고 유지: 순환
        self.assertEqual(run.typer.ime.text, "ㅋ")

    def test_shallow_pinky_hold(self):
        run = Runner(PinchTyper(hold=0.3)).ready()
        run.frames(11, sig(), sig((0.6, 0.6, 0.65, 0.18))).frames(3, sig(), sig())  # 소지는 0.18 정도로 얕게 닿는다
        self.assertEqual(run.typer.ime.text, "ㅇ")

    def test_hold_progress(self):
        pinch = FingerPinch(hold=0.3)
        for k in range(3):
            pinch.update(sig(), k / 30)
        self.assertEqual(pinch.hold_progress(0.1), 0.0)
        pinch.update(sig(pinch_ratios_for(0, 0.05)), 0.1)
        self.assertAlmostEqual(pinch.hold_progress(0.25), 0.5)
        self.assertEqual(pinch.update(sig(pinch_ratios_for(0, 0.05)), 0.41), (0, 0.41 - 0.1))
        self.assertEqual(pinch.hold_progress(0.5), 1.0)
        self.assertEqual(FingerPinch(hold=0).hold_progress(0.5), 0.0)


class VisionSessionTest(unittest.TestCase):
    """Vision Pro 앱이 내장 Python으로 부르는 판정 세션(pinch_vision.Session). 합성 손(common.synthetic)은 왼손 손바닥이다."""

    @staticmethod
    def joints(thumb):
        from common import synthetic
        world = synthetic.world_hand(thumb)
        return {"world": world.tolist(), "image": synthetic.to_image(world).tolist()}

    def run_frames(self, session, frames, start=0.0):
        outputs, now = [], start
        for joints in frames:
            now += 1 / 30
            outputs.append(json.loads(session.step(json.dumps({"t": now, "hands": {"Left": joints}}))))
        return outputs, now

    def pinch_frames(self):
        from common import synthetic
        world = synthetic.world_hand(synthetic.AWAY)
        index_pad = tuple((world[7] + world[8]) / 2 + [0.004, 0, 0])  # 검지 끝마디 가운데 옆
        return [self.joints(synthetic.AWAY)] * 25 + [self.joints(index_pad)] * 5 + [self.joints(__import__("common.synthetic", fromlist=["AWAY"]).AWAY)] * 3

    def test_step_types_from_joints(self):
        from pinch_vision import Session
        outputs, _ = self.run_frames(Session(), self.pinch_frames())
        self.assertEqual([e for out in outputs for e in out["events"]], [["input", "ㅂ"]])
        last = outputs[-1]
        self.assertEqual((last["text"], last["hands"]["Left"]["face"]), ("ㅂ", "palm"))
        self.assertEqual([f["queue"] for f in last["hands"]["Left"]["fingers"]],
                         [["ㅍ", "ㅃ", "ㅂ"], ["모음"], ["ㅅ", "ㅎ", "ㅆ"], ["ㅈ", "ㅊ", "ㅉ"]])
        self.assertTrue(any(out["hands"]["Left"]["fingers"][0]["active"] for out in outputs))

    def test_fist_deletes_with_meter_coordinates(self):
        # 기기처럼 image를 m 단위로 넘겨도 편 손 / 주먹 자세가 판정되고, 손바닥에서 주먹을 쥐면 지운다.
        # (예전에는 자세 판정의 2px 기준에 모든 마디가 걸려 늘 closing이었다.)
        from common import synthetic
        from pinch_vision import Session, signal_of

        def meters(world):
            return {"world": world.tolist(), "image": world.tolist()}

        open_hand = synthetic.world_hand(synthetic.AWAY)
        fist = synthetic.world_hand(synthetic.AWAY)
        synthetic.fold_fist(fist)
        self.assertEqual(signal_of("Left", meters(open_hand)).pose, "open")
        self.assertEqual(signal_of("Left", meters(fist)).pose, "fist")
        session = Session()
        session.typer.ime.press("1", 0.0)  # ㅣ
        outputs, _ = self.run_frames(session, [meters(open_hand)] * 25 + [meters(fist)] * 10 + [meters(open_hand)] * 3)
        self.assertIn(["delete", None], [e for out in outputs for e in out["events"]])
        self.assertEqual(outputs[-1]["text"], "")

    def test_vowel_drag_state(self):
        # 모음 버튼(왼손 중지)을 꼬집은 채 아래로 끌면, 꼬집는 동안 끌기 상태(프레임 수·기준 거리 m·미리 볼 획)가 나오고
        # 떼면 ㅣ가 들어간다. 기기처럼 image는 m 단위다.
        from common import synthetic
        from pinch_vision import Session

        def meters(world, down=0.0):
            moved = world + [0.0, down, 0.0]
            return {"world": moved.tolist(), "image": moved.tolist()}

        open_hand = synthetic.world_hand(synthetic.AWAY)
        middle_pad = tuple((open_hand[11] + open_hand[12]) / 2 + [0.004, 0, 0])
        pinch = synthetic.world_hand(middle_pad)
        frames = ([meters(open_hand)] * 25 + [meters(pinch, 0.006 * k) for k in range(8)]
                  + [meters(open_hand, 0.042)] * 3)
        outputs, _ = self.run_frames(Session(), frames)
        dragging = [out["hands"]["Left"] for out in outputs if out["hands"]["Left"]["dragging"] is not None]
        self.assertTrue(dragging)
        self.assertEqual({hand["dragging"] for hand in dragging}, {1})
        counts = [hand["drag_frames"] for hand in dragging]
        self.assertEqual(counts, sorted(counts))
        self.assertTrue(0.01 < dragging[-1]["drag_radius"] < 0.04, dragging[-1]["drag_radius"])
        self.assertEqual((dragging[0]["drag_preview"], dragging[0]["drag_passed"]), ("ㆍ", False))
        self.assertEqual((dragging[-1]["drag_preview"], dragging[-1]["drag_passed"]), ("ㅣ", True))
        self.assertEqual(outputs[-1]["hands"]["Left"]["dragging"], None)
        self.assertEqual([e for out in outputs for e in out["events"]], [["input", "ㅣ"]])

    def test_demo_and_clear(self):
        from pinch_vision import Session
        session = Session()
        out = json.loads(session.command(json.dumps({"cmd": "demo"})))
        self.assertEqual(out["text"], "안녕하세요")
        self.assertEqual(json.loads(session.command(json.dumps({"cmd": "clear"})))["text"], "")
        settings = json.loads(session.command(json.dumps(
            {"cmd": "settings", "values": {"hold": True, "settle": False, "index_sweep": False, "occlusion": False}})))
        self.assertEqual(settings["settings"], {"hold": True, "settle": False, "index_sweep": False, "occlusion": False})
        self.assertTrue(settings["hold_mode"])
        self.assertEqual((session.typer.settle, session.typer.occluded_roll), (0.0, -90.0))
        back = json.loads(session.command(json.dumps({"cmd": "settings", "values": {"hold": False, "settle": True}})))
        self.assertEqual(back["settings"], {"hold": False, "settle": True, "index_sweep": False, "occlusion": False})
        info = json.loads(session.command(json.dumps({"cmd": "info"})))
        self.assertIn("'안녕하세요'", info["notice"])
        self.assertEqual(info["text"], "")  # 입력 칸은 그대로

    def test_log_replays_with_pinch_eval_fields(self):
        import tempfile
        from pinch_typer import signal_from_log
        from pinch_vision import Session
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            self.assertIn("기록 시작", json.loads(session.command(json.dumps({"cmd": "log_start"})))["notice"])
            self.run_frames(session, self.pinch_frames())
            path = Path(session.log_path)
            session.command(json.dumps({"cmd": "log_stop"}))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), len(self.pinch_frames()))
        self.assertEqual([e for row in rows for e in row["events"]], [["input", "ㅂ"]])
        typer = PinchTyper()  # 기록 행만으로 다시 판정해도 같은 입력
        for row in rows:
            signals = {hand: signal_from_log(v, row["size"]) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in ("Left", "Right")}, row["t"])
        self.assertEqual(typer.ime.text, "ㅂ")


class WordCollectTest(unittest.TestCase):
    """단어 기록(pinch_collect.WordCollector)과 단어 기록 평가(pinch_eval.align)."""

    def test_every_word_has_verified_keys(self):
        from pinch_collect import COMMIT, WORDS, word_keys, word_steps
        for word in WORDS:
            self.assertEqual(replay(word_steps(word)).ime.text, word)
        keys = {key for word in WORDS for key in word_keys(word)}
        palms = {(hand, finger, "palm") for hand in ("Left", "Right") for finger in range(4)}
        self.assertTrue(palms | {COMMIT} <= keys)  # 모든 손바닥 키와 확정이 들어 있다
        drags = {step[4] for word in WORDS for step in word_steps(word)}
        self.assertEqual(drags, {None, "h", "v"})  # 모음 버튼의 세 가지(ㆍ·ㅡ·ㅣ)
        # 학교: ㅎ(왼손 약지 2번) ㅏ(세로·그대로) ㄱ, 같은 손가락 ㄱ이 이어지므로 확정, ㄱ ㅛ
        index = ("Right", 0, "palm")
        self.assertEqual(word_keys("학교")[:2], [("Left", 2, "palm")] * 2)
        self.assertEqual(word_keys("학교")[4:7], [index, COMMIT, index])

    def test_word_flow_and_config(self):
        from pinch_collect import REST, WordCollector
        collector = WordCollector(words=("바다", "꽃"))
        self.assertEqual(collector.prompt(0.0), (0, "바다"))
        self.assertEqual(collector.describe((0, "바다")), {"id": 0, "word": "바다"})
        self.assertEqual(collector.update([], 0.1, "바"), [])
        self.assertEqual(collector.update([], 0.2, "바다"), [("collect", 0, "바다", "done")])
        self.assertIsNone(collector.prompt(0.2 + REST / 2))
        self.assertEqual(collector.skip(0.2 + REST), [("collect", 1, "꽃", "skip")])
        self.assertTrue(collector.done)
        config = collector.config()["collect"]
        self.assertEqual((config["kind"], config["order"]), ("words", ["바다", "꽃"]))
        self.assertEqual(config["keys"]["꽃"][0], ["Right", 0, "palm"])

    def test_align_counts_extra_and_wrong_keys(self):
        from pinch_eval import align
        a, b, c, x = ("Right", 1, "palm"), ("Left", 0, "palm"), ("Right", 3, "palm"), ("Right", 0, "palm")
        ops = align([a, b, c], [a, x, b, ("Right", 2, "palm")])
        self.assertEqual([op[0] for op in ops], ["match", "extra", "match", "sub"])
        self.assertEqual([op[0] for op in align([a, b], [a])], ["match", "miss"])

    def test_word_view(self):
        from pinch_cheonjiin import collect_view
        from pinch_collect import WordCollector
        collector = WordCollector(words=("바다",))
        view = collect_view(collector, collector.prompt(0.0), None)
        self.assertIn("바다", view["title"])
        self.assertEqual(collect_view(collector, None, ("collect", 0, "바다", "done"))["tone"], "ok")


class EditKeyTest(unittest.TestCase):
    """키보드 모드에서 왼손 손등 꼬집기 = 지움 · 공백 · 확정 · 줄바꿈."""

    def tap(self, run, hand, finger, left_facing=0.8):
        pinched = sig(pinch_ratios_for(finger), facing=left_facing if hand == "Left" else 0.8)
        rest = sig(facing=left_facing)
        left, right = (pinched, sig()) if hand == "Left" else (rest, pinched)
        run.frames(4, left, right).frames(3, rest, sig())

    def flip_left(self, run, facing):
        run.frames(20, sig(facing=facing), sig())  # 방향 인정 0.15초 + SETTLE 0.3초 + 쉬는 자세

    def test_back_index_deletes_repeatedly(self):
        run = Runner().ready()
        for hand, finger in (("Right", 0), ("Left", 0), ("Right", 1), ("Left", 2)):  # ㄱ ㅂ ㄴ ㅅ
            self.tap(run, hand, finger)
        before = run.typer.ime.text
        self.flip_left(run, -0.8)
        self.tap(run, "Left", 0, left_facing=-0.8)
        self.tap(run, "Left", 0, left_facing=-0.8)
        self.assertEqual(len(run.kinds("delete")), 2)
        self.assertNotEqual(run.typer.ime.text, before)
        self.flip_left(run, 0.8)
        self.tap(run, "Left", 0)  # 손바닥으로 돌아오면 다시 ㅂ
        self.assertEqual(run.kinds("input")[-1], ("input", "ㅂ"))

    def test_back_space_commit_enter(self):
        run = Runner().ready()
        self.tap(run, "Right", 0)  # ㄱ 후보
        self.flip_left(run, -0.8)
        self.tap(run, "Left", 2, left_facing=-0.8)  # 확정
        self.assertIsNone(run.typer.pending)
        self.assertEqual(run.kinds("commit"), [("commit", None)])
        self.tap(run, "Left", 1, left_facing=-0.8)  # 공백
        self.tap(run, "Left", 3, left_facing=-0.8)  # 문장부호(처음은 .)
        self.assertEqual(run.typer.ime.text, "ㄱ .")

    def test_pinch_while_turning_is_ignored(self):
        run = Runner().ready()
        # 손바닥에서 꼬집기 시작 → 손등이 된 뒤 뗌: 어느 쪽 키도 입력하지 않는다.
        run.frames(2, sig(pinch_ratios_for(0)), sig()).frames(8, sig(pinch_ratios_for(0), facing=-0.8), sig())
        run.frames(3, sig(facing=-0.8), sig())
        self.assertEqual(run.typer.ime.text, "")
        self.assertEqual(run.typer.pinches, [])

    def test_tip_labels_follow_left_hand_face(self):
        from pinch_typer import tip_text
        run = Runner().ready()
        self.assertEqual(tip_text(run.typer, "Left", 0), "ㅂ")
        self.flip_left(run, -0.8)
        self.assertEqual([tip_text(run.typer, "Left", f) for f in range(4)], ["지움", "공백", "확정", "."])
        self.assertEqual(tip_text(run.typer, "Right", 0), "ㄱ")


class CycleQueueTest(unittest.TestCase):
    """손끝 라벨 = 다음에 입력될 글자, 나머지 순환 글자는 손바닥 마디 쪽에서 대기."""

    def test_queue_starts_with_next_input(self):
        from pinch_typer import cycle_queue, tip_text
        typer = PinchTyper()
        self.assertEqual(cycle_queue(typer, "Right", 0), ["ㄱ", "ㅋ", "ㄲ"])
        self.assertEqual(cycle_queue(typer, "Right", 3), ["ㅇ", "ㅁ"])
        typer.press_finger("Right", 0, 0.0)  # ㄱ 입력 → 다음은 ㅋ
        self.assertEqual(cycle_queue(typer, "Right", 0), ["ㅋ", "ㄲ", "ㄱ"])
        self.assertEqual(tip_text(typer, "Right", 0), "ㅋ")
        typer.press_finger("Right", 0, 0.5)  # ㅋ → 다음은 ㄲ
        self.assertEqual(cycle_queue(typer, "Right", 0), ["ㄲ", "ㄱ", "ㅋ"])
        self.assertEqual(cycle_queue(typer, "Right", 1), ["ㄴ", "ㄹ"])  # 다른 손가락은 처음부터
        # 왼손 손바닥: ㅂㅍㅃ · 모음 · ㅅㅎㅆ · ㅈㅊㅉ. 같은 손·손가락일 때만 순환한다(오른손 검지 ≠ 왼손 검지).
        self.assertEqual([cycle_queue(typer, "Left", f) for f in range(4)],
                         [["ㅂ", "ㅍ", "ㅃ"], ["모음"], ["ㅅ", "ㅎ", "ㅆ"], ["ㅈ", "ㅊ", "ㅉ"]])
        typer.press_finger("Left", 2, 1.0)  # ㅅ → 같은 손가락 다시 꼬집으면 ㅎ
        self.assertEqual(cycle_queue(typer, "Left", 2), ["ㅎ", "ㅆ", "ㅅ"])
        self.assertEqual(cycle_queue(typer, "Right", 0), ["ㄱ", "ㅋ", "ㄲ"])
        typer.hands["Right"].orientation.state = "back"  # 오른손 손등: 검지만 확정/공백
        self.assertEqual([cycle_queue(typer, "Right", f) for f in range(4)], [["확정"], [], [], []])
        typer.press_finger("Right", 0, 1.5, "back")  # 확정
        self.assertEqual(cycle_queue(typer, "Right", 0), ["공백"])
        typer.hands["Left"].orientation.state = "back"
        self.assertEqual([tip_text(typer, "Left", f) for f in range(4)], ["지움", "공백", "확정", "."])
        self.assertEqual(cycle_queue(typer, "Left", 3), [".", ",", "?"])  # 문장부호는 앞 3개
        typer.press_finger("Left", 3, 2.0, "back")
        self.assertEqual(cycle_queue(typer, "Left", 3), [",", "?", "!"])

    def test_path_runs_from_label_to_knuckle(self):
        from pinch_cheonjiin import finger_path_point
        points = [(0.0, 0.0)] * 21
        for i, y in zip((5, 6, 7, 8), (100.0, 70.0, 50.0, 30.0)):  # 검지 MCP~TIP이 위로 곧게
            points[i] = (10.0, y)
        start = (10.0, 10.0)
        self.assertEqual(finger_path_point(points, 0, start, 0.0), start)
        self.assertEqual(finger_path_point(points, 0, start, 1.0), (10.0, 100.0))
        self.assertEqual(finger_path_point(points, 0, start, 0.5), (10.0, 55.0))

    def test_motion_only_when_queue_turns(self):
        from pinch_cheonjiin import CYCLE_ANIM, cycle_motion
        cycles = {}
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㄱ", "ㅋ", "ㄲ"), 0.0), (1.0, None))
        eased, previous = cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0)  # 한 칸 돎
        self.assertEqual((eased, previous), (0.0, ("ㄱ", "ㅋ", "ㄲ")))
        self.assertGreater(cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0 + CYCLE_ANIM / 2)[0], 0.5)
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㅋ", "ㄲ", "ㄱ"), 1.0 + CYCLE_ANIM), (1.0, None))
        # 확정 등으로 처음 순서로 돌아가면 애니메이션 없이 바로 바뀐다.
        self.assertEqual(cycle_motion(cycles, ("Right", 0), ("ㄱ", "ㅋ", "ㄲ"), 2.0), (1.0, None))


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
        from common.hands import assign_hands

        def hand(x):
            return [SimpleNamespace(x=x, y=0.5, z=0.0)] * 21
        # 라벨이 서로 다르면 화면 위치와 상관없이 라벨을 따른다(카메라가 손 맞은편에 있는 배치).
        hands = assign_hands([("Left", hand(0.8), "w-left", 0.9), ("Right", hand(0.2), "w-right", 0.9)])
        self.assertEqual(hands["Left"][2], "w-left")
        self.assertEqual(hands["Right"][2], "w-right")
        # 두 손 모두 Right로 불리면 화면 왼쪽 손을 왼손으로 정하고, 방향 계산용 원래 라벨은 남긴다.
        hands = assign_hands([("Right", hand(0.7), "w-right", 0.9), ("Right", hand(0.2), "w-left", 0.6)])
        self.assertEqual((hands["Left"][2], hands["Left"][0]), ("w-left", "Right"))
        self.assertEqual((hands["Right"][2], hands["Right"][0]), ("w-right", "Right"))
        self.assertEqual(list(assign_hands([("Left", hand(0.8), "w", 0.9)])), ["Left"])

    def test_facing_follows_mediapipe_label_not_position(self):
        """카메라가 손 맞은편에 있어 오른손이 화면 왼쪽에 보여도 손바닥이면 손바닥이다."""
        from common.hands import Detection, assign_hands
        from pinch_cheonjiin import build_signals

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
        hands = assign_hands([Detection("Right", right_img, right_world, 0.9), Detection("Left", left_img, left_world, 0.9)])
        signals = build_signals(hands, 640, 480, mirrored=False)
        self.assertGreater(signals["Right"].facing, 0.25)
        self.assertGreater(signals["Left"].facing, 0.25)

    def test_log_row_keeps_landmarks_and_replays(self):
        """--log 한 줄에 landmark가 들어가고, 재생에 쓰는 필드로 같은 신호를 다시 만들 수 있다."""
        from common.hands import Detection, assign_hands
        from common.synthetic import detection
        from pinch_cheonjiin import build_signals, log_row

        image, world = detection()
        hands = assign_hands([Detection("Left", image, world, 0.9)])
        signals = build_signals(hands, 640, 480, mirrored=False)
        row = json.loads(json.dumps(log_row(1.0, PinchTyper(), hands, signals, [], 640, 480), ensure_ascii=False))
        hand = row["hands"]["Left"]
        self.assertEqual(row["size"], [640, 480])
        self.assertEqual((len(hand["image"]), len(hand["world"])), (21, 21))
        self.assertAlmostEqual(hand["world"][8][1], world[8].y, places=5)
        self.assertEqual(tuple(hand["ratios"]), tuple(round(v, 4) for v in signals["Left"].ratios))
        self.assertEqual(hand["roll"], round(signals["Left"].roll, 1))
        self.assertAlmostEqual(signal_from_log(hand, row["size"]).roll, signals["Left"].roll, delta=0.1)
        self.assertNotIn("frame", row)
        self.assertEqual(log_row(1.0, PinchTyper(), hands, signals, [], 640, 480, frame=12)["frame"], 12)

    def test_open_video_writes_frames_next_to_log(self):
        """--save-video: 로그와 같은 이름의 영상에 프레임을 쓰고, 프레임 번호로 다시 읽을 수 있다."""
        import tempfile
        import cv2
        import numpy as np
        from pinch_cheonjiin import open_video

        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "collect-test.jsonl"
            writer, path = open_video(cv2, log, 30.0, (64, 48))
            for value in (0, 120, 240):
                writer.write(np.full((48, 64, 3), value, np.uint8))
            writer.release()
            self.assertEqual(path.stem, log.stem)
            capture = cv2.VideoCapture(str(path))
            self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 3)
            capture.set(cv2.CAP_PROP_POS_FRAMES, 1)
            ok, frame = capture.read()
            capture.release()
            self.assertTrue(ok)
            self.assertAlmostEqual(float(frame.mean()), 120, delta=12)  # 손실 압축


class CameraSizeTests(unittest.TestCase):
    def test_resolution_argument(self):
        import argparse
        from common.camera import parse_resolution

        self.assertEqual(parse_resolution("auto"), "auto")
        self.assertEqual(parse_resolution("1280x720"), (1280, 720))
        self.assertEqual(parse_resolution("1920X1080"), (1920, 1080))
        for bad in ("720p", "100x50", "x"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_resolution(bad)

    def test_auto_keeps_jetson_at_640x480(self):
        from common.camera import capture_size

        self.assertEqual(capture_size("auto", "linux"), (640, 480))
        self.assertEqual(capture_size("auto", "darwin"), (1280, 720))
        self.assertEqual(capture_size((1920, 1080), "linux"), (1920, 1080))

    def test_detection_frame_is_capped_and_keeps_aspect(self):
        import numpy as np
        from common.hands import detection_frame

        small = np.zeros((480, 640, 3), np.uint8)
        self.assertIs(detection_frame(small, 640), small)
        self.assertEqual(detection_frame(np.zeros((720, 1280, 3), np.uint8), 640).shape, (360, 640, 3))
        self.assertEqual(detection_frame(np.zeros((1080, 1920, 3), np.uint8), 640).shape, (360, 640, 3))

    def test_window_size(self):
        from common.camera import window_size

        self.assertEqual(window_size(640, 480), (960, 720))
        self.assertEqual(window_size(1280, 720), (1280, 720))
        self.assertEqual(window_size(1920, 1080), (1440, 810))
        self.assertEqual(window_size(1920, 1440), (1200, 900))


if __name__ == "__main__":
    unittest.main()
