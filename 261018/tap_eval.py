"""기록한 세션으로 누르기 검출기를 비교한다.

예: python 261018/tap_eval.py 261018/data/tap/* --detector all
세션은 (손, 카메라 위치) 묶음마다 가장 늦은 세션을 평가용으로 떼어 두고 나머지로 임계값을 고른다.
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from itertools import product
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from cheonjiin import FistHold
from project01 import classify_hand_pose
from tap_detect import SEGMENT_NAMES, HandFrame, build
from tap_record import own_slot


PRE, POST = 0.2, 0.3  # 안내보다 조금 일찍 누르거나 늦게 떼는 것을 허용한다.
NEGATIVE_KINDS = ("nearmiss", "hover", "idle", "fist")
NONE = "none"
GRIDS = {
    "point2d": {"ratio": [0.10, 0.13, 0.16, 0.20, 0.25], "dwell": [0.03, 0.06, 0.09, 0.12, 0.18]},
    "seg/world": {"enter": [0.006, 0.009, 0.012, 0.015, 0.018, 0.022], "gap": [0.004, 0.008, 0.012],
                  "dwell": [0.0, 0.03, 0.06, 0.10]},
    "seg/image": {"enter": [0.04, 0.06, 0.08, 0.10, 0.13, 0.16], "gap": [0.03, 0.06, 0.09],
                  "dwell": [0.0, 0.03, 0.06, 0.10]},
    "segdip/world": {"enter": [0.010, 0.015, 0.020, 0.025, 0.030], "depth": [0.004, 0.008, 0.012, 0.016],
                     "rise": [0.002, 0.004, 0.006], "window": [0.4, 0.7, 1.0]},
    "segdip/image": {"enter": [0.06, 0.09, 0.12, 0.16, 0.20], "depth": [0.03, 0.06, 0.09, 0.12],
                     "rise": [0.015, 0.03, 0.05], "window": [0.4, 0.7, 1.0]},
}


@dataclass
class Session:
    path: Path
    meta: dict
    trials: list
    times: np.ndarray
    hands: list  # 프레임별 HandFrame 또는 None
    poses: list = field(default_factory=list)  # 프레임별 classify_hand_pose 결과
    flipped: float = 0.0  # 손이 하나인 프레임 중 좌우 라벨이 세션 손과 다른 비율

    @property
    def group(self):
        return self.meta["hand"], self.meta["placement"]


def pick_hand(labels, image, world, hand, width, height):
    """기록 도구와 같은 규칙(own_slot)으로 세션의 손을 고른다."""
    slot = own_slot(list(labels), hand)
    return None if slot is None else HandFrame(image[slot], world[slot], width, height)


def load_session(path):
    path = Path(path)
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    trials = json.loads((path / "trials.json").read_text(encoding="utf-8"))["trials"]
    data = np.load(path / "frames.npz")
    width, height = meta["width"], meta["height"]
    hands = [pick_hand(labels, image, world, meta["hand"], width, height)
             for labels, image, world in zip(data["labels"], data["image"], data["world"])]
    poses = [None if hand is None else
             classify_hand_pose([SimpleNamespace(x=x, y=y, z=z) for x, y, z in hand.image], width, height)
             for hand in hands]
    single = [labels for labels in data["labels"] if sum(bool(label) for label in labels) == 1]
    flipped = sum(meta["hand"] not in labels for labels in single) / max(1, len(single))
    return Session(path, meta, trials, data["t"], hands, poses, flipped)


def replay(detector, times, features):
    """features는 프레임별 특징(손이 없으면 None). 이벤트 (t, 마디) 목록을 돌려준다."""
    detector.reset()
    events = []
    for t, feature in zip(times, features):
        if feature is None:
            detector.reset()
            continue
        key = detector.step(float(t), feature)
        if key is not None:
            events.append((float(t), key))
    return events


def fist_events(times, poses, hold=0.18):
    """project01의 지움 동작(FistHold)과 같은 규칙으로 주먹 이벤트 시각을 구한다."""
    gate, events = FistHold(hold), []
    for t, pose in zip(times, poses):
        if pose is None:
            gate.lost()
        elif gate.update(pose, float(t)):
            events.append(float(t))
    return events


@dataclass
class Score:
    taps: int = 0
    hits: int = 0
    correct: int = 0
    exact: int = 0
    false_positives: int = 0
    negatives: int = 0
    fist_false: int = 0
    fist_trials: int = 0
    fist_hits: int = 0
    confusion: Counter = field(default_factory=Counter)

    def add(self, other):
        for name in ("taps", "hits", "correct", "exact", "false_positives", "negatives",
                     "fist_false", "fist_trials", "fist_hits"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.confusion.update(other.confusion)
        return self

    @property
    def precision(self):
        return self.hits / max(1, self.hits + self.false_positives)

    @property
    def recall(self):
        return self.hits / max(1, self.taps)

    @property
    def key_accuracy(self):
        return self.correct / max(1, self.hits)

    @property
    def typing_accuracy(self):
        """맞게 입력된 누르기 / (누르기 시행 + 오검출). 임계값 선택 기준."""
        return self.correct / max(1, self.taps + self.false_positives)


def score_trials(trials, events, fists=()):
    """시행 단위 판정. tap 구간의 첫 이벤트만 입력으로 보고 나머지는 오검출로 센다."""
    score = Score()
    for trial in trials:
        if not trial.get("valid", True):
            continue
        start, end = trial["start"] - PRE, trial["end"] + POST
        inside = [key for t, key in events if start <= t <= end]
        fist_inside = sum(start <= t <= end for t in fists)
        if trial["kind"] == "tap":
            score.taps += 1
            score.confusion[(trial["target"], inside[0] if inside else NONE)] += 1
            if inside:
                score.hits += 1
                score.correct += inside[0] == trial["target"]
                score.exact += len(inside) == 1
                score.false_positives += len(inside) - 1
            score.fist_false += fist_inside > 0
        elif trial["kind"] in NEGATIVE_KINDS:
            score.negatives += 1
            score.false_positives += len(inside)
            for key in inside or [NONE]:
                score.confusion[(NONE, key)] += 1
            if trial["kind"] == "fist":
                score.fist_trials += 1
                score.fist_hits += fist_inside > 0
            else:
                score.fist_false += fist_inside > 0
    return score


def configs(grid_name):
    family, _, space = grid_name.partition("/")
    grid = GRIDS[grid_name]
    for values in product(*grid.values()):
        params = dict(zip(grid, values))
        if space:
            params["space"] = space
        if "gap" in params:
            params["exit"] = round(params["enter"] + params.pop("gap"), 4)
        yield family, params


def session_features(session, family, params, cache):
    detector = build(family, **params)
    key = (session.path, detector.feature_key(params))
    if key not in cache:
        cache[key] = [None if hand is None else detector.features(hand) for hand in session.hands]
    return cache[key]


def evaluate(sessions, family, params, cache):
    total = Score()
    for session in sessions:
        detector = build(family, **params)
        events = replay(detector, session.times, session_features(session, family, params, cache))
        total.add(score_trials(session.trials, events, fist_events(session.times, session.poses)))
    return total


def split(sessions):
    """(손, 카메라 위치)마다 마지막 세션을 평가용으로. 세션이 하나뿐인 묶음은 학습·평가에 함께 쓴다."""
    groups = {}
    for session in sorted(sessions, key=lambda s: s.path.name):
        groups.setdefault(session.group, []).append(session)
    train, test, shared = [], [], []
    for members in groups.values():
        if len(members) == 1:
            shared.append(members[0].path.name)
            train.append(members[0])
            test.append(members[0])
        else:
            train.extend(members[:-1])
            test.append(members[-1])
    return train, test, shared


def format_confusion(confusion):
    labels = list(SEGMENT_NAMES) + [NONE]
    lines = ["정답\\예측 " + " ".join(f"{label:>4}" for label in labels)]
    for target in labels:
        lines.append(f"{target:>9} " + " ".join(f"{confusion.get((target, guess), 0) or '.':>4}"
                                                  for guess in labels))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--detector", default="all", choices=["all", *GRIDS])
    parser.add_argument("--confusion", action="store_true", help="평가용 세션의 혼동 행렬 출력")
    args = parser.parse_args()
    sessions = [load_session(path) for path in args.sessions if (path / "meta.json").is_file()]
    empty = [s for s in sessions if not any(t["kind"] == "tap" and t.get("valid", True) for t in s.trials)]
    for session in empty:
        print(f"건너뜀: {session.path.name} (유효한 누르기 시행 없음, 종료 이유: {session.meta.get('exit')})")
    sessions = [s for s in sessions if s not in empty]
    if not sessions:
        parser.error("세션 폴더(meta.json 포함)를 지정하세요.")
    train, test, shared = split(sessions)
    print(f"세션 {len(sessions)}개: 학습 {len(train)}, 평가 {len(test)}")
    for session in sorted(sessions, key=lambda s: s.path.name):
        valid = sum(trial.get("valid", True) for trial in session.trials)
        print(f"  {session.path.name}: {session.meta.get('fps')} fps, 유효 시행 {valid}/{len(session.trials)}, "
              f"좌우 라벨 뒤집힘 {session.flipped:.1%}")
    if shared:
        print("주의: 세션이 하나뿐인 묶음은 학습과 평가에 같은 세션을 썼습니다 -> " + ", ".join(shared))
    cache = {}
    names = list(GRIDS) if args.detector == "all" else [args.detector]
    print(f"{'검출기':<14} {'정밀도':>6} {'재현율':>6} {'키정확도':>7} {'입력정확도':>8} {'주먹오검출':>8}  파라미터")
    for name in names:
        best = max(configs(name), key=lambda config: evaluate(train, *config, cache).typing_accuracy)
        result = evaluate(test, *best, cache)
        fist_rate = result.fist_false / max(1, result.taps + result.negatives - result.fist_trials)
        params = ", ".join(f"{k}={v}" for k, v in best[1].items() if k != "space")
        print(f"{name:<14} {result.precision:6.1%} {result.recall:6.1%} {result.key_accuracy:7.1%} "
              f"{result.typing_accuracy:8.1%} {fist_rate:8.1%}  {params}")
        by_placement = {}
        for session in test:
            by_placement.setdefault(session.meta["placement"], []).append(session)
        for placement, members in sorted(by_placement.items()):
            part = evaluate(members, *best, cache)
            print(f"  └ {placement:<10} {part.precision:6.1%} {part.recall:6.1%} {part.key_accuracy:7.1%} "
                  f"{part.typing_accuracy:8.1%}")
        if args.confusion:
            print(format_confusion(result.confusion))


if __name__ == "__main__":
    main()
