"""MediaPipe 손 검출과 왼손·오른손 배정.

검출 흐름
  BGR 프레임 → (필요하면 `detection_frame`으로 축소) → `detect_hands` → `Detection` 목록
  → `assign_hands`로 {"Left": ..., "Right": ...} 배정

좌우 라벨 규칙(`true_label`)
  MediaPipe Tasks(1.1.1 확인)는 입력 영상에 보이는 그대로의 실제 손을 라벨로 낸다. 공식 테스트 이미지
  right_hands.jpg(오른손 손등)는 원본에서 Right, 좌우 반전하면 Left다. 예전 MediaPipe Hands 솔루션 문서의
  "셀카(좌우 반전) 영상 기준" 가정은 맞지 않는다. 그래서 영상을 좌우 반전했을 때만 라벨을 뒤집어 실제 손으로
  되돌린다. 이 규칙이 틀리면 손바닥 방향 부호도 뒤집힌다(common.pose.palm_facing).

mediapipe·OpenCV는 함수 안에서 import한다.
"""

from typing import Any, NamedTuple

from common.landmarks import mean_x


class Detection(NamedTuple):
    """손 하나의 검출 결과. 튜플처럼 (label, landmarks, world, score)로 풀어 쓸 수도 있다."""
    label: str  # 실제 왼손/오른손 ("Left" | "Right"), true_label 규칙 적용 후
    landmarks: Any  # 이미지 정규화 좌표 21개
    world: Any  # world 좌표(미터) 21개
    score: float  # 좌우 분류 확신도. 손 검출 확신도가 아니므로 이 값으로 손을 거르지 않는다.


def create_detector(model):
    """VIDEO 모드 HandLandmarker. `with create_detector(path) as detector:`로 쓰고 닫는다.

    VIDEO 모드는 앞 프레임의 손 위치를 이어서 추적하므로, 매 프레임 손을 새로 찾는 IMAGE 모드보다 빠르고
    안정적이다. 대신 detect_for_video에 넘기는 시각(ms)이 엄격히 증가해야 한다(common.camera.VideoClock).
    """
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    if not model.is_file():
        raise FileNotFoundError("MediaPipe 모델이 없습니다: " + str(model))
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.VIDEO, num_hands=2,
        min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    return vision.HandLandmarker.create_from_options(options)


def true_label(label, mirrored):
    """MediaPipe 라벨을 실제 왼손/오른손으로. 영상을 좌우 반전했을 때만 뒤집는다(모듈 설명 참고)."""
    if mirrored:
        return "Left" if label == "Right" else "Right"
    return label


def detections_from_result(result, mirrored):
    """HandLandmarker 결과 → Detection 목록."""
    detections = []
    for landmarks, world, handedness in zip(result.hand_landmarks, result.hand_world_landmarks, result.handedness):
        category = handedness[0]  # 가장 확신도가 높은 좌우 분류
        detections.append(Detection(true_label(category.category_name, mirrored), landmarks, world, category.score))
    return detections


def detect_hands(detector, frame, timestamp, mirrored):
    """BGR 프레임에서 손을 찾는다. timestamp는 엄격히 증가하는 ms, mirrored는 프레임을 좌우 반전했는지."""
    import cv2
    import mediapipe as mp

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
    return detections_from_result(result, mirrored)


def detection_frame(frame, max_width):
    """손 검출에 넣을 영상. 가로가 max_width보다 크면 비율을 유지해 줄인다.

    landmark는 0~1 비율 좌표라 원본 해상도 화면에 그대로 쓸 수 있다. 검출 입력을 640 이하로 두면
    화면 해상도와 상관없이 검출 시간이 Jetson 640x480 시연과 같은 조건이 된다.
    """
    import cv2

    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    return cv2.resize(frame, (max_width, max(1, round(h * max_width / w))), interpolation=cv2.INTER_AREA)


def assign_hands(detections):
    """{"Left": 검출, "Right": 검출}. 검출은 (label, landmarks, ...) 꼴이면 무엇이든 되고, 그대로 돌려준다.

    좌우는 라벨(손바닥 주름·손톱 같은 겉모습으로 MediaPipe가 판단)을 따른다. 카메라가 손 맞은편에 있으면
    화면 위치로 정한 좌우는 뒤바뀌기 때문이다. 두 손에 같은 라벨이 붙었을 때만 화면 위치로 나눈다
    (화면 왼쪽 손 = Left). 세 손 이상이면 앞의 두 손만 본다.
    배정된 키("Left"/"Right")와 검출의 label이 다를 수 있다(같은 라벨 두 손). 손바닥 방향 계산에는
    MediaPipe가 본 label을 써야 하므로 검출 객체를 그대로 넘긴다.
    """
    if len(detections) >= 2:
        first, second = detections[:2]
        if first[0] != second[0]:
            return {detection[0]: detection for detection in (first, second)}
        left, right = sorted((first, second), key=lambda detection: mean_x(detection[1]))
        return {"Left": left, "Right": right}
    return {detection[0]: detection for detection in detections[:1]}


def session_hand(labels, hand):
    """녹화·평가 도구용: 프레임의 손 칸(slot) 중 지정한 손(hand)의 번호. 없으면 None.

    녹화할 때 `--hand`로 손을 이미 정했다. 화면에 손이 하나면 좌우 라벨과 상관없이 그 손을 쓴다. 비스듬한
    카메라에서는 MediaPipe 좌우 라벨이 뒤집힐 수 있기 때문이다. 손이 둘이면 라벨이 맞는 하나만 쓴다.
    labels: 칸마다 "Left"/"Right"/""(빈 칸)
    """
    present = [slot for slot, label in enumerate(labels) if label]
    if len(present) == 1:
        return present[0]
    matching = [slot for slot in present if labels[slot] == hand]
    return matching[0] if len(matching) == 1 else None
