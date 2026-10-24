"""카메라 영상 위에 입력기 화면(선 HUD)을 그리는 모듈"""

from functools import lru_cache
import math
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from keyboard import (DISTAL_SEGMENTS, DRAG_MIN, DRAG_STROKES, FINGER_MCPS, FINGER_TIPS, HAND_CONNECTIONS, MIDDLE_MCP,
                      THUMB_TIP, VOWELS, WRIST, cycle_queue)


# 색은 (R, G, B, 불투명도)임. 가는 선과 눈금으로 정보를 나타내고 강조색은 파랑 한 가지만 씀
WHITE = (250, 250, 252, 255)
MUTED = (230, 231, 236, 215)
LINE = (236, 240, 248, 225)
LINE_DIM = (236, 240, 248, 95)
ACCENT = (158, 197, 255, 255)
ACCENT_FILL = (46, 74, 128, 150)
BACKDROP = (8, 10, 16, 120)  # 밝은 영상 위에서도 선과 글자가 보이게 까는 어두운 면
HALO = (8, 10, 16, 120)  # 가는 선 아래에 까는 어두운 테두리
OK_GREEN = (126, 224, 176, 255)
DELETE_RED = (255, 128, 116, 255)
OUTLINE = (28, 29, 34, 170)  # 밝은 영상 위 글자의 외곽선

FONT_SIZES = (16, 21, 29)
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Ubuntu와 Jetson의 fonts-noto-cjk
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
)

CYCLE_ANIM = 0.25  # 순환할 때 대기 글자가 한 칸 올라오는 시간(초)
# 손끝 버튼은 짧은 직선 30개를 원형으로 늘어놓은 모양이고, 직선은 버튼 반지름의 0.84~1.04배 자리에 놓음
DONUT_BARS, DONUT_INNER, DONUT_OUTER = 30, 0.84, 1.04
# 손끝 버튼 지름은 손끝 사이 가장 좁은 간격의 0.8배를 640x480 기준 16~36픽셀로 자른 값임
# 2026-10-24 Mac 화면에서 글자가 보이지 않아 키웠음(이전 12~21픽셀)
TIP_SPACING, TIP_MIN, TIP_MAX, TIP_GLYPH, TIP_WORD = 0.8, 16, 36, 0.6, 13
HUB_BARS, HUB_GAP = 96, 70  # 손바닥 원 바깥 막대 수와, 손목 쪽에 비워 두는 각도(도)
HUB_HINT = "주먹 쥐기로 지움"
# 손바닥 원의 띠 안쪽, 막대 시작, 눈금자, 바깥 막대의 반지름(손 크기에 대한 비율)
# 바깥 막대가 손가락 뿌리를 넘지 않게 작게 둠
HUB_BAND, HUB_BARS_AT, HUB_SCALE, HUB_RING = 0.23, 0.28, 0.39, 0.42
# 입력한 글자는 손바닥 가운데에 0.08초 동안 나타나 0.25초까지 머물고 0.6초까지 커지며 사라짐
ECHO_IN, ECHO_HOLD, ECHO_OUT = 0.08, 0.25, 0.6
# 주먹을 쥐면 손끝 버튼이 0.15초에 손바닥 가운데로 모이고, 손을 편 뒤 0.15초가 지나면 0.35초에 펼쳐짐
# 펼치기 전에 기다리는 시간은 주먹 판정이 프레임마다 흔들릴 때 버튼이 들썩이지 않게 하기 위함
SPREAD_OPEN, SPREAD_CLOSE, SPREAD_HOLD = 0.35, 0.15, 0.15

# --- 글꼴과 글자 ---

def load_fonts():
    """설치된 한글 글꼴을 작음·보통·큼 세 크기로 불러오는 함수"""
    path = next((Path(name) for name in FONT_CANDIDATES if Path(name).is_file()), None)
    if path is None:
        raise FileNotFoundError("한글 폰트를 찾지 못했습니다. FONT_CANDIDATES에 글꼴 경로를 더하세요.")
    return tuple(ImageFont.truetype(str(path), size) for size in FONT_SIZES)


# 같은 글자를 매 프레임 다시 그리지 않도록 글꼴 크기, 글자 폭, 글자 이미지를 캐시함
@lru_cache(maxsize=96)
def hud_font(font, size):
    return font.font_variant(size=size)


