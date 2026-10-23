"""손끝 꼬집기 횟수로 고르는 양손 천지인 입력기 (1인칭 카메라 기준).

카메라를 눈 위치에 두고 손을 내려다본다. 손바닥이 카메라를 향하면 기본 자판, 손을 뒤집어 손등이 보이면
왼손은 편집 키, 오른손은 아랫줄 자판이다. 자판과 규칙은 pinch_typer.py 머리말을 본다.

실행:  python 261018/pinch_cheonjiin.py --camera 0
시연:  python 261018/pinch_cheonjiin.py --demo
"""

# 한 프레임의 처리 흐름 (run)
#   카메라 프레임 → (--mirror면 좌우 반전) → 손 검출(가로 640 이하로 줄인 영상) → 왼손·오른손 배정
#   → 손마다 HandSignal(꼬집기 비율·굽음·자세·손바닥 방향) → PinchTyper.update → 입력 이벤트
#   → HUD 그리기 → 창 표시 → 키 입력 처리 · 진단 스크린샷
# 판정 규칙은 모두 pinch_typer.py에 있고, 이 파일은 카메라·화면·키보드 단축키만 다룬다.

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

from common.camera import FpsMeter, VideoClock, Window, capture_size, open_capture, parse_resolution, read_frame
from common.cli import add_camera_arguments, positive_float, resolve_pipeline
from common.hands import assign_hands, create_detector, detect_hands, detection_frame
from common.hud import MUTED, WHITE, HudCanvas, ellipsize_left, hud_font, hud_text_width, load_fonts, \
    rounded_rectangle
from common.landmarks import (DISTAL_SEGMENTS, FINGER_MCPS, FINGER_TIPS, HAND_CONNECTIONS, MIDDLE_MCP, THUMB_TIP, WRIST,
                              to_pixels)
from common.pose import pose_from_image
from pinch_collect import FINGER_NAMES, KINDS, Collector, WordCollector
from pinch_typer import (DRAG_MIN, DRAG_STROKES, HOLD_TIME, KEYS, PINCH_ENTER, PINCH_EXIT, VOWEL_LABELS, PinchTyper,
                         cycle_queue, group_of, hand_signal, key_label, replay)


WINDOW = "Pinch Cheonjiin"
FIXTURES = Path(__file__).resolve().with_name("fixtures") / "pinch-typer-keys.json"
DATA = Path(__file__).resolve().with_name("data")
ORIENTATION_NAMES = {"palm": "손바닥", "back": "손등", "turning": "회전 중", None: ""}
FACE_CAPTIONS = {"Left": "손등 모음·편집", "Right": "손등 확정/공백"}  # 손등일 때 손 이름 옆 표시
# 키보드 시험용: 1~4 = 왼손 검지~소지, 7 8 9 0 = 오른손 검지~소지, Shift를 누르면 손등(US 자판의 !@#$ / &*(),
# ! = 모음 ㆍ), - = 모음 가로 끌기(ㅡ), / = 모음 세로 끌기(ㅣ), Space = 왼손 주먹, Backspace = 오른손 주먹(지움)
TEST_KEYS = {ord(char): (hand, finger, face, None)
             for chars, hand, face in (("1234", "Left", "palm"), ("!@#$", "Left", "back"),
                                       ("7890", "Right", "palm"), ("&*()", "Right", "back"))
             for finger, char in enumerate(chars)}
TEST_KEYS.update({ord("-"): ("Left", 0, "back", "h"), ord("/"): ("Left", 0, "back", "v")})

DIM = (200, 202, 210, 150)
OK_GREEN = (126, 224, 176, 255)
DELETE_RED = (255, 128, 116, 255)
OUTLINE = (28, 29, 34, 170)  # 밝은 영상 위 글자 외곽선
PROMPT_YELLOW = (255, 214, 102, 255)  # 기록 모드: 꼬집을 손끝


# ---------------------------------------------------------------------------
# 검출 결과 → 손 신호

def build_signals(hands, width, height, mirrored):
    """{"Left"|"Right": Detection} → {"Left"|"Right": HandSignal}.

    손바닥 방향은 검출의 label(MediaPipe가 본 손)로 계산한다. 같은 라벨 두 손을 화면 위치로 나눈 경우에도
    landmark 배치와 짝이 맞는 것은 원래 label이기 때문이다(common.hands.assign_hands 설명).
    자세는 이미지 좌표 방식(pose_from_image)을 쓴다.
    """
    return {role: hand_signal(hand.label, hand.landmarks, hand.world, mirrored, width, height,
                              pose_from_image(hand.landmarks, width, height))
            for role, hand in hands.items()}


# ---------------------------------------------------------------------------
# 손끝 라벨

CYCLE_ANIM = 0.25  # 순환할 때 대기 글자가 한 칸 올라오는 시간(초)
# 손끝 버튼 접기·펼치기: 주먹을 쥐면 SPREAD_CLOSE초에 손바닥 가운데로 모여 사라지고, 손을 편 뒤 SPREAD_HOLD초가 지나면
# SPREAD_OPEN초에 손끝 자리로 펼쳐진다(사용자, 2026-10-23). SPREAD_HOLD는 주먹 판정이 프레임마다 흔들릴 때 버튼이
# 들썩이지 않게 한다. Vision Pro 앱(HandLayout.swift SpreadMotion)도 같은 값이다.
SPREAD_OPEN, SPREAD_CLOSE, SPREAD_HOLD = 0.35, 0.15, 0.15


