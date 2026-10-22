"""손가락 마디 12칸 천지인 자판과, 그 자판을 손 위에 그리는 HUD.

엄지 마디 앱(project01.py)과 꼬집기 키보드(pinch_keyboard.py)가 함께 쓴다.

자판 (손바닥이 카메라를 향한 자세. 지움은 키가 아니라 주먹이다)
              검지    중지    약지    소지
    끝마디    ㅣ      ㆍ      ㅡ      ㅇㅁ
    중간마디  ㄱㅋㄲ  ㄴㄹ    ㄷㅌㄸ  공백
    뿌리마디  ㅂㅍㅃ  ㅅㅎㅆ  ㅈㅊㅉ  확정
검지~약지는 기존 천지인 배열(모음 / ㄱㄴㄷ / ㅂㅅㅈ)을 그대로 두고, 소지에 ㅇ·공백·확정을 둔다.
키는 손가락 기준이라 왼손과 오른손에서 같은 손가락에 같은 키가 놓인다(화면의 좌우 순서는 손마다 반대).

화면에서 각 키는 마디(선분)의 중점에 작은 원형 유리 버튼으로 그린다. 손바닥 가운데에는 가장 가까운 키를
누르면 들어갈 자모를 크게 보여준다.
"""

from dataclasses import dataclass, field
import math
import time
from typing import Optional

from common.hud import DARK_INK, MUTED, WHITE, HudCanvas, ellipsize_left, hud_font, hud_text_width, rounded_rectangle
from common.landmarks import FINGER_MCPS, HAND_CONNECTIONS, SEGMENTS, THUMB_TIP, WRIST, palm_size, to_pixels


# (마디 이름, 천지인 키, 화면 라벨). 마디 이름은 common.landmarks.SEGMENTS 규칙(I1 = 검지 끝마디 등)이다.
KEY_MAP = (
    ("I1", "1", "ㅣ"), ("M1", "2", "ㆍ"), ("R1", "3", "ㅡ"), ("P1", "0", "ㅇㅁ"),
    ("I2", "4", "ㄱㅋㄲ"), ("M2", "5", "ㄴㄹ"), ("R2", "6", "ㄷㅌㄸ"), ("P2", "SPACE", "공백"),
    ("I3", "7", "ㅂㅍㅃ"), ("M3", "8", "ㅅㅎㅆ"), ("R3", "9", "ㅈㅊㅉ"), ("P3", "COMMIT", "확정"),
)
KEY_OF_SEGMENT = {segment: key for segment, key, _ in KEY_MAP}
MIN_PALM_PIXELS = 35  # 손바닥이 이보다 작게 보이면(멀리 있으면) 키를 놓지 않는다.
OUTLINE = (28, 29, 34)  # 글자 외곽선 색. 투명도는 쓰는 곳마다 다르다.


def key_targets(landmarks, width, height):
    """12개 마디의 중점에 키를 놓는다.

    반환: (points, targets)
      points: 21개 landmark 픽셀 좌표 뒤에 마디 중점 12개를 덧붙인 목록. 키 위치는 points[번호]로 찾는다.
      targets: 키마다 (키, 라벨, points 번호, 마디 길이, 엄지 끝까지 2D 거리)
    엄지 거리는 가장 가까운 키를 강조하는 데만 쓰고, 입력 판정에는 쓰지 않는다.
    """
    points = to_pixels(landmarks, width, height)
    if palm_size(points) < MIN_PALM_PIXELS:
        return points, []
    targets = []
    for segment, key, label in KEY_MAP:
        a, b = SEGMENTS[segment]
        points.append(((points[a][0] + points[b][0]) / 2, (points[a][1] + points[b][1]) / 2))
        targets.append((key, label, len(points) - 1, math.dist(points[a], points[b]),
                        math.dist(points[THUMB_TIP], points[-1])))
    return points, targets


@dataclass
class HandView:
    """손 하나를 그리는 데 필요한 정보. 판정기(앱)가 만들고 draw_ui가 그린다."""
    label: str  # "Left" | "Right"
    points: list  # key_targets의 points (픽셀)
    targets: list  # key_targets의 targets. 비어 있으면 키를 그리지 않는다(선택 손 등).
    nearest: Optional[str]  # 강조할 키(엄지·커서와 가장 가까운 키)
    pose: str = "open"
    armed: bool = False  # 다음 입력을 받을 준비가 됐는지(엄지를 뗐는지)
    fist_progress: float = 0.0  # 주먹 유지 진행 0~1
    last_press: tuple = (None, float("-inf"))  # (마지막으로 입력한 키, 시각). 잠깐 밝게 점등한다.
    focus: dict = field(default_factory=dict)  # 키별 강조 정도 0~1(부드럽게 커졌다 작아진다)
    distances: Optional[list] = None  # 엄지 끝 ~ 12개 마디 world 거리(m), KEY_MAP 순서. 진단 표시·기록용
    thumb: bool = True  # 엄지 끝 표시를 그릴지. 꼬집기 키보드의 선택 손은 커서로 대신 그린다.


