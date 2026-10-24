"""MediaPipe로 손을 검출하고 손 관절에서 판정에 쓰는 손 신호를 계산하는 모듈"""

from dataclasses import dataclass
import math
from typing import Any, NamedTuple

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision


# MediaPipe 손 관절 번호. 손목이 0이고 손가락마다 뿌리(MCP)에서 끝(TIP) 쪽으로 번호가 늘어남
WRIST = 0
THUMB_TIP = 4
INDEX_MCP, MIDDLE_MCP, PINKY_MCP = 5, 9, 17
FINGER_MCPS = (5, 9, 13, 17)  # 검지·중지·약지·소지의 뿌리 관절
FINGER_TIPS = (8, 12, 16, 20)
FINGER_NAMES = ("검지", "중지", "약지", "소지")
DISTAL_SEGMENTS = ((7, 8), (11, 12), (15, 16), (19, 20))  # 손가락 끝마디. 엄지로 핀치할 때 닿는 부분임
HAND_CONNECTIONS = tuple(
    (a, b)
    for chain in ((0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (5, 9, 10, 11, 12),
                  (9, 13, 14, 15, 16), (13, 17, 18, 19, 20), (0, 17))
    for a, b in zip(chain, chain[1:])
)
CURL_ANGLE = 130.0  # 손가락 가운데 관절 각도가 이 값(도) 이하면 굽은 것으로 봄


class Detection(NamedTuple):
    """손 하나의 검출 결과를 나타내는 클래스"""
    label: str  # 실제 왼손이면 "Left", 오른손이면 "Right"
    landmarks: Any  # 영상 폭과 높이에 대한 비율 좌표 21개
    world: Any  # 손 중심을 원점으로 한 미터 단위 3D 좌표 21개


def create_detector(model):
    """VIDEO 모드 손 검출기를 만드는 함수"""
    # VIDEO 모드는 앞 프레임의 손 위치를 이어서 추적해 IMAGE 모드보다 빠르고 안정적임
    if not model.is_file():
        raise FileNotFoundError("MediaPipe 모델이 없습니다: " + str(model))
    options = vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.VIDEO, num_hands=2,
        min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    return vision.HandLandmarker.create_from_options(options)


def detect_hands(detector, frame, timestamp, mirrored):
    """BGR 프레임에서 손을 찾아 Detection 목록을 돌려주는 함수"""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
    detections = []
    for landmarks, world, handedness in zip(result.hand_landmarks, result.hand_world_landmarks, result.handedness):
        label = handedness[0].category_name
        # MediaPipe Tasks는 입력 영상에 보이는 그대로의 손을 라벨로 냄(1.1.1, 공식 테스트 이미지로 확인)
        # 그래서 영상을 좌우 반전했을 때만 라벨을 뒤집음. 이 라벨이 틀리면 손바닥과 손등이 뒤바뀜
        if mirrored:
            label = "Left" if label == "Right" else "Right"
        detections.append(Detection(label, landmarks, world))
    return detections


def detection_frame(frame, max_width):
    """손 검출에 넣을 영상을 가로 max_width 이하로 줄이는 함수"""
    # 검출 입력을 640 이하로 두면 화면 해상도와 상관없이 Jetson 640x480 시연과 검출 시간이 같아짐
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    return cv2.resize(frame, (max_width, max(1, round(h * max_width / w))), interpolation=cv2.INTER_AREA)


def assign_hands(detections):
    """검출한 손을 왼손과 오른손 자리에 배정하는 함수"""
    # 카메라가 손 맞은편에 있으면 화면 위치로 정한 좌우가 뒤바뀌므로 MediaPipe 라벨을 따름
    # 두 손에 같은 라벨이 붙었을 때만 화면 왼쪽에 있는 손을 왼손으로 봄
    if len(detections) >= 2:
        first, second = detections[:2]
        if first.label != second.label:
            return {detection.label: detection for detection in (first, second)}
        left, right = sorted((first, second), key=lambda d: sum(p.x for p in d.landmarks) / len(d.landmarks))
        return {"Left": left, "Right": right}
    return {detection.label: detection for detection in detections[:1]}


def xyz(point):
    return (point.x, point.y, getattr(point, "z", 0.0))


def point_segment_distance(point, a, b):
    """점에서 선분 a-b까지의 최단 거리를 구하는 함수"""
    ab = [y - x for x, y in zip(a, b)]
    length = sum(v * v for v in ab)
    t = 0.0 if length < 1e-12 else max(0.0, min(1.0, sum((p - x) * v for p, x, v in zip(point, a, ab)) / length))
    return math.dist(point, [x + t * v for x, v in zip(a, ab)])


def joint_angle(a, b, c):
    """점 b에서 a와 c로 가는 두 벡터 사이의 각도(도)를 구하는 함수"""
    first = [x - y for x, y in zip(a, b)]
    second = [x - y for x, y in zip(c, b)]
    norm = math.hypot(*first) * math.hypot(*second)
    if norm < 1e-12:  # 길이가 0이면 편 상태로 봄
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(first, second)) / norm))))


