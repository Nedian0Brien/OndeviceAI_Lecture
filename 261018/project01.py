"""엄지로 손가락 마디를 누르면 입력하고, 주먹을 쥐면 지우는 천지인 키보드.

실행: python 261018/project01.py
카메라 없이 조합 확인: python 261018/project01.py --demo
"""

import argparse
from collections import Counter
import json
from dataclasses import dataclass, field
from functools import lru_cache
import math
from pathlib import Path
import time
from typing import Optional

from cheonjiin import CheonjiinIME, FistHold


ROOT = Path(__file__).resolve().parents[1]
# 엄지로 누르는 12개 마디(tap_detect.SEGMENTS: 검지 I·중지 M·약지 R·소지 P × 끝 1·중간 2·뿌리 3)와
# 천지인 키. 검지~약지는 기존 배열(모음 / ㄱㄴㄷ / ㅂㅅㅈ)을 그대로 두고, 소지에 ㅇ·공백·확정을 둔다.
# 지움은 주먹이다.
KEY_MAP = (
    ("I1", "1", "ㅣ"), ("M1", "2", "ㆍ"), ("R1", "3", "ㅡ"), ("P1", "0", "ㅇㅁ"),
    ("I2", "4", "ㄱㅋㄲ"), ("M2", "5", "ㄴㄹ"), ("R2", "6", "ㄷㅌㄸ"), ("P2", "SPACE", "공백"),
    ("I3", "7", "ㅂㅍㅃ"), ("M3", "8", "ㅅㅎㅆ"), ("R3", "9", "ㅈㅊㅉ"), ("P3", "COMMIT", "확정"),
)
KEY_OF_SEGMENT = {segment: key for segment, key, _ in KEY_MAP}
# 누르기 판정 기본값: world 좌표(m)에서 엄지 끝과 마디 선분 사이 거리. tap_detect.Seg 참고.
TAP_ENTER, TAP_EXIT, TAP_DWELL = 0.012, 0.020, 0.05
HAND_CONNECTIONS = tuple(
    (a, b)
    for chain in ((0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (5, 9, 10, 11, 12),
                  (9, 13, 14, 15, 16), (13, 17, 18, 19, 20), (0, 17))
    for a, b in zip(chain, chain[1:])
)
GSTREAMER = (
    "v4l2src device=/dev/video0 ! "
    "video/x-raw,format=YUY2,width=640,height=480,framerate=30/1 ! "
    "videoconvert ! video/x-raw,format=BGR ! "
    "queue leaky=downstream max-size-buffers=1 ! "
    "appsink drop=true max-buffers=1 sync=false"
)


def key_targets(landmarks, width, height):
    """12개 마디의 중점에 키를 놓는다.

    points 뒤에 마디 중점 12개를 덧붙이고, 각 키는 (키, 라벨, points 번호, 마디 길이, 엄지 거리)다.
    엄지 거리는 화면 강조용 2D 거리이며 입력 판정에는 쓰지 않는다.
    """
    from tap_detect import SEGMENTS

    points = [(p.x * width, p.y * height) for p in landmarks]
    palm_size = max(math.dist(points[5], points[17]), math.dist(points[0], points[9]))
    if palm_size < 35:  # 너무 작은 손은 판정하지 않는다.
        return points, []
    targets = []
    for segment, key, label in KEY_MAP:
        a, b = SEGMENTS[segment]
        points.append(((points[a][0] + points[b][0]) / 2, (points[a][1] + points[b][1]) / 2))
        targets.append((key, label, len(points) - 1, math.dist(points[a], points[b]),
                        math.dist(points[4], points[-1])))
    return points, targets


def classify_hand_pose(landmarks, width, height):
    """practice의 관절 각도에 손끝-뿌리 거리를 더해 편 손/접는 중/주먹을 구분한다."""
    points = [(p.x * width, p.y * height, getattr(p, "z", 0.0) * width) for p in landmarks]
    extended = curled = 0
    for base in (5, 9, 13, 17):
        mcp, pip, dip, tip = points[base:base + 4]
        first = tuple(a - b for a, b in zip(mcp, pip))
        second = tuple(a - b for a, b in zip(dip, pip))
        length1, length2 = math.dist(mcp, pip), math.dist(dip, pip)
        if min(length1, length2) < 2:
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


@dataclass
class HandView:
    label: str
    points: list
    targets: list
    nearest: Optional[str]
    pose: str = "open"
    armed: bool = False  # 엄지가 손가락에서 떨어져 다음 누르기를 받을 수 있는 상태
    fist_progress: float = 0.0
    last_press: tuple = (None, float("-inf"))
    focus: dict = field(default_factory=dict)
    distances: Optional[list] = None  # 엄지 끝 ~ 12개 마디 world 거리(m), KEY_MAP 순서. 진단 표시·기록용
    thumb: bool = True  # 엄지 끝 표시를 그릴지. 꼬집기 입력기의 선택 손은 커서로 대신 그린다.


class HandKeyboardTracker:
    """왼손·오른손마다 누르기 검출기와 주먹(지움) 상태를 따로 유지한다."""

    def __init__(self, mode="both", tap_enter=TAP_ENTER, tap_exit=TAP_EXIT, tap_dwell=TAP_DWELL, fist_hold=0.18):
        from tap_detect import Seg

        self.mode = "both" if mode == "auto" else mode
        self.taps = {label: Seg("world", tap_enter, tap_exit, tap_dwell) for label in ("Left", "Right")}
        self.fists = {label: FistHold(fist_hold) for label in self.taps}
        self.last_press = {label: (None, float("-inf")) for label in self.taps}
        self.focus = {label: {} for label in self.taps}
        self.last_time = None

    def set_tap(self, enter, exit):
        for tap in self.taps.values():
            tap.enter, tap.exit = enter, exit
        self.reset()

    def set_fist_hold(self, hold):
        for fist in self.fists.values():
            fist.hold = hold
        self.reset()

    def reset(self):
        for label in self.taps:
            self.lost(label)

    def lost(self, label):
        self.taps[label].reset()
        self.fists[label].lost()

    def update(self, detections, width, height, now, enabled=True):
        """detections: (좌우 라벨, 이미지 landmark, world landmark). 이벤트는 (라벨, 키), 지움은 BACK."""
        from tap_detect import HandFrame, as_points

        detections = sorted(((label, landmarks, world) for label, landmarks, world in detections
                             if label in self.taps and (self.mode == "both" or label.lower() == self.mode)),
                            key=lambda detection: detection[0])
        counts = Counter(label for label, _, _ in detections)
        for label in self.taps:
            if counts[label] != 1:
                self.lost(label)
                self.focus[label] = {}
        # 80ms의 짧은 보간으로 가까운 키의 확대가 프레임마다 튀지 않게 한다.
        blend = 1.0 if self.last_time is None else 1 - math.exp(-max(0, now - self.last_time) / 0.08)
        self.last_time = now
        views, events = [], []
        for label, landmarks, world in detections:
            distances = None
            points, targets = key_targets(landmarks, width, height)
            pose = classify_hand_pose(landmarks, width, height)
            nearest = min(targets, key=lambda target: target[4])[0] if targets else None
            if not enabled or not targets or counts[label] != 1:
                self.lost(label)
            elif pose == "fist":
                # 주먹을 쥐는 동안 엄지가 마디에 닿아도 입력하지 않고, 손을 펴서 엄지를 떼야 다시 입력한다.
                self.taps[label].reset()
                if self.fists[label].update(pose, now):
                    events.append((label, "BACK"))
                    self.last_press[label] = ("BACK", now)
            else:
                self.fists[label].update(pose, now)
                tap = self.taps[label]
                distances = tap.features(HandFrame(as_points(landmarks), as_points(world), width, height))
                segment = tap.step(now, distances)
                if segment is not None:
                    key = KEY_OF_SEGMENT[segment]
                    events.append((label, key))
                    self.last_press[label] = (key, now)
            self.focus[label] = {
                key: self.focus[label].get(key, 0.0) + blend * ((1.0 if key == nearest else 0.0) - self.focus[label].get(key, 0.0))
                for key, _, _, _, _ in targets
            }
            views.append(HandView(label, points, targets, nearest, pose, self.taps[label].armed,
                                  self.fists[label].progress, self.last_press[label], self.focus[label],
                                  None if distances is None else [float(d) for d in distances]))
        return views, events


def key_diameter(targets, points, index, focus, scale):
    spacing = min((math.dist(points[index], points[other]) for _, _, other, _, _ in targets
                   if other != index), default=40 * scale)
    base = max(18 * scale, min(32 * scale, spacing * 0.78))
    return base * (1 + 0.30 * focus)


def palm_center(points):
    return tuple((points[0][axis] + sum(points[index][axis] for index in (5, 9, 13, 17)) / 4) / 2
                 for axis in (0, 1))


def find_font(requested):
    if requested:
        path = Path(requested).expanduser()
        if not path.is_file():
            raise FileNotFoundError("한글 폰트 파일이 없습니다: " + str(path))
        return path
    candidates = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    )
    for name in candidates:
        if Path(name).is_file():
            return Path(name)
    raise FileNotFoundError("한글 폰트를 찾지 못했습니다. --font /경로/폰트.ttf 를 지정하세요.")


