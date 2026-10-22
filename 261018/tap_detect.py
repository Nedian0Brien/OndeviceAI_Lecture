"""엄지로 손가락 마디를 누르는 순간과 누른 마디를 찾는 검출기 (numpy만 사용).

마디 이름은 손가락(I 검지, M 중지, R 약지, P 소지)과 위치(1 끝, 2 중간, 3 뿌리)로 붙인다.
검출기는 프레임마다 update(t, hand)를 받고, 누르기를 판정한 프레임에서 마디 이름을 돌려준다.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from cheonjiin import TouchGate


THUMB_TIP = 4
FINGERS = (("I", 5), ("M", 9), ("R", 13), ("P", 17))
# 끝 마디 DIP-TIP, 중간 마디 PIP-DIP, 뿌리 마디 MCP-PIP
SEGMENTS = {f"{finger}{row}": (base + 3 - row, base + 4 - row)
            for finger, base in FINGERS for row in (1, 2, 3)}
SEGMENT_NAMES = tuple(SEGMENTS)


def as_points(landmarks):
    """MediaPipe landmark 목록이나 (21, 3) 배열을 float 배열로 바꾼다."""
    if isinstance(landmarks, np.ndarray):
        return landmarks.astype(float)
    return np.array([(p.x, p.y, p.z) for p in landmarks], dtype=float)


@dataclass
class HandFrame:
    """한 프레임의 한 손. image는 0~1 정규화 좌표, world는 손 중심 기준 미터 단위."""
    image: np.ndarray
    world: np.ndarray
    width: int
    height: int

    def pixels(self):
        """화면 비율을 반영한 2D 좌표를 손바닥 크기로 나눈 값. 손 크기와 해상도에 무관하다."""
        points = self.image[:, :2] * (self.width, self.height)
        palm = max(np.linalg.norm(points[5] - points[17]), np.linalg.norm(points[0] - points[9]))
        return points / max(palm, 1e-6)


def segment_distances(points, thumb=THUMB_TIP):
    """엄지 끝에서 12개 마디 선분까지의 거리. points는 (21, 2) 또는 (21, 3)."""
    starts = points[[a for a, _ in SEGMENTS.values()]]
    ends = points[[b for _, b in SEGMENTS.values()]]
    direction = ends - starts
    length = np.maximum((direction ** 2).sum(axis=1), 1e-12)
    ratio = np.clip(((points[thumb] - starts) * direction).sum(axis=1) / length, 0.0, 1.0)
    closest = starts + direction * ratio[:, None]
    return np.linalg.norm(points[thumb] - closest, axis=1)


def hand_distances(hand, space):
    if space == "world":
        return segment_distances(hand.world)
    if space == "image":
        return segment_distances(hand.pixels())
    raise ValueError("space는 world 또는 image: " + space)


class Detector:
    """features(hand)는 파라미터와 무관한 거리 계산, step(t, feature)는 상태 머신.

    평가에서 같은 프레임을 여러 파라미터로 돌릴 때 features를 한 번만 계산한다.
    """

    def update(self, t, hand):
        if hand is None:
            self.reset()
            return None
        return self.step(t, self.features(hand))


class Point2D(Detector):
    """기준선: 기존 project01과 같은 2D 거리 + 머무름(TouchGate) 방식을 마디 중점에 적용한다."""

    family = "point2d"

    def __init__(self, ratio=0.16, dwell=0.12, release=0.10):
        self.name = f"point2d(ratio={ratio},dwell={dwell})"
        self.ratio = ratio
        self.gate = TouchGate(dwell, release)

    @staticmethod
    def feature_key(params):
        return ("point2d",)

    @staticmethod
    def features(hand):
        points = hand.pixels()
        starts = points[[a for a, _ in SEGMENTS.values()]]
        ends = points[[b for _, b in SEGMENTS.values()]]
        distance = np.linalg.norm((starts + ends) / 2 - points[THUMB_TIP], axis=1)
        spacing = np.linalg.norm(ends - starts, axis=1)
        return np.stack([distance, spacing])

    def reset(self):
        self.gate.lost()

    def step(self, t, feature):
        distance, spacing = feature
        # pixels()는 손바닥 크기로 나눈 값이므로 ratio를 그대로 반경으로 쓴다.
        radius = np.minimum(self.ratio, spacing * 0.46)
        nearest = int(distance.argmin())
        key = SEGMENT_NAMES[nearest] if distance[nearest] <= radius[nearest] else None
        released = bool((distance > radius * 1.45).all())
        return self.gate.update(key, released, t)


class Seg(Detector):
    """마디 선분까지 거리가 enter 아래로 dwell초 유지되면 한 번 입력, exit 위로 떨어지면 재준비."""

    def __init__(self, space="world", enter=0.012, exit=0.020, dwell=0.05):
        if exit < enter:
            raise ValueError("exit는 enter 이상이어야 합니다.")
        self.name = f"seg({space},enter={enter},exit={exit},dwell={dwell})"
        self.space, self.enter, self.exit, self.dwell = space, enter, exit, dwell
        self.reset()

    family = "seg"

    @staticmethod
    def feature_key(params):
        return ("dist", params.get("space", "world"))

    def features(self, hand):
        return hand_distances(hand, self.space)

    def reset(self):
        self.armed = False  # 손이 처음 보이거나 돌아오면 먼저 떨어져 있어야 한다.
        self.since = None

    def step(self, t, distance):
        nearest = int(distance.argmin())
        if distance[nearest] > self.exit:
            self.armed, self.since = True, None
            return None
        if not self.armed or distance[nearest] > self.enter:
            self.since = None
            return None
        if self.since is None:
            self.since = t
        if t - self.since >= self.dwell:
            self.armed, self.since = False, None
            return SEGMENT_NAMES[nearest]
        return None


class SegDip(Detector):
    """가장 가까운 마디까지의 거리 곡선에서 '내려갔다 올라오는' 골을 누르기로 본다.

    골의 바닥이 enter 이하이고, 직전 window초 안의 최댓값보다 depth 이상 낮으며,
    바닥 이후 rise 이상 다시 멀어지면 바닥 시점의 가장 가까운 마디를 입력한다.
    엄지를 띄운 채 멈춰 있으면 골이 생기지 않으므로 입력되지 않는다.
    """

    def __init__(self, space="world", enter=0.015, depth=0.008, rise=0.003, window=0.40):
        self.name = f"segdip({space},enter={enter},depth={depth},rise={rise})"
        self.space, self.enter, self.depth, self.rise, self.window = space, enter, depth, rise, window
        self.reset()

    family = "segdip"

    @staticmethod
    def feature_key(params):
        return ("dist", params.get("space", "world"))

    def features(self, hand):
        return hand_distances(hand, self.space)

    def reset(self):
        self.history = []  # (t, 최소 거리)
        self.bottom = None  # (t, 거리, 마디)

    def step(self, t, distance):
        nearest = int(distance.argmin())
        value = float(distance[nearest])
        self.history = [(when, d) for when, d in self.history if t - when <= self.window]
        peak = max((d for _, d in self.history), default=value)
        self.history.append((t, value))
        if self.bottom is None:
            if value <= self.enter and peak - value >= self.depth:
                self.bottom = (t, value, nearest)
            return None
        if value < self.bottom[1]:
            # 더 깊이 내려가는 중: 바닥과 마디를 갱신한다.
            self.bottom = (t, value, nearest)
            return None
        if value - self.bottom[1] >= self.rise:
            key = SEGMENT_NAMES[self.bottom[2]]
            self.bottom = None
            self.history = [(t, value)]  # 같은 골을 두 번 세지 않는다.
            return key
        return None


FAMILIES = {"point2d": Point2D, "seg": Seg, "segdip": SegDip}


def build(family, **params):
    return FAMILIES[family](**params)
