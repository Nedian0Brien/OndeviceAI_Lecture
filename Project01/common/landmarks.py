"""MediaPipe 손 landmark 번호와 좌표 계산 (표준 라이브러리만 사용).

MediaPipe Hand Landmarker는 손마다 21개 점을 냅니다. 번호는 손목 0에서 시작해 손가락마다 뿌리 → 끝 순서입니다.

    엄지 1 2 3 4 | 검지 5 6 7 8 | 중지 9 10 11 12 | 약지 13 14 15 16 | 소지 17 18 19 20
    손가락마다 MCP(뿌리 관절) → PIP → DIP → TIP(손끝)

좌표는 세 가지를 씁니다.
- 이미지 정규화 좌표: landmark.x, y는 영상 폭·높이에 대한 0~1 비율입니다. z는 손목 기준 상대 깊이입니다.
- 픽셀 좌표: x × 폭, y × 높이. 화면에 그리거나 2D 거리를 잴 때 씁니다(`to_pixels`).
- world 좌표: 손의 대략 중심을 원점으로 한 미터 단위 3D 좌표(hand_world_landmarks). 크기는 모델이 추정한
  값입니다. 같은 손 안의 거리(예: 엄지 끝~검지 끝)를 잴 때 씁니다.
  손마다 원점이 달라서 두 손 사이 거리는 잴 수 없습니다.

landmark 객체는 x, y, z 속성만 있으면 됩니다. MediaPipe 결과든 테스트용 SimpleNamespace든 같이 씁니다.
"""

import math


WRIST = 0
THUMB_TIP = 4
INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP = 5, 9, 13, 17
FINGER_MCPS = (INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)  # 검지·중지·약지·소지 뿌리
FINGER_TIPS = (8, 12, 16, 20)
FINGER_NAMES = ("검지", "중지", "약지", "소지")
# 손가락 끝마디(DIP→TIP) 선분. 엄지로 손끝을 꼬집을 때 닿는 부분임
DISTAL_SEGMENTS = ((7, 8), (11, 12), (15, 16), (19, 20))


# 화면에 손 골격을 그릴 때 잇는 landmark 쌍(손가락 사슬과 손바닥 둘레를 이웃한 점끼리 이음)
HAND_CONNECTIONS = tuple(
    (a, b)
    for chain in ((0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (5, 9, 10, 11, 12),
                  (9, 13, 14, 15, 16), (13, 17, 18, 19, 20), (0, 17))
    for a, b in zip(chain, chain[1:])
)


def xyz(point):
    """landmark 하나를 (x, y, z) 튜플로 바꿉니다. z가 없는 객체는 0으로 봅니다."""
    return (point.x, point.y, getattr(point, "z", 0.0))


def point_segment_distance(point, a, b):
    """점에서 선분 a-b까지의 최단 거리를 돌려줍니다. 2D·3D 모두 됩니다.

    점을 선분 위로 수직 투영하고(비율 t를 0~1로 자름), 투영점까지 거리를 잽니다.
    """
    ab = [y - x for x, y in zip(a, b)]
    length = sum(v * v for v in ab)  # 선분 길이의 제곱
    # 투영 비율 t: 길이가 0인 선분은 끝점 a까지의 거리
    t = 0.0 if length < 1e-12 else max(0.0, min(1.0, sum((p - x) * v for p, x, v in zip(point, a, ab)) / length))
    return math.dist(point, [x + t * v for x, v in zip(a, ab)])


def to_pixels(landmarks, width, height):
    """이미지 정규화 좌표 landmark 목록 → 픽셀 (x, y) 목록."""
    return [(p.x * width, p.y * height) for p in landmarks]


def mean_x(landmarks):
    """landmark들의 평균 x(정규화 좌표). 두 손 중 어느 쪽이 화면 왼쪽인지 비교할 때 씁니다."""
    return sum(p.x for p in landmarks) / len(landmarks)
