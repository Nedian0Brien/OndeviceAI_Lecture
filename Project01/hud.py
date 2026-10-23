"""카메라 영상 위에 그리는 입력기 화면(선 HUD).

draw_hud가 한 프레임을 그린다. 그리는 순서
  손마다(draw_hand)  골격선 → 손바닥 원형 표시(draw_palm_hub) → 손끝 버튼과 순환 대기 글자 → 엄지 점 → 모음 끌기
  화면 위            입력 한 줄(draw_input_line) → 순환 중인 그룹(draw_cycle_row) → 단어 입력 안내(draw_word_panel)
  화면 아래          자판 지도(K) → 상태 안내 → 도움말(H)

판정은 하지 않는다. typer(PinchTyper)의 상태를 읽어 보여 주기만 한다. 애니메이션(손끝 버튼 펼침, 순환, 입력 표시)의
진행 상태는 ui dict(project01.run)에 남긴다. Vision Pro 앱(visionos/)도 같은 값으로 같은 모양을 그린다.
"""

import math
import time

from common.canvas import (ACCENT, ACCENT_FILL, BACKDROP, LINE, LINE_DIM, MUTED, WHITE, HudCanvas, ellipsize_left,
                           hud_font, hud_text_width, rounded_rectangle)
from common.landmarks import DISTAL_SEGMENTS, FINGER_MCPS, FINGER_TIPS, HAND_CONNECTIONS, MIDDLE_MCP, THUMB_TIP, WRIST
from layout import DRAG_MIN, DRAG_STROKES, KEYS, VOWEL_LABELS, cycle_queue, group_of, key_label


DIM = (200, 202, 210, 150)
OK_GREEN = (126, 224, 176, 255)
DELETE_RED = (255, 128, 116, 255)
OUTLINE = (28, 29, 34, 170)  # 밝은 영상 위 글자 외곽선
WARN_YELLOW = (255, 214, 102, 255)

CYCLE_ANIM = 0.25  # 순환할 때 대기 글자가 한 칸 올라오는 시간(초)
# 손끝 버튼(직선 도넛): 짧은 직선 DONUT_BARS개를 원형으로 늘어놓는다. 직선은 버튼 반지름의 DONUT_INNER~DONUT_OUTER배.
DONUT_BARS, DONUT_INNER, DONUT_OUTER = 30, 0.84, 1.04
HUB_BARS, HUB_GAP = 96, 70  # 손바닥 원 바깥 막대 링의 막대 수, 손목 쪽에 비워 두는 각도(°)
HUB_HINT = "주먹 쥐기로 지움"  # 손바닥 원 가운데 문구(주먹을 쥐면 "지움")
# 손바닥 원 반지름(손 길이 비율): 띠 안쪽·막대 시작·눈금자·바깥 막대 링. 바깥 링이 손가락 뿌리를 넘지 않게 작게 둔다.
HUB_BAND, HUB_BARS_AT, HUB_SCALE, HUB_RING = 0.23, 0.28, 0.39, 0.42
# 입력 표시: 입력될 때 손바닥 가운데에 그 글자가 ECHO_IN초에 나타나 ECHO_HOLD초까지 머물고 ECHO_OUT초까지 커지며 사라진다.
ECHO_IN, ECHO_HOLD, ECHO_OUT = 0.08, 0.25, 0.6
# 손끝 버튼 접기·펼치기: 주먹을 쥐면 SPREAD_CLOSE초에 손바닥 가운데로 모여 사라지고, 손을 편 뒤 SPREAD_HOLD초가 지나면
# SPREAD_OPEN초에 손끝 자리로 펼쳐진다. SPREAD_HOLD는 주먹 판정이 프레임마다 흔들릴 때 버튼이 들썩이지 않게 한다.
SPREAD_OPEN, SPREAD_CLOSE, SPREAD_HOLD = 0.35, 0.15, 0.15