def finger_path_point(points, finger, start, fraction):
    """손가락 위 경로(손끝 라벨 자리 start → TIP → DIP → PIP → MCP)에서 길이 비율 fraction(0~1)의 점."""
    mcp = FINGER_MCPS[finger]
    path = [start] + [points[mcp + i] for i in (3, 2, 1, 0)]
    lengths = [math.dist(a, b) for a, b in zip(path, path[1:])]
    remaining = max(0.0, min(1.0, fraction)) * sum(lengths)
    for (a, b), length in zip(zip(path, path[1:]), lengths):
        if remaining <= length or length == 0 and remaining == 0:
            k = remaining / length if length else 0.0
            return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        remaining -= length
    return path[-1]


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


# ---------------------------------------------------------------------------
# HUD

def draw_hud(frame, typer, hands, ui, fonts, fps):
    """hands: {"Left"|"Right": 픽셀 좌표 21개}. ui: 화면 상태(dict, run 참고)."""
    canvas = HudCanvas(frame, fonts)
    w, h, s, stroke = canvas.w, canvas.h, canvas.scale, canvas.stroke
    small, normal, large = canvas.small, canvas.normal, canvas.large
    draw, glass = canvas.draw, canvas.glass
    now = time.monotonic()
    margin = 16 * s
    pinch_enter, pinch_exit = typer.hands["Left"].pinch.enter, typer.hands["Left"].pinch.exit
    collect = ui.get("collect")  # 기록 모드 안내(run의 collect_view), 없으면 None

    def write(position, text, font, fill, anchor="la", outline=False):
        canvas.write(position, text, font, fill, anchor, stroke if outline else 0, OUTLINE if outline else None)

    # 손: 얇은 골격선, 손 이름과 방향, 손끝 라벨, 꼬집기 진행, 주먹 진행
    spreads = ui.setdefault("spreads", {})
    for gone in set(spreads) - set(hands):  # 안 보이게 된 손은 다시 나타날 때 손바닥에서 펼쳐진다
        del spreads[gone]
    for label, points in hands.items():
        state = typer.hands[label]
        for a, b in HAND_CONNECTIONS:
            draw.line((points[a], points[b]), fill=(237, 242, 250, 95), width=stroke)
        caption = ("왼손" if label == "Left" else "오른손")
        if state.orientation.state == "back":
            caption += " · " + FACE_CAPTIONS[label]
        elif state.orientation.state:
            caption += " · " + ORIENTATION_NAMES[state.orientation.state]
        if not state.pinch.armed:
            caption += " · 손을 펴세요"
        x, y = points[WRIST]
        occluded = typer.occluded(label)  # 소지 쪽으로 기울어 엄지가 가려짐: 이 자세의 꼬집기는 입력하지 않는다.
        x, y = min(max(x, 40 * s), w - 40 * s), min(y + 12 * s, h - (44 if occluded else 20) * s)
        write((x, y), caption, small, MUTED, "ma", True)
        if occluded:
            write((x, y + 22 * s), "엄지 쪽을 카메라로 돌려 주세요", small, PROMPT_YELLOW, "ma", True)
        # 손끝 라벨: 손가락 끝마디 방향으로 손끝 바로 바깥에 둔다(엄지가 닿는 면을 가리지 않게).
        tips = [points[tip] for tip in FINGER_TIPS]
        spacing = min(math.dist(a, b) for a, b in zip(tips, tips[1:]))
        diameter = min(26 * s, max(15 * s, spacing * 0.9))
        values = None if state.signal is None else state.signal.ratios
        progresses = [0.0] * 4 if values is None else state.pinch.progress(values)
        # 손끝 버튼 펼침: 손바닥 가운데(손목~중지 뿌리 가운데)와 제자리 사이 위치, 크기·불투명도도 함께(spread_motion).
        spread = spread_motion(spreads, label, state.pinch.closed, now)
        palm = ((points[WRIST][0] + points[MIDDLE_MCP][0]) / 2, (points[WRIST][1] + points[MIDDLE_MCP][1]) / 2)

        def place(point):
            return palm[0] + (point[0] - palm[0]) * spread, palm[1] + (point[1] - palm[1]) * spread

        def faded(alpha):
            return WHITE[:3] + (round(alpha * spread),)

        for finger in range(4):
            tx, ty = tips[finger]
            dx, dy = tx - points[DISTAL_SEGMENTS[finger][0]][0], ty - points[DISTAL_SEGMENTS[finger][0]][1]
            norm = math.hypot(dx, dy) or 1.0
            cx, cy = tx + dx / norm * diameter * 0.85, ty + dy / norm * diameter * 0.85
            progress = progresses[finger]  # 꼬집기 진행: 기준값보다 다가온 정도(pinch_typer.FingerPinch.progress)
            active = state.pinch.active == finger
            target = collect is not None and collect["target"] == (label, finger)
            queue = cycle_queue(typer, label, finger)
            text = queue[0] if queue else None
            color = WHITE
            if ui["tip_labels"] and len(queue) > 1:
                # 순환 대기열: 다음 글자들은 손가락을 따라 손바닥 마디(MCP) 쪽에서 기다리고, 순환하면 한 칸씩 올라온다.
                eased, previous = cycle_motion(ui.setdefault("cycles", {}), (label, finger), tuple(queue), now)
                waiting = len(queue) - 1

                def slot_fraction(slot):
                    return slot / waiting

                small_d = diameter * 0.7 * spread
                wait_font = hud_font(fonts[1], max(8, round(small_d * 0.6)))
                for slot in range(len(queue) - 1, 0, -1):
                    fraction = slot_fraction(slot)
                    if previous is not None and slot < waiting:  # 한 칸 아래 자리에서 올라오는 중
                        fraction = slot_fraction(slot + 1) + (fraction - slot_fraction(slot + 1)) * eased
                    wx, wy = place(finger_path_point(points, finger, (cx, cy), fraction))
                    if spread < 0.05:
                        continue
                    if previous is not None and slot == waiting:  # 마지막 자리는 손바닥 마디에서 나타난다
                        alpha = round(230 * eased / 16) * 16
                        write((wx, wy), queue[slot], wait_font, faded(alpha), "mm", True)
                        continue
                    glass((wx - small_d / 2, wy - small_d / 2, wx + small_d / 2, wy + small_d / 2), circle=True)
                    write((wx, wy), queue[slot], wait_font, faded(215), "mm")
                if previous is not None:
                    # 방금 입력된 글자: 손끝에서 조금 올라가며 사라진다. 다음 글자는 한 칸 아래에서 손끝으로 올라온다.
                    lift = diameter * 0.8 * eased
                    alpha = round(255 * (1 - eased) / 16) * 16
                    write(place((cx + dx / norm * lift, cy + dy / norm * lift)), previous[0],
                          hud_font(fonts[1], max(8, round(diameter * 0.55))), faded(alpha), "mm", True)
                    cx, cy = finger_path_point(points, finger, (cx, cy), slot_fraction(1) * (1 - eased))
            if ui["tip_labels"] and text and spread >= 0.05:
                px, py = place((cx, cy))
                size = diameter * spread
                word = hud_font(fonts[0], max(8, round(11 * s * spread)))  # 여러 글자 라벨(canvas.small과 같은 크기)
                width = max(size, hud_text_width(text, word) + 10 * s * spread)
                bounds = (px - width / 2, py - size / 2, px + width / 2, py + size / 2)
                glass(bounds, circle=width == size, hover=progress > 0.5, pressed=active,
                      tint=PROMPT_YELLOW if target else None)
                ink = (28, 29, 34, 255) if active or target else faded(color[3])
                write((px, py), text, hud_font(fonts[1], max(8, round(size * 0.55))) if len(text) == 1 else word,
                      ink, "mm")
            if target:
                canvas.ring((cx, cy), diameter * 0.8, PROMPT_YELLOW, min_width=2)
            if progress > 0 and not active and spread >= 1:
                canvas.progress_arc((tx, ty), 6 * s, progress, color, max(2, round(2 * s)))
            if active and values is not None:
                canvas.ring((cx, cy), diameter * 0.72, (237, 242, 250, 90), min_width=2)
                if state.pinch.hold:
                    # 누르고 유지 진행: 손끝에 머문 시간. 다 차면 입력되고, 뗄 때까지 꽉 찬 원으로 남는다.
                    held = state.pinch.hold_progress(now)
                    if held > 0:
                        canvas.progress_arc((cx, cy), diameter * 0.72, held, OK_GREEN, max(2, round(3 * s)))
                else:
                    # 떼기 진행(뗄 때 입력 방식): 눌린 손끝을 얼마나 벌렸는지. 다 차면 입력된다.
                    opened = state.pinch.release_progress(values)
                    if opened > 0:
                        canvas.progress_arc((cx, cy), diameter * 0.72, opened, OK_GREEN, max(2, round(3 * s)))
            if ui["debug"] and values is not None:
                write((tx - dx / norm * 12 * s, ty - dy / norm * 12 * s),
                      f"{values[finger]:.2f}", small, WHITE if values[finger] < pinch_enter else DIM, "mm", True)
        thumb = points[THUMB_TIP]
        r = 3 * s
        draw.ellipse((thumb[0] - r, thumb[1] - r, thumb[0] + r, thumb[1] + r), fill=(245, 248, 252, 200))
        drag = typer.drag_view(label)
        if drag is not None:
            # 모음 끌기: 꼬집은 지점(엄지 끝과 중지 끝의 가운데)이 지나온 길, 시작점 둘레의 끌기 기준 원(이 밖으로
            # 나가면 끌기), 지금 떼면 들어갈 획. 판정(PinchTyper.drag)과 같은 점들이다.
            passed = drag["drag"] is not None
            ink = OK_GREEN if passed else WHITE
            path = drag["points"]
            ox, oy = path[0]
            radius = DRAG_MIN * drag["size"]
            draw.ellipse((ox - radius, oy - radius, ox + radius, oy + radius), outline=(237, 242, 250, 90),
                         width=stroke)
            if len(path) > 1:
                draw.line(path, fill=ink[:3] + (230,), width=max(3, round(4 * s)), joint="curve")
            ex, ey = path[-1]
            r = max(3, 4 * s)
            draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=ink[:3] + (245,))
            # 획 미리 보기: 꼬집은 지점에서 손목 → 중지 뿌리 방향으로 손끝 라벨 줄 바깥에 둔다(라벨을 가리지 않게).
            badge = diameter * 1.3
            ux, uy = points[MIDDLE_MCP][0] - points[WRIST][0], points[MIDDLE_MCP][1] - points[WRIST][1]
            norm = math.hypot(ux, uy) or 1.0
            bx, by = ex + ux / norm * diameter * 2.6, ey + uy / norm * diameter * 2.6
            bx = min(max(bx, badge / 2 + margin), w - badge / 2 - margin)  # 화면 밖으로 나가지 않게
            by = min(max(by, badge / 2 + margin), h - badge / 2 - margin)
            glass((bx - badge / 2, by - badge / 2, bx + badge / 2, by + badge / 2), circle=True,
                  tint=OK_GREEN if passed else None)
            write((bx, by), VOWEL_LABELS[DRAG_STROKES[drag["drag"]]],
                  hud_font(fonts[1], max(10, round(badge * 0.62))), WHITE, "mm", True)
        if state.fist.progress > 0:
            # 주먹 진행: 손목~중지 뿌리 가운데에 호(양손 모두 지움). 쥐고 있으면 다음 지움까지의 진행이다.
            cx = (points[WRIST][0] + points[MIDDLE_MCP][0]) / 2
            cy = (points[WRIST][1] + points[MIDDLE_MCP][1]) / 2
            canvas.progress_arc((cx, cy), 22 * s, state.fist.progress, DELETE_RED, max(2, round(3 * s)))
            write((cx, cy), "지움", small, DELETE_RED, "mm", True)

    # 위: 입력 한 줄 캡슐. 확정 전 후보 글자 아래에 밑줄.
    text = typer.ime.text.split("\n")[-1]
    font = large if text else normal
    shown = ellipsize_left(text or "손끝을 꼬집어 입력", font, min(w - 2 * margin, 420 * s) - 36 * s)
    width = min(w - 2 * margin, max(140 * s, hud_text_width(shown, font) + 40 * s))
    height = (40 if text else 34) * s
    x1 = (w - width) / 2
    glass((x1, margin, x1 + width, margin + height))
    write((w / 2, margin + height / 2), shown, font, WHITE if text else MUTED, "mm")
    text_right = w / 2 + hud_text_width(shown, font) / 2
    if text and typer.pending is not None and typer.pending.key != "PUNCT":
        last = hud_text_width(shown[-1:], font)
        draw.line((text_right - last, margin + height - 9 * s, text_right, margin + height - 9 * s),
                  fill=WHITE, width=max(2, round(2 * s)))
    elif text and int(now * 2) % 2 == 0 and not ui["paused"]:  # 0.5초 간격 깜빡이는 커서
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

    # 아래: 자판 지도(K). 손에 라벨을 붙이지 않고 패널로 보여준다(visionOS에서도 같은 원칙).
    if ui["show_map"]:
        cell_w, cell_h, gap = 50 * s, 28 * s, 5 * s
        top = h - margin - cell_h
        for hand, center in (("Left", w * 0.27), ("Right", w * 0.73)):
            state, face = typer.hands[hand], typer.face(hand) or "palm"
            start = center - (4 * cell_w + 3 * gap) / 2
            keys = KEYS[(hand, face)]
            for i, key in enumerate(keys):
                if key is None:  # 자판에 없는 자리(오른손 손등 중지~소지)
                    continue
                bounds = (start + i * (cell_w + gap), top, start + i * (cell_w + gap) + cell_w, top + cell_h)
                glass(bounds, hover=state.pinch.active == i)
                ink = WHITE
                name = key_label(key)
                write(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2), name,
                      normal if len(name) <= 3 and key != "PUNCT" else small, ink, "mm")

    # 기록 모드: 입력 칸 아래에 꼬집을 손가락과 진행
    if collect is not None:
        title_font = large
        pw = min(w - 2 * margin, max(hud_text_width(collect["title"], title_font),
                                     hud_text_width(collect["detail"], small)) + 40 * s)
        top = row_y + 30 * s
        glass(((w - pw) / 2, top, (w + pw) / 2, top + 62 * s), tint=PROMPT_YELLOW if collect["target"] else None)
        tone = {"ok": OK_GREEN, "wrong": DELETE_RED}.get(collect["tone"], WHITE)
        write((w / 2, top + 22 * s), collect["title"], title_font, tone, "mm", True)
        write((w / 2, top + 47 * s), collect["detail"], small, MUTED, "mm", True)

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
        top = h - (ui["show_map"] and 92 or 58) * s  # 자판 지도가 있으면 그 위에
        glass(((w - mw) / 2, top, (w + mw) / 2, top + 26 * s))
        write((w / 2, top + 13 * s), message, small, MUTED, "mm")

    if ui["show_help"]:
        draw.rectangle((0, 0, w, h), fill=(16, 17, 21, 165))
        bw = min(w - 2 * margin, 470 * s)
        box = ((w - bw) / 2, 44 * s, (w + bw) / 2, h - 44 * s)
        rounded_rectangle(draw, box, 22 * s, fill=(38, 40, 47, 238), outline=(255, 255, 255, 65), width=stroke)
        for i, line in enumerate(ui["help_lines"] + [f"꼬집기 {pinch_enter:.2f} / 떼기 {pinch_exit:.2f} · {fps:.0f} FPS"]):
            write((box[0] + 20 * s, box[1] + (20 + i * 27) * s), line, normal if i == 0 else small,
                  WHITE if i == 0 else MUTED)

    return canvas.finish()