def enhance_frame(frame, clahe):
    """practice의 LAB 명도 보정과 Gaussian 필터를 적용한다."""
    import cv2

    light, a, b = cv2.split(cv2.cvtColor(frame, cv2.COLOR_BGR2LAB))
    enhanced = cv2.cvtColor(cv2.merge((clahe.apply(light), a, b)), cv2.COLOR_LAB2BGR)
    return cv2.GaussianBlur(enhanced, (3, 3), 0)


def skin_overlay(frame, points):
    """손바닥에서 색을 샘플링해 손 주변의 유사 색 영역/윤곽선을 시각화한다.

    조명이나 피부색에 따라 마스크가 달라지므로 이 결과로 키 입력을 막지는 않는다.
    """
    import cv2
    import numpy as np

    lab = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2LAB)
    h, w = frame.shape[:2]
    palm = np.array([points[i] for i in (0, 5, 9, 13, 17)])
    x, y = np.mean(palm, axis=0).astype(int)
    x, y = int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))
    patch = lab[max(0, y - 5):min(h, y + 6), max(0, x - 5):min(w, x + 6)]
    _, a, b = np.median(patch.reshape(-1, 3), axis=0)
    lower = np.array([0, max(0, a - 18), max(0, b - 22)], dtype=np.uint8)
    upper = np.array([255, min(255, a + 18), min(255, b + 22)], dtype=np.uint8)
    mask = cv2.inRange(lab, lower, upper)
    region = np.zeros((h, w), dtype=np.uint8)
    hull = cv2.convexHull(np.array(points, dtype=np.int32))
    cv2.fillConvexPoly(region, hull, 255)
    mask = cv2.bitwise_and(mask, region)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) >= 30]
    tint = frame.copy()
    tint[mask > 0] = (95, 185, 95)
    overlay = cv2.addWeighted(frame, 0.75, tint, 0.25, 0)
    cv2.drawContours(overlay, contours, -1, (110, 230, 130), 1)
    return overlay