@lru_cache(maxsize=1024)
def hud_text_width(text, font):
    return font.getlength(text)


@lru_cache(maxsize=384)
def hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill):
    """글자 하나를 작은 RGBA 이미지로 그려 기준점까지의 위치와 함께 돌려주는 함수"""
    left, top, right, bottom = font.getbbox(text, anchor=anchor, stroke_width=stroke_width)
    sprite = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(sprite).text((-left, -top), text, font=font, fill=fill, anchor=anchor,
                               stroke_width=stroke_width, stroke_fill=stroke_fill)
    return sprite, left, top


def ellipsize_left(text, font, available):
    """폭을 넘는 글자열의 앞부분을 "…"로 줄이는 함수"""
    # 입력 줄은 마지막 글자가 보여야 하므로 뒤를 남김. 남길 글자 수를 이분 탐색함
    if hud_text_width(text, font) <= available:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if hud_text_width("…" + text[-middle:], font) <= available:
            low = middle
        else:
            high = middle - 1
    return "…" + (text[-low:] if low else "")


# --- 캔버스 ---

class HudCanvas:
    """BGR 프레임 위에 선 HUD를 그리는 클래스"""
    # 각도는 12시 방향이 0°이고 시계 방향으로 잼. 크기는 640x480 기준 값에 화면 배율(scale)을 곱함
    # 번지는 효과(glow)는 따로 그린 층을 흐리게 해서 overlay 아래에 깖

    def __init__(self, frame, fonts):
        self.h, self.w = frame.shape[:2]
        self.scale = min(self.w / 640, self.h / 480)
        self.stroke = max(1, round(self.scale))
        self.small, self.normal, self.large = (hud_font(font, max(8, round(size * self.scale)))
                                               for font, size in zip(fonts, (11, 14, 21)))
        self.picture = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        self.overlay = Image.new("RGBA", self.picture.size)
        self.draw = ImageDraw.Draw(self.overlay)
        self.glow = Image.new("RGBA", self.picture.size)
        self.glow_draw = ImageDraw.Draw(self.glow)

    def write(self, position, text, font, fill, anchor="la", stroke_width=0, stroke_fill=None, glow=False):
        """글자를 position에 anchor 기준으로 놓는 메서드"""
        sprite, left, top = hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill)
        self.overlay.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))
        if glow:
            self.glow.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))

    @staticmethod
    def polar(center, radius, degrees):
        a = math.radians(degrees)
        return center[0] + radius * math.sin(a), center[1] - radius * math.cos(a)

    def _line_width(self, width):
        return max(1, round(width if width is not None else self.scale))

    def hairline_circle(self, center, radius, color, width=None, fill=None, glow=False):
        """어두운 테두리를 깐 가는 원을 그리는 메서드"""
        x, y = center
        line = self._line_width(width)
        box = (x - radius, y - radius, x + radius, y + radius)
        if fill is not None:
            self.draw.ellipse(box, fill=fill)
        self.draw.ellipse((x - radius - 1, y - radius - 1, x + radius + 1, y + radius + 1), outline=HALO,
                          width=line + 2)
        self.draw.ellipse(box, outline=color, width=line)
        if glow:
            self.glow_draw.ellipse(box, outline=color, width=line + 2)

    def ticks(self, center, radius, length, count, color, lit=0, lit_color=None, width=None, start=0.0, end=360.0,
              glow=False):
        """원 둘레에 짧은 눈금 count개를 그리고 앞에서부터 lit개를 켜는 메서드"""
        line = self._line_width(width)
        span = end - start
        # 한 바퀴면 끝 눈금이 첫 눈금과 겹치지 않게 count등분, 호면 양 끝에 눈금이 오게 count-1등분함
        step = span / count if span >= 360 else span / max(1, count - 1)
        for k in range(count):
            angle = start + k * step
            on = k < lit
            a, b = self.polar(center, radius, angle), self.polar(center, radius + length, angle)
            ink = (lit_color or color) if on else color
            self.draw.line((a, b), fill=ink, width=line)
            if glow and on:
                self.glow_draw.line((a, b), fill=ink, width=line + 2)

    def dashed_circle(self, center, radius, color, dashes=32, duty=0.5, width=None):
        x, y = center
        line = self._line_width(width)
        box = (x - radius, y - radius, x + radius, y + radius)
        step = 360 / dashes
        for k in range(dashes):
            begin = -90 + k * step
            self.draw.arc(box, begin, begin + step * duty, fill=color, width=line)

    def pointer(self, center, radius, degrees, size, color, glow=False):
        """원 바깥에서 degrees 방향을 가리키는 작은 삼각형을 그리는 메서드"""
        tip = self.polar(center, radius + size, degrees)
        left = self.polar(center, radius, degrees - size / max(radius, 1) * 40)
        right = self.polar(center, radius, degrees + size / max(radius, 1) * 40)
        self.draw.polygon((tip, left, right), fill=color)
        if glow:
            self.glow_draw.polygon((tip, left, right), fill=color)

    @staticmethod
    def _box(center, radius):
        return center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius

    def arc(self, center, radius, start, end, color, width=None, glow=False):
        line = self._line_width(width)
        # Pillow arc는 3시 방향이 0°라 12시 기준 각도에서 90°를 뺌
        self.draw.arc(self._box(center, radius), start - 90, end - 90, fill=color, width=line)
        if glow:
            self.glow_draw.arc(self._box(center, radius), start - 90, end - 90, fill=color, width=line + 2)

    def band(self, center, inner, outer, start, end, fill, glow=False):
        """반지름 inner에서 outer까지의 두꺼운 호를 그리는 메서드"""
        width = max(1, round(outer - inner))
        self.draw.arc(self._box(center, outer), start - 90, end - 90, fill=fill, width=width)
        if glow:
            self.glow_draw.arc(self._box(center, outer), start - 90, end - 90, fill=fill, width=width)

    def glow_line(self, points, color, width):
        self.draw.line(points, fill=HALO, width=width + 2, joint="curve")
        self.draw.line(points, fill=color, width=width, joint="curve")
        self.glow_draw.line(points, fill=color, width=width + 3, joint="curve")

    def clear_circle(self, center, radius):
        """그동안 그린 HUD를 원 영역만큼 지우는 메서드"""
        # 버튼 둘레를 비워 뒤에 그린 요소가 버튼을 가리지 않게 함
        box = self._box(center, radius)
        self.draw.ellipse(box, fill=(0, 0, 0, 0))
        self.glow_draw.ellipse(box, fill=(0, 0, 0, 0))

    def panel(self, bounds, active=False):
        """어두운 면과 가는 테두리로 된 둥근 사각형 패널을 그리는 메서드"""
        # Pillow 12.3은 좌표가 소수이고 폭이 반지름의 2배에 가까우면 ValueError를 냄(무작위 2만 회 중 8%)
        # 좌표를 정수로 반올림하고 반지름을 짧은 변의 절반 이하로 제한하면 생기지 않음
        x1, y1, x2, y2 = (round(v) for v in bounds)
        radius = max(0, min(round(4 * self.scale), (x2 - x1) // 2, (y2 - y1) // 2))
        self.draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=ACCENT_FILL if active else BACKDROP,
                                    outline=ACCENT if active else LINE_DIM, width=self._line_width(None))

    def finish(self):
        """번짐 층과 overlay를 영상에 합성해 BGR 프레임으로 돌려주는 메서드"""
        picture = self.picture
        box = self.glow.getbbox()
        if box is not None:
            # 그린 영역만 잘라 절반으로 줄이고 흐리게 한 뒤 다시 키워 더함. 화면 전체를 흐리게 하는 것보다 빠름
            pad = round(8 * self.scale)
            x1, y1 = max(0, box[0] - pad), max(0, box[1] - pad)
            x2, y2 = min(picture.width, box[2] + pad), min(picture.height, box[3] + pad)
            region = self.glow.crop((x1, y1, x2, y2))
            small = region.reduce(2) if min(region.size) >= 4 else region
            blurred = small.filter(ImageFilter.GaussianBlur(max(1.5, 2.5 * self.scale))).resize(region.size,
                                                                                               Image.BILINEAR)
            picture.paste(Image.alpha_composite(picture.crop((x1, y1, x2, y2)), blurred), (x1, y1))
        picture = Image.alpha_composite(picture, self.overlay).convert("RGB")
        return cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)


