"""손 자세(편 손 · 접는 중 · 주먹)와 손바닥 방향 판정 (표준 라이브러리만 사용).

자세 판정은 두 가지가 있고, 결과가 조금 다르다. 앱마다 쓰는 쪽이 정해져 있으므로 하나로 합치지 않는다.

- `pose_from_image`: 이미지 좌표(픽셀 + 상대 깊이 z)로 PIP 관절 각도와 손끝~뿌리 거리를 함께 본다.
  수업 practice 코드의 방식이다. 엄지 마디 앱, 꼬집기 키보드, 손끝 꼬집기 앱의 주먹 판정이 이것을 쓴다.
- `finger_angles` + `pose_from_angles`: world 좌표(3D)의 PIP 관절 각도만 본다. 화면 쪽으로 굽힌 손가락도
  각도가 잡힌다. 손끝 꼬집기 앱은 이 각도로 손가락별 "굽음" 여부(꼬집는 중 주먹으로 바뀌는지)를 본다.

자세 이름: "open"(편 손), "fist"(주먹), "closing"(그 사이 또는 판단 불가).
"""

import math

from common.landmarks import FINGER_MCPS, INDEX_MCP, PINKY_MCP, WRIST, xyz


# world 좌표 PIP 각도 기준(도). 130° 이하면 굽음, 155° 이상이면 폄. 그 사이는 어느 쪽도 아니다.
CURL_ANGLE, EXTEND_ANGLE = 130.0, 155.0


def pose_from_image(landmarks, width, height):
    """이미지 좌표로 편 손 / 접는 중 / 주먹을 구분한다(practice 방식).

    손가락마다 PIP 관절 각도(MCP-PIP-DIP)와 손끝이 뿌리에서 얼마나 멀리 뻗었는지(reach)를 본다.
    - 폄: 각도 150° 이상이고 reach가 첫 마디 길이의 1.6배 이상
    - 굽음: 각도 135° 이하이고 reach가 1.7배 이하
    네 손가락 중 셋 이상 굽고 펴진 손가락이 없으면 주먹, 셋 이상 펴지고 굽은 손가락이 없으면 편 손이다.
    z는 x와 같은 축척(폭)으로 곱해 픽셀 단위로 맞춘다.
    """
    points = [(p.x * width, p.y * height, getattr(p, "z", 0.0) * width) for p in landmarks]
    extended = curled = 0
    for base in FINGER_MCPS:
        mcp, pip, dip, tip = points[base:base + 4]
        first = tuple(a - b for a, b in zip(mcp, pip))
        second = tuple(a - b for a, b in zip(dip, pip))
        length1, length2 = math.dist(mcp, pip), math.dist(dip, pip)
        if min(length1, length2) < 2:  # 마디가 2픽셀보다 짧으면 각도를 믿을 수 없다.
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


def finger_angles(world):
    """손가락별 PIP 관절 각도(도), 검지·중지·약지·소지 순. 180°에 가까울수록 펴져 있다.

    world 좌표(3D)라 카메라 쪽으로 굽힌 손가락도 각도가 잡힌다.
    """
    angles = []
    for base in FINGER_MCPS:
        mcp, pip, dip = (xyz(world[base + i]) for i in range(3))
        first = [a - b for a, b in zip(mcp, pip)]
        second = [a - b for a, b in zip(dip, pip)]
        norm = math.hypot(*first) * math.hypot(*second)
        if norm < 1e-12:
            angles.append(180.0)
            continue
        cosine = sum(a * b for a, b in zip(first, second)) / norm
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
    return tuple(angles)


def _joint_angle(a, b, c):
    """b에서 a와 c로 가는 두 벡터 사이 각도(도). 길이가 0이면 180°(편 상태)로 본다."""
    first = [x - y for x, y in zip(a, b)]
    second = [x - y for x, y in zip(c, b)]
    norm = math.hypot(*first) * math.hypot(*second)
    if norm < 1e-12:
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(first, second)) / norm))))