HELP_LINES = [
    "손끝 꼬집기 천지인",
    "같은 손가락 반복 = 순환(대기 글자가 손끝으로 올라옴) · 다른 키 = 자동 확정",
    "왼손 손바닥: ㅂㅍㅃ ㄹ ㅅㅎㅆ ㅈㅊㅉ  ·  오른손 손바닥: ㄱㅋㄲ ㄴ ㄷㅌㄸ ㅇㅁ  (ㄴ·ㄹ은 순환 없이 바로)",
    "왼손 손등: 모음 · 공백 · 확정 · .,?!줄바꿈  ·  모음(검지): 꼬집기 ㆍ, 꼬집은 채 가로로 끌기 ㅡ, 세로로 끌기 ㅣ",
    "오른손 손등 검지: 확정/공백(후보가 있으면 확정, 없으면 공백)",
    "주먹(양손): 마지막 자모 지움(감 → 가 → ㄱ), 쥐고 있으면 0.5초마다 계속",
    "C 복사 · X 전체 지우기 · P 일시정지 · K 자판 지도 · F 전체 화면 · H 닫기 · Q 종료",
    "단어 입력(--words): N 건너뛰기",
]


# --- 작은 도구 ----------------------------------------------------------------------

def write(canvas, position, text, font, fill, anchor="la", outline=False):
    """글자. outline이면 어두운 외곽선을 둘러 밝은 영상 위에서도 읽히게 한다."""
    canvas.write(position, text, font, fill, anchor, canvas.stroke if outline else 0, OUTLINE if outline else None)


def faded(color, k):
    """color의 불투명도에 k(0~1)를 곱한다."""
    return color[:3] + (max(0, min(255, round(color[3] * k))),)


def with_alpha(color, alpha):
    """color의 불투명도를 alpha(0~255)로 바꾼다."""
    return color[:3] + (round(alpha),)


def bearing(center, point):
    """center에서 point로 가는 방향(12시 0°에서 시계 방향, 화면 좌표)."""
    return math.degrees(math.atan2(point[0] - center[0], center[1] - point[1])) % 360


def toward(palm, point, spread):
    """손끝 버튼 펼침 위치: spread 0이면 손바닥 가운데(palm), 1이면 제자리(point)."""
    return palm[0] + (point[0] - palm[0]) * spread, palm[1] + (point[1] - palm[1]) * spread


def finger_path_point(points, finger, start, fraction):
    """손가락 위 경로(손끝 버튼 자리 start → TIP → DIP → PIP)에서 길이 비율 fraction(0~1)의 점.

    순환 대기 글자가 이 경로에 놓인다. 끝(마지막 대기 글자)을 PIP 관절로 두어 손바닥 원과 겹치지 않게 한다.
    """
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
    """손끝 버튼 제자리: 손가락 끝마디 방향으로 손끝 바로 바깥(지름 × 0.85). (중심, 끝마디 방향 단위 벡터)"""
    tx, ty = points[FINGER_TIPS[finger]]
    dx, dy = tx - points[DISTAL_SEGMENTS[finger][0]][0], ty - points[DISTAL_SEGMENTS[finger][0]][1]
    norm = math.hypot(dx, dy) or 1.0
    return (tx + dx / norm * diameter * 0.85, ty + dy / norm * diameter * 0.85), (dx / norm, dy / norm)


# --- 애니메이션 -----------------------------------------------------------------------

def spread_motion(spreads, hand, closed, now):
    """손끝 버튼 펼침 정도(0 손바닥 가운데에 모여 안 보임 ~ 1 손끝 자리, ease-out 적용).

    spreads: 손별 (선형 진행, 직전 시각, 마지막으로 주먹이던 시각)을 담는 dict(ui["spreads"]). 처음 보이는 손은 0에서
    시작해 펼쳐진다. closed: 이번 프레임이 주먹 자세인가(FingerPinch.closed).
    """
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
    """순환 애니메이션 진행(0~1)과 직전 대기열. 대기열이 한 칸 돌았을 때만 움직이고, 그 밖의 변화는 바로 바꾼다.

    cycles: 손가락별 (대기열, 바뀐 시각, 직전 대기열)을 담는 dict(ui["cycles"]).
    """
    old = cycles.get(slot)
    if old is None or old[0] != queue:
        turned = old is not None and len(queue) > 1 and old[0][1:] == queue[:-1]
        cycles[slot] = (queue, now if turned else None, old[0] if turned else None)
    queue, since, previous = cycles[slot]
    if since is None or now - since >= CYCLE_ANIM:
        return 1.0, None
    linear = (now - since) / CYCLE_ANIM
    return 1 - (1 - linear) ** 3, previous  # ease-out: 빠르게 출발해 부드럽게 멈춘다