# --- 작은 계산 ---

def write(canvas, position, text, font, fill, anchor="la", outline=False):
    """글자를 쓰고, outline이면 어두운 외곽선을 두르는 함수"""
    canvas.write(position, text, font, fill, anchor, canvas.stroke if outline else 0, OUTLINE if outline else None)


def faded(color, k):
    return color[:3] + (max(0, min(255, round(color[3] * k))),)


def with_alpha(color, alpha):
    return color[:3] + (round(alpha),)


def bearing(center, point):
    """center에서 point로 가는 방향 각도를 구하는 함수"""
    return math.degrees(math.atan2(point[0] - center[0], center[1] - point[1])) % 360


def toward(palm, point, spread):
    """손바닥 가운데(spread 0)와 제자리(spread 1) 사이의 위치를 구하는 함수"""
    return palm[0] + (point[0] - palm[0]) * spread, palm[1] + (point[1] - palm[1]) * spread


def finger_path_point(points, finger, start, fraction):
    """손끝 버튼 자리에서 손가락 관절을 따라 PIP까지 가는 경로 위의 점을 구하는 함수"""
    # 순환 대기 글자를 이 경로에 놓음. 끝을 PIP로 두어 손바닥 원과 겹치지 않게 함
    mcp = FINGER_MCPS[finger]
    path = [start] + [points[mcp + i] for i in (3, 2, 1)]
    lengths = [math.dist(a, b) for a, b in zip(path, path[1:])]
    remaining = max(0.0, min(1.0, fraction)) * sum(lengths)
    for (a, b), length in zip(zip(path, path[1:]), lengths):
        if remaining <= length or length == 0 and remaining == 0:
            k = remaining / length if length else 0.0
            return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        remaining -= length
    return path[-1]


