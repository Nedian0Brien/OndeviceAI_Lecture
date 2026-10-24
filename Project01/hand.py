"""MediaPipe로 손을 검출하고 왼손과 오른손 자리에 배정하는 모듈"""

from typing import Any, NamedTuple

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision


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


def detect_hands(detector, frame, timestamp):
    """BGR 프레임에서 손을 찾아 Detection 목록을 돌려주는 함수"""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
    detections = []
    for landmarks, world, handedness in zip(result.hand_landmarks, result.hand_world_landmarks, result.handedness):
        # MediaPipe Tasks는 입력 영상에 보이는 그대로의 실제 손을 라벨로 냄(1.1.1, 공식 테스트 이미지로 확인)
        detections.append(Detection(handedness[0].category_name, landmarks, world))
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
