"""테스트용 합성 손 (numpy).

실제 카메라 없이 판정기를 검증하려고, 손바닥이 카메라를 향한 편 손을 world 좌표(미터)로 만든다.
- 손목은 (0, 0.09, 0), 네 손가락 뿌리는 y = 0에서 x = -0.03, -0.01, 0.01, 0.03에 나란히 있다.
- 손가락은 -y 방향(화면 위쪽)으로 뻗고, 마디 길이는 뿌리 4.5cm · 중간 2.8cm · 끝 2.2cm다.
- 손바닥 면은 z = 0이다. 엄지 끝 위치(thumb)만 바꿔 가며 누르기·띄우기·꼬집기를 흉내 낸다.
- 이미지 좌표는 world를 4배 해 화면 가운데(0.5, 0.5)에 놓는다. 픽셀 비율이 같도록 x에 HEIGHT/WIDTH를 곱한다.
  z(깊이)는 이미지 x, y에 보이지 않는다. 그래서 엄지를 띄운 것과 누른 것이 화면에서는 같아 보인다.
"""

from types import SimpleNamespace

import numpy as np

from common.landmarks import SEGMENTS


WIDTH, HEIGHT = 640, 480
FINGER_X = {5: -0.03, 9: -0.01, 13: 0.01, 17: 0.03}  # 손가락 뿌리 landmark 번호 → x(m)
LENGTHS = (0.045, 0.028, 0.022)  # 뿌리, 중간, 끝 마디 (m)
AWAY = (-0.08, 0.08, 0.05)  # 엄지를 모든 마디에서 멀리 둔 위치


def world_hand(thumb):
    """엄지 끝이 thumb에 있는 편 손의 world 좌표 (21, 3) 배열."""
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


def fold_fist(world):
    """네 손가락을 접어 주먹 모양으로 바꾼다(world 좌표, 제자리 수정)."""
    for base in (5, 9, 13, 17):
        mcp = world[base].copy()
        world[base + 1] = mcp + (0.0, -0.03, 0.0)
        world[base + 2] = mcp + (0.0, -0.02, -0.02)
        world[base + 3] = mcp + (0.0, 0.005, -0.015)
    return world


def to_image(world):
    """world 좌표 → 이미지 정규화 좌표 (21, 3) 배열. 모듈 설명의 투영 규칙을 따른다."""
    return np.column_stack([0.5 + world[:, 0] * 4 * HEIGHT / WIDTH, 0.5 + world[:, 1] * 4, world[:, 2] * 4])


def landmark_list(points):
    """(21, 3) 배열 → x, y, z 속성을 가진 landmark 객체 목록(MediaPipe 결과와 같은 모양)."""
    return [SimpleNamespace(x=x, y=y, z=z) for x, y, z in points]


def detection(thumb=AWAY, fist=False, mirrored=False):
    """(이미지 landmark 목록, world landmark 목록). mirrored면 화면 좌표만 좌우 반전한다."""
    world = world_hand(np.asarray(thumb, float))
    if fist:
        fold_fist(world)
    image = to_image(world)
    if mirrored:
        image[:, 0] = 1 - image[:, 0]
    return landmark_list(image), landmark_list(world)


def middle(name, lift=0.0):
    """마디 name(예: "I1")의 중점 world 좌표. lift만큼 카메라 쪽(z)으로 띄운다."""
    a, b = SEGMENTS[name]
    points = world_hand(np.zeros(3))
    return (points[a] + points[b]) / 2 + (0.0, 0.0, lift)


def tap(name, frames_down=4, lift=0.0):
    """목표 마디 옆 9mm, 위 4cm에서 내려와 닿은 뒤 같은 길로 떨어지는 엄지 궤적(프레임별 위치 목록).

    화면상으로 다른 마디를 가로지르지 않게 해서 상태 머신만 검증한다. 가로지를 때의 오입력은
    기록 데이터 평가(tap_eval.py)에서 측정한다.
    """
    target = middle(name, lift)
    start = target + (0.009, 0.0, 0.04)
    approach = [start + (target - start) * k / 6 for k in range(7)]
    return approach + [target] * frames_down + approach[::-1]