def tip_home(points, finger, diameter):
    """손끝 바로 바깥의 버튼 자리와 끝마디 방향 단위 벡터를 구하는 함수"""
    tx, ty = points[FINGER_TIPS[finger]]
    dx, dy = tx - points[DISTAL_SEGMENTS[finger][0]][0], ty - points[DISTAL_SEGMENTS[finger][0]][1]
    norm = math.hypot(dx, dy) or 1.0
    return (tx + dx / norm * diameter * 0.85, ty + dy / norm * diameter * 0.85), (dx / norm, dy / norm)


# --- 애니메이션 ---

def spread_motion(spreads, hand, closed, now):
    """손끝 버튼이 펼쳐진 정도(0에서 1 사이)를 구하는 함수"""
    # spreads에 손마다 (진행, 직전 시각, 마지막으로 주먹이던 시각)을 남김. 처음 보이는 손은 0에서 펼쳐짐
    value, last, closed_at = spreads.get(hand, (0.0, now, None))
    if closed:
        closed_at = now
    step = max(0.0, now - last)
    if closed_at is not None and now - closed_at < SPREAD_HOLD:
        value = max(0.0, value - step / SPREAD_CLOSE)
    else:
        value = min(1.0, value + step / SPREAD_OPEN)
    spreads[hand] = (value, now, closed_at)
    return 1 - (1 - value) ** 3


def cycle_motion(cycles, slot, queue, now):
    """순환 애니메이션 진행과 직전 대기열을 구하는 함수"""
    # 대기열이 한 칸 돌았을 때만 움직이고, 그 밖의 변화는 바로 바꿈
    old = cycles.get(slot)
    if old is None or old[0] != queue:
        turned = old is not None and len(queue) > 1 and old[0][1:] == queue[:-1]
        cycles[slot] = (queue, now if turned else None, old[0] if turned else None)
    queue, since, previous = cycles[slot]
    if since is None or now - since >= CYCLE_ANIM:
        return 1.0, None
    linear = (now - since) / CYCLE_ANIM
    return 1 - (1 - linear) ** 3, previous


def echo_look(age):
    """입력 표시가 나온 지 age초일 때의 불투명도와 배율을 구하는 함수"""
    if age < 0 or age >= ECHO_OUT:
        return None
    if age < ECHO_IN:
        return age / ECHO_IN, 0.85 + 0.15 * age / ECHO_IN
    if age < ECHO_HOLD:
        return 1.0, 1.0
    out = (age - ECHO_HOLD) / (ECHO_OUT - ECHO_HOLD)
    return 1 - out, 1 + 0.15 * out