def finger_flex(world):
    """손가락별 굽힘(도), 검지·중지·약지·소지 순: MCP·PIP·DIP 세 관절의 (180° - 관절 각도) 합. 0이면 펴짐.

    MCP 각도는 손목-MCP-PIP로 잰다. world 좌표(3D)라 카메라 쪽으로 굽힌 손가락도 잡힌다.
    """
    wrist = xyz(world[WRIST])
    flex = []
    for base in FINGER_MCPS:
        mcp, pip, dip, tip = (xyz(world[base + i]) for i in range(4))
        flex.append((180.0 - _joint_angle(wrist, mcp, pip)) + (180.0 - _joint_angle(mcp, pip, dip))
                    + (180.0 - _joint_angle(pip, dip, tip)))
    return tuple(flex)


def pose_from_angles(angles):
    """`finger_angles` 결과로 자세를 정한다. 기준은 `pose_from_image`와 같은 3개 손가락 규칙이다."""
    curled = sum(angle <= CURL_ANGLE for angle in angles)
    extended = sum(angle >= EXTEND_ANGLE for angle in angles)
    if curled >= 3 and extended == 0:
        return "fist"
    if extended >= 3 and curled == 0:
        return "open"
    return "closing"


def palm_roll(landmarks, width=1.0, height=1.0):
    """손의 긴 축을 기준으로 한 기울기(도). 엄지 쪽(검지 뿌리)이 카메라에 가까우면 양수, 소지 쪽이 가까우면 음수.

    소지 뿌리(17) → 검지 뿌리(5) 벡터가 이미지 평면과 이루는 각도다. 이미지 landmark의 z는 손목 기준 상대 깊이로
    작을수록 카메라에 가깝고, x와 같은 축척(폭)이다. 왼손·오른손, 좌우 반전과 관계없이 같은 부호다.
    """
    dx = (landmarks[INDEX_MCP].x - landmarks[PINKY_MCP].x) * width
    dy = (landmarks[INDEX_MCP].y - landmarks[PINKY_MCP].y) * height
    dz = (landmarks[INDEX_MCP].z - landmarks[PINKY_MCP].z) * width
    return math.degrees(math.atan2(-dz, math.hypot(dx, dy)))


def palm_facing(landmarks, label, mirrored=False, width=1.0, height=1.0):
    """손바닥이 카메라를 향하면 양수, 손등이면 음수(-1~1).

    손목→검지 뿌리, 손목→소지 뿌리 두 벡터의 2D 외적(사잇각의 sin)이다.
    - 이미지 평면 안에서 손을 돌려도 값은 변하지 않는다.
    - 거울상이 되면 부호가 바뀐다. 왼손과 오른손은 서로 거울상이고, 좌우 반전 영상도 거울상이다.
    그래서 label(실제 왼손/오른손)과 mirrored(영상을 좌우 반전했는지)로 부호를 맞춘다.
    label이 실제와 반대면 손바닥과 손등이 뒤바뀌므로, label은 `common.hands.true_label` 규칙을 따른
    값이어야 한다(2026-10-22: 이 규칙이 틀려 손바닥이 손등으로 판정된 버그가 있었다).
    width·height는 화면 비율(가로가 긴 영상)을 반영하려고 곱한다.
    """
    wrist = landmarks[WRIST]
    a = ((landmarks[INDEX_MCP].x - wrist.x) * width, (landmarks[INDEX_MCP].y - wrist.y) * height)
    b = ((landmarks[PINKY_MCP].x - wrist.x) * width, (landmarks[PINKY_MCP].y - wrist.y) * height)
    norm = math.hypot(*a) * math.hypot(*b)
    if norm < 1e-12:
        return 0.0
    value = -(a[0] * b[1] - a[1] * b[0]) / norm  # 오른손 손바닥(엄지가 보는 사람 기준 오른쪽)이 양수
    if label == "Left":
        value = -value
    if mirrored:
        value = -value
    return value
