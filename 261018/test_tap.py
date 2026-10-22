"""python -m unittest discover -s 261018 -p 'test_*.py' -v"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from tap_detect import SEGMENTS, SEGMENT_NAMES, HandFrame, Point2D, Seg, SegDip, segment_distances
from tap_eval import Score, fist_events, load_session, replay, score_trials, split
from tap_record import Recorder, make_queue, own_slot


WIDTH, HEIGHT = 640, 480
FINGER_X = {5: -0.03, 9: -0.01, 13: 0.01, 17: 0.03}
LENGTHS = (0.045, 0.028, 0.022)  # 뿌리, 중간, 끝 마디 (m)


def world_hand(thumb):
    """손바닥이 카메라를 향한 편 손. 손가락은 -y 방향, 손바닥 면은 z=0."""
    points = np.zeros((21, 3))
    points[0] = (0.0, 0.09, 0.0)
    points[1:4] = [(-0.04, 0.07, 0.0), (-0.055, 0.05, 0.0), (-0.06, 0.03, 0.0)]
    for base, x in FINGER_X.items():
        y = 0.0
        points[base] = (x, y, 0.0)
        for step, length in enumerate(LENGTHS, start=1):
            y -= length
            points[base + step] = (x, y, 0.0)
    points[4] = thumb
    return points


def frame(thumb):
    world = world_hand(np.asarray(thumb, float))
    # 픽셀 비율이 같도록 x를 HEIGHT/WIDTH로 줄여 정규화 좌표를 만든다. 깊이(z)는 화면에 보이지 않는다.
    image = np.column_stack([0.5 + world[:, 0] * 4 * HEIGHT / WIDTH, 0.5 + world[:, 1] * 4, world[:, 2] * 4])
    return HandFrame(image, world, WIDTH, HEIGHT)


def middle(name, lift=0.0):
    a, b = SEGMENTS[name]
    points = world_hand(np.zeros(3))
    return (points[a] + points[b]) / 2 + (0.0, 0.0, lift)


AWAY = (-0.08, 0.08, 0.05)


def run(detector, thumbs, step=1 / 30):
    detector.reset()
    events = []
    for index, thumb in enumerate(thumbs):
        key = detector.update(index * step, None if thumb is None else frame(thumb))
        if key is not None:
            events.append(key)
    return events


def tap(name, frames_down=4, lift=0.0):
    """목표 마디 옆 9mm, 위 4cm에서 내려와 닿은 뒤 같은 길로 떨어지는 궤적.

    화면상으로 다른 마디를 가로지르지 않게 해서 상태 머신만 검증한다. 가로지를 때의
    오입력은 기록 데이터 평가에서 측정한다.
    """
    target = middle(name, lift)
    start = target + (0.009, 0.0, 0.04)
    approach = [start + (target - start) * k / 6 for k in range(7)]
    return approach + [target] * frames_down + approach[::-1]


class GeometryTests(unittest.TestCase):
    def test_segment_indices_follow_finger_rows(self):
        self.assertEqual(len(SEGMENT_NAMES), 12)
        self.assertEqual(SEGMENTS["I1"], (7, 8))
        self.assertEqual(SEGMENTS["I3"], (5, 6))
        self.assertEqual(SEGMENTS["P2"], (18, 19))

    def test_thumb_on_each_segment_is_nearest(self):
        for name in SEGMENT_NAMES:
            distance = segment_distances(world_hand(middle(name)))
            self.assertEqual(SEGMENT_NAMES[int(distance.argmin())], name)
            self.assertAlmostEqual(float(distance.min()), 0.0)

    def test_image_distance_ignores_hand_size_and_resolution(self):
        small = frame(middle("R2", 0.0)).pixels()
        big = HandFrame(frame(middle("R2")).image, None, WIDTH * 2, HEIGHT * 2).pixels()
        np.testing.assert_allclose(small, big)


class DetectorTests(unittest.TestCase):
    def test_each_detector_types_each_segment_once(self):
        for detector in (Seg("world"), Seg("image", enter=0.08, exit=0.14), SegDip("world"),
                         SegDip("image", enter=0.12, depth=0.06, rise=0.03), Point2D(dwell=0.06)):
            for name in SEGMENT_NAMES:
                with self.subTest(detector=detector.name, key=name):
                    self.assertEqual(run(detector, [AWAY] * 5 + tap(name)), [name])

    def test_world_distance_rejects_hover_but_image_distance_cannot(self):
        # 엄지를 3cm 띄운 상태는 화면상 위치가 누르기와 같다. 단안 2D 거리로는 구분할 수 없다.
        hover = [AWAY] * 5 + tap("M1", lift=0.03)
        self.assertEqual(run(Seg("world"), hover), [])
        self.assertEqual(run(SegDip("world"), hover), [])
        self.assertEqual(run(Seg("image", enter=0.08, exit=0.14), hover), ["M1"])

    def test_starting_in_contact_needs_release(self):
        held = [middle("I2")] * 10
        self.assertEqual(run(Seg("world"), held), [])
        self.assertEqual(run(Seg("world"), held + [AWAY] * 3 + tap("I2")), ["I2"])

    def test_long_contact_types_once(self):
        self.assertEqual(run(Seg("world"), [AWAY] * 3 + tap("P3", frames_down=40)), ["P3"])
        self.assertEqual(run(SegDip("world"), [AWAY] * 3 + tap("P3", frames_down=40)), ["P3"])

    def test_still_thumb_close_to_finger_does_not_dip(self):
        resting = [middle("R1", 0.005)] * 60
        self.assertEqual(run(SegDip("world"), resting), [])

    def test_lost_hand_resets_state(self):
        broken = [AWAY] * 3 + tap("M2")[:7] + [None] + [middle("M2")] * 5
        self.assertEqual(run(Seg("world"), broken), [])

    def test_replay_matches_update(self):
        thumbs = [AWAY] * 5 + tap("I3") + [None] + [AWAY] * 3 + tap("R2")
        detector = SegDip("world")
        features = [None if t is None else detector.features(frame(t)) for t in thumbs]
        events = replay(detector, [i / 30 for i in range(len(thumbs))], features)
        self.assertEqual([key for _, key in events], run(SegDip("world"), thumbs))


class ScoreTests(unittest.TestCase):
    def test_trial_scoring(self):
        trials = [
            {"kind": "tap", "target": "I1", "start": 0.0, "end": 1.0},   # 맞게 1번
            {"kind": "tap", "target": "I2", "start": 2.0, "end": 3.0},   # 다른 키 + 중복 1번
            {"kind": "tap", "target": "M1", "start": 4.0, "end": 5.0},   # 놓침
            {"kind": "nearmiss", "target": "M1", "start": 6.0, "end": 7.0},  # 오검출
            {"kind": "idle", "target": None, "start": 8.0, "end": 9.0},  # 정상
            {"kind": "tap", "target": "P1", "start": 10.0, "end": 11.0, "valid": False},
        ]
        events = [(0.5, "I1"), (2.4, "I3"), (2.8, "I2"), (6.5, "M1"), (10.5, "P1")]
        score = score_trials(trials, events)
        self.assertEqual((score.taps, score.hits, score.correct, score.exact), (3, 2, 1, 1))
        self.assertEqual(score.false_positives, 2)
        self.assertAlmostEqual(score.precision, 2 / 4)
        self.assertAlmostEqual(score.recall, 2 / 3)
        self.assertAlmostEqual(score.key_accuracy, 1 / 2)
        self.assertAlmostEqual(score.typing_accuracy, 1 / 5)
        self.assertEqual(score.confusion[("I2", "I3")], 1)
        self.assertEqual(score.confusion[("M1", "none")], 1)
        self.assertEqual(score.confusion[("none", "M1")], 1)
        self.assertEqual(score.confusion[("none", "none")], 1)

    def test_window_margins(self):
        trials = [{"kind": "tap", "target": "I1", "start": 1.0, "end": 2.0}]
        self.assertEqual(score_trials(trials, [(0.85, "I1")]).hits, 1)
        self.assertEqual(score_trials(trials, [(0.75, "I1")]).hits, 0)
        self.assertEqual(score_trials(trials, [(2.25, "I1")]).hits, 1)

    def test_fist_events_need_reopen(self):
        times = [i * 0.05 for i in range(30)]
        poses = ["open"] * 3 + ["fist"] * 10 + ["closing"] * 2 + ["fist"] * 5 + ["open"] * 2 + ["fist"] * 8
        self.assertEqual(len(fist_events(times, poses, hold=0.18)), 2)

    def test_fist_counts_per_trial(self):
        trials = [{"kind": "fist", "target": None, "start": 0.0, "end": 1.0},
                  {"kind": "tap", "target": "I1", "start": 2.0, "end": 3.0}]
        score = score_trials(trials, [], fists=[0.5, 2.5])
        self.assertEqual((score.fist_trials, score.fist_hits, score.fist_false), (1, 1, 1))

    def test_score_add(self):
        total = Score().add(Score(taps=2, hits=1)).add(Score(taps=1, hits=1))
        self.assertEqual((total.taps, total.hits), (3, 2))


def landmark_list(points):
    return [SimpleNamespace(x=x, y=y, z=z) for x, y, z in points]


class SessionTests(unittest.TestCase):
    def write_session(self, root, name, hand="Right", placement="front"):
        path = Path(root) / name
        path.mkdir()
        recorder = Recorder()
        hand_frame = frame(AWAY)
        own = (hand, landmark_list(hand_frame.image), landmark_list(hand_frame.world), 0.9)
        other = ("Left" if hand == "Right" else "Right", own[1], own[2], 0.8)
        recorder.add(0.0, [own])
        recorder.add(0.033, [other, own])
        recorder.add(0.066, [])
        recorder.add(0.1, [own, own])  # 같은 라벨 두 개는 쓰지 않는다.
        recorder.add(0.133, [other])  # 손이 하나면 라벨이 뒤집혀도 쓴다.
        recorder.save(path / "frames.npz")
        (path / "trials.json").write_text(json.dumps({"trials": []}))
        (path / "meta.json").write_text(json.dumps({"hand": hand, "placement": placement,
                                                    "width": WIDTH, "height": HEIGHT}))
        return path

    def test_round_trip_picks_session_hand(self):
        with tempfile.TemporaryDirectory() as root:
            session = load_session(self.write_session(root, "a"))
            self.assertEqual([hand is not None for hand in session.hands], [True, True, False, False, True])
            self.assertAlmostEqual(session.flipped, 1 / 2)
            np.testing.assert_allclose(session.hands[1].world, frame(AWAY).world, atol=1e-6)
            self.assertEqual(session.poses[0], "open")

    def test_split_holds_out_latest_session_per_group(self):
        sessions = [SimpleNamespace(path=Path(name), group=group) for name, group in
                    [("1", "rf"), ("2", "rf"), ("3", "lo")]]
        train, test, shared = split(sessions)
        self.assertEqual([s.path.name for s in train], ["1", "3"])
        self.assertEqual([s.path.name for s in test], ["2", "3"])
        self.assertEqual(shared, ["3"])


class QueueTests(unittest.TestCase):
    def test_queue_counts_and_seed(self):
        queue = make_queue(5, 2, 1, 5, 3, seed=7)
        kinds = [trial["kind"] for trial in queue]
        self.assertEqual([kinds.count(kind) for kind in ("tap", "nearmiss", "hover", "fist", "idle")],
                         [60, 24, 12, 5, 3])
        self.assertEqual(queue, make_queue(5, 2, 1, 5, 3, seed=7))

    def test_own_slot(self):
        self.assertEqual(own_slot(["Left", ""], "Right"), 0)  # 혼자 있으면 라벨이 달라도 사용
        self.assertEqual(own_slot(["Left", "Right"], "Right"), 1)
        self.assertIsNone(own_slot(["Right", "Right"], "Right"))
        self.assertIsNone(own_slot(["", ""], "Right"))


if __name__ == "__main__":
    unittest.main()
