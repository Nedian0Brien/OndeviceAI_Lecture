"""손끝 꼬집기 횟수로 고르는 양손 천지인 입력기 + 손등 마우스 (1인칭 카메라 기준).

카메라를 눈 위치에 두고 손을 내려다본다. 손바닥이 카메라를 향하면 키보드, 오른손을 뒤집어 손등이 보이면 마우스다.
자판과 규칙은 pinch_typer.py 머리말을 본다.

실행:  python 261018/pinch_cheonjiin.py --camera 0
시연:  python 261018/pinch_cheonjiin.py --demo
"""

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

from cheonjiin import CONSONANTS
from pinch_typer import (DISTAL, LEFT_KEYS, PINCH_ENTER, PINCH_EXIT, RIGHT_KEYS, RIGHT_SHIFT_KEYS, TIPS,
                         VOWEL_LABELS, PinchTyper, group_of, hand_signal, key_label, replay)


WINDOW = "Pinch Cheonjiin"
# 시연 장비(Jetson USB 카메라, GStreamer 640x480 30fps)가 기준이다. macOS에서 iPhone으로 시험할 때만 기본값을 올린다.
AUTO_RESOLUTION = {"darwin": (1280, 720)}
DEFAULT_RESOLUTION = (640, 480)


def parse_resolution(value):
    """--resolution 값: 'auto' 또는 '1280x720' 꼴."""
    if value == "auto":
        return value
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("auto 또는 1280x720 꼴로 지정하세요.") from None
    if width < 160 or height < 120:
        raise argparse.ArgumentTypeError("너무 작은 해상도입니다.")
    return width, height


def capture_size(resolution, platform=None):
    """카메라 번호로 열 때 요청할 해상도. auto는 macOS 1280x720, 그 밖(Jetson 등) 640x480."""
    if resolution != "auto":
        return resolution
    return AUTO_RESOLUTION.get(platform or sys.platform, DEFAULT_RESOLUTION)


def detection_frame(cv2, frame, max_width):
    """손 검출용 영상. 가로가 max_width보다 크면 비율을 유지해 줄인다.

    landmark는 0~1 비율 좌표라 원본 해상도 화면에 그대로 쓴다. 검출 입력을 640 이하로 두면 화면 해상도와
    상관없이 검출 시간이 Jetson 640x480 시연과 같은 조건이 된다.
    """
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    return cv2.resize(frame, (max_width, max(1, round(h * max_width / w))), interpolation=cv2.INTER_AREA)


def window_size(w, h, min_width=960, max_width=1440, max_height=900):
    """처음 여는 창 크기. 640x480은 960x720으로 키우고, 큰 영상은 화면에 들어오게 줄인다."""
    scale = min(max(min_width / w, 1.0), max_width / w, max_height / h)
    return round(w * scale), round(h * scale)
FIXTURES = Path(__file__).resolve().with_name("fixtures") / "pinch-typer-keys.json"
BUTTONS = (("save", "저장"), ("pause", "일시정지"), ("help", "도움말"))
MENU = (("copy", "복사"), ("clear", "전체 지우기"), ("close", "닫기"))
ORIENTATION_NAMES = {"palm": "손바닥", "back": "손등", "turning": "회전 중", None: ""}
# 키보드 시험용: 1~4 = 왼손 검지~소지, 7 8 9 0 = 오른손 검지~소지, Space = 왼손 주먹, Backspace = 오른손 주먹
TEST_KEYS = {ord("1"): ("Left", 0), ord("2"): ("Left", 1), ord("3"): ("Left", 2), ord("4"): ("Left", 3),
             ord("7"): ("Right", 0), ord("8"): ("Right", 1), ord("9"): ("Right", 2), ord("0"): ("Right", 3)}

WHITE = (250, 250, 252, 255)
MUTED = (230, 231, 236, 215)
DIM = (200, 202, 210, 150)
SHIFT_BLUE = (132, 186, 255, 255)
MOUSE_GREEN = (126, 224, 176, 255)
DELETE_RED = (255, 128, 116, 255)


# ---------------------------------------------------------------------------
# 검출 결과 → 손 신호