def echo_look(age):
    """입력 표시가 나온 지 age초일 때 (불투명도, 배율). 끝났으면 None."""
    if age < 0 or age >= ECHO_OUT:
        return None
    if age < ECHO_IN:
        return age / ECHO_IN, 0.85 + 0.15 * age / ECHO_IN
    if age < ECHO_HOLD:
        return 1.0, 1.0
    out = (age - ECHO_HOLD) / (ECHO_OUT - ECHO_HOLD)
    return 1 - out, 1 + 0.15 * out


def echo_view(ui, typer, now):
    """손바닥 가운데 입력 표시: (손, 글자, 불투명도, 배율) 또는 None. typer.echo 번호가 바뀐 화면 시각부터 잰다."""
    if typer.echo is None:
        return None
    seen = ui.get("echo")
    if seen is None or seen[0] != typer.echo[0]:
        ui["echo"] = seen = (typer.echo[0], now)
    look = echo_look(now - seen[1])
    return None if look is None else (typer.echo[1], typer.echo[2], *look)


# --- 손 ---------------------------------------------------------------------------

def draw_palm_hub(canvas, points, state, progresses, paths, spread, font, echo=None):
    """손바닥 가운데 원형 표시.

    - 안쪽 파랑 반투명 띠: 손가락 방향마다 막대 3개, 길이 = 다가옴 진행. 누르는 손가락 방향의 띠는 밝게.
    - 눈금자 호(손가락 쪽): 삼각형이 누르는 손가락(없으면 가장 많이 다가온 손가락) 방향을 가리킨다.
    - 바깥 막대 링: 손목 쪽을 비운 촘촘한 막대. 주먹 지움 진행만큼 빨갛게 켜진다.
    - 가운데: "주먹 쥐기로 지움"(주먹이면 "지움"), 입력 직후에는 그 글자(echo: (글자, 불투명도, 배율)).
    paths: 손가락별 MCP → PIP → DIP → TIP → 손끝 버튼. 손가락 방향 각도(MCP 쪽)에 쓴다.
    spread: 손끝 버튼 펼침(주먹이면 0). 안쪽 층은 함께 사라지고 바깥 막대 링(지움)은 남는다.
    """
    s = canvas.scale
    palm = (WRIST, 5, 9, 13, 17)
    c = (sum(points[i][0] for i in palm) / 5, sum(points[i][1] for i in palm) / 5)
    size = math.dist(points[WRIST], points[MIDDLE_MCP])
    r_band, r_bars, r_scale, r_ring = HUB_BAND * size, HUB_BARS_AT * size, HUB_SCALE * size, HUB_RING * size
    wrist = bearing(c, points[WRIST])
    away = wrist + 180  # 손가락 쪽 가운데: 각도를 이 둘레로 펴서 0°/360° 경계를 넘지 않게 한다
    angles = [away + ((bearing(c, path[0]) - away + 180) % 360) - 180 for path in paths]
    lo, hi = min(angles) - 22, max(angles) + 22
    k = spread
    # 바깥 막대 링(주먹 지움 진행)
    canvas.ticks(c, r_ring, 0.04 * size, HUB_BARS, (236, 240, 248, 90), lit=round(state.fist.progress * HUB_BARS),
                 lit_color=DELETE_RED, start=wrist + HUB_GAP / 2, end=wrist + 360 - HUB_GAP / 2,
                 width=max(1, round(1.2 * s)))
    if k > 0.05:
        # 눈금자 호: 작은 눈금 3°, 큰 눈금 15°
        canvas.arc(c, r_scale, lo - 10, hi + 10, faded(LINE, 0.8 * k))
        canvas.ticks(c, r_scale - 0.02 * size, 0.02 * size, int((hi - lo + 20) / 3) + 1, faded(LINE_DIM, k),
                     start=lo - 10, end=hi + 10, width=1)
        canvas.ticks(c, r_scale - 0.04 * size, 0.04 * size, int((hi - lo + 20) / 15) + 1, faded(LINE, 0.8 * k),
                     start=lo - 10, end=hi + 10, width=1)
        # 안쪽 파랑 띠와 손가락 방향 막대 묶음
        canvas.band(c, r_band, r_bars - 0.02 * size, lo, hi, faded(ACCENT_FILL, 0.7 * k))
        canvas.arc(c, r_band, lo, hi, faded(LINE_DIM, k), width=1)
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
    if state.fist.progress > 0:
        canvas.write(c, "지움", font, DELETE_RED, "mm", canvas.stroke, OUTLINE)
    elif echo is not None:
        text, alpha, grow = echo
        ratio = 0.18 if len(text) == 1 else 0.09  # 한 글자는 크게, 공백·확정 같은 이름은 작게
        canvas.write(c, text, hud_font(font, max(10, round(ratio * size * grow))), faded(WHITE, alpha), "mm",
                     canvas.stroke, faded(OUTLINE, alpha), glow=True)
    elif k > 0.05:
        canvas.write(c, HUB_HINT, font, faded(MUTED, k), "mm", canvas.stroke, OUTLINE)


