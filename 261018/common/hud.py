"""영상 위에 반투명 HUD를 그리는 도구.

그리는 방식
- 영상 크기는 그대로 두고, 그 위에 RGBA 오버레이를 합성한다. 별도 패널을 붙이지 않는다.
- 버튼과 입력창은 "유리" 재질이다. 유리 아래 영상만 살짝 흐리게 하고(material 마스크), 그 위에 중성 톤의
  반투명 색과 얇은 테두리를 칠한다. visionOS의 유리 재질·원형/캡슐 버튼 모양을 참고한 2D 표현이다.
- 모든 크기는 640x480 기준 값에 `ui_scale`(영상 크기 비율)을 곱한다. 그래서 Jetson 640x480과 macOS
  1280x720에서 화면 비율이 같게 보인다.

`HudCanvas` 사용 순서
    canvas = HudCanvas(frame, fonts)          # BGR 프레임과 글꼴 3종(작음·보통·큼)
    canvas.glass(bounds, circle=True, ...)   # 유리 버튼
    canvas.write(position, "가", canvas.small, WHITE, "mm")
    image = canvas.finish()                  # 흐림 + 합성 → BGR 프레임

Pillow·numpy·OpenCV는 함수 안에서 import한다.
"""

from functools import lru_cache
from pathlib import Path


WHITE = (250, 250, 252, 255)
MUTED = (230, 231, 236, 215)  # 보조 문구
DARK_INK = (28, 29, 34, 255)  # 밝게 눌린 버튼 위 글자
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


def blur_under(picture, mask, pad=8):
    """mask가 칠해진 영역(경계 상자 + pad)만 1/3 축소 · Gaussian blur · 확대해 picture에 합성한다.

    유리 재질 아래 영상만 흐리게 하는 용도다. 프레임 전체를 처리하던 방식보다 640x480에서 약 2.6배,
    1920x1080에서 약 2.7배 빠르다(2026-10-22 측정, Jetson 저해상도 시연과 macOS 고해상도 모두 해당).
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


# --- 캔버스 -----------------------------------------------------------------------

class HudCanvas:
    """BGR 프레임 위에 HUD를 그리는 판.

    picture: 원본 영상(RGBA), overlay: 그 위에 얹을 그림, material: 흐리게 할 유리 영역(L 마스크)
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
        self.material = Image.new("L", self.picture.size)
        self.material_draw = ImageDraw.Draw(self.material)

    def write(self, position, text, font, fill, anchor="la", stroke_width=0, stroke_fill=None):
        """글자를 position에 anchor 기준으로 놓는다(anchor는 Pillow 규칙: "mm" 가운데, "la" 왼쪽 위 등)."""
        sprite, left, top = hud_text_sprite(text, font, fill, anchor, stroke_width, stroke_fill)
        self.overlay.alpha_composite(sprite, (round(position[0] + left), round(position[1] + top)))

    def glass(self, bounds, circle=False, hover=False, pressed=False, tint=None, raised=False):
        """유리 버튼. 원(circle) 또는 캡슐(양 끝이 둥근 사각형).

        상태별 색: 기본은 어두운 반투명, hover는 조금 밝게, pressed는 거의 흰색이다.
        tint를 주면 그 색을 옅게 깐다(쉬프트 파랑, 마우스 초록 등).
        raised=True면 원 버튼에 그림자와 윗면 반사선을 더한다(엄지 마디 앱·꼬집기 키보드의 키 모양).
        캡슐은 항상 아래쪽 그림자가 있다.
        """
        x1, y1, x2, y2 = bounds
        radius = (y2 - y1) / 2
        shadow = (x1, y1 + 2 * self.scale, x2, y2 + 2 * self.scale)
        fill = (245, 246, 250, 215) if pressed else ((85, 92, 104, 200) if hover else (31, 33, 40, 140))
        if tint and not pressed:
            fill = tint[:3] + (70 if not hover else 120,)
        edge = (255, 255, 255, 185 if hover or pressed else 65)
        if circle:
            if raised:
                self.draw.ellipse(shadow, fill=(0, 0, 0, 30))
            self.material_draw.ellipse(bounds, fill=205)
            self.draw.ellipse(bounds, fill=fill, outline=edge, width=self.stroke)
            if raised:
                self.draw.arc(bounds, 205, 325, fill=(255, 255, 255, 125), width=self.stroke)
        else:
            rounded_rectangle(self.draw, shadow, radius, fill=(0, 0, 0, 25))
            rounded_rectangle(self.material_draw, bounds, radius, fill=205)
            rounded_rectangle(self.draw, bounds, radius, fill=fill, outline=edge, width=self.stroke)

    def ring(self, center, radius, color, fill=None, min_width=1):
        """어두운 바깥 테두리를 두른 원형 표시(엄지 끝·커서). 밝은 영상 위에서도 보이게 한다.

        min_width: 안쪽 원 선의 최소 두께. 손끝 꼬집기 HUD의 커서는 2, 나머지는 1이다(기존 모양 유지).
        """
        x, y = center
        r, s = radius, self.scale
        self.draw.ellipse((x - r - s, y - r - s, x + r + s, y + r + s), outline=(20, 23, 29, 190),
                          width=max(2, round(3 * s)))
        self.draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=max(min_width, round(2 * s)), fill=fill)

    def progress_arc(self, center, radius, progress, color, width):
        """12시 방향에서 시계 방향으로 progress(0~1)만큼 호를 그린다."""
        x, y = center
        self.draw.arc((x - radius, y - radius, x + radius, y + radius), -90, -90 + 360 * progress, fill=color,
                      width=width)

    def finish(self):
        """유리 아래 흐림 → 오버레이 합성 → BGR 프레임."""
        import cv2
        import numpy as np
        from PIL import Image

        picture = blur_under(self.picture, self.material)
        picture = Image.alpha_composite(picture, self.overlay).convert("RGB")
        return cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