@lru_cache(maxsize=96)
def hud_font(font, size):
    # 손 크기에 따른 글꼴을 캐시해 프레임마다 폰트 파일을 다시 읽지 않는다.
    return font.font_variant(size=size)


@lru_cache(maxsize=1024)
def hud_text_width(text, font):
    return font.getlength(text)


@lru_cache(maxsize=384)
def hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill):
    """변하지 않는 한글은 작은 RGBA 이미지로 재사용한다."""
    from PIL import Image, ImageDraw

    left, top, right, bottom = font.getbbox(text, anchor=anchor, stroke_width=stroke_width)
    sprite = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(sprite).text((-left, -top), text, font=font, fill=fill, anchor=anchor,
                               stroke_width=stroke_width, stroke_fill=stroke_fill)
    return sprite, left, top


def rounded_rectangle(draw, bounds, radius, **style):
    """Pillow rounded_rectangle을 안전하게 그린다.

    Pillow 12.3은 좌표가 소수이고 폭이 반지름의 2배에 가까우면 ValueError를 낸다(무작위 2만 회 중 8%).
    좌표를 정수로 반올림하고 반지름을 짧은 변의 절반 이하로 제한하면 발생하지 않는다.
    """
    x1, y1, x2, y2 = (round(v) for v in bounds)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=max(0, min(round(radius), (x2 - x1) // 2, (y2 - y1) // 2)),
                           **style)


def blur_under(picture, mask, pad=8):
    """mask가 칠해진 영역(경계 상자 + pad)만 1/3 축소 · Gaussian blur · 확대해 picture에 합성한다.

    유리 재질 아래 영상만 흐리게 하는 용도다. 프레임 전체를 처리하던 방식보다 640x480에서 약 2.6배,
    1920x1080에서 약 2.7배 빠르다(Jetson 저해상도 시연과 macOS 고해상도 모두 해당).
    """
    from PIL import Image, ImageFilter

    box = mask.getbbox()
    if box is None:
        return picture
    x1, y1 = max(0, box[0] - pad), max(0, box[1] - pad)
    x2, y2 = min(picture.width, box[2] + pad), min(picture.height, box[3] + pad)
    region = picture.crop((x1, y1, x2, y2))
    factor = max(1, min(3, region.width, region.height))
    blurred = region.reduce(factor).filter(ImageFilter.GaussianBlur(2)).resize(region.size, Image.BILINEAR)
    picture.paste(Image.composite(blurred, region, mask.crop((x1, y1, x2, y2))), (x1, y1))
    return picture


def draw_ui(frame, ime, hands, status, fonts, fps, paused, enhanced=False, show_mask=False,
            show_help=False, notice_since=None, fist_hold=0.18, tap_enter=TAP_ENTER,
            hint=None, help_lines=None, cursors=()):
    """영상 위 작은 원형 유리 키와 한 줄 입력 캡슐을 합성한다.

    hint: 입력이 비었을 때 입력창 문구. help_lines: H 도움말 내용.
    cursors: (x, y, 진행 0~1, 보조 문구) 목록. 꼬집기 입력기의 커서와 오므린 정도를 그린다.
    """
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    h, w = frame.shape[:2]
    scale = min(w / 640, h / 480)
    small, normal, large = (hud_font(font, max(8, round(size * scale)))
                            for font, size in zip(fonts, (11, 14, 21)))
    picture = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", picture.size)
    draw = ImageDraw.Draw(overlay)
    material_mask = Image.new("L", picture.size)
    mask_draw = ImageDraw.Draw(material_mask)
    now = time.monotonic()
    margin = 16 * scale
    white = (250, 250, 252, 255)
    muted = (230, 231, 236, 215)
    stroke = max(1, round(scale))

    def write(position, text, font, fill, anchor="la", stroke_width=0, stroke_fill=None):
        sprite, left, top = hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill)
        overlay.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))

    def glass(bounds, circle=False, hover=False, pressed=False):
        """배경 blur, 중성 tint, 얇은 반사광. 실제 blur는 마스크 안에만 적용한다."""
        x1, y1, x2, y2 = bounds
        radius = (y2 - y1) / 2
        shadow = (x1, y1 + 2 * scale, x2, y2 + 2 * scale)
        fill = (245, 246, 250, 215) if pressed else ((85, 92, 104, 200) if hover else (31, 33, 40, 140))
        edge = (255, 255, 255, 185 if hover or pressed else 65)
        if circle:
            draw.ellipse(shadow, fill=(0, 0, 0, 30))
            mask_draw.ellipse(bounds, fill=205)
            draw.ellipse(bounds, fill=fill, outline=edge, width=stroke)
            draw.arc(bounds, 205, 325, fill=(255, 255, 255, 125), width=stroke)
        else:
            rounded_rectangle(draw, shadow, radius, fill=(0, 0, 0, 25))
            rounded_rectangle(mask_draw, bounds, radius, fill=205)
            rounded_rectangle(draw, bounds, radius, fill=fill, outline=edge, width=stroke)

    # 한 줄만 보이는 입력 캡슐. 전체 텍스트는 조합기에 유지하고 저장한다.
    text = ime.text.split("\n")[-1] if ime.text else (hint or "마디를 눌러 입력 · 주먹은 지움")
    input_font = large if ime.text else normal
    available = min(w - 2 * margin, 460 * scale) - 36 * scale
    if hud_text_width(text, input_font) > available:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if hud_text_width("…" + text[-middle:], input_font) <= available:
                low = middle
            else:
                high = middle - 1
        text = "…" + (text[-low:] if low else "")
    input_width = min(w - 2 * margin, max(132 * scale, hud_text_width(text, input_font) + 38 * scale))
    input_height = (40 if ime.text else 34) * scale
    input_x = (w - input_width) / 2
    glass((input_x, margin, input_x + input_width, margin + input_height))
    write((w / 2, margin + input_height / 2), text, font=input_font, fill=white, anchor="mm")
    if ime.text and not paused and not show_help and int(now * 2) % 2 == 0:
        cursor_x = w / 2 + hud_text_width(text, input_font) / 2 + 4 * scale
        draw.line((cursor_x, margin + 12 * scale, cursor_x, margin + input_height - 12 * scale),
                  fill=muted, width=stroke)

    # 손 형태는 얇은 landmark mesh로 표시하고, 엄지는 모든 키보다 위에 그린다.
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
        x, y = points[0]
        write((min(max(x, 24 * scale), w - 24 * scale), min(y + 12 * scale, h - 20 * scale)),
              "오른손" if hand.label == "Right" else "왼손", font=small, fill=muted, anchor="ma",
              stroke_width=stroke, stroke_fill=(28, 29, 34, 160))

    for hand in hands:
        points = hand.points
        if hand.pose == "fist":
            continue
        # 확대된 키가 이웃 키에 가려지지 않도록 마지막에 그린다.
        for key, label, index, _, _ in sorted(hand.targets, key=lambda target: hand.focus.get(target[0], 0.0)):
            x, y = points[index]
            focus = 0.0 if paused or show_help else hand.focus.get(key, 0.0)
            diameter = key_diameter(hand.targets, points, index, focus, scale)
            selected = key == hand.nearest and not paused and not show_help
            recent = key == hand.last_press[0] and 0 <= now - hand.last_press[1] < 0.28
            cx = min(max(x, diameter / 2 + 2), w - diameter / 2 - 2)
            cy = min(max(y, diameter / 2 + 2), h - diameter / 2 - 2)
            bounds = (cx - diameter / 2, cy - diameter / 2, cx + diameter / 2, cy + diameter / 2)
            glass(bounds, circle=True, hover=selected, pressed=recent)
            display = label[:2]
            size = max(8, round((15 if len(display) == 1 else 12) * scale * (1 + 0.20 * focus)))
            key_font = hud_font(fonts[1], size)
            while size > max(8, round(8 * scale)) and hud_text_width(display, key_font) > diameter * 0.74:
                size -= 1
                key_font = hud_font(fonts[1], size)
            ink = (28, 29, 34, 255) if recent else (muted if paused else white)
            write((cx, cy), display, anchor="mm", font=key_font, fill=ink)

    # 손바닥 한가운데: 엄지와 가장 가까운 키를 누르면 들어갈 자모, 주먹을 쥐는 중이면 지움 진행.
    for hand in hands:
        deleting = hand.fist_progress > 0 or hand.pose == "fist"
        key = "BACK" if deleting else hand.nearest
        if key is None or not hand.targets:
            continue
        points = hand.points
        cx, cy = palm_center(points)
        palm_size = max(math.dist(points[5], points[17]), math.dist(points[0], points[9]))
        diameter = min(62 * scale, max(40 * scale, palm_size * 0.38))
        cx = min(max(cx, diameter / 2 + 3), w - diameter / 2 - 3)
        cy = min(max(cy, diameter / 2 + 3), h - diameter / 2 - 3)
        recent = 0 <= now - hand.last_press[1] < 0.28
        glyph = ime.preview_label(key, now)
        bounds = (cx - diameter / 2, cy - diameter / 2, cx + diameter / 2, cy + diameter / 2)
        glass(bounds, circle=True, hover=hand.armed or deleting, pressed=recent)
        font = hud_font(fonts[2], max(10, round(diameter * (0.42 if len(glyph) == 1 else 0.29))))
        ink = (28, 29, 34, 255) if recent else (white if hand.armed or deleting else muted)
        write((cx, cy), glyph, anchor="mm", font=font, fill=ink)
        if hand.fist_progress > 0 and not paused and not show_help:
            r = diameter / 2 + 3 * scale
            draw.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * hand.fist_progress,
                     fill=white, width=max(2, round(2 * scale)))

    for hand in hands:
        if not hand.thumb:
            continue
        x, y = hand.points[4]
        if hand.distances is not None:
            # 진단: 엄지와 가장 가까운 마디까지의 world 거리. 판정 거리 안이면 흰색, 밖이면 회색.
            gap = min(hand.distances)
            write((x + 11 * scale, y - 6 * scale), f"{gap * 1000:.0f}mm", font=small,
                  fill=white if gap <= tap_enter else muted, stroke_width=stroke, stroke_fill=(28, 29, 34, 200))
        r = 7 * scale
        draw.ellipse((x - r - scale, y - r - scale, x + r + scale, y + r + scale),
                     outline=(20, 23, 29, 190), width=max(2, round(3 * scale)))
        draw.ellipse((x - r, y - r, x + r, y + r), outline=white, width=max(1, round(2 * scale)))
        r = 2 * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=white)

    for x, y, progress, caption in cursors:
        r = 9 * scale
        draw.ellipse((x - r - scale, y - r - scale, x + r + scale, y + r + scale),
                     outline=(20, 23, 29, 190), width=max(2, round(3 * scale)))
        draw.ellipse((x - r, y - r, x + r, y + r), outline=white, width=max(1, round(2 * scale)))
        if progress > 0:
            r = 13 * scale
            draw.arc((x - r, y - r, x + r, y + r), -90, -90 + 360 * progress, fill=white,
                     width=max(2, round(2 * scale)))
        if caption:
            write((x + 16 * scale, y - 6 * scale), caption, font=small, fill=muted,
                  stroke_width=stroke, stroke_fill=(28, 29, 34, 200))

    # 상태가 달라질 때만 작은 안내. 정상 입력 준비 상태에서는 안내창도 숨긴다.
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
        glass(((w - message_width) / 2, top, (w + message_width) / 2, top + 26 * scale))
        write((w / 2, top + 13 * scale), message, font=small, fill=muted, anchor="mm")
    write((w - margin, h - 22 * scale), "H 도움말", anchor="ra", font=small,
          fill=muted, stroke_width=stroke, stroke_fill=(28, 29, 34, 115))

    if show_help:
        draw.rectangle((0, 0, w, h), fill=(16, 17, 21, 160))
        box_width = min(w - 2 * margin, 440 * scale)
        box = ((w - box_width) / 2, 60 * scale, (w + box_width) / 2, h - 60 * scale)
        # 명시적으로 연 도움말에서만 단축키와 진단 정보를 보여준다.
        rounded_rectangle(draw, box, 24 * scale, fill=(38, 40, 47, 235), outline=(255, 255, 255, 65), width=stroke)
        help_lines = help_lines or (
            "손마디 천지인",
            "엄지로 마디를 누르면 입력 · 주먹을 쥐면 지움",
            "같은 자음 키를 빠르게 반복하면 다음 자음으로 바뀝니다.",
            "1~9 / 0 자판 테스트    - 공백    = 지움",
            "Tab 확정    Enter 줄바꿈    P 일시정지",
            "E 보정    M 피부색 영역    [ ] 누르기 판정 거리",
            "S 저장    Q / Esc 종료    H 도움말 닫기",
            f"누르기 {tap_enter * 1000:.0f}mm · 주먹 {fist_hold:.2f}초 · 보정 {'ON' if enhanced else 'OFF'} · {fps:.0f} FPS",
            " / ".join("오른손" if hand.label == "Right" else "왼손" for hand in hands) + f" · 전체 입력 {len(ime.text)}자",
        )
        for i, text in enumerate(help_lines):
            write((box[0] + 22 * scale, box[1] + (22 + i * 34) * scale), text,
                  font=normal if i == 0 else small, fill=white if i == 0 else muted)

    # 실제 영상은 유리 표면 안에서만 흐려진다. 나머지 영상은 원본 그대로다.
    picture = blur_under(picture, material_mask)
    picture = Image.alpha_composite(picture, overlay).convert("RGB")
    return cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)