def key_diameter(targets, points, index, focus, scale):
    """키 버튼 지름. 가장 가까운 이웃 키 간격의 78%(18~32px × 배율), 강조되면 최대 30% 커진다."""
    spacing = min((math.dist(points[index], points[other]) for _, _, other, _, _ in targets
                   if other != index), default=40 * scale)
    base = max(18 * scale, min(32 * scale, spacing * 0.78))
    return base * (1 + 0.30 * focus)


def palm_center(points):
    """손바닥 가운데: 손목과 네 손가락 뿌리 평균의 중점."""
    return tuple((points[WRIST][axis] + sum(points[index][axis] for index in FINGER_MCPS) / 4) / 2
                 for axis in (0, 1))


def draw_ui(frame, ime, hands, status, fonts, paused, show_help=False, notice_since=None, tap_enter=0.012,
            hint=None, help_lines=(), cursors=()):
    """영상 위에 손 위 키, 손바닥 미리보기, 한 줄 입력창, 안내를 합성한다.

    ime: 한글 조합기(CheonjiinIME). hands: HandView 목록.
    status, notice_since: "저장"·"누르기 판정"으로 시작하는 알림은 notice_since부터 2.5초 보여준다.
    tap_enter: 엄지 거리 진단 표시를 흰색으로 바꾸는 기준(m). 엄지 마디 앱의 누르기 판정 거리다.
    hint: 입력이 비었을 때 입력창 문구. help_lines: H 도움말 줄(첫 줄은 제목).
    cursors: (x, y, 진행 0~1, 보조 문구) 목록. 꼬집기 키보드의 커서와 오므린 정도를 그린다.
    """
    canvas = HudCanvas(frame, fonts)
    w, h, scale, stroke = canvas.w, canvas.h, canvas.scale, canvas.stroke
    small, normal, large = canvas.small, canvas.normal, canvas.large
    draw = canvas.draw
    now = time.monotonic()
    margin = 16 * scale

    # 위: 한 줄 입력창. 전체 텍스트는 조합기에 있고, 현재 줄의 끝부분만 보여준다.
    text = ime.text.split("\n")[-1] if ime.text else (hint or "마디를 눌러 입력 · 주먹은 지움")
    input_font = large if ime.text else normal
    text = ellipsize_left(text, input_font, min(w - 2 * margin, 460 * scale) - 36 * scale)
    input_width = min(w - 2 * margin, max(132 * scale, hud_text_width(text, input_font) + 38 * scale))
    input_height = (40 if ime.text else 34) * scale
    input_x = (w - input_width) / 2
    canvas.glass((input_x, margin, input_x + input_width, margin + input_height))
    canvas.write((w / 2, margin + input_height / 2), text, input_font, WHITE, "mm")
    if ime.text and not paused and not show_help and int(now * 2) % 2 == 0:  # 0.5초 간격 깜빡이는 커서
        cursor_x = w / 2 + hud_text_width(text, input_font) / 2 + 4 * scale
        draw.line((cursor_x, margin + 12 * scale, cursor_x, margin + input_height - 12 * scale),
                  fill=MUTED, width=stroke)

    # 손: 옅은 손바닥 면, 골격선, 주요 관절 점, 왼손/오른손 이름
    for hand in hands:
        points = hand.points
        palm = [points[i] for i in (0, 1, 2, 5, 9, 13, 17)]
        draw.polygon(palm, fill=(225, 234, 244, 12))
        for a, b in HAND_CONNECTIONS:
            draw.line((points[a], points[b]), fill=(237, 242, 250, 100), width=stroke)
        for index in (0, 1, 2, 3, 8, 12, 16, 20):
            x, y = points[index]
            r = 1.4 * scale
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(245, 248, 252, 165))
        x, y = points[WRIST]
        canvas.write((min(max(x, 24 * scale), w - 24 * scale), min(y + 12 * scale, h - 20 * scale)),
                     "오른손" if hand.label == "Right" else "왼손", small, MUTED, "ma",
                     stroke_width=stroke, stroke_fill=OUTLINE + (160,))

    # 손 위 키. 주먹을 쥐는 동안은 손가락이 겹쳐 보이므로 숨긴다.
    for hand in hands:
        points = hand.points
        if hand.pose == "fist":
            continue
        # 강조된 키를 마지막에 그려야 커진 키가 이웃 키에 가려지지 않는다.
        for key, label, index, _, _ in sorted(hand.targets, key=lambda target: hand.focus.get(target[0], 0.0)):
            x, y = points[index]
            focus = 0.0 if paused or show_help else hand.focus.get(key, 0.0)
            diameter = key_diameter(hand.targets, points, index, focus, scale)
            selected = key == hand.nearest and not paused and not show_help
            recent = key == hand.last_press[0] and 0 <= now - hand.last_press[1] < 0.28  # 입력 직후 0.28초 점등
            cx = min(max(x, diameter / 2 + 2), w - diameter / 2 - 2)  # 화면 밖으로 나가지 않게
            cy = min(max(y, diameter / 2 + 2), h - diameter / 2 - 2)
            canvas.glass((cx - diameter / 2, cy - diameter / 2, cx + diameter / 2, cy + diameter / 2),
                         circle=True, hover=selected, pressed=recent, raised=True)
            display = label[:2]  # "ㄱㅋㄲ" → "ㄱㅋ"
            size = max(8, round((15 if len(display) == 1 else 12) * scale * (1 + 0.20 * focus)))
            key_font = hud_font(fonts[1], size)
            while size > max(8, round(8 * scale)) and hud_text_width(display, key_font) > diameter * 0.74:
                size -= 1
                key_font = hud_font(fonts[1], size)
            ink = DARK_INK if recent else (MUTED if paused else WHITE)
            canvas.write((cx, cy), display, key_font, ink, "mm")

    # 손바닥 가운데: 가장 가까운 키를 누르면 들어갈 자모. 주먹을 쥐는 중이면 "지움"과 진행 표시.
    for hand in hands:
        deleting = hand.fist_progress > 0 or hand.pose == "fist"
        key = "BACK" if deleting else hand.nearest
        if key is None or not hand.targets:
            continue
        points = hand.points
        cx, cy = palm_center(points)
        diameter = min(62 * scale, max(40 * scale, palm_size(points) * 0.38))
        cx = min(max(cx, diameter / 2 + 3), w - diameter / 2 - 3)
        cy = min(max(cy, diameter / 2 + 3), h - diameter / 2 - 3)
        recent = 0 <= now - hand.last_press[1] < 0.28
        glyph = ime.preview_label(key, now)
        canvas.glass((cx - diameter / 2, cy - diameter / 2, cx + diameter / 2, cy + diameter / 2),
                     circle=True, hover=hand.armed or deleting, pressed=recent, raised=True)
        font = hud_font(fonts[2], max(10, round(diameter * (0.42 if len(glyph) == 1 else 0.29))))
        ink = DARK_INK if recent else (WHITE if hand.armed or deleting else MUTED)
        canvas.write((cx, cy), glyph, font, ink, "mm")
        if hand.fist_progress > 0 and not paused and not show_help:
            canvas.progress_arc((cx, cy), diameter / 2 + 3 * scale, hand.fist_progress, WHITE,
                                max(2, round(2 * scale)))

    # 엄지 끝 표시. 진단용으로 가장 가까운 마디까지 world 거리를 함께 적는다(판정 거리 안이면 흰색).
    for hand in hands:
        if not hand.thumb:
            continue
        x, y = hand.points[THUMB_TIP]
        if hand.distances is not None:
            gap = min(hand.distances)
            canvas.write((x + 11 * scale, y - 6 * scale), f"{gap * 1000:.0f}mm", small,
                         WHITE if gap <= tap_enter else MUTED, stroke_width=stroke, stroke_fill=OUTLINE + (200,))
        canvas.ring((x, y), 7 * scale, WHITE)
        r = 2 * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)

    # 꼬집기 키보드의 커서: 링, 오므린 정도(호), 꼬집기 비율 숫자
    for x, y, progress, caption in cursors:
        canvas.ring((x, y), 9 * scale, WHITE)
        if progress > 0:
            canvas.progress_arc((x, y), 13 * scale, progress, WHITE, max(2, round(2 * scale)))
        if caption:
            canvas.write((x + 16 * scale, y - 6 * scale), caption, small, MUTED,
                         stroke_width=stroke, stroke_fill=OUTLINE + (200,))

    # 아래: 상태가 달라질 때만 짧은 안내. 정상 입력 준비 상태에서는 숨긴다.
    message = ""
    if paused:
        message = "일시정지 · P로 재개"
    elif not any(hand.targets for hand in hands):
        message = "손을 더 가까이" if hands else "손바닥을 보여주세요"
    elif not any(hand.armed for hand in hands if hand.targets):
        message = "엄지를 손가락에서 떼세요"
    if notice_since is not None and now - notice_since < 2.5 and status.startswith(("저장", "누르기 판정")):
        message = status
    if message and not show_help:
        message_width = min(w - 2 * margin, hud_text_width(message, small) + 28 * scale)
        top = h - 58 * scale
        canvas.glass(((w - message_width) / 2, top, (w + message_width) / 2, top + 26 * scale))
        canvas.write((w / 2, top + 13 * scale), message, small, MUTED, "mm")
    canvas.write((w - margin, h - 22 * scale), "H 도움말", small, MUTED, "ra",
                 stroke_width=stroke, stroke_fill=OUTLINE + (115,))

    # H 도움말: 화면을 어둡게 덮고 가운데 상자에 단축키와 진단 정보를 보여준다.
    if show_help:
        draw.rectangle((0, 0, w, h), fill=(16, 17, 21, 160))
        box_width = min(w - 2 * margin, 440 * scale)
        box = ((w - box_width) / 2, 60 * scale, (w + box_width) / 2, h - 60 * scale)
        rounded_rectangle(draw, box, 24 * scale, fill=(38, 40, 47, 235), outline=(255, 255, 255, 65), width=stroke)
        for i, line in enumerate(help_lines):
            canvas.write((box[0] + 22 * scale, box[1] + (22 + i * 34) * scale), line,
                         normal if i == 0 else small, WHITE if i == 0 else MUTED)

    return canvas.finish()