HELP_LINES = [
    "손끝 꼬집기 천지인",
    "같은 손가락 반복 = 순환(대기 글자가 손끝으로 올라옴) · 다른 키 = 자동 확정",
    "왼손 손바닥: ㅂㅍㅃ ㄹ ㅅㅎㅆ ㅈㅊㅉ  ·  오른손 손바닥: ㄱㅋㄲ ㄴ ㄷㅌㄸ ㅇㅁ  (ㄴ·ㄹ은 순환 없이 바로)",
    "왼손 손등: 모음 · 공백 · 확정 · .,?!줄바꿈  ·  모음(검지): 꼬집기 ㆍ, 꼬집은 채 가로로 끌기 ㅡ, 세로로 끌기 ㅣ",
    "오른손 손등 검지: 확정/공백(후보가 있으면 확정, 없으면 공백)",
    "주먹(양손): 마지막 자모 지움(감 → 가 → ㄱ), 쥐고 있으면 0.5초마다 계속",
    "키보드 시험: 1~4 왼손 · 7 8 9 0 오른손 · Shift와 함께 손등(! 모음, - 가로 끌기, / 세로 끌기) · Space/Backspace 주먹(지움)",
    "T 손끝 라벨 · K 자판 지도 · D 진단 수치(꼬집기 거리) · [ ] 꼬집기 판정 거리",
    "C 복사 · X 전체 지우기 · P 일시정지 · S 저장 · G 스크린샷 · F 전체 화면 · H 닫기 · Q 종료",
]