def is_window_closed(cv2, name):
    """미지원(-1)과 닫힘(0)을 구분한다. 미지원 환경은 Q/ESC로 종료한다."""
    try:
        visible = cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE)
    except cv2.error:
        # 일부 GUI backend는 속성 조회 자체를 지원하지 않는다.
        return False
    return visible == 0


def open_capture(cv2, args, size=(640, 480)):
    """--pipeline이 있으면 GStreamer, 없으면 카메라 번호로 캡처를 연다.

    size는 카메라 번호로 열 때 요청하는 해상도다. 카메라가 지원하지 않으면 가까운 값이 들어온다.
    GStreamer 파이프라인은 문자열 안의 width/height가 해상도를 정한다(GSTREAMER는 640x480).
    """
    capture = (cv2.VideoCapture(args.pipeline, cv2.CAP_GSTREAMER) if args.pipeline
               else cv2.VideoCapture(args.camera))
    if not capture.isOpened():
        raise RuntimeError("카메라를 열 수 없습니다. --camera 또는 --pipeline 값을 확인하세요.")
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
    return capture


def read_frame(cv2, capture, wait=10.0):
    """프레임과 끊겼던 시간(초)을 돌려준다.

    iPhone Continuity Camera는 기기를 움직이거나 잠금을 풀면 잠시 멈춘다. wait초까지 기다린다.
    """
    ok, frame = capture.read()
    if ok:
        return frame, 0.0
    since = time.monotonic()
    print("카메라 프레임이 끊겼습니다. 다시 들어오기를 기다립니다.", flush=True)
    while time.monotonic() - since <= wait:
        cv2.waitKey(50)
        ok, frame = capture.read()
        if ok:
            gap = time.monotonic() - since
            print(f"카메라 프레임이 다시 들어옵니다 ({gap:.1f}초 끊김).", flush=True)
            return frame, gap
    raise RuntimeError(f"카메라 프레임을 {wait:.0f}초 동안 읽지 못했습니다. 연결/파이프라인을 확인하세요.")