def draw_cycle_queue(canvas, ui, key, points, finger, queue, home, direction, diameter, palm, spread, fonts, now):
    """순환 대기 글자: 다음 글자들이 손가락을 따라 PIP 쪽에서 기다리고, 순환하면 한 칸씩 손끝으로 올라온다.

    손끝 버튼의 지금 중심(올라오는 중이면 경로 위, 아니면 제자리 home)을 돌려준다.
    """
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
            canvas.clear_circle((wx, wy), small_d / 2 + 4 * s)  # 둘레를 비워 손바닥 원 요소가 가리지 않게
            if previous is not None and slot == waiting:  # 마지막 자리는 손바닥 마디에서 나타난다
                alpha = round(230 * eased / 16) * 16
                write(canvas, (wx, wy), queue[slot], wait_font, with_alpha(WHITE, alpha * spread), "mm", True)
                continue
            canvas.hairline_circle((wx, wy), small_d / 2, with_alpha(LINE, (150 - 40 * (slot - 1)) * spread),
                                   fill=with_alpha(BACKDROP, BACKDROP[3] * spread))
            write(canvas, (wx, wy), queue[slot], wait_font, with_alpha(WHITE, (215 - 50 * (slot - 1)) * spread), "mm")
    if previous is None:
        return home
    # 방금 입력된 글자: 손끝에서 조금 올라가며 사라진다. 다음 글자는 한 칸 아래에서 손끝으로 올라온다.
    lift = diameter * 0.8 * eased
    alpha = round(255 * (1 - eased) / 16) * 16
    (cx, cy), (dx, dy) = home, direction
    write(canvas, toward(palm, (cx + dx * lift, cy + dy * lift), spread), previous[0],
          hud_font(fonts[1], max(8, round(diameter * 0.55))), with_alpha(WHITE, alpha * spread), "mm", True)
    return finger_path_point(points, finger, home, 1 / waiting * (1 - eased))


def draw_tip_button(canvas, pinch, finger, text, center, diameter, spread, progress, ratios, fonts):
    """손끝 버튼(직선 도넛): 짧은 직선 DONUT_BARS개를 원형으로 늘어놓고, 진행만큼 12시부터 시계 방향으로 켠다.

    - 다가옴(꼬집기 진행): 흰 직선.
    - 누르는 중: 안쪽이 파랑 면, 꺼진 직선이 옅은 파랑, 떼기 진행만큼 밝은 파랑(번짐).
    """
    s = canvas.scale
    px, py = center
    size = diameter * spread
    word = hud_font(fonts[0], max(8, round(10 * s * spread)))  # 여러 글자 라벨
    radius = max(size, hud_text_width(text, word) + 6 * s * spread) / 2
    inner, outer = radius * DONUT_INNER, radius * DONUT_OUTER
    active = pinch.active == finger
    if active and ratios is not None:
        lit, base, lit_ink = round(pinch.release_progress(ratios) * DONUT_BARS), ACCENT[:3] + (90,), ACCENT
    else:
        lit = round(progress * DONUT_BARS) if spread >= 1 else 0
        base, lit_ink = (236, 240, 248, 70), LINE
    canvas.clear_circle((px, py), outer + 3 * s * spread)  # 둘레를 비워 손바닥 원 요소가 가리지 않게
    fill = with_alpha(ACCENT_FILL, (ACCENT_FILL[3] + 60) * spread) if active else \
        with_alpha(BACKDROP, BACKDROP[3] * spread)
    canvas.draw.ellipse((px - inner, py - inner, px + inner, py + inner), fill=fill)
    canvas.ticks((px, py), inner, outer - inner, DONUT_BARS, with_alpha(base, base[3] * spread), lit=lit,
                 lit_color=with_alpha(lit_ink, lit_ink[3] * spread), width=max(1, round(s)), glow=active)
    write(canvas, (px, py), text, hud_font(fonts[1], max(8, round(size * 0.55))) if len(text) == 1 else word,
          with_alpha(WHITE, 255 * spread), "mm")


