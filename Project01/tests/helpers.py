"""테스트에서 쓰는 손 신호, 합성 손, 프레임 실행기를 모은 모듈"""

from types import SimpleNamespace

import numpy as np

from hand import HandSignal
from keyboard import PinchTyper

DT = 1 / 30
OPEN = (0.6, 0.6, 0.65, 0.7)  # 편 손의 검지·중지·약지·소지 핀치 거리
BACK = -0.8  # 손등이 카메라를 향한 손 방향 값


def sig(ratios=OPEN, pose="open", facing=0.8, curled=(False,) * 4, roll=0.0, tips=None, size=0.0, flex=None):
    return HandSignal(tuple(ratios), tuple(curled), pose, facing, roll, flex, tips, size)


def pinched(finger, value=0.08):
    """finger만 엄지에 닿은 핀치 거리를 만드는 함수"""
    ratios = list(OPEN)
    ratios[finger] = value
    return tuple(ratios)


def detection(fist=False, mirrored=False):
    """손바닥이 카메라를 향한 합성 손의 (영상 관절, world 관절)을 만드는 함수"""
    # 손목은 (0, 0.09, 0), 네 손가락 뿌리는 y = 0에 나란히 있고 손가락은 화면 위쪽으로 뻗음(단위 m)
    world = np.zeros((21, 3))
    world[0] = (0.0, 0.09, 0.0)
    world[1:5] = [(-0.04, 0.07, 0.0), (-0.055, 0.05, 0.0), (-0.06, 0.03, 0.0), (-0.08, 0.08, 0.05)]
    for base, x in {5: -0.03, 9: -0.01, 13: 0.01, 17: 0.03}.items():
        y = 0.0
        world[base] = (x, y, 0.0)
        for step, length in enumerate((0.045, 0.028, 0.022), start=1):
            y -= length
            world[base + step] = (x, y, 0.0)
        if fist:  # 손가락을 뿌리 가까이 접음
            world[base + 1:base + 4] = world[base] + [(0.0, -0.03, 0.0), (0.0, -0.02, -0.02), (0.0, 0.005, -0.015)]
    # 영상 좌표는 world를 4배 해서 화면 가운데에 놓고, 픽셀 비율이 같도록 x에 480/640을 곱함
    image = np.column_stack([0.5 + world[:, 0] * 4 * 480 / 640, 0.5 + world[:, 1] * 4, world[:, 2] * 4])
    if mirrored:
        image[:, 0] = 1 - image[:, 0]
    return ([SimpleNamespace(x=x, y=y, z=z) for x, y, z in image],
            [SimpleNamespace(x=x, y=y, z=z) for x, y, z in world])


class Runner:
    """프레임 신호를 30fps로 넣고 이벤트를 모으는 클래스"""

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
        """양손을 보여 손 방향을 인정받는 메서드"""
        return self.frames(20, sig(facing=left_facing), sig(facing=right_facing))

    def tap(self, hand, finger, frames=4):
        """손바닥 핀치 한 번을 넣는 메서드"""
        signal = sig(pinched(finger))
        left, right = (signal, sig()) if hand == "Left" else (sig(), signal)
        return self.frames(frames, left, right).frames(3, sig(), sig())

    def vowel(self, drag=None, frames=6):
        """왼손 손등 검지(모음) 핀치 한 번을 넣는 메서드"""
        # drag가 "h"나 "v"면 핀치한 채 손 크기(100)의 0.4배만큼 가로나 세로로 끎
        self.frames(20, sig(facing=BACK), sig())
        for k in range(frames):
            step = 40.0 * k / (frames - 1) if drag else 0.0
            point = (50 + step, 50.0) if drag == "h" else (50.0, 50 + step) if drag == "v" else (50.0, 50.0)
            self.frames(1, sig(pinched(0), facing=BACK, tips=(point,) * 5, size=100.0), sig())
        self.frames(3, sig(facing=BACK), sig())
        return self.frames(20, sig(), sig())

    def kinds(self, kind):
        return [event for event in self.events if event[0] == kind]
