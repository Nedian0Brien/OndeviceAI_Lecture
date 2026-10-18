"""엄지와 손가락 마디의 영상상 접촉으로 입력하는 천지인 키보드.

실행: python 261018/project01.py
카메라 없이 조합 확인: python 261018/project01.py --demo
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
import math
from pathlib import Path
import time
from typing import Optional

from cheonjiin import CheonjiinIME, TouchGate


ROOT = Path(__file__).resolve().parents[1]
# 표준 천지인: 세 손가락을 열로, 손끝 → DIP → PIP → MCP를 네 행으로 쓴다.
# 아래 index는 검지가 화면 왼쪽일 때의 기본값. 실제 좌우 열은 key_layout에서 정한다.
ROWS = (
    ("손끝", (("1", "ㅣ", 8), ("2", "ㆍ", 12), ("3", "ㅡ", 16))),
    ("끝쪽 관절", (("4", "ㄱㅋㄲ", 7), ("5", "ㄴㄹ", 11), ("6", "ㄷㅌㄸ", 15))),
    ("가운데 관절", (("7", "ㅂㅍㅃ", 6), ("8", "ㅅㅎㅆ", 10), ("9", "ㅈㅊㅉ", 14))),
    ("뿌리 관절", (("SPACE", "공백", 5), ("0", "ㅇㅁ", 9), ("BACK", "지움", 13))),
)
KEYS = [key for _, row in ROWS for key in row]
KEY_LABELS = {key: label for key, label, _ in KEYS}
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


def key_layout(landmarks):
    """양손 모두 화면 왼쪽부터 ㅣㆍㅡ로 읽히게 검지/중지/약지 열을 정한다.

    손가락 끝의 움직임에 열이 바뀌지 않도록 뿌리(MCP)의 좌우 위치를 사용한다.
    """
    columns = sorted((5, 9, 13), key=lambda base: landmarks[base].x)
    return [(key, label, columns[column] + 3 - row_index)
            for row_index, (_, row) in enumerate(ROWS)
            for column, (key, label, _) in enumerate(row)]


def contact_geometry(landmarks, width, height, ratio):
    """실제 픽셀 비율로 거리를 재고 손바닥/인접 마디 크기로 접촉 반경을 제한한다."""
    points = [(p.x * width, p.y * height) for p in landmarks]
    palm_size = max(math.dist(points[5], points[17]), math.dist(points[0], points[9]))
    if palm_size < 35:  # 너무 작은 손은 판정하지 않는다.
        return points, [], None, False
    targets = []
    for key, label, index in key_layout(landmarks):
        # 같은 손가락의 이웃 마디와 겹치지 않도록 반경을 제한.
        base = 5 + ((index - 5) // 4) * 4
        neighbors = [other for other in (index - 1, index + 1) if base <= other <= base + 3]
        spacing = min(math.dist(points[index], points[n]) for n in neighbors)
        radius = min(palm_size * ratio, spacing * 0.46)
        distance = math.dist(points[4], points[index])
        targets.append((key, label, index, radius, distance))
    closest = min(targets, key=lambda target: target[4])
    candidate = closest[0] if closest[4] <= closest[3] else None
    released = all(distance > radius * 1.45 for _, _, _, radius, distance in targets)
    return points, targets, candidate, released


@dataclass
class HandView:
    label: str
    points: list
    targets: list
    candidate: Optional[str]
    nearest: Optional[str]
    gate: TouchGate
    last_press: tuple = (None, float("-inf"))
    focus: dict = field(default_factory=dict)


class HandKeyboardTracker:
    """왼손·오른손의 접촉/해제 상태를 각각 유지한다. auto는 both와 같은 동작이다."""

    def __init__(self, mode="both", dwell=0.12, release=0.10):
        self.mode = "both" if mode == "auto" else mode
        self.gates = {label: TouchGate(dwell, release) for label in ("Left", "Right")}
        self.last_press = {label: (None, float("-inf")) for label in self.gates}
        self.focus = {label: {} for label in self.gates}
        self.last_time = None

    def reset(self):
        for gate in self.gates.values():
            gate.lost()

    def update(self, detections, width, height, ratio, now, enabled=True):
        detections = sorted(((label, landmarks) for label, landmarks in detections
                             if label in self.gates and (self.mode == "both" or label.lower() == self.mode)),
                            key=lambda detection: detection[0])
        counts = Counter(label for label, _ in detections)
        for label, gate in self.gates.items():
            if counts[label] != 1:
                gate.lost()
                self.focus[label] = {}
        # 80ms의 짧은 보간으로 가까운 키의 확대가 프레임마다 튀지 않게 한다.
        blend = 1.0 if self.last_time is None else 1 - math.exp(-max(0, now - self.last_time) / 0.08)
        self.last_time = now
        views, events = [], []
        for label, landmarks in detections:
            points, targets, candidate, released = contact_geometry(landmarks, width, height, ratio)
            gate = self.gates[label]
            nearest = min(targets, key=lambda target: target[4])[0] if targets else None
            self.focus[label] = {
                key: self.focus[label].get(key, 0.0) + blend * ((1.0 if key == nearest else 0.0) - self.focus[label].get(key, 0.0))
                for key, _, _, _, _ in targets
            }
            if not enabled or not targets or counts[label] != 1:
                gate.lost()
            else:
                pressed = gate.update(candidate, released, now)
                if pressed:
                    self.last_press[label] = (pressed, now)
                    events.append((label, pressed))
            views.append(HandView(label, points, targets, candidate, nearest, gate,
                                  self.last_press[label], self.focus[label]))
        return views, events


def key_diameter(targets, points, index, focus, scale):
    spacing = min((math.dist(points[index], points[other]) for _, _, other, _, _ in targets
                   if other != index), default=40 * scale)
    base = max(18 * scale, min(32 * scale, spacing * 0.78))
    return base * (1 + 0.30 * focus)


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


def draw_ui(frame, ime, hands, status, ratio, fonts, fps, paused, enhanced=False, show_mask=False,
            show_help=False, notice_since=None):
    """영상 위 작은 원형 유리 키와 한 줄 입력 캡슐을 합성한다."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

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
            draw.rounded_rectangle(shadow, radius=radius, fill=(0, 0, 0, 25))
            mask_draw.rounded_rectangle(bounds, radius=radius, fill=205)
            draw.rounded_rectangle(bounds, radius=radius, fill=fill, outline=edge, width=stroke)

    # 한 줄만 보이는 입력 캡슐. 전체 텍스트는 조합기에 유지하고 저장한다.
    text = ime.text.split("\n")[-1] if ime.text else "엄지로 눌러 입력"
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
            if key == hand.candidate and hand.gate.armed and hand.gate.progress > 0 and not paused and not show_help:
                r = diameter / 2 + 2 * scale
                draw.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * hand.gate.progress,
                         fill=white, width=max(1, round(2 * scale)))

    for hand in hands:
        x, y = hand.points[4]
        r = 7 * scale
        draw.ellipse((x - r - scale, y - r - scale, x + r + scale, y + r + scale),
                     outline=(20, 23, 29, 190), width=max(2, round(3 * scale)))
        draw.ellipse((x - r, y - r, x + r, y + r), outline=white, width=max(1, round(2 * scale)))
        r = 2 * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=white)

    # 상태가 달라질 때만 작은 안내. 정상 입력 준비 상태에서는 안내창도 숨긴다.
    message = ""
    if paused:
        message = "일시정지 · P로 재개"
    elif not any(hand.targets for hand in hands):
        message = "손을 더 가까이" if hands else "손바닥을 보여주세요"
    elif not any(hand.gate.armed for hand in hands if hand.targets):
        message = "엄지를 떼세요"
    if notice_since is not None and now - notice_since < 2.5 and status.startswith("저장"):
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
        draw.rounded_rectangle(box, radius=24 * scale, fill=(38, 40, 47, 235),
                               outline=(255, 255, 255, 65), width=stroke)
        help_lines = (
            "손마디 천지인",
            "엄지를 유지해 입력하고, 떼어 다음 키를 입력하세요.",
            "같은 자음 키를 빠르게 반복하면 다음 자음으로 바뀝니다.",
            "1~9 / 0 자판 테스트    - 공백    = 지움",
            "Tab 확정    Enter 줄바꿈    P 일시정지",
            "E 보정    M 피부색 영역    [ ] 접촉 범위",
            "S 저장    Q / Esc 종료    H 도움말 닫기",
            f"접촉 {ratio:.2f} · 보정 {'ON' if enhanced else 'OFF'} · 영역 {'ON' if show_mask else 'OFF'} · {fps:.0f} FPS",
            " / ".join("오른손" if hand.label == "Right" else "왼손" for hand in hands) + f" · 전체 입력 {len(ime.text)}자",
        )
        for i, text in enumerate(help_lines):
            write((box[0] + 22 * scale, box[1] + (22 + i * 34) * scale), text,
                  font=normal if i == 0 else small, fill=white if i == 0 else muted)

    # 실제 영상은 유리 표면 안에서만 흐려진다. 나머지 영상은 원본 그대로다.
    blurred = picture.resize((max(1, w // 3), max(1, h // 3)))
    blurred = blurred.filter(ImageFilter.GaussianBlur(2)).resize(picture.size)
    picture = Image.composite(blurred, picture, material_mask)
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


def run(args):
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        from PIL import ImageFont
    except ImportError as exc:
        raise RuntimeError("필요 패키지를 확인하세요: mediapipe, numpy, Pillow, OpenCV. " + str(exc)) from exc

    if not args.model.is_file():
        raise FileNotFoundError("MediaPipe 모델이 없습니다: " + str(args.model))
    font_path = find_font(args.font)
    fonts = tuple(ImageFont.truetype(str(font_path), size) for size in (16, 21, 29))
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(args.model)),
        running_mode=vision.RunningMode.VIDEO, num_hands=2,
        min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    capture = None
    ime = CheonjiinIME(args.cycle)
    keyboards = HandKeyboardTracker(args.hand, args.dwell, args.release)
    ratio = args.touch_ratio
    enhanced = args.enhance
    show_mask = args.show_mask
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    paused = False
    show_help = False
    notice_since = None
    status = "키를 누른 뒤 떼어야 다시 입력됩니다"
    timestamp = -1
    previous_frame = time.monotonic()
    fps = 0.0
    exit_reason = None
    try:
        with vision.HandLandmarker.create_from_options(options) as detector:
            capture = (cv2.VideoCapture(args.pipeline, cv2.CAP_GSTREAMER) if args.pipeline
                       else cv2.VideoCapture(args.camera))
            if not capture.isOpened():
                raise RuntimeError("카메라를 열 수 없습니다. --camera 또는 --pipeline 값을 확인하세요.")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cv2.namedWindow("Hand Cheonjiin", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("Hand Cheonjiin", 960, 720)
            while True:
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("카메라 프레임을 읽지 못했습니다. 연결/파이프라인을 확인하세요.")
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
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                timestamp = max(timestamp + 1, int(now * 1000))
                result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
                detections = []
                for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                    category = handedness[0]
                    label = category.category_name
                    # MediaPipe handedness는 selfie(좌우 반전) 영상을 기준으로 한다.
                    if args.no_mirror:
                        label = "Left" if label == "Right" else "Right"
                    # handedness 점수는 좌/우 분류 확신도이며 손 검출 확신도가 아니다.
                    # 검출된 오른손을 추가 점수 임계값으로 누락시키지 않는다.
                    detections.append((label, landmarks))
                hands, events = keyboards.update(detections, w, h, ratio, now, enabled=not paused and not show_help)
                for label, pressed in events:
                    ime.press(pressed, now)
                    status = ("오른손" if label == "Right" else "왼손") + " 입력: " + KEY_LABELS[pressed]
                if show_mask:
                    for hand in hands:
                        if hand.targets:
                            frame = skin_overlay(frame, hand.points)
                view = draw_ui(frame, ime, hands, status, ratio, fonts, fps, paused,
                               enhanced, show_mask, show_help, notice_since)
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
                elif char == "[":
                    ratio = max(0.05, round(ratio - 0.01, 2))
                    keyboards.reset()
                elif char == "]":
                    ratio = min(0.35, round(ratio + 0.01, 2))
                    keyboards.reset()
    finally:
        if capture is not None:
            capture.release()
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
    parser.add_argument("--touch-ratio", type=positive_float, default=0.16, help="손바닥 대비 접촉 반경")
    parser.add_argument("--dwell", type=positive_float, default=0.12, help="접촉 유지 시간 (초)")
    parser.add_argument("--release", type=positive_float, default=0.10, help="다시 누르기 전 떼는 시간 (초)")
    parser.add_argument("--cycle", type=positive_float, default=0.85, help="같은 자음 키를 순환하는 시간 (초)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().with_name("project01_output.txt"))
    parser.add_argument("--demo", action="store_true", help="카메라/외부 패키지 없이 '안녕하세요' 조합 시연")
    args = parser.parse_args()
    if not 0.05 <= args.touch_ratio <= 0.35:
        parser.error("--touch-ratio는 0.05~0.35 범위로 지정하세요.")
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