def assign_hands(detections):
    """{"Left"|"Right": (landmarks, world, MediaPipe 라벨)}.

    좌우는 MediaPipe 라벨(손바닥 주름·손톱 같은 겉모습으로 판단)을 따른다. 카메라가 손 맞은편에 있거나
    영상이 돌아가 있으면 화면 위치로 정한 좌우는 뒤바뀌기 때문이다. 두 손에 같은 라벨이 붙었을 때만
    화면 위치로 나눈다(왼쪽 = 왼손).
    세 번째 값(MediaPipe 라벨)은 손바닥/손등 계산에 쓴다. 손 방향은 그 손을 MediaPipe가 어느 손으로
    봤는지와 landmark 배치를 함께 봐야 정해지므로, 위치로 바꾼 라벨이 아니라 원래 라벨을 써야 한다.
    """
    if len(detections) >= 2:
        first, second = detections[:2]
        if first[0] != second[0]:
            return {label: (landmarks, world, label) for label, landmarks, world, _ in (first, second)}
        first, second = sorted((first, second), key=lambda d: sum(p.x for p in d[1]) / len(d[1]))
        return {"Left": (first[1], first[2], first[0]), "Right": (second[1], second[2], second[0])}
    return {label: (landmarks, world, label) for label, landmarks, world, _ in detections[:1]}


def build_signals(hands, width, height, mirrored):
    from project01 import classify_hand_pose

    return {label: hand_signal(seen_as, landmarks, world, mirrored, width, height,
                               classify_hand_pose(landmarks, width, height))
            for label, (landmarks, world, seen_as) in hands.items()}


# ---------------------------------------------------------------------------
# 손끝 라벨

def tip_text(typer, hand, finger):
    """손끝 라벨. 자음 그룹은 첫 자모, 순환 중인 손가락은 현재 후보를 보여준다."""
    if typer.mode == "mouse":
        return ("이동" if hand == "Right" else "클릭") if finger == 0 else None
    if hand == "Left":
        key = LEFT_KEYS[finger]
    else:
        key = (RIGHT_SHIFT_KEYS if typer.shift else RIGHT_KEYS)[finger]
        pending = typer.pending
        if pending is not None and pending.finger == finger and not typer.shift:
            return "줄" if pending.label == "\n" else pending.label
    return {"SHIFT": "⇧", "PUNCT": "."}.get(key) or VOWEL_LABELS.get(key) or CONSONANTS[key][0]


# ---------------------------------------------------------------------------
# HUD 배치 (그리기와 클릭 판정이 같은 좌표를 쓴다)

def ui_scale(w, h):
    return min(w / 640, h / 480)


def button_rects(w, h):
    s = ui_scale(w, h)
    bw, bh, margin = 70 * s, 26 * s, 16 * s
    return [(key, label, (w - margin - bw, margin + i * (bh + 6 * s), w - margin, margin + i * (bh + 6 * s) + bh))
            for i, (key, label) in enumerate(BUTTONS)]


def menu_rects(position, w, h):
    s = ui_scale(w, h)
    mw, mh = 104 * s, 26 * s
    x = min(max(position[0], 4), w - mw - 4)
    y = min(max(position[1], 4), h - len(MENU) * (mh + 4 * s) - 4)
    return [(key, label, (x, y + i * (mh + 4 * s), x + mw, y + i * (mh + 4 * s) + mh))
            for i, (key, label) in enumerate(MENU)]


def hit(rects, point):
    for key, _, (x1, y1, x2, y2) in rects:
        if x1 <= point[0] <= x2 and y1 <= point[1] <= y2:
            return key
    return None


# ---------------------------------------------------------------------------
# HUD