def pose_from_image(landmarks, width, height):
    """영상 좌표로 손 자세를 "open", "closing", "fist" 가운데 하나로 정하는 함수"""
    # 수업 practice 코드의 방식임. 가운데 관절 각도와 손끝이 뿌리에서 뻗은 거리(reach)를 함께 봄
    points = [(p.x * width, p.y * height, getattr(p, "z", 0.0) * width) for p in landmarks]
    extended = curled = 0
    for base in FINGER_MCPS:
        mcp, pip, dip, tip = points[base:base + 4]
        first = tuple(a - b for a, b in zip(mcp, pip))
        second = tuple(a - b for a, b in zip(dip, pip))
        length1, length2 = math.dist(mcp, pip), math.dist(dip, pip)
        if min(length1, length2) < 2:  # 마디가 2픽셀보다 짧으면 각도를 믿을 수 없음
            continue
        cosine = sum(a * b for a, b in zip(first, second)) / (length1 * length2)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        reach = math.dist(tip, mcp) / length1
        extended += angle >= 150 and reach >= 1.6
        curled += angle <= 135 and reach <= 1.7
    if curled >= 3 and extended == 0:
        return "fist"
    if extended >= 3 and curled == 0:
        return "open"
    return "closing"


def finger_flex(world):
    """손가락마다 세 관절의 굽힘 각도 합을 구하는 함수"""
    # 3D 좌표라 카메라 쪽으로 굽힌 손가락도 잡힘. 0이면 다 편 상태임
    wrist = xyz(world[WRIST])
    flex = []
    for base in FINGER_MCPS:
        mcp, pip, dip, tip = (xyz(world[base + i]) for i in range(4))
        flex.append((180.0 - joint_angle(wrist, mcp, pip)) + (180.0 - joint_angle(mcp, pip, dip))
                    + (180.0 - joint_angle(pip, dip, tip)))
    return tuple(flex)


def palm_roll(landmarks, width=1.0, height=1.0):
    """손의 긴 축을 기준으로 손이 기운 각도(도)를 구하는 함수"""
    # 엄지 쪽이 카메라에 가까우면 양수, 소지 쪽이 가까우면 음수임. 왼손과 오른손, 좌우 반전과 관계없이 부호가 같음
    dx = (landmarks[INDEX_MCP].x - landmarks[PINKY_MCP].x) * width
    dy = (landmarks[INDEX_MCP].y - landmarks[PINKY_MCP].y) * height
    dz = (landmarks[INDEX_MCP].z - landmarks[PINKY_MCP].z) * width
    return math.degrees(math.atan2(-dz, math.hypot(dx, dy)))


