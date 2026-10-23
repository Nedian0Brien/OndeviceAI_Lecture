"""양손 꼬집기 → 한글 입력(typer.PinchTyper)과 손끝 대기열(layout.cycle_queue)."""

import json
from pathlib import Path
import unittest

from common.gestures import FistHold
from layout import cycle_queue
from tests.helpers import BACK, OPEN, Runner, pinched, sig
from typer import FIST_REARM, FIST_REPEAT, PinchTyper, replay

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pinch-typer-keys.json"
FIST = sig((0.3, 0.32, 0.4, 0.45), pose="fist", curled=(True,) * 4)


class FixtureTest(unittest.TestCase):
    def test_key_fixtures(self):
        data = json.loads(FIXTURES.read_text(encoding="utf-8"))
        for case in data["cases"]:
            with self.subTest(case["name"]):
                self.assertEqual(replay(case["steps"]).ime.text, case["text"])

    def test_fist_after_cycle_deletes_whole_letter(self):
        typer = replay([["tap", "Right", 0], ["tap", "Left", 0, "back", "v"], ["tap", "Left", 0, "back"],
                        ["tap", "Right", 2], ["tap", "Right", 2], ["fist", "Right"]])
        self.assertEqual((typer.ime.text, typer.pending), ("가", None))


class FrameTest(unittest.TestCase):
    """프레임 신호로 입력하는 규칙."""

    def test_typing_from_frames(self):
        run = Runner().ready()
        run.tap("Right", 0).vowel("v").vowel()  # ㄱ ㅣ ㆍ
        self.assertEqual(run.typer.ime.text, "가")
        self.assertEqual(run.typer.pinches, [])  # 이번 프레임에 인정된 꼬집기만 남는다

    def test_vowel_drag_direction(self):
        for drag, expected in ((None, "ㆍ"), ("h", "ㅡ"), ("v", "ㅣ")):
            run = Runner().ready().vowel(drag)
            self.assertEqual(run.kinds("input"), [("input", expected)], drag)

    def test_drag_view_while_pinching_vowel(self):
        run = Runner().ready(left_facing=BACK)
        self.assertIsNone(run.typer.drag_view("Left"))
        for k in range(6):
            run.frames(1, sig(pinched(0), facing=BACK, tips=((50.0, 50.0 + 8 * k),) * 5, size=100.0), sig())
        view = run.typer.drag_view("Left")
        self.assertEqual((view["finger"], len(view["points"]), view["drag"], view["pointer"]), (0, 6, "v", 180))
        run.frames(3, sig(facing=BACK), sig())
        self.assertIsNone(run.typer.drag_view("Left"))
        self.assertEqual(run.kinds("input"), [("input", "ㅣ")])

    def test_release_motion_is_not_drag(self):
        # 떼는 동안(마지막 RELEASE_FRAMES 프레임)만 크게 움직이면 끌기로 세지 않는다(ㆍ).
        run = Runner().ready(left_facing=BACK)
        still = sig(pinched(0), facing=BACK, tips=((50.0, 50.0),) * 5, size=100.0)
        opening = sig(facing=BACK, tips=((50.0, 110.0),) * 5, size=100.0)
        run.frames(5, still, sig()).frames(2, opening, sig()).frames(3, sig(facing=BACK), sig())
        self.assertEqual(run.kinds("input"), [("input", "ㆍ")])

    def test_back_pinch_with_other_fingers_curled(self):
        # 손등 모음 꼬집기는 중지·약지·소지가 굽어도 입력한다. 손바닥에서는 주먹 쥐는 동작으로 보고 버린다.
        curled = (False, True, True, True)
        for facing, expected in ((BACK, [("input", "ㆍ")]), (0.8, [])):
            run = Runner().ready(left_facing=facing)
            run.frames(6, sig(pinched(0), facing=facing, curled=curled), sig()).frames(3, sig(facing=facing), sig())
            self.assertEqual(run.kinds("input"), expected, facing)

    def test_vowel_right_after_flip_to_back(self):
        # 손바닥에서 손등으로 뒤집고 손 방향이 인정되기 전(FACING_DWELL)에 꼬집어도 손등 모음으로 들어간다.
        # 2026-10-24 Vision Pro 기록: 뒤집고 0.14초 뒤 시작한 ㅣ 3개가 손바닥 꼬집기로 기록돼 모두 버려졌다.
        run = Runner().ready()
        run.frames(3, sig(facing=BACK), sig())  # 뒤집는 중: 아직 손바닥으로 인정된 상태
        self.assertEqual(run.typer.face("Left"), "palm")
        for k in range(6):
            run.frames(1, sig(pinched(0), facing=BACK, curled=(False, True, True, True),
                              tips=((50.0, 50.0 + 8 * k),) * 5, size=100.0), sig())
        run.frames(3, sig(facing=BACK), sig())
        self.assertEqual(run.kinds("input"), [("input", "ㅣ")])

    def test_back_pinch_keeps_through_one_frame_palm_jitter(self):
        # 손등 모음 접촉 중 손 방향 값이 한 프레임 손바닥 쪽으로 튀어도, 다른 손가락이 굽었다고 버리지 않는다.
        curled = (False, True, True, True)
        run = Runner().ready(left_facing=BACK)
        run.frames(3, sig(pinched(0), facing=BACK, curled=curled), sig())
        run.frames(1, sig(pinched(0), facing=0.6, curled=curled), sig())
        run.frames(3, sig(pinched(0), facing=BACK, curled=curled), sig()).frames(3, sig(facing=BACK), sig())
        self.assertEqual(run.kinds("input"), [("input", "ㆍ")])

    def test_turning_and_flipping_hand_is_ignored(self):
        run = Runner().ready()
        run.frames(4, sig(pinched(0), facing=0.1), sig()).frames(3, sig(facing=0.1), sig())  # 회전 중
        self.assertEqual(run.typer.ime.text, "")
        run = Runner().ready()
        run.frames(5, sig(), sig(pinched(0))).frames(2, sig(), sig(facing=-0.6)).frames(8, sig(), sig())  # 뒤집으며 뗌
        self.assertEqual(run.typer.ime.text, "")

    def test_one_frame_facing_jitter_keeps_tap(self):
        run = Runner().ready()
        run.frames(3, sig(), sig(pinched(0))).frames(1, sig(), sig(pinched(0), facing=-0.4))
        run.frames(3, sig(), sig(pinched(0))).frames(3, sig(), sig())
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_thumb_hidden_by_roll_is_ignored(self):
        # 소지 쪽으로 OCCLUDED_ROLL(-25°)보다 기울면 엄지가 가려진다: 손바닥 꼬집기를 버리고 안내한다.
        run = Runner().ready()
        run.frames(3, sig(), sig(roll=-35.0))
        self.assertTrue(run.typer.occluded("Right"))
        run.frames(5, sig(), sig(pinched(0), roll=-35.0)).frames(3, sig(), sig(roll=-35.0))
        self.assertEqual(run.typer.ime.text, "")
        run.frames(3, sig(), sig(roll=-10.0)).frames(5, sig(), sig(pinched(0), roll=-10.0))
        run.frames(3, sig(), sig(roll=-10.0))
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_index_overlap_goes_to_bent_finger(self):
        # 엄지 옆면이 검지를 눌러 검지·중지 값이 함께 내려왔다. 중지만 굽었으면 중지(ㄴ), 아니면 검지(ㄱ).
        rest = (20.0,) * 4
        for flex, expected in (((22.0, 60.0, 30.0, 25.0), "ㄴ"), ((30.0, 25.0, 22.0, 21.0), "ㄱ"), (None, "ㄱ")):
            run = Runner().ready()
            run.frames(10, sig(), sig(flex=rest if flex else None))
            run.frames(2, sig(), sig((0.08, 0.30, 0.55, 0.7), flex=rest if flex else None))
            run.frames(3, sig(), sig((0.10, 0.11, 0.5, 0.7), flex=flex)).frames(3, sig(), sig(flex=flex))
            self.assertEqual(run.typer.ime.text, expected, flex)

    def test_right_index_sweep_is_ignored(self):
        # 오른손 중지(ㄴ)를 뗀 직후 엄지가 돌아가며 검지를 지나간 접촉은 입력하지 않는다. 왼손은 그대로 입력한다.
        rest, bent = (20.0,) * 4, (35.0, 20.0, 20.0, 20.0)

        def frames(run, hand, count, ratios, flex):
            signal = sig(ratios, flex=flex)
            run.frames(count, signal if hand == "Left" else sig(), signal if hand == "Right" else sig())

        for hand, expected in (("Right", "ㄴ"), ("Left", "ㄹㅂ")):
            run = Runner().ready()
            frames(run, hand, 10, OPEN, rest)
            frames(run, hand, 4, (0.6, 0.05, 0.6, 0.7), rest)  # 중지
            frames(run, hand, 2, (0.6, 0.30, 0.6, 0.7), rest)
            frames(run, hand, 1, OPEN, rest)
            frames(run, hand, 4, (0.05, 0.4, 0.6, 0.7), bent)  # 곧바로 검지
            frames(run, hand, 3, OPEN, rest)
            self.assertEqual(run.typer.ime.text, expected, hand)

    def test_shallow_repinch_counts_twice(self):
        # 눌린 채 0.14만 벌렸다가 다시 꼬집어도 두 번 입력되어 ㄱ → ㅋ으로 순환한다.
        run = Runner().ready()
        pressed, half = sig(pinched(0, 0.05)), sig(pinched(0, 0.19))
        run.frames(3, sig(), pressed).frames(3, sig(), half).frames(3, sig(), pressed).frames(3, sig(), sig())
        self.assertEqual(run.kinds("input"), [("input", "ㄱ"), ("input", "ㅋ")])

    def test_short_contact_is_ignored(self):
        run = Runner().ready()
        run.tap("Right", 0, frames=1)  # 0.067초: 스침
        self.assertEqual(run.typer.ime.text, "")
        run.tap("Right", 0, frames=3)  # 0.133초
        self.assertEqual(run.typer.ime.text, "ㄱ")

    def test_echo_shows_last_input(self):
        typer = PinchTyper()
        typer.press_finger("Right", 0, 0.0)
        self.assertEqual(typer.echo, (1, "Right", "ㄱ"))
        typer.press_finger("Right", 0, 0.3)
        self.assertEqual(typer.echo, (2, "Right", "ㅋ"))
        typer.press_finger("Right", 0, 0.6, "back")  # 후보 있음: 확정
        self.assertEqual(typer.echo, (3, "Right", "확정"))
        typer.press_finger("Right", 0, 0.9, "back")  # 후보 없음: 공백
        self.assertEqual(typer.echo, (4, "Right", "공백"))


