"""한 손의 엄지 끝(4)과 검지 끝(8)이 맞닿는 꼬집기(pinch) 판정 (표준 라이브러리만 사용).

두 끝점은 같은 손이므로 MediaPipe world 좌표의 3D 거리를 그대로 쓴다. 손 크기 차이를 없애려고
손목(0)~중지 뿌리(9) 길이로 나눈 비율을 쓴다.
"""

import math
from typing import Optional


THUMB_TIP, INDEX_TIP, WRIST, MIDDLE_MCP = 4, 8, 0, 9
PINCH_ENTER, PINCH_EXIT = 0.30, 0.45


def _xyz(point):
    return (point.x, point.y, getattr(point, "z", 0.0))


def pinch_ratio(world):
    """엄지 끝~검지 끝 거리 / 손목~중지 뿌리 거리. 펴면 1 안팎, 맞닿으면 0에 가깝다."""
    scale = math.dist(_xyz(world[WRIST]), _xyz(world[MIDDLE_MCP]))
    return math.dist(_xyz(world[THUMB_TIP]), _xyz(world[INDEX_TIP])) / max(scale, 1e-6)


def pinch_cursor(points):
    """화면 좌표 (x, y) 목록에서 엄지 끝과 검지 끝의 중점. 맞닿는 순간 두 점이 이 자리로 모인다."""
    (x1, y1), (x2, y2) = points[THUMB_TIP][:2], points[INDEX_TIP][:2]
    return (x1 + x2) / 2, (y1 + y2) / 2


class PinchGate:
    """벌림(exit 이상) → 오므리기 시작(exit 아래)에서 키 고정 → enter 아래에서 한 번 입력 → 다시 벌려야 재입력.

    오므리는 동안 커서가 움직여도 오므리기 시작할 때 고정한 키를 입력한다.
    처음 보이는 손이나 유실 후 돌아온 손은 먼저 벌려야 한다.
    """

    def __init__(self, enter=PINCH_ENTER, exit=PINCH_EXIT):
        if exit <= enter:
            raise ValueError("exit는 enter보다 커야 합니다.")
        self.enter, self.exit = enter, exit
        self.lost()

    def lost(self):
        self.armed = False
        self.closing = False
        self.latched: Optional[str] = None
        self.ratio: Optional[float] = None

    @property
    def progress(self):
        """오므린 정도 0~1. 화면 표시용."""
        if self.ratio is None or not self.armed:
            return 0.0
        return min(1.0, max(0.0, (self.exit - self.ratio) / (self.exit - self.enter)))

    def update(self, ratio, key):
        self.ratio = ratio
        if ratio >= self.exit:
            self.armed, self.closing, self.latched = True, False, None
            return None
        if not self.armed:
            return None
        if not self.closing:
            self.closing, self.latched = True, key
        if ratio <= self.enter:
            pressed = self.latched
            self.armed, self.closing, self.latched = False, False, None
            return pressed
        return None