def draw_hud(frame, typer, hands, ui, fonts, fps):
    """hands: {"Left"|"Right": 픽셀 좌표 21개}. ui: 화면 상태(dict)."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    from project01 import HAND_CONNECTIONS, blur_under, hud_font, hud_text_sprite, hud_text_width, rounded_rectangle

    h, w = frame.shape[:2]
    s = ui_scale(w, h)
    small, normal, large = (hud_font(font, max(8, round(size * s))) for font, size in zip(fonts, (11, 14, 21)))
    picture = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", picture.size)
    draw = ImageDraw.Draw(overlay)
    material = Image.new("L", picture.size)
    material_draw = ImageDraw.Draw(material)
    now = time.monotonic()
    margin, stroke = 16 * s, max(1, round(s))
    pinch_enter, pinch_exit = typer.hands["Left"].pinch.enter, typer.hands["Left"].pinch.exit
    mouse = typer.mode == "mouse"
    cursor = (typer.cursor[0] * w, typer.cursor[1] * h)

    def write(position, text, font, fill, anchor="la", outline=False):
        sprite, left, top = hud_text_sprite(text, font, fill, anchor, stroke if outline else 0,
                                            (28, 29, 34, 170) if outline else None)
        overlay.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))

    def glass(bounds, circle=False, hover=False, pressed=False, tint=None):
        x1, y1, x2, y2 = bounds
        radius = (y2 - y1) / 2
        fill = (245, 246, 250, 215) if pressed else ((85, 92, 104, 200) if hover else (31, 33, 40, 140))
        if tint and not pressed:
            fill = tint[:3] + (70 if not hover else 120,)
        edge = (255, 255, 255, 185 if hover or pressed else 65)
        if circle:
            material_draw.ellipse(bounds, fill=205)
            draw.ellipse(bounds, fill=fill, outline=edge, width=stroke)
        else:
            rounded_rectangle(draw, (x1, y1 + 2 * s, x2, y2 + 2 * s), radius, fill=(0, 0, 0, 25))
            rounded_rectangle(material_draw, bounds, radius, fill=205)
            rounded_rectangle(draw, bounds, radius, fill=fill, outline=edge, width=stroke)

    # 손: 얇은 mesh, 손별 방향, 가장 가까운 손끝의 꼬집기 진행, 주먹 진행
    for label, points in hands.items():
        state = typer.hands[label]
        for a, b in HAND_CONNECTIONS:
            draw.line((points[a], points[b]), fill=(237, 242, 250, 95), width=stroke)
        caption = ("왼손" if label == "Left" else "오른손")
        if state.orientation.state:
            caption += " · " + ORIENTATION_NAMES[state.orientation.state]
        if not state.pinch.armed:
            caption += " · 손을 펴세요"
        x, y = points[0]
        write((min(max(x, 40 * s), w - 40 * s), min(y + 12 * s, h - 20 * s)), caption, small, MUTED, "ma", True)
        # 손끝 라벨: 손가락 끝마디 방향으로 손끝 바로 바깥에 둔다(닿는 면을 가리지 않게).
        tips = [points[tip] for tip in TIPS]
        spacing = min(math.dist(a, b) for a, b in zip(tips, tips[1:]))
        diameter = min(26 * s, max(15 * s, spacing * 0.9))
        values = None if state.signal is None else state.signal.ratios
        for finger in range(4):
            tx, ty = tips[finger]
            dx, dy = tx - points[DISTAL[finger][0]][0], ty - points[DISTAL[finger][0]][1]
            norm = math.hypot(dx, dy) or 1.0
            cx, cy = tx + dx / norm * diameter * 0.85, ty + dy / norm * diameter * 0.85
            progress = 0.0 if values is None else min(1.0, max(0.0, (pinch_exit - values[finger])
                                                                / (pinch_exit - pinch_enter)))
            active = state.pinch.active == finger
            text = tip_text(typer, label, finger)
            color = MOUSE_GREEN if mouse else WHITE
            if ui["tip_labels"] and text:
                width = max(diameter, hud_text_width(text, small) + 10 * s)
                bounds = (cx - width / 2, cy - diameter / 2, cx + width / 2, cy + diameter / 2)
                shifted = label == "Right" and typer.shift and not mouse
                glass(bounds, circle=width == diameter, hover=progress > 0.5, pressed=active,
                      tint=SHIFT_BLUE if shifted or (text == "⇧" and typer.shift) else None)
                ink = (28, 29, 34, 255) if active else (SHIFT_BLUE if shifted else color)
                write((cx, cy), text, hud_font(fonts[1], max(8, round(diameter * 0.55))) if len(text) == 1 else small,
                      ink, "mm")
            if progress > 0 and not active:
                r = 6 * s
                draw.arc((tx - r, ty - r, tx + r, ty + r), -90, -90 + 360 * progress, fill=color,
                         width=max(2, round(2 * s)))
            if ui["debug"] and values is not None:
                write((tx - dx / norm * 12 * s, ty - dy / norm * 12 * s),
                      f"{values[finger]:.2f}", small, WHITE if values[finger] < pinch_enter else DIM, "mm", True)
        thumb = points[4]
        r = 3 * s
        draw.ellipse((thumb[0] - r, thumb[1] - r, thumb[0] + r, thumb[1] + r), fill=(245, 248, 252, 200))
        if state.fist.progress > 0 and not mouse:
            cx = (points[0][0] + points[9][0]) / 2
            cy = (points[0][1] + points[9][1]) / 2
            r = 22 * s
            color = DELETE_RED if label == "Right" else WHITE
            draw.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * state.fist.progress, fill=color,
                     width=max(2, round(3 * s)))
            if label == "Right":
                text = "지움"
            else:
                text = "확정" if typer.pending is not None else "공백"
            write((cx, cy), text, small, color, "mm", True)

    # 위: 입력 한 줄 캡슐. 확정 전 후보 글자 아래에 밑줄.
    text = typer.ime.text.split("\n")[-1]
    shown = text or "손끝을 꼬집어 입력"
    font = large if text else normal
    available = min(w - 2 * margin, 420 * s) - 36 * s
    if hud_text_width(shown, font) > available:
        keep = len(shown)
        while keep > 0 and hud_text_width("…" + shown[-keep:], font) > available:
            keep -= 1
        shown = "…" + shown[-keep:] if keep else "…"
    width = min(w - 2 * margin, max(140 * s, hud_text_width(shown, font) + 40 * s))
    height = (40 if text else 34) * s
    x1 = (w - width) / 2
    glass((x1, margin, x1 + width, margin + height))
    write((w / 2, margin + height / 2), shown, font, WHITE if text else MUTED, "mm")
    text_right = w / 2 + hud_text_width(shown, font) / 2
    if text and typer.pending is not None and typer.pending.key != "PUNCT":
        last = hud_text_width(shown[-1:], font)
        draw.line((text_right - last, margin + height - 9 * s, text_right, margin + height - 9 * s),
                  fill=SHIFT_BLUE if typer.pending.key in RIGHT_SHIFT_KEYS else WHITE, width=max(2, round(2 * s)))
    elif text and int(now * 2) % 2 == 0 and not ui["paused"]:
        draw.line((text_right + 4 * s, margin + 12 * s, text_right + 4 * s, margin + height - 12 * s),
                  fill=MUTED, width=stroke)

    # 캡슐 아래: 순환 중인 그룹(현재 자모 강조)
    row_y = margin + height + 8 * s
    if typer.pending is not None:
        group = [("줄" if c == "\n" else c) for c in group_of(typer.pending.key)]
        current = (typer.pending.count - 1) % len(group)
        cell = 22 * s
        x = w / 2 - len(group) * cell / 2
        for i, char in enumerate(group):
            write((x + (i + 0.5) * cell, row_y + cell / 2), char, normal,
                  WHITE if i == current else DIM, "mm", True)

    # 왼쪽 위: 모드와 쉬프트
    chip_y = margin
    mode_text = ("마우스" + (" · OS" if ui["os_mouse"] else "")) if mouse else "키보드"
    chip_w = hud_text_width(mode_text, small) + 24 * s
    glass((margin, chip_y, margin + chip_w, chip_y + 24 * s), tint=MOUSE_GREEN if mouse else None)
    write((margin + chip_w / 2, chip_y + 12 * s), mode_text, small, WHITE, "mm")
    if typer.shift:
        chip_y += 30 * s
        shift_w = hud_text_width("⇧ 아랫줄", small) + 24 * s
        glass((margin, chip_y, margin + shift_w, chip_y + 24 * s), tint=SHIFT_BLUE)
        write((margin + shift_w / 2, chip_y + 12 * s), "⇧ 아랫줄", small, WHITE, "mm")

    # 오른쪽 위: 버튼 (마우스 모드에서 가상 커서로 누른다)
    for key, label, bounds in button_rects(w, h):
        hover = mouse and not ui["os_mouse"] and ui["menu"] is None and hit([(key, label, bounds)], cursor)
        title = "재개" if key == "pause" and ui["paused"] else label
        glass(bounds, hover=hover, pressed=hover and typer.button_down)
        write(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2), title, small, WHITE, "mm")

    # 아래: 자판 지도. 손에 라벨을 붙이지 않고 패널로 보여준다(visionOS에서도 같은 원칙).
    if ui["show_map"] and not mouse:
        cell_w, cell_h, gap = 50 * s, 28 * s, 5 * s
        top = h - margin - cell_h
        for hand, keys, center in (("Left", LEFT_KEYS, w * 0.27),
                                   ("Right", RIGHT_SHIFT_KEYS if typer.shift else RIGHT_KEYS, w * 0.73)):
            state = typer.hands[hand]
            start = center - (4 * cell_w + 3 * gap) / 2
            for i, key in enumerate(keys):
                bounds = (start + i * (cell_w + gap), top, start + i * (cell_w + gap) + cell_w, top + cell_h)
                active = state.pinch.active == i and state.orientation.state == "palm"
                glass(bounds, hover=active, tint=SHIFT_BLUE if (key == "SHIFT" and typer.shift) else None)
                ink = SHIFT_BLUE if hand == "Right" and typer.shift else WHITE
                name = key_label(key)
                write(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2), name,
                      normal if len(name) <= 3 and key != "PUNCT" else small, ink, "mm")

    # 안내: 상태가 달라질 때만
    message = ""
    if ui["paused"]:
        message = "일시정지 · P로 재개"
    elif not hands:
        message = "손바닥을 카메라 쪽으로 보여주세요"
    elif ui["notice_since"] is not None and now - ui["notice_since"] < 2.5:
        message = ui["status"]
    if message and not ui["show_help"]:
        mw = min(w - 2 * margin, hud_text_width(message, small) + 28 * s)
        top = h - (ui["show_map"] and not mouse and 92 or 58) * s
        glass(((w - mw) / 2, top, (w + mw) / 2, top + 26 * s))
        write((w / 2, top + 13 * s), message, small, MUTED, "mm")

    # 마우스 모드: 우클릭 메뉴와 가상 커서
    if ui["menu"] is not None:
        for key, label, bounds in menu_rects(ui["menu"], w, h):
            hover = hit([(key, label, bounds)], cursor)
            glass(bounds, hover=hover)
            write((bounds[0] + 12 * s, (bounds[1] + bounds[3]) / 2), label, small, WHITE, "lm")
    if mouse and not ui["os_mouse"]:
        x, y = cursor
        r = 8 * s
        draw.ellipse((x - r - s, y - r - s, x + r + s, y + r + s), outline=(20, 23, 29, 190), width=max(2, round(3 * s)))
        draw.ellipse((x - r, y - r, x + r, y + r), outline=MOUSE_GREEN, width=max(2, round(2 * s)),
                     fill=MOUSE_GREEN[:3] + (150,) if typer.button_down else None)

    if ui["show_help"]:
        draw.rectangle((0, 0, w, h), fill=(16, 17, 21, 165))
        bw = min(w - 2 * margin, 470 * s)
        box = ((w - bw) / 2, 44 * s, (w + bw) / 2, h - 44 * s)
        rounded_rectangle(draw, box, 22 * s, fill=(38, 40, 47, 238), outline=(255, 255, 255, 65), width=stroke)
        for i, line in enumerate(ui["help_lines"] + [f"꼬집기 {pinch_enter:.2f} / 떼기 {pinch_exit:.2f} · {fps:.0f} FPS"]):
            write((box[0] + 20 * s, box[1] + (20 + i * 27) * s), line, normal if i == 0 else small,
                  WHITE if i == 0 else MUTED)

    picture = blur_under(picture, material)
    picture = Image.alpha_composite(picture, overlay).convert("RGB")
    return cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)


HELP_LINES = [
    "손끝 꼬집기 천지인",
    "손바닥을 카메라 쪽으로 · 같은 손가락 반복 = 순환 · 다른 버튼 = 자동 확정",
    "왼손: ㅣ ㆍ ㅡ ⇧(소지, 다음 자음 하나만 아랫줄)",
    "오른손: ㄱㅋㄲ ㄴㄹ ㄷㅌㄸ ㅇㅁ  ·  ⇧: ㅂㅍㅃ ㅅㅎㅆ ㅈㅊㅉ .,?!줄바꿈",
    "왼손 주먹: 후보 확정 / 없으면 공백  ·  오른손 주먹: 지움",
    "오른손 손등: 마우스 · 오른손 검지 꼬집어 이동 · 왼손 손등 꼬집기 1회 좌 / 2회 우 / 유지 누름",
    "키보드 시험: 1~4 왼손 검지~소지 · 7 8 9 0 오른손 · Space 왼손 주먹 · Backspace 오른손 주먹",
    "T 손끝 라벨 · K 자판 지도 · D 진단 수치(꼬집기 거리) · [ ] 꼬집기 판정 거리",
    "P 일시정지 · S 저장 · G 스크린샷 · F 전체 화면 · H 닫기 · Q 종료",
]


# ---------------------------------------------------------------------------
# OS 마우스 (B)

class OsMouse:
    """가상 커서 대신 실제 OS 마우스를 움직인다. macOS는 터미널 앱에 손쉬운 사용 권한이 필요하다."""

    def __init__(self, scale):
        try:
            from pynput.mouse import Button, Controller
        except ImportError as exc:
            raise RuntimeError("--os-mouse에는 pynput이 필요합니다: python -m pip install pynput") from exc
        self.mouse, self.button, self.scale = Controller(), Button, scale
        self.rest = [0.0, 0.0]  # 1px보다 작은 이동을 모아 둔다

    def handle(self, event):
        kind = event[0]
        if kind == "move":
            self.rest = [self.rest[0] + event[1] * self.scale, self.rest[1] + event[2] * self.scale]
            dx, dy = int(self.rest[0]), int(self.rest[1])
            if dx or dy:
                self.mouse.move(dx, dy)
                self.rest = [self.rest[0] - dx, self.rest[1] - dy]
        elif kind == "click":
            self.mouse.click(self.button.left if event[1] == "left" else self.button.right)
        elif kind == "down":
            self.mouse.press(self.button.left)
        elif kind == "up":
            self.mouse.release(self.button.left)


def annotate_snapshot(cv2, image, hands, signals, typer, w, h):
    """진단용 스크린샷에 손별 판정값을 ASCII로 덧붙인다: 역할(MediaPipe 라벨) / facing / 방향 / 모드."""
    out = image.copy()
    for role, (landmarks, _, seen_as) in hands.items():
        signal = signals.get(role)
        x, y = int(landmarks[0].x * w), int(landmarks[0].y * h)
        state = typer.hands[role].orientation.state or "-"
        text = f"{role}(mp:{seen_as}) f{signal.facing:+.2f} {state} min{min(signal.ratios):.2f}"
        for color, width in (((0, 0, 0), 3), ((0, 255, 255), 1)):
            cv2.putText(out, text, (max(4, x - 120), min(h - 8, y + 34)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, width)
    cv2.putText(out, f"mode={typer.mode} t={time.strftime('%H:%M:%S')}", (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 255, 255), 1)
    return out


def copy_text(text):
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=text.encode("utf-8"), check=False)
            return True
    return False


# ---------------------------------------------------------------------------
# 실행

def demo():
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    case = data["cases"][0]
    print(case["name"])
    print(replay(case["steps"]).ime.text)


def run(args):
    import cv2
    import mediapipe as mp
    from PIL import ImageFont
    from project01 import create_detector, detect_hands, find_font, is_window_closed, open_capture, read_frame

    fonts = tuple(ImageFont.truetype(str(find_font(args.font)), size) for size in (16, 21, 29))
    typer = PinchTyper(enter=args.pinch_enter, exit=args.pinch_exit, fist_hold=args.fist_hold, gain=args.gain)
    os_mouse = OsMouse(args.os_scale) if args.os_mouse else None
    if os_mouse:
        print("OS 마우스 모드: macOS에서는 시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 터미널을 허용하세요.")
    ui = {"paused": False, "show_help": False, "show_map": False, "tip_labels": True, "debug": False, "menu": None,
          "os_mouse": bool(os_mouse), "status": "", "notice_since": None, "help_lines": HELP_LINES}
    log = open(args.log, "a", encoding="utf-8") if args.log else None
    # 진단 스크린샷: 1초마다(최근 40장 순환), 모드가 바뀔 때마다, G 키를 누를 때 저장한다.
    args.snapshots.mkdir(parents=True, exist_ok=True)
    print("스크린샷 저장 위치:", args.snapshots)
    snapshot_count, last_snapshot = 0, 0.0
    dark_since, dark_warned = None, False  # 카메라가 열렸지만 검은 프레임만 들어오는 경우(Continuity Camera 멈춤 등)
    capture, exit_reason = None, None
    timestamp, previous, fps = -1, time.monotonic(), 0.0

    def notice(message):
        ui["status"], ui["notice_since"] = message, time.monotonic()

    def action(key):
        if key == "save":
            try:
                args.output.write_text(typer.ime.text, encoding="utf-8")
                notice("저장됨: " + args.output.name)
            except OSError as exc:
                notice("저장 실패 (터미널 확인)")
                print("저장 실패:", exc)
        elif key == "pause":
            ui["paused"] = not ui["paused"]
            typer.reset()
        elif key == "help":
            ui["show_help"] = not ui["show_help"]
            typer.reset()
        elif key == "copy":
            notice("복사됨" if copy_text(typer.ime.text) else "복사 도구가 없습니다")
        elif key == "clear":
            typer.ime.clear()
            typer.pending = None
            notice("전체 지움")

    try:
        with create_detector(args.model) as detector:
            size = capture_size(args.resolution)
            capture = open_capture(cv2, args, size)
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            window_ready, fullscreen = False, False
            while True:
                frame, gap = read_frame(cv2, capture)
                if not window_ready:
                    fh, fw = frame.shape[:2]
                    small = detection_frame(cv2, frame, args.detect_width).shape
                    requested = "GStreamer 파이프라인" if args.pipeline else f"요청 {size[0]}x{size[1]}"
                    print(f"카메라 해상도 {fw}x{fh} ({requested}), 손 검출 입력 {small[1]}x{small[0]}", flush=True)
                    cv2.resizeWindow(WINDOW, *window_size(fw, fh))
                    window_ready = True
                if gap:
                    typer.reset()
                now = time.monotonic()
                fps = 0.9 * fps + 0.1 / max(now - previous, 1e-6)
                previous = now
                if frame[::16, ::16].max() < 8:
                    dark_since = dark_since or now
                    if now - dark_since > 2.0 and not dark_warned:
                        dark_warned = True
                        message = "카메라 영상이 검습니다. iPhone을 잠근 채 거치했는지, 다른 앱이 카메라를 쓰는지 확인하세요."
                        print(message + " (내장 카메라는 --camera 1)", flush=True)
                        notice(message)
                else:
                    dark_since, dark_warned = None, False
                if args.mirror:
                    frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                timestamp = max(timestamp + 1, int(now * 1000))
                hands = assign_hands(detect_hands(cv2, mp, detector, detection_frame(cv2, frame, args.detect_width),
                                                  timestamp, args.mirror))
                signals = build_signals(hands, w, h, args.mirror)
                enabled = not ui["paused"] and not ui["show_help"]
                events = typer.update(signals, now) if enabled else []
                for event in events:
                    kind = event[0]
                    if os_mouse and kind in ("move", "click", "down", "up"):
                        os_mouse.handle(event)
                    elif kind == "click":
                        point = (typer.cursor[0] * w, typer.cursor[1] * h)
                        if event[1] == "right":
                            ui["menu"] = point
                        elif ui["menu"] is not None:
                            chosen = hit(menu_rects(ui["menu"], w, h), point)
                            ui["menu"] = None
                            if chosen and chosen != "close":
                                action(chosen)
                        else:
                            chosen = hit(button_rects(w, h), point)
                            if chosen:
                                action(chosen)
                    elif kind == "mode" and event[1] == "keyboard":
                        ui["menu"] = None
                if log:
                    log.write(json.dumps({
                        "t": round(now, 4), "mode": typer.mode,
                        "hands": {label: {"seen_as": hands[label][2],
                                          "ratios": [round(v, 4) for v in sig.ratios], "facing": round(sig.facing, 3),
                                          "pose": sig.pose, "curled": list(sig.curled)}
                                  for label, sig in signals.items()},
                        "events": [list(e) for e in events]}, ensure_ascii=False) + "\n")
                points = {label: [(p.x * w, p.y * h) for p in landmarks] for label, (landmarks, _, _) in hands.items()}
                view = draw_hud(frame, typer, points, ui, fonts, fps)
                cv2.imshow(WINDOW, view)
                code = cv2.waitKey(1) & 0xFF
                reasons = []
                if now - last_snapshot >= 1.0:
                    reasons.append(f"auto-{snapshot_count % 40:02d}")
                if any(event[0] == "mode" for event in events):
                    reasons.append(f"mode-{snapshot_count:04d}-{typer.mode}")
                if code in (ord("g"), ord("G")):
                    reasons.append(f"manual-{snapshot_count:04d}")
                    notice("스크린샷 저장")
                if reasons:
                    shot = annotate_snapshot(cv2, view, hands, signals, typer, w, h)
                    for reason in reasons:
                        cv2.imwrite(str(args.snapshots / f"{reason}.jpg"), shot, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    snapshot_count += 1
                    last_snapshot = now
                if code in (27, ord("q"), ord("Q")):
                    exit_reason = "Q/ESC 키"
                    break
                if is_window_closed(cv2, WINDOW):
                    exit_reason = "영상 창 닫힘"
                    break
                if code in TEST_KEYS:
                    typer.press_finger(*TEST_KEYS[code], now)
                elif code == 32:
                    typer.make_fist("Left", now)
                elif code in (8, 127):
                    typer.make_fist("Right", now)
                elif code in (ord("s"), ord("S")):
                    action("save")
                elif code in (ord("f"), ord("F")):
                    fullscreen = not fullscreen
                    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN,
                                          cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
                elif code in (ord("p"), ord("P")):
                    action("pause")
                elif code in (ord("h"), ord("H")):
                    action("help")
                elif code in (ord("k"), ord("K")):
                    ui["show_map"] = not ui["show_map"]
                elif code in (ord("t"), ord("T")):
                    ui["tip_labels"] = not ui["tip_labels"]
                elif code in (ord("d"), ord("D")):
                    ui["debug"] = not ui["debug"]
                elif code in (ord("["), ord("]")):
                    gap = typer.hands["Left"].pinch.exit - typer.enter
                    enter = min(0.5, max(0.1, round(typer.enter + (0.02 if code == ord("]") else -0.02), 2)))
                    typer.set_pinch(enter, round(enter + gap, 2))
                    notice(f"꼬집기 판정 {enter:.2f}")
    finally:
        if capture is not None:
            capture.release()
        if log:
            log.close()
        cv2.destroyAllWindows()
        if exit_reason:
            print("종료 이유: " + exit_reason)
        print(f"꼬집기 판정: enter {typer.enter:.2f}, exit {typer.hands['Left'].pinch.exit:.2f}")
        print("\n입력 결과:\n" + typer.ime.text)


def main():
    from project01 import GSTREAMER, ROOT, positive_float

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=0, help="카메라 번호 (macOS: iPhone 연결 시 0)")
    parser.add_argument("--pipeline", help="GStreamer 문자열. 'usb'는 practice의 /dev/video0 설정")
    parser.add_argument("--model", type=Path, default=ROOT / "src/models/MediaPipe/hand_landmarker.task")
    parser.add_argument("--font", help="한글 폰트 ttf/ttc/otf 경로")
    parser.add_argument("--mirror", action="store_true", help="좌우 반전 (카메라가 사람을 마주 보는 셀카 배치)")
    parser.add_argument("--resolution", type=parse_resolution, default="auto",
                        help="카메라 번호로 열 때 요청할 해상도. auto: macOS 1280x720, 그 밖(Jetson) 640x480. "
                             "--pipeline이면 파이프라인 문자열이 정한다")
    parser.add_argument("--detect-width", type=int, default=640,
                        help="손 검출에 넣는 영상의 최대 가로 크기. 화면은 원래 해상도로 그린다")
    parser.add_argument("--pinch-enter", type=positive_float, default=PINCH_ENTER,
                        help="엄지 끝~손가락 끝마디 거리 / 손목~중지 뿌리 길이가 이 값 아래면 꼬집은 것으로 본다")
    parser.add_argument("--pinch-exit", type=positive_float, default=PINCH_EXIT, help="이 값 위로 벌리면 1회로 센다")
    parser.add_argument("--fist-hold", type=positive_float, default=0.18, help="주먹 유지 시간 (초)")
    parser.add_argument("--gain", type=positive_float, default=2.0, help="손 이동 대비 커서 이동 배율")
    parser.add_argument("--os-mouse", action="store_true", help="실제 OS 마우스를 움직인다 (pynput 필요)")
    parser.add_argument("--os-scale", type=positive_float, default=1400.0, help="OS 마우스: 화면 폭 대비 이동 픽셀")
    parser.add_argument("--log", type=Path, help="프레임별 손 신호와 이벤트를 jsonl로 남긴다 (임계값 조정용)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().with_name("pinch_output.txt"))
    parser.add_argument("--snapshots", type=Path, default=Path(__file__).resolve().with_name("data") / "snapshots",
                        help="진단 스크린샷 폴더 (1초마다·모드 전환 시·G 키)")
    parser.add_argument("--demo", action="store_true", help="카메라 없이 고정 데이터의 '안녕하세요'를 재생")
    args = parser.parse_args()
    if args.demo:
        demo()
        return
    if args.pinch_exit <= args.pinch_enter:
        parser.error("--pinch-exit는 --pinch-enter보다 커야 합니다.")
    if args.detect_width < 160:
        parser.error("--detect-width는 160 이상으로 지정하세요.")
    if args.pipeline == "usb":
        args.pipeline = GSTREAMER
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