def create_detector(model):
    """VIDEO 모드 HandLandmarker. with 문으로 닫는다."""
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


def detect_hands(cv2, mp, detector, frame, timestamp, mirrored):
    """BGR 프레임의 손을 (좌우 라벨, 이미지 landmark, world landmark, 좌우 점수) 목록으로 돌려준다."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
    detections = []
    for landmarks, world, handedness in zip(result.hand_landmarks, result.hand_world_landmarks, result.handedness):
        category = handedness[0]
        label = category.category_name
        # MediaPipe Tasks(1.1.1 확인)는 입력 영상에 보이는 실제 손을 라벨로 낸다. 공식 테스트 이미지
        # right_hands.jpg(오른손 손등)는 원본에서 Right, 좌우 반전하면 Left다. 예전 Hands 솔루션 문서의
        # "selfie(좌우 반전) 영상 기준" 가정은 맞지 않는다. 영상을 반전했을 때만 실제 손으로 되돌린다.
        if mirrored:
            label = "Left" if label == "Right" else "Right"
        # handedness 점수는 좌/우 분류 확신도이며 손 검출 확신도가 아니다.
        # 검출된 오른손을 추가 점수 임계값으로 누락시키지 않는다.
        detections.append((label, landmarks, world, category.score))
    return detections


def run(args):
    try:
        import cv2
        import mediapipe as mp
        from PIL import ImageFont
    except ImportError as exc:
        raise RuntimeError("필요 패키지를 확인하세요: mediapipe, numpy, Pillow, OpenCV. " + str(exc)) from exc

    font_path = find_font(args.font)
    fonts = tuple(ImageFont.truetype(str(font_path), size) for size in (16, 21, 29))
    capture = None
    log = args.log.open("w", encoding="utf-8") if args.log else None
    ime = CheonjiinIME(args.cycle)
    tap_enter, tap_gap = args.tap_enter / 1000, (args.tap_exit - args.tap_enter) / 1000
    keyboards = HandKeyboardTracker(args.hand, tap_enter, tap_enter + tap_gap, args.tap_dwell, args.fist_hold)
    fist_hold = args.fist_hold
    enhanced = args.enhance
    show_mask = args.show_mask
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    paused = False
    show_help = False
    notice_since = None
    status = "마디를 눌러 입력하세요"
    timestamp = -1
    previous_frame = time.monotonic()
    fps = 0.0
    exit_reason = None
    try:
        with create_detector(args.model) as detector:
            capture = open_capture(cv2, args)
            cv2.namedWindow("Hand Cheonjiin", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("Hand Cheonjiin", 960, 720)
            while True:
                frame, gap = read_frame(cv2, capture)
                if gap:
                    keyboards.reset()  # 끊긴 동안의 엄지 위치로 입력하지 않는다.
                now = time.monotonic()
                delta = now - previous_frame
                previous_frame = now
                fps = 0.9 * fps + 0.1 / max(delta, 1e-6)
                if not args.no_mirror:
                    frame = cv2.flip(frame, 1)  # 1: 좌우 반전. 0은 상하 반전이다.
                if args.flip_vertical:
                    frame = cv2.flip(frame, 0)
                if enhanced:
                    frame = enhance_frame(frame, clahe)
                h, w = frame.shape[:2]
                timestamp = max(timestamp + 1, int(now * 1000))
                detections = [(label, landmarks, world) for label, landmarks, world, _ in
                              detect_hands(cv2, mp, detector, frame, timestamp, not args.no_mirror)]
                hands, events = keyboards.update(detections, w, h, now, enabled=not paused and not show_help)
                if log is not None:
                    for hand in hands:
                        log.write(json.dumps({
                            "t": round(now, 4), "hand": hand.label, "pose": hand.pose, "armed": hand.armed,
                            "nearest": hand.nearest, "enter": keyboards.taps[hand.label].enter,
                            "mm": None if hand.distances is None else [round(d * 1000, 1) for d in hand.distances],
                            "events": [key for label, key in events if label == hand.label],
                        }, ensure_ascii=False) + "\n")
                for label, pressed in events:
                    glyph = ime.preview_label(pressed, now)
                    ime.press(pressed, now)
                    status = ("오른손" if label == "Right" else "왼손") + " 입력: " + glyph
                if show_mask:
                    for hand in hands:
                        if hand.targets:
                            frame = skin_overlay(frame, hand.points)
                view = draw_ui(frame, ime, hands, status, fonts, fps, paused,
                               enhanced, show_mask, show_help, notice_since, fist_hold, keyboards.taps["Left"].enter)
                cv2.imshow("Hand Cheonjiin", view)
                key_code = cv2.waitKey(1) & 0xFF
                if key_code in (27, ord("q"), ord("Q")):
                    exit_reason = "Q/ESC 키"
                    break
                if is_window_closed(cv2, "Hand Cheonjiin"):
                    exit_reason = "영상 창 닫힘"
                    break
                char = chr(key_code)
                if char in "1234567890":
                    ime.press(char, now)
                    for label in keyboards.last_press:
                        keyboards.last_press[label] = (char, now)
                elif char == "-":
                    ime.press("SPACE", now)
                elif key_code in (8, 127, ord("=")):
                    ime.backspace()
                elif key_code == 9:
                    ime.press("COMMIT", now)
                elif key_code in (10, 13):
                    ime.press("ENTER", now)
                elif char.lower() == "p":
                    paused = not paused
                    keyboards.reset()
                elif char.lower() == "h":
                    show_help = not show_help
                    keyboards.reset()
                elif char.lower() == "e":
                    enhanced = not enhanced
                    keyboards.reset()
                elif char.lower() == "m":
                    show_mask = not show_mask
                elif char.lower() == "s":
                    try:
                        args.output.write_text(ime.text, encoding="utf-8")
                        status = "저장됨: " + args.output.name
                    except OSError as exc:
                        status = "저장 실패 (터미널 확인)"
                        print("저장 실패:", exc)
                    notice_since = now
                elif char in "[]":
                    # 누르기 판정 거리 1mm 단위 조절. 잘 안 눌리면 ], 닿지 않았는데 입력되면 [.
                    tap_enter = min(0.04, max(0.004, round(tap_enter + (0.001 if char == "]" else -0.001), 3)))
                    keyboards.set_tap(tap_enter, round(tap_enter + tap_gap, 3))
                    status = f"누르기 판정 거리 {tap_enter * 1000:.0f}mm"
                    notice_since = now
    finally:
        if capture is not None:
            capture.release()
        if log is not None:
            log.close()
            print("진단 기록: " + str(args.log))
        cv2.destroyAllWindows()
        if exit_reason:
            print("종료 이유: " + exit_reason)
        print("\n입력 결과:\n" + ime.text)


def positive_float(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("0보다 큰 유한수를 지정하세요.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=0, help="USB 카메라 번호 (기본 0)")
    parser.add_argument("--pipeline", help="GStreamer 문자열. 'usb'는 practice의 /dev/video0 설정")
    parser.add_argument("--model", type=Path, default=ROOT / "src/models/MediaPipe/hand_landmarker.task")
    parser.add_argument("--font", help="한글 폰트 ttf/ttc/otf 경로")
    parser.add_argument("--hand", choices=("both", "auto", "left", "right"), default="both",
                        help="기본 both: 양손 인식/입력. left/right는 한 손만, auto는 both와 동일")
    parser.add_argument("--no-mirror", action="store_true", help="좌우 반전 끄기")
    parser.add_argument("--flip-vertical", action="store_true", help="거꾸로 설치된 카메라 상하 반전")
    parser.add_argument("--enhance", action="store_true", help="LAB/CLAHE 명도 보정과 Gaussian 필터 사용")
    parser.add_argument("--show-mask", action="store_true", help="손 주변 피부색 분할 영역과 윤곽선 표시")
    parser.add_argument("--tap-enter", type=positive_float, default=TAP_ENTER * 1000,
                        help="엄지 끝과 마디 사이가 이 거리(mm, MediaPipe world 좌표) 안이면 누른 것으로 본다")
    parser.add_argument("--tap-exit", type=positive_float, default=TAP_EXIT * 1000,
                        help="엄지가 이 거리(mm) 밖으로 떨어져야 다음 누르기를 받는다")
    parser.add_argument("--tap-dwell", type=positive_float, default=TAP_DWELL, help="누른 상태 유지 시간 (초)")
    parser.add_argument("--fist-hold", type=positive_float, default=0.18, help="지움에 필요한 주먹 유지 시간 (초)")
    parser.add_argument("--cycle", type=positive_float, default=0.85, help="같은 자음 키를 순환하는 시간 (초)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().with_name("project01_output.txt"))
    parser.add_argument("--log", type=Path, help="프레임별 엄지-마디 거리와 입력 이벤트를 JSON Lines로 기록")
    parser.add_argument("--demo", action="store_true", help="카메라/외부 패키지 없이 '안녕하세요' 조합 시연")
    args = parser.parse_args()
    if args.tap_exit < args.tap_enter:
        parser.error("--tap-exit는 --tap-enter 이상이어야 합니다.")
    if args.demo:
        ime = CheonjiinIME()
        now = 0.0
        for syllable_keys in ("0125", "52210", "8812", "8211", "0223"):
            for key in syllable_keys:
                ime.press(key, now)
                now += 0.25
            now += 1.0  # 안/녕 경계의 ㄴ을 ㄹ로 순환시키지 않는다.
        print(ime.text)
        return
    if args.pipeline == "usb":
        args.pipeline = GSTREAMER
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