def echo_view(ui, typer, now):
    """손바닥 가운데에 띄울 입력 표시의 손, 글자, 불투명도, 배율을 구하는 함수"""
    if typer.echo is None:
        return None
    seen = ui.get("echo")
    if seen is None or seen[0] != typer.echo[0]:  # 새 입력이면 표시 시작 시각을 지금으로 둠
        ui["echo"] = seen = (typer.echo[0], now)
    look = echo_look(now - seen[1])
    return None if look is None else (typer.echo[1], typer.echo[2], *look)


# --- 손 ---

def draw_palm_hub(canvas, points, state, progresses, paths, spread, font, echo=None):
    """손바닥 가운데 원형 표시를 그리는 함수"""
    # 안쪽 띠의 막대는 손가락마다 다가온 정도, 삼각형은 누르는 손가락, 바깥 막대는 주먹 지움 진행을 나타냄
    s = canvas.scale
    palm = (WRIST, 5, 9, 13, 17)
    c = (sum(points[i][0] for i in palm) / 5, sum(points[i][1] for i in palm) / 5)
    size = math.dist(points[WRIST], points[MIDDLE_MCP])
    r_band, r_bars, r_scale, r_ring = HUB_BAND * size, HUB_BARS_AT * size, HUB_SCALE * size, HUB_RING * size
    wrist = bearing(c, points[WRIST])
    away = wrist + 180  # 각도를 손가락 쪽 가운데 둘레로 펴서 0°와 360° 경계를 넘지 않게 함
    angles = [away + ((bearing(c, path[0]) - away + 180) % 360) - 180 for path in paths]
    lo, hi = min(angles) - 22, max(angles) + 22
    k = spread
    canvas.ticks(c, r_ring, 0.04 * size, HUB_BARS, (236, 240, 248, 90), lit=round(state.fist.progress * HUB_BARS),
                 lit_color=DELETE_RED, start=wrist + HUB_GAP / 2, end=wrist + 360 - HUB_GAP / 2,
                 width=max(1, round(1.2 * s)))
    if k > 0.05:  # 손끝 버튼이 펼쳐져 있을 때만 안쪽 층을 그림
        canvas.arc(c, r_scale, lo - 10, hi + 10, faded(LINE, 0.8 * k))
        canvas.ticks(c, r_scale - 0.02 * size, 0.02 * size, int((hi - lo + 20) / 3) + 1, faded(LINE_DIM, k),
                     start=lo - 10, end=hi + 10, width=1)
        canvas.ticks(c, r_scale - 0.04 * size, 0.04 * size, int((hi - lo + 20) / 15) + 1, faded(LINE, 0.8 * k),
                     start=lo - 10, end=hi + 10, width=1)
        canvas.band(c, r_band, r_bars - 0.02 * size, lo, hi, faded(ACCENT_FILL, 0.7 * k))
        canvas.arc(c, r_band, lo, hi, faded(LINE_DIM, k), width=1)
        # 삼각형은 누르는 손가락을, 누르는 손가락이 없으면 0.2 넘게 다가온 손가락 중 가장 많이 다가온 손가락을 가리킴
        target = state.pinch.active
        if target is None and max(progresses) > 0.2:
            target = max(range(4), key=progresses.__getitem__)
        for finger, angle in enumerate(angles):
            pressed = state.pinch.active == finger
            if pressed:
                canvas.band(c, r_band, r_bars - 0.02 * size, angle - 8, angle + 8, faded(ACCENT, 0.55 * k), glow=True)
            amount = 1.0 if pressed else progresses[finger]
            ink = faded(ACCENT, k) if amount > 0 else faded(LINE_DIM, k)
            for offset in (-3.5, 0, 3.5):
                length = (0.02 + 0.08 * amount) * size
                a = canvas.polar(c, r_bars, angle + offset)
                b = canvas.polar(c, r_bars + length, angle + offset)
                canvas.draw.line((a, b), fill=ink, width=max(2, round(2.5 * s)))
                if pressed:
                    canvas.glow_draw.line((a, b), fill=ink, width=max(3, round(4 * s)))
        if target is not None:
            canvas.pointer(c, r_scale + 2 * s, angles[target], 5 * s, faded(ACCENT, k), glow=True)
        canvas.dashed_circle(c, 0.10 * size, faded(LINE_DIM, k), dashes=24)
    # 가운데 문구는 주먹 지움 진행 중이면 "지움", 입력 직후면 그 글자, 평소에는 안내 문구임
    if state.fist.progress > 0:
        canvas.write(c, "지움", font, DELETE_RED, "mm", canvas.stroke, OUTLINE)
    elif echo is not None:
        text, alpha, grow = echo
        ratio = 0.18 if len(text) == 1 else 0.09
        canvas.write(c, text, hud_font(font, max(10, round(ratio * size * grow))), faded(WHITE, alpha), "mm",
                     canvas.stroke, faded(OUTLINE, alpha), glow=True)
    elif k > 0.05:
        canvas.write(c, HUB_HINT, font, faded(MUTED, k), "mm", canvas.stroke, OUTLINE)