class EditKeyTest(unittest.TestCase):
    """손등 키와 주먹 지움."""

    def test_left_back_keys(self):
        run = Runner().ready().tap("Right", 0)  # ㄱ 후보
        run.frames(20, sig(facing=BACK), sig())
        for finger in (2, 1, 3):  # 확정 · 공백 · 문장부호(처음은 .)
            run.frames(4, sig(pinched(finger), facing=BACK), sig()).frames(3, sig(facing=BACK), sig())
        self.assertEqual((run.typer.ime.text, run.kinds("commit")), ("ㄱ .", [("commit", None)]))

    def test_right_back_index_is_commit_space(self):
        # 오른손 손등 검지 = 확정/공백. 손등 중지·약지는 손가락이 가려 무시한다.
        run = Runner().ready().tap("Right", 2)  # ㄷ 후보
        back = sig(facing=BACK)
        run.frames(20, sig(), back)
        for finger in (1, 2, 0, 0):
            run.frames(4, sig(), sig(pinched(finger), facing=BACK)).frames(3, sig(), back)
        self.assertEqual(run.typer.ime.text, "ㄷ ")

    def test_both_fists_delete_last_jamo(self):
        run = Runner().ready().tap("Right", 3).tap("Right", 3).vowel("v")  # 미
        run.frames(8, FIST, sig()).frames(3, sig(), sig())
        self.assertEqual(run.typer.ime.text, "ㅁ")
        run.frames(8, sig(), FIST).frames(3, sig(), sig())
        self.assertEqual((run.typer.ime.text, len(run.kinds("delete"))), ("", 2))

    def test_fist_held_keeps_deleting(self):
        run = Runner().ready()
        for consonant in (0, 1):  # 가나
            run.tap("Right", consonant).vowel("v").vowel()
        run.frames(round((0.18 + 2 * FIST_REPEAT + 0.25) * 30), sig(), FIST).frames(3, sig(), sig())
        self.assertEqual(run.typer.ime.text, "ㄱ")  # 가나 → 간 → 가 → ㄱ

    def test_fist_rearms_without_full_open(self):
        # 주먹을 연달아 쥘 때 손을 반쯤만 펴도 주먹이 아닌 상태가 FIST_REARM초 이어지면 다음 주먹으로 다시 지운다.
        for rearm, expected in ((FIST_REARM, 2), (None, 1)):
            fist, now, fired = FistHold(repeat=None, rearm=rearm), 0.0, 0
            frames = ([("open", True)] * 5 + [("fist", False)] * 10 + [("closing", True)] * 2 + [("fist", False)] * 3
                      + [("closing", True)] * 10 + [("fist", False)] * 10)
            for pose, relaxed in frames:
                now += 1 / 45
                fired += fist.update(pose, now, relaxed)
            self.assertEqual(fired, expected, rearm)


class CycleQueueTest(unittest.TestCase):
    def test_queue_starts_with_next_input(self):
        typer = PinchTyper()
        self.assertEqual(cycle_queue(typer, "Right", 0), ["ㄱ", "ㅋ", "ㄲ"])
        typer.press_finger("Right", 0, 0.0)  # ㄱ 입력 → 다음은 ㅋ
        self.assertEqual(cycle_queue(typer, "Right", 0), ["ㅋ", "ㄲ", "ㄱ"])
        self.assertEqual(cycle_queue(typer, "Right", 1), ["ㄴ"])  # ㄴ은 순환 없음
        self.assertEqual(cycle_queue(typer, "Left", 0), ["ㅂ", "ㅍ", "ㅃ"])  # 다른 손은 처음부터
        typer.hands["Right"].orientation.state = "back"
        self.assertEqual([cycle_queue(typer, "Right", f) for f in range(4)], [["확정"], [], [], []])
        typer.hands["Left"].orientation.state = "back"
        self.assertEqual([cycle_queue(typer, "Left", f) for f in range(4)], [["모음"], ["공백"], ["확정"], [".", ",", "?"]])


if __name__ == "__main__":
    unittest.main()
