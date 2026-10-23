"""테스트용 손 신호와 프레임 실행기."""

from hand_signal import HandSignal
from typer import PinchTyper

DT = 1 / 30  # 30fps
OPEN = (0.6, 0.6, 0.65, 0.7)  # 편 손의 꼬집기 거리(검지·중지·약지·소지)
BACK = -0.8  # 손등이 카메라를 향한 facing


def sig(ratios=OPEN, pose="open", facing=0.8, curled=(False,) * 4, roll=0.0, tips=None, size=0.0, flex=None):
    return HandSignal(tuple(ratios), tuple(curled), pose, facing, roll, flex, tips, size)


def pinched(finger, value=0.08):
    """finger만 엄지에 닿은 꼬집기 거리."""
    ratios = list(OPEN)
    ratios[finger] = value
    return tuple(ratios)


class Runner:
    """프레임 묶음을 30fps로 흘려 넣고 이벤트를 모은다."""

    def __init__(self):
        self.typer = PinchTyper()
        self.now = 0.0
        self.events = []

    def frames(self, count, left=None, right=None):
        for _ in range(count):
            self.now += DT
            self.events += self.typer.update({"Left": left, "Right": right}, self.now)
        return self

    def ready(self, left_facing=0.8, right_facing=0.8):
        """양손을 보여 손 방향을 인정받는다."""
        return self.frames(20, sig(facing=left_facing), sig(facing=right_facing))

    def tap(self, hand, finger, frames=4):
        """손바닥 꼬집기 한 번: frames 프레임 닿았다가 편다."""
        signal = sig(pinched(finger))
        left, right = (signal, sig()) if hand == "Left" else (sig(), signal)
        return self.frames(frames, left, right).frames(3, sig(), sig())

    def vowel(self, drag=None, frames=6):
        """왼손 손등 검지(모음) 꼬집기. drag "h"/"v"면 꼬집은 채 손 크기(100)의 0.4배만큼 가로/세로로 끈다."""
        self.frames(20, sig(facing=BACK), sig())
        for k in range(frames):
            step = 40.0 * k / (frames - 1) if drag else 0.0
            point = (50 + step, 50.0) if drag == "h" else (50.0, 50 + step) if drag == "v" else (50.0, 50.0)
            self.frames(1, sig(pinched(0), facing=BACK, tips=(point,) * 5, size=100.0), sig())
        self.frames(3, sig(facing=BACK), sig())
        return self.frames(20, sig(), sig())

    def kinds(self, kind):
        return [event for event in self.events if event[0] == kind]