def draw_cycle_queue(canvas, ui, key, points, finger, queue, home, direction, diameter, palm, spread, fonts, now):
    """손가락을 따라 순환 대기 글자를 그리고 손끝 버튼의 지금 중심을 돌려주는 함수"""
    # 다음 글자들이 PIP 쪽에서 기다리다가 순환하면 한 칸씩 손끝으로 올라옴
    s = canvas.scale
    eased, previous = cycle_motion(ui.setdefault("cycles", {}), key, tuple(queue), now)
    waiting = len(queue) - 1
    small_d = diameter * 0.7 * spread
    wait_font = hud_font(fonts[1], max(8, round(small_d * 0.6)))
    if spread >= 0.05:
        for slot in range(waiting, 0, -1):
            fraction = slot / waiting
            if previous is not None and slot < waiting:  # 한 칸 아래 자리에서 올라오는 중
                fraction = (slot + 1) / waiting + (fraction - (slot + 1) / waiting) * eased
            wx, wy = toward(palm, finger_path_point(points, finger, home, fraction), spread)
            canvas.clear_circle((wx, wy), small_d / 2 + 4 * s)
            if previous is not None and slot == waiting:  # 마지막 자리는 손바닥 쪽에서 나타남
                alpha = round(230 * eased / 16) * 16
                write(canvas, (wx, wy), queue[slot], wait_font, with_alpha(WHITE, alpha * spread), "mm", True)
                continue
            canvas.hairline_circle((wx, wy), small_d / 2, with_alpha(LINE, (150 - 40 * (slot - 1)) * spread),
                                   fill=with_alpha(BACKDROP, BACKDROP[3] * spread))
            write(canvas, (wx, wy), queue[slot], wait_font, with_alpha(WHITE, (215 - 50 * (slot - 1)) * spread), "mm")
    if previous is None:
        return home
    # 방금 입력된 글자는 손끝에서 조금 올라가며 사라지고, 다음 글자는 한 칸 아래에서 손끝으로 올라옴
    lift = diameter * 0.8 * eased
    alpha = round(255 * (1 - eased) / 16) * 16
    (cx, cy), (dx, dy) = home, direction
    write(canvas, toward(palm, (cx + dx * lift, cy + dy * lift), spread), previous[0],
          hud_font(fonts[1], max(8, round(diameter * TIP_GLYPH))), with_alpha(WHITE, alpha * spread), "mm", True)
    return finger_path_point(points, finger, home, 1 / waiting * (1 - eased))


def draw_tip_button(canvas, pinch, finger, text, center, diameter, spread, progress, ratios, fonts):
    """손끝 버튼을 그리는 함수"""
    # 다가온 정도만큼 흰 직선을 켜고, 누르는 중에는 안쪽을 파랗게 칠하고 떼기 진행만큼 밝은 파랑을 켬
    s = canvas.scale
    px, py = center
    size = diameter * spread
    word = hud_font(fonts[0], max(8, round(TIP_WORD * s * spread)))
    radius = max(size, hud_text_width(text, word) + 6 * s * spread) / 2  # 여러 글자 라벨이 들어가게 넓힘
    inner, outer = radius * DONUT_INNER, radius * DONUT_OUTER
    active = pinch.active == finger
    if active and ratios is not None:
        lit, base, lit_ink = round(pinch.release_progress(ratios) * DONUT_BARS), ACCENT[:3] + (90,), ACCENT
    else:
        lit = round(progress * DONUT_BARS) if spread >= 1 else 0
        base, lit_ink = (236, 240, 248, 70), LINE
    canvas.clear_circle((px, py), outer + 3 * s * spread)
    fill = with_alpha(ACCENT_FILL, (ACCENT_FILL[3] + 60) * spread) if active else \
        with_alpha(BACKDROP, BACKDROP[3] * spread)
    canvas.draw.ellipse((px - inner, py - inner, px + inner, py + inner), fill=fill)
    canvas.ticks((px, py), inner, outer - inner, DONUT_BARS, with_alpha(base, base[3] * spread), lit=lit,
                 lit_color=with_alpha(lit_ink, lit_ink[3] * spread), width=max(1, round(s)), glow=active)
    write(canvas, (px, py), text, hud_font(fonts[1], max(8, round(size * TIP_GLYPH))) if len(text) == 1 else word,
          with_alpha(WHITE, 255 * spread), "mm")


