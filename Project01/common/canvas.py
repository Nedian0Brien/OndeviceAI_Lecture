"""영상 위에 선 HUD를 그리는 도구.

- 영상 크기는 그대로 두고, 그 위에 RGBA 오버레이를 그려 합성한다.
- 가는 선·눈금·호로 정보를 나타내고 강조색은 파랑 한 가지다(ACCENT). 번짐(glow) 층은 흐리게 해서 오버레이 아래 깐다.
- 모든 크기는 640x480 기준 값에 `ui_scale`(영상 크기 비율)을 곱한다. 그래서 Jetson 640x480과 macOS
  1280x720에서 화면 비율이 같게 보인다.

`HudCanvas` 사용 순서
    canvas = HudCanvas(frame, fonts)          # BGR 프레임과 글꼴 3종(작음·보통·큼)
    canvas.ticks(center, radius, ...)         # 선 HUD 도구(각도는 12시 0°에서 시계 방향)
    canvas.write(position, "가", canvas.small, WHITE, "mm")
    image = canvas.finish()                   # 번짐 + 합성 → BGR 프레임

Pillow·numpy·OpenCV는 함수 안에서 import한다.
"""

from functools import lru_cache
import math
from pathlib import Path


WHITE = (250, 250, 252, 255)
MUTED = (230, 231, 236, 215)  # 보조 문구
# 선 HUD(꼬집기 입력기, 2026-10-23): 가는 선과 눈금으로 정보를 나타내고 강조는 채도 낮은 파랑 한 가지다.
# Vision Pro 앱(visionos/PinchKeyboard/HUDStyle.swift)도 같은 값을 쓴다.
LINE = (236, 240, 248, 225)  # 기본 선
LINE_DIM = (236, 240, 248, 95)  # 보조 선·꺼진 눈금
ACCENT = (158, 197, 255, 255)  # 강조 선·켜진 눈금(#9EC5FF)
ACCENT_FILL = (46, 74, 128, 150)  # 강조 면(#2E4A80 반투명)
BACKDROP = (8, 10, 16, 120)  # 밝은 영상 위에서도 선·글자가 보이게 까는 어두운 면
HALO = (8, 10, 16, 120)  # 가는 선 아래 어두운 테두리
FONT_SIZES = (16, 21, 29)  # 불러올 기본 글꼴 크기. 실제 크기는 hud_font로 화면 비율에 맞춰 바꾼다.
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Ubuntu·Jetson: fonts-noto-cjk
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
)


def find_font(requested):
    """한글 글꼴 파일 경로. requested(--font)가 있으면 그것, 없으면 일반 설치 경로에서 찾는다."""
    if requested:
        path = Path(requested).expanduser()
        if not path.is_file():
            raise FileNotFoundError("한글 폰트 파일이 없습니다: " + str(path))
        return path
    for name in FONT_CANDIDATES:
        if Path(name).is_file():
            return Path(name)
    raise FileNotFoundError("한글 폰트를 찾지 못했습니다. --font /경로/폰트.ttf 를 지정하세요.")


def load_fonts(requested=None, sizes=FONT_SIZES):
    """HUD에 넘길 글꼴 묶음(작음·보통·큼)."""
    from PIL import ImageFont

    path = str(find_font(requested))
    return tuple(ImageFont.truetype(path, size) for size in sizes)


def ui_scale(w, h):
    """640x480 기준 대비 화면 비율. 가로·세로 중 작은 쪽을 따른다."""
    return min(w / 640, h / 480)


# --- 글자 캐시: 같은 글자를 매 프레임 다시 그리지 않는다 ----------------------------

@lru_cache(maxsize=96)
def hud_font(font, size):
    """글꼴의 다른 크기 버전. 손 크기에 따라 키 글자 크기가 바뀌므로 크기별로 캐시한다."""
    return font.font_variant(size=size)


@lru_cache(maxsize=1024)
def hud_text_width(text, font):
    return font.getlength(text)