def draw_vowel_drag(canvas, drag, points, diameter, fonts):
    """모음 끌기: 꼬집은 지점(엄지 끝과 손끝의 가운데)이 지나온 길, 시작점 둘레의 끌기 기준 원(이 밖으로 나가면 끌기),
    지금 떼면 들어갈 획. 판정(PinchTyper.drag_view)과 같은 점들이다."""
    s, w, h = canvas.scale, canvas.w, canvas.h
    margin = 16 * s
    passed = drag["drag"] is not None
    ink = ACCENT if passed else LINE
    path = drag["points"]
    ox, oy = path[0]
    radius = DRAG_MIN * drag["size"]
    canvas.dashed_circle((ox, oy), radius, LINE_DIM)  # 끌기 기준 원: 파선 + 가로·세로 축 눈금
    canvas.ticks((ox, oy), radius, 5 * s, 4, LINE)
    if len(path) > 1:
        canvas.glow_line(path, ink, max(2, round(3 * s)))
    ex, ey = path[-1]
    r = max(3, 3.5 * s)
    canvas.draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=ink)
    # 획 미리 보기: 꼬집은 지점에서 손목 → 중지 뿌리 방향으로 손끝 버튼 줄 바깥에 둔다(버튼을 가리지 않게).
    badge = diameter * 1.3
    ux, uy = points[MIDDLE_MCP][0] - points[WRIST][0], points[MIDDLE_MCP][1] - points[WRIST][1]
    norm = math.hypot(ux, uy) or 1.0
    bx, by = ex + ux / norm * diameter * 2.6, ey + uy / norm * diameter * 2.6
    bx = min(max(bx, badge / 2 + margin), w - badge / 2 - margin)  # 화면 밖으로 나가지 않게
    by = min(max(by, badge / 2 + margin), h - badge / 2 - margin)
    canvas.hairline_circle((bx, by), badge / 2, ACCENT if passed else LINE,
                           fill=ACCENT_FILL if passed else BACKDROP, glow=passed)
    if passed:  # 끈 방향을 가리키는 삼각형
        canvas.pointer((bx, by), badge / 2 + 2 * s, drag["pointer"], 5 * s, ACCENT, glow=True)
    write(canvas, (bx, by), VOWEL_LABELS[DRAG_STROKES[drag["drag"]]],
          hud_font(fonts[1], max(10, round(badge * 0.62))), WHITE, "mm", True)