def annotate_snapshot(cv2, image, hands, signals, typer, w, h):
    """진단용 스크린샷에 손별 판정값을 ASCII로 덧붙인다: 역할(MediaPipe 라벨) / facing / 방향 / 최소 비율."""
    out = image.copy()
    for role, hand in hands.items():
        signal = signals.get(role)
        x, y = int(hand.landmarks[WRIST].x * w), int(hand.landmarks[WRIST].y * h)
        state = typer.hands[role].orientation.state or "-"
        text = f"{role}(mp:{hand.label}) f{signal.facing:+.2f} {state} min{min(signal.ratios):.2f}"
        for color, width in (((0, 0, 0), 3), ((0, 255, 255), 1)):
            cv2.putText(out, text, (max(4, x - 120), min(h - 8, y + 34)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, width)
    cv2.putText(out, f"t={time.strftime('%H:%M:%S')}", (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 255, 255), 1)
    return out


def log_row(now, typer, hands, signals, events, w, h, collecting=False, prompt=None, frame=None):
    """--log의 한 줄(프레임 하나). 상태 머신 재생에는 ratios·facing·pose·curled만 쓴다.

    image(정규화 x, y, z)와 world(m) landmark는 판정 방식을 바꿀 때 다시 계산해 보려고 남긴다.
    world 비율이 화면에서 보이는 거리와 어긋나는 자세를 찾는 데 쓴다(2026-10-22 약지→검지 오입력 조사).
    size는 이미지 좌표를 픽셀로 바꿀 때의 프레임 크기다.
    collecting이면(--collect) prompt 필드를 넣는다: 이 프레임에 안내 중인 정답 (id, 손, 손가락, 방향), 쉬는 중이면 null.
    frame: --save-video로 함께 저장한 영상 안의 프레임 번호(0부터). 영상을 저장하지 않으면 필드가 없다.
    """
    def points(landmarks, digits):
        return [[round(p.x, digits), round(p.y, digits), round(p.z, digits)] for p in landmarks]

    row = {
        "t": round(now, 4), "size": [w, h],
        "hands": {role: {"seen_as": hands[role].label,
                         "ratios": [round(v, 4) for v in sig.ratios], "facing": round(sig.facing, 3),
                         "roll": round(sig.roll, 1),
                         "pose": sig.pose, "curled": list(sig.curled),
                         "image": points(hands[role].landmarks, 4), "world": points(hands[role].world, 5)}
                  for role, sig in signals.items()},
        "events": [list(e) for e in events]}
    if collecting:  # prompt: Collector.describe 결과(dict) 또는 손가락 차례 튜플
        if prompt is not None and not isinstance(prompt, dict):
            prompt = dict(zip(Collector.FIELDS, prompt))
        row["prompt"] = prompt
    if frame is not None:
        row["frame"] = frame
    return row


def open_video(cv2, log_path, fps, size):
    """--save-video: 로그 옆 같은 이름의 .mp4(mp4v)를 연다. 열리지 않으면 .avi(MJPG)로 다시 시도한다.

    Jetson의 OpenCV 빌드에 mp4v 인코더가 없을 수 있어서다. (writer, 경로)를 돌려준다.
    """
    for suffix, codec in ((".mp4", "mp4v"), (".avi", "MJPG")):
        path = Path(log_path).with_suffix(suffix)
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, size)
        if writer.isOpened():
            return writer, path
        writer.release()
    raise RuntimeError("영상 파일을 열 수 없습니다(mp4v, MJPG 모두 실패): " + str(log_path))