@lru_cache(maxsize=384)
def hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill):
    """글자를 작은 RGBA 이미지로 그려 재사용한다. (이미지, 기준점에서 왼쪽 위까지 x, y)를 돌려준다."""
    from PIL import Image, ImageDraw

    left, top, right, bottom = font.getbbox(text, anchor=anchor, stroke_width=stroke_width)
    sprite = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(sprite).text((-left, -top), text, font=font, fill=fill, anchor=anchor,
                               stroke_width=stroke_width, stroke_fill=stroke_fill)
    return sprite, left, top


def ellipsize_left(text, font, available):
    """폭이 available을 넘으면 앞부분을 "…"로 줄인다. 입력창은 마지막 글자가 보여야 하므로 뒤를 남긴다."""
    if hud_text_width(text, font) <= available:
        return text
    low, high = 0, len(text)  # 남길 글자 수를 이분 탐색한다(글자가 늘면 폭도 는다).
    while low < high:
        middle = (low + high + 1) // 2
        if hud_text_width("…" + text[-middle:], font) <= available:
            low = middle
        else:
            high = middle - 1
    return "…" + (text[-low:] if low else "")


# --- 도형 -------------------------------------------------------------------------

def rounded_rectangle(draw, bounds, radius, **style):
    """Pillow rounded_rectangle을 안전하게 그린다.

    Pillow 12.3은 좌표가 소수이고 폭이 반지름의 2배에 가까우면 ValueError를 낸다(무작위 2만 회 중 8%).
    좌표를 정수로 반올림하고 반지름을 짧은 변의 절반 이하로 제한하면 발생하지 않는다.
    """
    x1, y1, x2, y2 = (round(v) for v in bounds)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=max(0, min(round(radius), (x2 - x1) // 2, (y2 - y1) // 2)),
                           **style)


def glow_onto(picture, glow, scale):
    """번짐 층에 그린 영역(경계 상자)만 1/2 축소 · Gaussian blur · 확대해 picture 위에 더한다."""
    from PIL import Image, ImageFilter

    box = glow.getbbox()
    if box is None:
        return picture
    pad = round(8 * scale)
    x1, y1 = max(0, box[0] - pad), max(0, box[1] - pad)
    x2, y2 = min(picture.width, box[2] + pad), min(picture.height, box[3] + pad)
    region = glow.crop((x1, y1, x2, y2))
    small = region.reduce(2) if min(region.size) >= 4 else region
    blurred = small.filter(ImageFilter.GaussianBlur(max(1.5, 2.5 * scale))).resize(region.size, Image.BILINEAR)
    base = picture.crop((x1, y1, x2, y2))
    picture.paste(Image.alpha_composite(base, blurred), (x1, y1))
    return picture


# --- 캔버스 -----------------------------------------------------------------------

class HudCanvas:
    """BGR 프레임 위에 HUD를 그리는 판.

    picture: 원본 영상(RGBA), overlay: 그 위에 얹을 그림, glow: 흐리게 번지게 할 층
    scale: 640x480 기준 배율, stroke: 선 두께, small·normal·large: 화면 비율에 맞춘 글꼴
    """

    def __init__(self, frame, fonts):
        import cv2
        from PIL import Image, ImageDraw

        self.h, self.w = frame.shape[:2]
        self.scale = ui_scale(self.w, self.h)
        self.stroke = max(1, round(self.scale))
        self.small, self.normal, self.large = (hud_font(font, max(8, round(size * self.scale)))
                                               for font, size in zip(fonts, (11, 14, 21)))
        self.picture = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        self.overlay = Image.new("RGBA", self.picture.size)
        self.draw = ImageDraw.Draw(self.overlay)
        self.glow = Image.new("RGBA", self.picture.size)  # 번짐 층: 흐리게 해서 오버레이 아래 깐다(finish)
        self.glow_draw = ImageDraw.Draw(self.glow)

    def write(self, position, text, font, fill, anchor="la", stroke_width=0, stroke_fill=None, glow=False):
        """글자를 position에 anchor 기준으로 놓는다(anchor는 Pillow 규칙: "mm" 가운데, "la" 왼쪽 위 등).
        glow면 번짐 층에도 그린다."""
        sprite, left, top = hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill)
        self.overlay.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))
        if glow:
            self.glow.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))

    # --- 선 HUD 도구: 각도는 12시 방향 0°에서 시계 방향 ---------------------------------
    @staticmethod
    def polar(center, radius, degrees):
        a = math.radians(degrees)
        return center[0] + radius * math.sin(a), center[1] - radius * math.cos(a)

    def _line_width(self, width):
        return max(1, round(width if width is not None else self.scale))

    def hairline_circle(self, center, radius, color, width=None, fill=None, glow=False):
        """가는 원. 아래에 어두운 테두리(HALO)를 깔아 밝은 영상 위에서도 보이게 한다. fill은 안쪽 면."""
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
        """반지름 radius에서 바깥으로 length만큼의 짧은 눈금 count개(분절 링). 12시부터 시계 방향으로 lit개는
        lit_color(켜짐), 나머지는 color. 진행 표시는 연속 호 대신 켜진 눈금 수로 나타낸다."""
        line = self._line_width(width)
        span = end - start
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
        """파선 원(보조 기준선)."""
        x, y = center
        line = self._line_width(width)
        box = (x - radius, y - radius, x + radius, y + radius)
        step = 360 / dashes
        for k in range(dashes):
            begin = -90 + k * step
            self.draw.arc(box, begin, begin + step * duty, fill=color, width=line)

    def pointer(self, center, radius, degrees, size, color, glow=False):
        """원 바깥(반지름 radius)에서 degrees 방향을 가리키는 작은 삼각형."""
        tip = self.polar(center, radius + size, degrees)
        left = self.polar(center, radius, degrees - size / max(radius, 1) * 40)
        right = self.polar(center, radius, degrees + size / max(radius, 1) * 40)
        self.draw.polygon((tip, left, right), fill=color)
        if glow:
            self.glow_draw.polygon((tip, left, right), fill=color)

    def _box(self, center, radius):
        return center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius

    def arc(self, center, radius, start, end, color, width=None, glow=False):
        """일부만 그린 원(12시 0°에서 시계 방향 start → end)."""
        line = self._line_width(width)
        self.draw.arc(self._box(center, radius), start - 90, end - 90, fill=color, width=line)
        if glow:
            self.glow_draw.arc(self._box(center, radius), start - 90, end - 90, fill=color, width=line + 2)

    def band(self, center, inner, outer, start, end, fill, glow=False):
        """반지름 inner~outer의 두꺼운 호(반투명 띠)."""
        width = max(1, round(outer - inner))
        self.draw.arc(self._box(center, outer), start - 90, end - 90, fill=fill, width=width)
        if glow:
            self.glow_draw.arc(self._box(center, outer), start - 90, end - 90, fill=fill, width=width)

    def glow_line(self, points, color, width):
        """번짐을 더한 선(끌기 궤적 등)."""
        self.draw.line(points, fill=HALO, width=width + 2, joint="curve")
        self.draw.line(points, fill=color, width=width, joint="curve")
        self.glow_draw.line(points, fill=color, width=width + 3, joint="curve")

    def clear_circle(self, center, radius):
        """그동안 그린 선 HUD를 원 영역만큼 지운다(버튼 둘레를 비워 뒤 요소가 버튼을 가리지 않게)."""
        box = self._box(center, radius)
        self.draw.ellipse(box, fill=(0, 0, 0, 0))
        self.glow_draw.ellipse(box, fill=(0, 0, 0, 0))

    def panel(self, bounds, active=False, radius=None):
        """선 HUD 패널: 어두운 면 + 가는 테두리. active면 강조 면·테두리."""
        r = 4 * self.scale if radius is None else radius
        rounded_rectangle(self.draw, bounds, r, fill=ACCENT_FILL if active else BACKDROP,
                          outline=ACCENT if active else LINE_DIM, width=self._line_width(None))

    def finish(self):
        """번짐 → 오버레이 합성 → BGR 프레임."""
        import cv2
        import numpy as np
        from PIL import Image

        picture = glow_onto(self.picture, self.glow, self.scale)
        picture = Image.alpha_composite(picture, self.overlay).convert("RGB")
        return cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