def draw_hand(canvas, typer, label, points, ui, fonts, now):
    """손 하나: 골격선, 엄지 가림 안내, 손바닥 원형 표시, 손끝 버튼, 엄지 점, 모음 끌기. points: 픽셀 좌표 21개."""
    s, w, h = canvas.scale, canvas.w, canvas.h
    state = typer.hands[label]
    for a, b in HAND_CONNECTIONS:
        canvas.draw.line((points[a], points[b]), fill=(237, 242, 250, 95), width=canvas.stroke)
    if typer.occluded(label):  # 소지 쪽으로 기울어 엄지가 가려짐: 이 자세의 꼬집기는 입력하지 않는다
        x, y = points[WRIST]
        x, y = min(max(x, 40 * s), w - 40 * s), min(y + 12 * s, h - 20 * s)
        write(canvas, (x, y), "엄지 쪽을 카메라로 돌려 주세요", canvas.small, WARN_YELLOW, "ma", True)
    tips = [points[tip] for tip in FINGER_TIPS]
    spacing = min(math.dist(a, b) for a, b in zip(tips, tips[1:]))
    diameter = min(21 * s, max(12 * s, spacing * 0.75))  # 손끝 버튼 지름
    ratios = None if state.signal is None else state.signal.ratios
    progresses = [0.0] * 4 if ratios is None else state.pinch.progress(ratios)
    # 손끝 버튼 펼침: 손바닥 가운데(손목~중지 뿌리 가운데)와 제자리 사이 위치, 크기·불투명도도 함께(spread_motion).
    spread = spread_motion(ui["spreads"], label, state.pinch.closed, now)
    palm = ((points[WRIST][0] + points[MIDDLE_MCP][0]) / 2, (points[WRIST][1] + points[MIDDLE_MCP][1]) / 2)

    echo = echo_view(ui, typer, now)
    paths = [[toward(palm, points[FINGER_MCPS[f] + i], spread) for i in range(4)]
             + [toward(palm, tip_home(points, f, diameter)[0], spread)] for f in range(4)]
    draw_palm_hub(canvas, points, state, progresses, paths, spread, canvas.small,
                  None if echo is None or echo[0] != label else echo[1:])
    for finger in range(4):
        queue = cycle_queue(typer, label, finger)
        if not queue:  # 자판에 없는 자리(오른손 손등 중지~소지)
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


# --- 화면 위·아래 패널 ------------------------------------------------------------------

def draw_input_line(canvas, typer, paused, now):
    """화면 위 입력 한 줄: 확정 전 후보 글자 아래에 파랑 밑줄, 그 밖에는 깜빡이는 커서. 패널 아래 y를 돌려준다."""
    s, w, stroke = canvas.scale, canvas.w, canvas.stroke
    margin = 16 * s
    text = typer.ime.text.split("\n")[-1]
    font = canvas.large if text else canvas.normal
    shown = ellipsize_left(text or "손끝을 꼬집어 입력", font, min(w - 2 * margin, 420 * s) - 36 * s)
    width = min(w - 2 * margin, max(140 * s, hud_text_width(shown, font) + 40 * s))
    height = (40 if text else 34) * s
    x1 = (w - width) / 2
    # 선 HUD 패널: 어두운 면 + 가는 테두리, 아래 모서리에 눈금자(큰 눈금 5칸마다).
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
    elif text and int(now * 2) % 2 == 0 and not paused:  # 0.5초 간격 깜빡이는 커서
        canvas.draw.line((text_right + 4 * s, margin + 12 * s, text_right + 4 * s, margin + height - 12 * s),
                         fill=MUTED, width=stroke)
    return margin + height


def draw_cycle_row(canvas, typer, top):
    """입력 줄 아래: 순환 중인 그룹 전체, 지금 글자 아래에 파랑 막대."""
    if typer.pending is None:
        return
    s = canvas.scale
    group = [("줄" if c == "\n" else c) for c in group_of(typer.pending.key)]
    current = (typer.pending.count - 1) % len(group)
    cell = 22 * s
    x = canvas.w / 2 - len(group) * cell / 2
    for i, char in enumerate(group):
        write(canvas, (x + (i + 0.5) * cell, top + cell / 2), char, canvas.normal, WHITE if i == current else DIM,
              "mm", True)
        bar = (x + i * cell + 4 * s, top + cell + 1 * s, x + (i + 1) * cell - 4 * s, top + cell + 1 * s)
        if i == current:
            canvas.glow_line((bar[:2], bar[2:]), ACCENT, max(2, round(2 * s)))
        else:
            canvas.draw.line(bar, fill=LINE_DIM, width=canvas.stroke)


def draw_word_panel(canvas, word, top):
    """단어 입력 안내(--words): word = {"title", "detail", "tone": "prompt"|"ok"|"skip"}. 맞히면 강조 패널."""
    s, w = canvas.scale, canvas.w
    margin = 16 * s
    pw = min(w - 2 * margin, max(hud_text_width(word["title"], canvas.large),
                                 hud_text_width(word["detail"], canvas.small)) + 40 * s)
    canvas.panel(((w - pw) / 2, top, (w + pw) / 2, top + 62 * s), active=word["tone"] == "ok")
    ink = {"ok": OK_GREEN, "skip": MUTED}.get(word["tone"], WHITE)
    write(canvas, (w / 2, top + 22 * s), word["title"], canvas.large, ink, "mm", True)
    write(canvas, (w / 2, top + 47 * s), word["detail"], canvas.small, MUTED, "mm", True)