def draw_vowel_drag(canvas, drag, points, diameter, fonts):
    """모음 끌기의 지나온 길, 기준 원, 지금 떼면 들어갈 획을 그리는 함수"""
    s, w, h = canvas.scale, canvas.w, canvas.h
    margin = 16 * s
    passed = drag["drag"] is not None
    ink = ACCENT if passed else LINE
    path = drag["points"]
    ox, oy = path[0]
    radius = DRAG_MIN * drag["size"]
    canvas.dashed_circle((ox, oy), radius, LINE_DIM)
    canvas.ticks((ox, oy), radius, 5 * s, 4, LINE)
    if len(path) > 1:
        canvas.glow_line(path, ink, max(2, round(3 * s)))
    ex, ey = path[-1]
    r = max(3, 3.5 * s)
    canvas.draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=ink)
    # 획 미리 보기는 손끝 버튼을 가리지 않게 손목에서 중지 뿌리 방향으로 버튼 줄 바깥에 둠
    badge = diameter * 1.3
    ux, uy = points[MIDDLE_MCP][0] - points[WRIST][0], points[MIDDLE_MCP][1] - points[WRIST][1]
    norm = math.hypot(ux, uy) or 1.0
    bx, by = ex + ux / norm * diameter * 2.6, ey + uy / norm * diameter * 2.6
    bx = min(max(bx, badge / 2 + margin), w - badge / 2 - margin)
    by = min(max(by, badge / 2 + margin), h - badge / 2 - margin)
    canvas.hairline_circle((bx, by), badge / 2, ACCENT if passed else LINE,
                           fill=ACCENT_FILL if passed else BACKDROP, glow=passed)
    if passed:
        canvas.pointer((bx, by), badge / 2 + 2 * s, drag["pointer"], 5 * s, ACCENT, glow=True)
    write(canvas, (bx, by), VOWELS[DRAG_STROKES[drag["drag"]]],
          hud_font(fonts[1], max(10, round(badge * 0.62))), WHITE, "mm", True)


def draw_hand(canvas, typer, label, points, ui, fonts, now):
    """손 하나의 골격선, 손바닥 원형 표시, 손끝 버튼, 모음 끌기를 그리는 함수"""
    s = canvas.scale
    state = typer.hands[label]
    for a, b in HAND_CONNECTIONS:
        canvas.draw.line((points[a], points[b]), fill=(237, 242, 250, 95), width=canvas.stroke)
    tips = [points[tip] for tip in FINGER_TIPS]
    spacing = min(math.dist(a, b) for a, b in zip(tips, tips[1:]))
    diameter = min(TIP_MAX * s, max(TIP_MIN * s, spacing * TIP_SPACING))
    ratios = None if state.signal is None else state.signal.ratios
    progresses = [0.0] * 4 if ratios is None else state.pinch.progress(ratios)
    spread = spread_motion(ui["spreads"], label, state.pinch.closed, now)
    palm = ((points[WRIST][0] + points[MIDDLE_MCP][0]) / 2, (points[WRIST][1] + points[MIDDLE_MCP][1]) / 2)

    echo = echo_view(ui, typer, now)
    paths = [[toward(palm, points[FINGER_MCPS[f] + i], spread) for i in range(4)]
             + [toward(palm, tip_home(points, f, diameter)[0], spread)] for f in range(4)]
    draw_palm_hub(canvas, points, state, progresses, paths, spread, canvas.small,
                  None if echo is None or echo[0] != label else echo[1:])
    for finger in range(4):
        queue = cycle_queue(typer, label, finger)
        if not queue:
            continue
        home, direction = tip_home(points, finger, diameter)
        center = home
        if len(queue) > 1:
            center = draw_cycle_queue(canvas, ui, (label, finger), points, finger, queue, home, direction, diameter,
                                      palm, spread, fonts, now)
        if spread >= 0.05:
            draw_tip_button(canvas, state.pinch, finger, queue[0], toward(palm, center, spread), diameter, spread,
                            progresses[finger], ratios, fonts)
    thumb, r = points[THUMB_TIP], 3 * s
    canvas.draw.ellipse((thumb[0] - r, thumb[1] - r, thumb[0] + r, thumb[1] + r), fill=(245, 248, 252, 200))
    drag = typer.drag_view(label)
    if drag is not None:
        draw_vowel_drag(canvas, drag, points, diameter, fonts)


