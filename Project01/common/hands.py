"""MediaPipe 손 검출과 왼손·오른손 배정.

검출 흐름
  BGR 프레임 → (필요하면 `detection_frame`으로 축소) → `detect_hands` → `Detection` 목록
  → `assign_hands`로 {"Left": ..., "Right": ...} 배정

좌우 라벨 규칙(`true_label`)
  MediaPipe Tasks(1.1.1 확인)는 입력 영상에 보이는 그대로의 실제 손을 라벨로 냅니다. 공식 테스트 이미지
  right_hands.jpg(오른손 손등)는 원본에서 Right, 좌우 반전하면 Left입니다. 예전 MediaPipe Hands 솔루션 문서의
  "셀카(좌우 반전) 영상 기준" 가정은 맞지 않습니다. 그래서 영상을 좌우 반전했을 때만 라벨을 뒤집어 실제 손으로
  되돌립니다. 이 규칙이 틀리면 손바닥 방향 부호도 뒤집힙니다(common.pose.palm_facing).

mediapipe·OpenCV는 함수 안에서 import합니다.
"""

from typing import Any, NamedTuple

from common.landmarks import mean_x


class Detection(NamedTuple):
    """손 하나의 검출 결과입니다. 튜플처럼 (label, landmarks, world, score)로 풀어 쓸 수도 있습니다."""
    label: str  # 실제 왼손/오른손 ("Left" | "Right"), true_label 규칙 적용 후
    landmarks: Any  # 이미지 정규화 좌표 21개
    world: Any  # world 좌표(미터) 21개
    score: float  # 좌우 분류 확신도. 손 검출 확신도가 아니므로 이 값으로 손을 거르지 않음


def create_detector(model):
    """VIDEO 모드 HandLandmarker를 만듭니다. `with create_detector(path) as detector:`로 쓰고 닫습니다.

    VIDEO 모드는 앞 프레임의 손 위치를 이어서 추적하므로, 매 프레임 손을 새로 찾는 IMAGE 모드보다 빠르고
    안정적입니다. 대신 detect_for_video에 넘기는 시각(ms)이 엄격히 증가해야 합니다(common.camera.VideoClock).
    """
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    if not model.is_file():  # 모델 파일이 없으면 MediaPipe 오류 대신 경로를 알려 줌
        raise FileNotFoundError("MediaPipe 모델이 없습니다: " + str(model))
    # 두 손까지, 검출·존재·추적 확신도 0.6 이상
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.VIDEO, num_hands=2,
        min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    return vision.HandLandmarker.create_from_options(options)


def true_label(label, mirrored):
    """MediaPipe 라벨을 실제 왼손/오른손으로 바꿉니다. 영상을 좌우 반전했을 때만 뒤집습니다(모듈 설명 참고)."""
    if mirrored:
        return "Left" if label == "Right" else "Right"
    return label


def detections_from_result(result, mirrored):
    """HandLandmarker 결과 → Detection 목록."""
    detections = []
    # 손마다 이미지 좌표, world 좌표, 좌우 분류가 같은 순서로 들어 있음
    for landmarks, world, handedness in zip(result.hand_landmarks, result.hand_world_landmarks, result.handedness):
        category = handedness[0]  # 가장 확신도가 높은 좌우 분류
        detections.append(Detection(true_label(category.category_name, mirrored), landmarks, world, category.score))
    return detections


def detect_hands(detector, frame, timestamp, mirrored):
    """BGR 프레임에서 손을 찾습니다. timestamp는 엄격히 증가하는 ms, mirrored는 프레임을 좌우 반전했는지입니다."""
    import cv2
    import mediapipe as mp

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # MediaPipe는 RGB 입력
    result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
    return detections_from_result(result, mirrored)


def detection_frame(frame, max_width):
    """손 검출에 넣을 영상을 돌려줍니다. 가로가 max_width보다 크면 비율을 유지해 줄입니다.

    landmark는 0~1 비율 좌표라 원본 해상도 화면에 그대로 쓸 수 있습니다. 검출 입력을 640 이하로 두면
    화면 해상도와 상관없이 검출 시간이 Jetson 640x480 시연과 같은 조건이 됩니다.
    """
    import cv2

    h, w = frame.shape[:2]
    if w <= max_width:  # 이미 충분히 작음: 그대로
        return frame
    return cv2.resize(frame, (max_width, max(1, round(h * max_width / w))), interpolation=cv2.INTER_AREA)


def assign_hands(detections):
    """{"Left": 검출, "Right": 검출}을 돌려줍니다.
    검출은 (label, landmarks, ...) 꼴이면 무엇이든 되고, 그대로 돌려줍니다.

    좌우는 라벨(손바닥 주름·손톱 같은 겉모습으로 MediaPipe가 판단)을 따릅니다. 카메라가 손 맞은편에 있으면
    화면 위치로 정한 좌우는 뒤바뀌기 때문입니다. 두 손에 같은 라벨이 붙었을 때만 화면 위치로 나눕니다
    (화면 왼쪽 손 = Left). 세 손 이상이면 앞의 두 손만 봅니다.
    배정된 키("Left"/"Right")와 검출의 label이 다를 수 있습니다(같은 라벨 두 손). 손바닥 방향 계산에는
    MediaPipe가 본 label을 써야 하므로 검출 객체를 그대로 넘깁니다.
    """
    if len(detections) >= 2:  # 두 손 이상: 앞의 두 손만 배정
        first, second = detections[:2]
        if first[0] != second[0]:  # 라벨이 다름: 라벨대로
            return {detection[0]: detection for detection in (first, second)}
        # 같은 라벨 두 손: 화면에서 왼쪽에 있는 손을 Left로
        left, right = sorted((first, second), key=lambda detection: mean_x(detection[1]))
        return {"Left": left, "Right": right}
    return {detection[0]: detection for detection in detections[:1]}  # 한 손 또는 없음: 라벨대로