def draw_key_map(canvas, typer):
    """화면 아래 자판 지도(K): 손마다 지금 방향의 키 4개, 누르는 키는 강조."""
    s, w, h = canvas.scale, canvas.w, canvas.h
    cell_w, cell_h, gap = 50 * s, 28 * s, 5 * s
    top = h - 16 * s - cell_h
    for hand, center in (("Left", w * 0.27), ("Right", w * 0.73)):
        state, face = typer.hands[hand], typer.face(hand) or "palm"
        start = center - (4 * cell_w + 3 * gap) / 2
        for i, key in enumerate(KEYS[(hand, face)]):
            if key is None:  # 자판에 없는 자리(오른손 손등 중지~소지)
                continue
            bounds = (start + i * (cell_w + gap), top, start + i * (cell_w + gap) + cell_w, top + cell_h)
            canvas.panel(bounds, active=state.pinch.active == i)
            name = key_label(key)
            write(canvas, ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2), name,
                  canvas.normal if len(name) <= 3 and key != "PUNCT" else canvas.small, WHITE, "mm")


def draw_notice(canvas, message, show_map):
    """화면 아래 상태 안내 한 줄(자판 지도가 있으면 그 위)."""
    s, w, h = canvas.scale, canvas.w, canvas.h
    mw = min(w - 32 * s, hud_text_width(message, canvas.small) + 28 * s)
    top = h - (92 if show_map else 58) * s
    canvas.panel(((w - mw) / 2, top, (w + mw) / 2, top + 26 * s))
    write(canvas, (w / 2, top + 13 * s), message, canvas.small, MUTED, "mm")


def draw_help(canvas, fps):
    """도움말(H): 화면을 어둡게 덮고 HELP_LINES와 fps를 보여 준다."""
    s, w, h = canvas.scale, canvas.w, canvas.h
    canvas.draw.rectangle((0, 0, w, h), fill=(16, 17, 21, 165))
    bw = min(w - 32 * s, 470 * s)
    box = ((w - bw) / 2, 44 * s, (w + bw) / 2, h - 44 * s)
    rounded_rectangle(canvas.draw, box, 22 * s, fill=(38, 40, 47, 238), outline=(255, 255, 255, 65),
                      width=canvas.stroke)
    for i, line in enumerate(HELP_LINES + [f"{fps:.0f} FPS"]):
        write(canvas, (box[0] + 20 * s, box[1] + (20 + i * 27) * s), line, canvas.normal if i == 0 else canvas.small,
              WHITE if i == 0 else MUTED)


def draw_hud(frame, typer, hands, ui, fonts, fps):
    """한 프레임의 화면. hands: {"Left"|"Right": 픽셀 좌표 21개}. ui: 화면 상태(project01.run)."""
    canvas = HudCanvas(frame, fonts)
    now = time.monotonic()
    spreads = ui.setdefault("spreads", {})
    for gone in set(spreads) - set(hands):  # 안 보이게 된 손은 다시 나타날 때 손바닥에서 펼쳐진다
        del spreads[gone]
    for label, points in hands.items():
        draw_hand(canvas, typer, label, points, ui, fonts, now)

    bottom = draw_input_line(canvas, typer, ui["paused"], now)
    draw_cycle_row(canvas, typer, bottom + 8 * canvas.scale)
    if ui["show_map"]:
        draw_key_map(canvas, typer)
    if ui.get("word") is not None:
        draw_word_panel(canvas, ui["word"], bottom + 38 * canvas.scale)

    message = ""
    if ui["paused"]:
        message = "일시정지 · P로 재개"
    elif not hands:
        message = "손바닥을 카메라 쪽으로 보여주세요"
    elif ui["notice_since"] is not None and now - ui["notice_since"] < 2.5:
        message = ui["status"]
    if message and not ui["show_help"]:
        draw_notice(canvas, message, ui["show_map"])
    if ui["show_help"]:
        draw_help(canvas, fps)
    return canvas.finish()