def collect_view(collector, prompt, last):
    """기록 모드 안내 패널의 내용(draw_hud의 ui["collect"]). prompt: 지금 차례, last: 직전 결과 이벤트."""
    if isinstance(collector, WordCollector):
        return word_view(collector, prompt, last)
    if prompt is not None:
        number, hand, finger, face = prompt
        name = key_label(KEYS[(hand, face)][finger])
        return {"target": (hand, finger), "tone": "prompt",
                "title": f"{collector.label(hand, finger, face)} · {name}",
                "detail": f"{number + 1} / {len(collector.order)} · {collector.round_of(number)}/{collector.reps}바퀴 · "
                          "N 입력 안 됨 · B 직전 다시"}
    if last is None:
        return None
    _, number, hand, finger, result = last[:5]
    if result == "skip":
        title, tone = "입력 안 됨 (건너뜀)", "skip"
    elif result == "redo":
        title, tone = "직전 차례를 다시 합니다", "skip"
    elif result == finger:
        title, tone = "맞음", "ok"
    else:
        title, tone = f"다른 손가락으로 입력됨: {FINGER_NAMES[result]}", "wrong"
    return {"target": None, "tone": tone, "title": title, "detail": f"{number + 1} / {len(collector.order)}"}


def word_view(collector, prompt, last):
    """단어 기록 안내: 칠 단어와 진행. 다 치면(입력 칸이 단어와 같으면) 다음 단어로 넘어간다."""
    if prompt is not None:
        number, word = prompt
        return {"target": None, "tone": "prompt", "title": f"「{word}」 입력",
                "detail": f"{number + 1} / {len(collector.order)} · 틀리면 지우고 고쳐 치기 · N 건너뜀 · B 직전 다시"}
    if last is None:
        return None
    _, number, word, result = last[:4]
    title, tone = {"done": ("완료", "ok"), "skip": ("건너뜀", "skip"), "redo": ("직전 단어를 다시 칩니다", "skip")}[result]
    return {"target": None, "tone": tone, "title": f"{title} · {word}", "detail": f"{number + 1} / {len(collector.order)}"}


