"""테스트용 합성 손 (numpy).

실제 카메라 없이 판정기를 검증하려고, 손바닥이 카메라를 향한 편 손을 world 좌표(미터)로 만듭니다.
- 손목은 (0, 0.09, 0), 네 손가락 뿌리는 y = 0에서 x = -0.03, -0.01, 0.01, 0.03에 나란히 있습니다.
- 손가락은 -y 방향(화면 위쪽)으로 뻗고, 마디 길이는 뿌리 4.5cm · 중간 2.8cm · 끝 2.2cm입니다.
- 손바닥 면은 z = 0입니다. 엄지 끝 위치(thumb)만 바꿔 가며 꼬집기를 흉내 냅니다.
- 이미지 좌표는 world를 4배 해 화면 가운데(0.5, 0.5)에 놓습니다. 픽셀 비율이 같도록 x에 HEIGHT/WIDTH를 곱합니다.
  z(깊이)는 이미지 x, y에 보이지 않습니다. 그래서 엄지를 띄운 것과 누른 것이 화면에서는 같아 보입니다.
"""

from types import SimpleNamespace

import numpy as np



WIDTH, HEIGHT = 640, 480
FINGER_X = {5: -0.03, 9: -0.01, 13: 0.01, 17: 0.03}  # 손가락 뿌리 landmark 번호 → x(m)
LENGTHS = (0.045, 0.028, 0.022)  # 뿌리, 중간, 끝 마디 (m)
AWAY = (-0.08, 0.08, 0.05)  # 엄지를 모든 마디에서 멀리 둔 위치


def world_hand(thumb):
    """엄지 끝이 thumb에 있는 편 손의 world 좌표 (21, 3) 배열."""
    points = np.zeros((21, 3))
    points[0] = (0.0, 0.09, 0.0)  # 손목
    points[1:4] = [(-0.04, 0.07, 0.0), (-0.055, 0.05, 0.0), (-0.06, 0.03, 0.0)]  # 엄지 마디
    # 네 손가락: 뿌리에서 -y 방향으로 마디 길이만큼씩 뻗음
    for base, x in FINGER_X.items():
        y = 0.0
        points[base] = (x, y, 0.0)
        for step, length in enumerate(LENGTHS, start=1):
            y -= length
            points[base + step] = (x, y, 0.0)
    points[4] = thumb
    return points


def fold_fist(world):
    """네 손가락을 접어 주먹 모양으로 바꿉니다(world 좌표, 제자리 수정)."""
    for base in (5, 9, 13, 17):  # 손가락마다 PIP·DIP·TIP을 뿌리 가까이 -z 방향으로 접어 놓음
        mcp = world[base].copy()
        world[base + 1] = mcp + (0.0, -0.03, 0.0)
        world[base + 2] = mcp + (0.0, -0.02, -0.02)
        world[base + 3] = mcp + (0.0, 0.005, -0.015)
    return world


def to_image(world):
    """world 좌표 → 이미지 정규화 좌표 (21, 3) 배열. 모듈 설명의 투영 규칙을 따릅니다."""
    return np.column_stack([0.5 + world[:, 0] * 4 * HEIGHT / WIDTH, 0.5 + world[:, 1] * 4, world[:, 2] * 4])


def landmark_list(points):
    """(21, 3) 배열 → x, y, z 속성을 가진 landmark 객체 목록(MediaPipe 결과와 같은 모양)."""
    return [SimpleNamespace(x=x, y=y, z=z) for x, y, z in points]


def detection(thumb=AWAY, fist=False, mirrored=False):
    """(이미지 landmark 목록, world landmark 목록)을 돌려줍니다. mirrored면 화면 좌표만 좌우 반전합니다."""
    world = world_hand(np.asarray(thumb, float))
    if fist:  # 주먹 모양으로 접음
        fold_fist(world)
    image = to_image(world)
    if mirrored:  # 셀카 영상처럼 x만 뒤집음
        image[:, 0] = 1 - image[:, 0]
    return landmark_list(image), landmark_list(world)
