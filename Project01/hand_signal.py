"""손 관절(landmark) → 판정에 쓰는 손 신호(HandSignal).

카메라(MediaPipe)와 판정을 나누는 경계다. 판정(pinch.py, typer.py)은 이 신호만 보고, 관절을 어디서 얻었는지
(카메라 영상, Vision Pro)는 모른다. 관절은 x, y, z 속성만 있으면 된다.

신호에 담는 값
  ratios  손가락별 꼬집기 거리: 엄지 끝 ~ 손가락 끝마디 선분 거리 / 손 크기(손목 ~ 중지 뿌리)
  curled  손가락별 굽음(PIP 관절 각도)            pose    편 손 / 접는 중 / 주먹
  facing  손바닥(+) / 손등(-)이 카메라를 향한 정도  roll    엄지 쪽(+) / 소지 쪽(-)으로 기운 각도
  flex    손가락별 굽힘 합(검지 겹침 규칙)          tips, size  화면 좌표의 엄지·손끝 위치와 손 크기(모음 끌기)
"""

from dataclasses import dataclass
import math
from typing import Optional

from common.landmarks import DISTAL_SEGMENTS, MIDDLE_MCP, THUMB_TIP, WRIST, point_segment_distance, xyz
from common.pose import CURL_ANGLE, finger_angles, finger_flex, palm_facing, palm_roll, pose_from_angles


@dataclass
class HandSignal:
    """판정기가 받는 손 하나의 신호."""
    ratios: tuple  # 검지·중지·약지·소지의 꼬집기 거리(pinch_ratios)
    curled: tuple = (False, False, False, False)  # 손가락별 굽음(world PIP 각도 ≤ CURL_ANGLE)
    pose: str = "open"  # open / closing / fist
    facing: float = 1.0  # > 0 손바닥, < 0 손등이 카메라를 향함(common.pose.palm_facing)
    roll: float = 0.0  # 긴 축 기준 기울기(도). < 0 소지 쪽이 카메라에 가까움(common.pose.palm_roll)
    flex: Optional[tuple] = None  # 손가락별 굽힘(도, common.pose.finger_flex)
    tips: Optional[tuple] = None  # 화면 좌표의 엄지 끝·검지·중지·약지·소지 끝 (x, y)
    size: float = 0.0  # 화면 좌표의 손 크기(손목 ~ 중지 뿌리)


def pinch_ratios(world):
    """엄지 끝(4) ~ 각 손가락 끝마디 선분(DIP → TIP) 거리 / 손목(0) ~ 중지 뿌리(9) 길이. 검지·중지·약지·소지.

    손끝 점끼리 재면 지문 면(끝마디 가운데)이 닿아도 거리가 남는다. 선분까지 재면 끝마디 어디에 닿든 같은 기준이 된다.
    손 크기로 나눠 사람·카메라 거리에 따른 차이를 없앤다.
    """
    scale = max(math.dist(xyz(world[WRIST]), xyz(world[MIDDLE_MCP])), 1e-6)
    thumb = xyz(world[THUMB_TIP])
    return tuple(point_segment_distance(thumb, xyz(world[a]), xyz(world[b])) / scale for a, b in DISTAL_SEGMENTS)


def image_tips(landmarks, width=1.0, height=1.0):
    """(엄지 끝·검지·중지·약지·소지 끝의 화면 좌표 (x, y), 손 크기 = 손목 ~ 중지 뿌리 화면 거리)."""
    points = [(landmarks[i].x * width, landmarks[i].y * height) for i in (THUMB_TIP, 8, 12, 16, 20)]
    wrist, middle = landmarks[WRIST], landmarks[MIDDLE_MCP]
    size = math.hypot((middle.x - wrist.x) * width, (middle.y - wrist.y) * height)
    return tuple(points), size


def hand_signal(label, landmarks, world, mirrored=False, width=1.0, height=1.0, pose=None):
    """한 손의 이미지 관절(정규화 좌표)과 world 관절(m)로 HandSignal을 만든다.

    label: 실제 왼손/오른손(손바닥 방향 부호에 쓴다). pose를 주지 않으면 world 각도로 자세를 정한다.
    """
    angles = finger_angles(world)
    tips, size = image_tips(landmarks, width, height)
    return HandSignal(
        ratios=pinch_ratios(world),
        curled=tuple(angle <= CURL_ANGLE for angle in angles),
        pose=pose or pose_from_angles(angles),
        facing=palm_facing(landmarks, label, mirrored, width, height),
        roll=palm_roll(landmarks, width, height),
        flex=finger_flex(world),
        tips=tips,
        size=size,
    )