# --- 화면 위와 아래 ---

def draw_input_line(canvas, typer, now):
    """화면 위에 입력 한 줄을 그리고 패널 아래 y를 돌려주는 함수"""
    # 확정 전 자음 후보 아래에는 파랑 밑줄을, 그 밖에는 깜빡이는 커서를 둠
    s, w, stroke = canvas.scale, canvas.w, canvas.stroke
    margin = 16 * s
    text = typer.ime.text.split("\n")[-1]
    font = canvas.large if text else canvas.normal
    shown = ellipsize_left(text or "손끝을 꼬집어 입력", font, min(w - 2 * margin, 420 * s) - 36 * s)
    width = min(w - 2 * margin, max(140 * s, hud_text_width(shown, font) + 40 * s))
    height = (40 if text else 34) * s
    x1 = (w - width) / 2
    canvas.panel((x1, margin, x1 + width, margin + height))
    count = int(width / (6 * s))
    for k in range(1, count):
        x = x1 + k * width / count
        length = (5 if k % 5 == 0 else 3) * s
        canvas.draw.line((x, margin + height, x, margin + height - length), fill=LINE_DIM, width=stroke)
    write(canvas, (w / 2, margin + height / 2), shown, font, WHITE if text else MUTED, "mm")
    text_right = w / 2 + hud_text_width(shown, font) / 2
    if text and typer.pending is not None and typer.pending.key != "PUNCT":
        last = hud_text_width(shown[-1:], font)
        canvas.glow_line(((text_right - last, margin + height - 9 * s), (text_right, margin + height - 9 * s)),
                         ACCENT, max(2, round(2 * s)))
    elif text and int(now * 2) % 2 == 0:
        canvas.draw.line((text_right + 4 * s, margin + 12 * s, text_right + 4 * s, margin + height - 12 * s),
                         fill=MUTED, width=stroke)
    return margin + height


def draw_word_panel(canvas, word, top):
    """단어 입력 시험의 안내 패널을 그리는 함수"""
    s, w = canvas.scale, canvas.w
    margin = 16 * s
    pw = min(w - 2 * margin, max(hud_text_width(word["title"], canvas.large),
                                 hud_text_width(word["detail"], canvas.small)) + 40 * s)
    canvas.panel(((w - pw) / 2, top, (w + pw) / 2, top + 62 * s), active=word["tone"] == "ok")
    ink = {"ok": OK_GREEN, "skip": MUTED}.get(word["tone"], WHITE)
    write(canvas, (w / 2, top + 22 * s), word["title"], canvas.large, ink, "mm", True)
    write(canvas, (w / 2, top + 47 * s), word["detail"], canvas.small, MUTED, "mm", True)


def draw_hud(frame, typer, hands, ui, fonts):
    """한 프레임의 화면을 그려 BGR 프레임으로 돌려주는 함수"""
    # hands는 손마다 픽셀 좌표 21개이고, ui는 애니메이션과 안내 상태를 프레임 사이에 이어 담음
    canvas = HudCanvas(frame, fonts)
    now = time.monotonic()
    spreads = ui.setdefault("spreads", {})
    for gone in set(spreads) - set(hands):  # 사라진 손은 다시 나타날 때 손바닥에서 펼쳐지게 함
        del spreads[gone]
    for label, points in hands.items():
        draw_hand(canvas, typer, label, points, ui, fonts, now)

    bottom = draw_input_line(canvas, typer, now)
    if ui.get("word") is not None:
        draw_word_panel(canvas, ui["word"], bottom + 38 * canvas.scale)
    return canvas.finish()