def palm_facing(landmarks, label, mirrored=False, width=1.0, height=1.0):
    """손바닥이 카메라를 향한 정도를 -1에서 1 사이 값으로 구하는 함수"""
    # 손목에서 검지 뿌리와 소지 뿌리로 가는 두 벡터의 외적임. 양수면 손바닥, 음수면 손등이 카메라를 향함
    # 거울상이 되면 부호가 바뀌므로 왼손과 좌우 반전 영상에서는 부호를 뒤집음
    wrist = landmarks[WRIST]
    a = ((landmarks[INDEX_MCP].x - wrist.x) * width, (landmarks[INDEX_MCP].y - wrist.y) * height)
    b = ((landmarks[PINKY_MCP].x - wrist.x) * width, (landmarks[PINKY_MCP].y - wrist.y) * height)
    norm = math.hypot(*a) * math.hypot(*b)
    if norm < 1e-12:
        return 0.0
    value = -(a[0] * b[1] - a[1] * b[0]) / norm
    if label == "Left":
        value = -value
    if mirrored:
        value = -value
    return value


@dataclass
class HandSignal:
    """판정에 쓰는 손 하나의 신호를 나타내는 클래스"""
    ratios: tuple  # 검지·중지·약지·소지의 핀치 거리
    curled: tuple = (False, False, False, False)  # 손가락마다 굽었는지
    pose: str = "open"  # "open", "closing", "fist"
    facing: float = 1.0  # 양수면 손바닥, 음수면 손등이 카메라를 향함
    roll: float = 0.0  # 음수면 소지 쪽이 카메라에 가깝게 기울었음(도)
    flex: tuple = None  # 손가락마다 굽힘 각도 합(도)
    tips: tuple = None  # 화면 좌표의 엄지 끝과 네 손끝 위치
    size: float = 0.0  # 화면 좌표의 손 크기(손목에서 중지 뿌리까지)


def pinch_ratios(world):
    """손가락마다 엄지 끝에서 끝마디까지의 거리를 손 크기로 나눈 값을 구하는 함수"""
    # 손끝 점끼리 재면 끝마디 가운데가 닿아도 거리가 남으므로 끝마디 선분까지 잼
    # 손 크기로 나눠 사람과 카메라 거리에 따른 차이를 없앰
    scale = max(math.dist(xyz(world[WRIST]), xyz(world[MIDDLE_MCP])), 1e-6)
    thumb = xyz(world[THUMB_TIP])
    return tuple(point_segment_distance(thumb, xyz(world[a]), xyz(world[b])) / scale for a, b in DISTAL_SEGMENTS)


def image_tips(landmarks, width=1.0, height=1.0):
    """화면 좌표의 엄지 끝과 네 손끝 위치, 손 크기를 구하는 함수"""
    points = [(landmarks[i].x * width, landmarks[i].y * height) for i in (THUMB_TIP, *FINGER_TIPS)]
    wrist, middle = landmarks[WRIST], landmarks[MIDDLE_MCP]
    size = math.hypot((middle.x - wrist.x) * width, (middle.y - wrist.y) * height)
    return tuple(points), size


def hand_signal(label, landmarks, world, mirrored, width, height):
    """한 손의 관절 좌표로 HandSignal을 만드는 함수"""
    # 거리와 굽힘은 카메라 거리와 무관한 3D 좌표로, 자세·방향·기울기·손끝 위치는 영상 좌표로 계산함
    tips, size = image_tips(landmarks, width, height)
    return HandSignal(
        ratios=pinch_ratios(world),
        curled=tuple(joint_angle(*(xyz(world[base + i]) for i in range(3))) <= CURL_ANGLE for base in FINGER_MCPS),
        pose=pose_from_image(landmarks, width, height),
        facing=palm_facing(landmarks, label, mirrored, width, height),
        roll=palm_roll(landmarks, width, height),
        flex=finger_flex(world),
        tips=tips,
        size=size,
    )