def copy_text(text):
    """시스템 클립보드에 복사한다(macOS pbcopy, Linux wl-copy / xclip). 도구가 없으면 False."""
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=text.encode("utf-8"), check=False)
            return True
    return False


# ---------------------------------------------------------------------------
# 실행

def demo():
    """카메라 없이 고정 데이터(fixtures/pinch-typer-keys.json)의 첫 시퀀스를 재생한다."""
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    case = data["cases"][0]
    print(case["name"])
    print(replay(case["steps"]).ime.text)


def is_dark(frame):
    """영상이 거의 검은지(16픽셀 간격 표본의 최댓값 < 8). Continuity Camera가 멈추면 검은 프레임이 들어온다."""
    return frame[::16, ::16].max() < 8


def run(args):
    import cv2

    fonts = load_fonts(args.font)
    typer = PinchTyper(enter=args.pinch_enter, exit=args.pinch_exit, fist_hold=args.fist_hold, hold=args.hold)
    # 화면 상태. draw_hud가 읽고, 단축키가 바꾼다.
    ui = {"paused": False, "show_help": False, "show_map": False, "tip_labels": True, "debug": False,
          "status": "", "notice_since": None, "help_lines": HELP_LINES}
    # 기록 모드: 꼬집을 손가락을 차례로 안내하고, 프레임마다 그 정답을 로그에 남긴다(pinch_collect.py).
    collector = None
    if args.collect and args.collect_set == "words":
        collector = WordCollector(args.collect_reps or 1)
    elif args.collect:
        collector = Collector(args.collect_reps or 10, args.collect_set)
    collect_last, collect_clear = None, False  # 직전 결과 이벤트, 다음 차례를 시작할 때 입력 칸을 비울지
    if collector and args.log is None:
        args.log = DATA / f"collect-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log = open(args.log, "a", encoding="utf-8") if args.log else None
    video, video_frames = None, 0  # --save-video: 카메라 프레임 저장(학습·전처리 비교용), 저장한 프레임 수
    if collector:
        log.write(json.dumps(collector.config(), ensure_ascii=False) + "\n")
        print(f"기록 모드({collector.kind}): {collector.reps}바퀴, {len(collector.order)}차례 → {args.log}", flush=True)
    # 진단 스크린샷: 1초마다(최근 40장 순환), 모드가 바뀔 때마다, G 키를 누를 때 저장한다.
    args.snapshots.mkdir(parents=True, exist_ok=True)
    print("스크린샷 저장 위치:", args.snapshots)
    snapshot_count, last_snapshot = 0, 0.0
    dark_since, dark_warned = None, False  # 카메라가 열렸지만 검은 프레임만 들어오는 경우(Continuity Camera 멈춤 등)
    capture, exit_reason = None, None
    clock, meter = VideoClock(), FpsMeter()

    def notice(message):
        ui["status"], ui["notice_since"] = message, time.monotonic()

    def action(key):
        """버튼·메뉴·단축키가 공통으로 부르는 동작."""
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
            capture = open_capture(args.camera, args.pipeline, size)
            window = Window(WINDOW)  # 첫 화면 크기에 맞춰 연다
            first_frame = True
            while True:
                frame, gap = read_frame(capture)
                if first_frame:
                    fh, fw = frame.shape[:2]
                    small = detection_frame(frame, args.detect_width).shape
                    requested = "GStreamer 파이프라인" if args.pipeline else f"요청 {size[0]}x{size[1]}"
                    print(f"카메라 해상도 {fw}x{fh} ({requested}), 손 검출 입력 {small[1]}x{small[0]}", flush=True)
                    first_frame = False
                if gap:
                    typer.reset()  # 끊긴 동안의 손 상태로 입력하지 않는다.
                now = time.monotonic()
                fps = meter.tick(now)
                if is_dark(frame):
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
                frame_number = None
                if args.save_video and log:  # HUD를 그리기 전 프레임. 로그 행의 frame으로 짝을 맞춘다.
                    if video is None:
                        video, video_path = open_video(cv2, args.log, 30.0, (w, h))
                        print("영상 저장:", video_path, flush=True)
                    video.write(frame)
                    frame_number, video_frames = video_frames, video_frames + 1

                # 검출 → 신호 → 상태 머신
                detections = detect_hands(detector, detection_frame(frame, args.detect_width), clock.ms(now),
                                          args.mirror)
                hands = assign_hands(detections)
                signals = build_signals(hands, w, h, args.mirror)
                enabled = not ui["paused"] and not ui["show_help"]
                prompt = collector.prompt(now) if collector else None
                if prompt is not None and collect_clear:
                    # 새 차례: 직전 차례에 입력된 글자를 지우고, 쉬는 동안 시작된 접촉을 버린다.
                    # (버리지 않으면 그 접촉이 새 차례 첫 프레임에 떼어져 엉뚱한 결과로 남는다. 2026-10-22 기록)
                    typer.ime.clear()
                    typer.pending, collect_clear = None, False
                    for state in typer.hands.values():
                        state.pinch.lost()
                        state.contact = None
                events = typer.update(signals, now) if enabled else []
                if collector and enabled:
                    finished = collector.update(typer.pinches, now, typer.ime.text)
                    events += finished
                    if finished:
                        collect_last, collect_clear = finished[-1], True
                if log:
                    described = None if prompt is None else collector.describe(prompt)
                    row = log_row(now, typer, hands, signals, events, w, h, collector is not None, described,
                                  frame_number)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                if collector and collector.done:
                    print(collector.summary(), flush=True)
                    notice("기록 완료 · 결과는 터미널에")
                    collector, ui["collect"] = None, None
                elif collector:
                    ui["collect"] = collect_view(collector, collector.prompt(now), collect_last)

                # 화면
                points = {role: to_pixels(hand.landmarks, w, h) for role, hand in hands.items()}
                view = draw_hud(frame, typer, points, ui, fonts, fps)
                window.show(view)
                code = window.read_key()
                reasons = []
                if now - last_snapshot >= 1.0:
                    reasons.append(f"auto-{snapshot_count % 40:02d}")
                if code in (ord("g"), ord("G")):
                    reasons.append(f"manual-{snapshot_count:04d}")
                    notice("스크린샷 저장")
                if reasons:
                    shot = annotate_snapshot(cv2, view, hands, signals, typer, w, h)
                    for reason in reasons:
                        cv2.imwrite(str(args.snapshots / f"{reason}.jpg"), shot, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    snapshot_count += 1
                    last_snapshot = now

                # 단축키
                if code in (27, ord("q"), ord("Q")):
                    exit_reason = "Q/ESC 키"
                    break
                if window.closed():
                    exit_reason = "영상 창 닫힘"
                    break
                if collector and code in (ord("n"), ord("N"), ord("b"), ord("B")):
                    marked = collector.skip(now) if code in (ord("n"), ord("N")) else collector.redo(now)
                    if marked:
                        collect_last, collect_clear = marked[-1], True
                        if log:  # 키로 남긴 결과도 같은 프레임 시각으로 로그에 남긴다.
                            log.write(json.dumps({"t": round(now, 4), "events": [list(e) for e in marked]},
                                                 ensure_ascii=False) + "\n")
                elif code in TEST_KEYS:
                    hand, finger, face, drag = TEST_KEYS[code]  # 인자 순서(손, 손가락, 시각, 방향, 끌기)에 맞춰 넘긴다
                    typer.press_finger(hand, finger, now, face, drag)
                elif code == 32:
                    typer.make_fist("Left", now)
                elif code in (8, 127):
                    typer.make_fist("Right", now)
                elif code in (ord("s"), ord("S")):
                    action("save")
                elif code in (ord("c"), ord("C")):
                    action("copy")
                elif code in (ord("x"), ord("X")):
                    action("clear")
                elif code in (ord("f"), ord("F")):
                    window.toggle_fullscreen()
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
                    # 꼬집기 판정 비율 0.02 단위 조절. 떼기 기준과의 간격은 유지한다.
                    gap = typer.hands["Left"].pinch.exit - typer.enter
                    enter = min(0.5, max(0.1, round(typer.enter + (0.02 if code == ord("]") else -0.02), 2)))
                    typer.set_pinch(enter, round(enter + gap, 2))
                    notice(f"꼬집기 판정 {enter:.2f}")
    finally:
        if capture is not None:
            capture.release()
        if video is not None:
            video.release()
        if log:
            log.close()
        cv2.destroyAllWindows()
        if exit_reason:
            print("종료 이유: " + exit_reason)
        if collector and collector.results:
            print("기록 중단 (" + f"{len(collector.results)}/{len(collector.order)}차례)\n" + collector.summary())
        print(f"꼬집기 판정: enter {typer.enter:.2f}, exit {typer.hands['Left'].pinch.exit:.2f}")
        print("\n입력 결과:\n" + typer.ime.text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_camera_arguments(parser, "카메라 번호 (macOS: iPhone 연결 시 0)")
    parser.add_argument("--mirror", action="store_true", help="좌우 반전 (카메라가 사람을 마주 보는 셀카 배치)")
    parser.add_argument("--resolution", type=parse_resolution, default="auto",
                        help="카메라 번호로 열 때 요청할 해상도. auto: macOS 1280x720, 그 밖(Jetson) 640x480. "
                             "--pipeline이면 파이프라인 문자열이 정한다")
    parser.add_argument("--detect-width", type=int, default=640,
                        help="손 검출에 넣는 영상의 최대 가로 크기. 화면은 원래 해상도로 그린다")
    parser.add_argument("--pinch-enter", type=positive_float, default=PINCH_ENTER,
                        help="꼬집기 후보가 되는 거리 상한(엄지 끝~손가락 끝마디 / 손목~중지 뿌리). "
                             "이 값 아래이면서 직전 값보다 충분히 다가온 손가락만 꼬집은 것으로 본다")
    parser.add_argument("--pinch-exit", type=positive_float, default=PINCH_EXIT, help="이 값 위로 벌리면 1회로 센다")
    parser.add_argument("--fist-hold", type=positive_float, default=0.18, help="주먹 유지 시간 (초)")
    parser.add_argument("--hold", type=float, default=HOLD_TIME,
                        help="실험: 꼬집고 이 시간(초) 머물러야 입력(누르고 유지, 예 0.3). 기본 0은 손끝을 뗄 때 입력")
    parser.add_argument("--log", type=Path, help="프레임별 손 신호·landmark와 이벤트를 jsonl로 남긴다 (판정값 조정용)")
    parser.add_argument("--collect", action="store_true",
                        help="기록 모드: 손마다 검지→소지를 한 번씩 꼬집도록 차례로 안내하고 정답과 함께 기록한다 "
                             "(--log가 없으면 data/collect-날짜-시각.jsonl)")
    parser.add_argument("--collect-reps", type=int, default=None,
                        help="기록 모드 바퀴 수: 손가락 차례는 손마다 검지→소지 바퀴(기본 10), words는 단어 목록 반복(기본 1)")
    parser.add_argument("--collect-set", choices=KINDS, default="typing",
                        help="기록 모드 차례: typing 손바닥 자판(양손), left/right 그 손 손바닥만, edit 왼손 손등 편집 키, "
                             "words 단어를 이어서 치기")
    parser.add_argument("--save-video", action="store_true",
                        help="--log(또는 --collect)와 같은 이름의 .mp4에 카메라 프레임을 저장하고 로그 행에 frame 번호를 남긴다")
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
    if args.save_video and not (args.log or args.collect):
        parser.error("--save-video는 --log나 --collect와 함께 쓰세요(로그 행의 frame 번호로 영상과 짝을 맞춘다).")
    if args.hold < 0:
        parser.error("--hold는 0 이상으로 지정하세요.")
    if args.collect_reps is not None and args.collect_reps < 1:
        parser.error("--collect-reps는 1 이상으로 지정하세요.")
    if args.detect_width < 160:
        parser.error("--detect-width는 160 이상으로 지정하세요.")
    resolve_pipeline(args)
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
