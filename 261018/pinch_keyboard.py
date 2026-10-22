"""자판 손의 마디 버튼을 선택 손으로 꼬집어 입력하는 천지인 키보드 (방식 1).

자판 손(기본 왼손)은 손바닥을 카메라로 향해 편다. 선택 손(기본 오른손)의 엄지·검지 중점이 커서이고,
커서를 키 위에 두고 엄지·검지를 맞닿으면 그 키를 입력한다. 자판 손 주먹은 지움이다.

실행: python 261018/pinch_keyboard.py --camera 0
"""

# 이전 시도 앱(방식 1, 2026-10-18). 입력은 되었지만, 맞은편 카메라 배치에서는 사용자 시점과 화면이 달라
# 조작하기 어려웠다. 현재 주 버전은 pinch_cheonjiin.py다.
#
# 한 프레임의 처리 흐름 (PinchKeyboard.update)
#   검출 → 자판 손 / 선택 손 배정 → 선택 손: 커서(엄지·검지 중점)와 꼬집기 비율
#   → 자판 손: 마디 키 위치. 선택 손이 겹치거나 잠깐 사라지면 직전 위치를 유지한다.
#   → 커서와 가장 가까운 키 강조 → 꼬집기(PinchGate)로 입력, 자판 손 주먹(FistHold)으로 지움
# 키 배치와 화면은 엄지 마디 앱과 같은 common/hand_keys.py를 쓴다.

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import time
from typing import Optional

from korean_ime import CheonjiinIME
from common.camera import FpsMeter, VideoClock, Window, open_capture, read_frame
from common.cli import add_camera_arguments, positive_float, resolve_pipeline
from common.gestures import FistHold
from common.hand_keys import HandView, draw_ui, key_targets
from common.hands import assign_hands, create_detector, detect_hands
from common.hud import load_fonts
from common.landmarks import MIDDLE_MCP, THUMB_TIP, WRIST, palm_size, to_pixels, xyz
from common.pose import pose_from_image


WINDOW = "Pinch Keyboard"
HOLD_KEYBOARD = 1.5  # 자판 손이 가려져 검출이 끊겨도 마지막 위치를 이 시간(초)까지 쓴다.
SELECT_RADIUS = 0.30  # 커서가 키에서 손바닥 크기의 30% 안에 있어야 선택한다.
SWITCH_MARGIN = 1.25  # 다른 키가 현재 키보다 1.25배 이상 가까워야 강조를 옮긴다.
INDEX_TIP = 8
# 꼬집기 비율(엄지 끝~검지 끝 / 손목~중지 뿌리)이 PINCH_ENTER 아래면 맞닿음, PINCH_EXIT 위로 벌려야 다음 입력.
PINCH_ENTER, PINCH_EXIT = 0.30, 0.45


# ---------------------------------------------------------------------------
# 선택 손의 꼬집기 (한 손 안의 거리라 world 좌표를 그대로 쓴다)

def pinch_ratio(world):
    """엄지 끝~검지 끝 거리 / 손목~중지 뿌리 거리. 펴면 1 안팎, 맞닿으면 0에 가깝다."""
    scale = math.dist(xyz(world[WRIST]), xyz(world[MIDDLE_MCP]))
    return math.dist(xyz(world[THUMB_TIP]), xyz(world[INDEX_TIP])) / max(scale, 1e-6)


def pinch_cursor(points):
    """화면 좌표 (x, y) 목록에서 엄지 끝과 검지 끝의 중점. 맞닿는 순간 두 점이 이 자리로 모인다."""
    (x1, y1), (x2, y2) = points[THUMB_TIP][:2], points[INDEX_TIP][:2]
    return (x1 + x2) / 2, (y1 + y2) / 2


class PinchGate:
    """벌림(exit 이상) → 오므리기 시작(exit 아래)에서 키 고정 → enter 아래에서 한 번 입력 → 다시 벌려야 재입력.

    오므리는 동안 커서가 움직여도 오므리기 시작할 때 고정한 키를 입력한다.
    처음 보이는 손이나 유실 후 돌아온 손은 먼저 벌려야 한다.
    """

    def __init__(self, enter=PINCH_ENTER, exit=PINCH_EXIT):
        if exit <= enter:
            raise ValueError("exit는 enter보다 커야 합니다.")
        self.enter, self.exit = enter, exit
        self.lost()

    def lost(self):
        self.armed = False
        self.closing = False
        self.latched: Optional[str] = None  # 오므리기 시작할 때 강조돼 있던 키
        self.ratio: Optional[float] = None

    @property
    def progress(self):
        """오므린 정도 0~1. 화면 표시용."""
        if self.ratio is None or not self.armed:
            return 0.0
        return min(1.0, max(0.0, (self.exit - self.ratio) / (self.exit - self.enter)))

    def update(self, ratio, key):
        """ratio: 꼬집기 비율, key: 지금 강조된 키. 맞닿은 프레임에서 고정해 둔 키를 돌려준다."""
        self.ratio = ratio
        if ratio >= self.exit:
            self.armed, self.closing, self.latched = True, False, None
            return None
        if not self.armed:
            return None
        if not self.closing:
            self.closing, self.latched = True, key
        if ratio <= self.enter:
            pressed = self.latched
            self.armed, self.closing, self.latched = False, False, None
            return pressed
        return None


# ---------------------------------------------------------------------------
# 자판 손 + 선택 손

def assign_roles(detections, keyboard_label):
    """(자판 손, 선택 손) 검출. 없으면 None. 왼손·오른손 배정 규칙은 common.hands.assign_hands와 같다."""
    hands = assign_hands(detections)
    selector_label = "Right" if keyboard_label == "Left" else "Left"
    return hands.get(keyboard_label), hands.get(selector_label)


@dataclass
class KeyboardCache:
    """마지막으로 믿을 수 있었던 자판 손 키 위치."""
    points: list
    targets: list
    since: float


class PinchKeyboard:
    """자판 손 위치 유지, 커서의 키 강조, 꼬집기 입력, 자판 손 주먹 지움 상태를 관리한다."""

    def __init__(self, keyboard_label="Left", enter=PINCH_ENTER, exit=PINCH_EXIT, fist_hold=0.18):
        self.keyboard_label = keyboard_label
        self.pinch = PinchGate(enter, exit)
        self.fist = FistHold(fist_hold)
        self.cache: Optional[KeyboardCache] = None
        self.highlight: Optional[str] = None
        self.last_press = (None, float("-inf"))
        self.focus = {}
        self.last_time = None

    def reset(self):
        self.pinch.lost()
        self.fist.lost()
        self.highlight = None

    def select(self, cursor, targets, points):
        """커서와 가장 가까운 키. 이웃 키 사이에서 강조가 번갈아 바뀌지 않게 여유를 둔다."""
        if cursor is None or not targets:
            return None
        distance = {key: math.dist(cursor, points[index]) for key, _, index, _, _ in targets}
        nearest = min(distance, key=distance.get)
        if distance[nearest] > SELECT_RADIUS * palm_size(points):
            return None
        current = self.highlight
        if current in distance and distance[current] <= distance[nearest] * SWITCH_MARGIN \
                and distance[current] <= SELECT_RADIUS * palm_size(points):
            return current
        return nearest

    def update(self, detections, width, height, now, enabled=True):
        """detections: (라벨, 이미지 landmark, world landmark). 반환: (HandView 목록, 이벤트, 커서).

        이벤트는 입력한 키 또는 "BACK"(지움). 커서는 (x, y, 오므린 정도, 꼬집기 비율) 또는 None.
        """
        keyboard, selector = assign_roles(detections, self.keyboard_label)
        events, views = [], []

        # 선택 손: 커서와 꼬집기 비율
        cursor = selector_points = None
        ratio = None
        if selector is not None:
            selector_points = to_pixels(selector[1], width, height)
            cursor = pinch_cursor(selector_points)
            ratio = pinch_ratio(selector[2])

        # 자판 손: 선택 손이 겹쳐 있거나 잠깐 사라지면 직전 위치를 쓴다.
        keyboard_pose = None
        if keyboard is not None:
            points, targets = key_targets(keyboard[1], width, height)
            keyboard_pose = pose_from_image(keyboard[1], width, height)
            overlapped = (selector_points is not None and targets
                          and self.cache is not None and self.inside(selector_points, self.cache.points))
            if targets and not overlapped and keyboard_pose != "fist":
                self.cache = KeyboardCache(points, targets, now)
        if self.cache is not None and now - self.cache.since > HOLD_KEYBOARD and keyboard is None:
            self.cache = None

        if not enabled:
            self.reset()
        else:
            if keyboard is not None and self.fist.update(keyboard_pose, now):
                events.append("BACK")
                self.last_press = ("BACK", now)
            if keyboard is None:
                self.fist.lost()
            targets = self.cache.targets if self.cache is not None else []
            points = self.cache.points if self.cache is not None else []
            self.highlight = self.select(cursor, targets, points) if keyboard_pose != "fist" else None
            if ratio is None:
                self.pinch.lost()
            else:
                pressed = self.pinch.update(ratio, self.highlight)
                if pressed is not None:
                    events.append(pressed)
                    self.last_press = (pressed, now)

        # 화면: 강조 정도를 80ms 보간하고, 자판 손과 선택 손 HandView를 만든다.
        blend = 1.0 if self.last_time is None else 1 - math.exp(-max(0, now - self.last_time) / 0.08)
        self.last_time = now
        if self.cache is not None:
            self.focus = {key: self.focus.get(key, 0.0) + blend * ((1.0 if key == self.highlight else 0.0)
                                                                   - self.focus.get(key, 0.0))
                          for key, _, _, _, _ in self.cache.targets}
            views.append(HandView(self.keyboard_label, self.cache.points, self.cache.targets, self.highlight,
                                  keyboard_pose or "open", True, self.fist.progress, self.last_press, self.focus,
                                  thumb=False))
        if selector is not None:
            views.append(HandView("Right" if self.keyboard_label == "Left" else "Left", selector_points, [], None,
                                  pose_from_image(selector[1], width, height), True, thumb=False))
        return views, events, (None if cursor is None else (*cursor, self.pinch.progress, ratio))

    @staticmethod
    def inside(selector_points, keyboard_points):
        """선택 손의 엄지·검지 끝이 자판 손 영역(21개 landmark 경계 상자, 여유 10%) 안에 있는지."""
        xs = [p[0] for p in keyboard_points[:21]]
        ys = [p[1] for p in keyboard_points[:21]]
        pad_x, pad_y = (max(xs) - min(xs)) * 0.1, (max(ys) - min(ys)) * 0.1
        return any(min(xs) - pad_x <= x <= max(xs) + pad_x and min(ys) - pad_y <= y <= max(ys) + pad_y
                   for x, y in (selector_points[THUMB_TIP], selector_points[INDEX_TIP]))


# ---------------------------------------------------------------------------
# 실행

def run(args):
    import cv2

    fonts = load_fonts(args.font)
    ime = CheonjiinIME(args.cycle)
    keyboard = PinchKeyboard(args.keyboard.capitalize(), args.pinch_enter, args.pinch_exit, args.fist_hold)
    gap = args.pinch_exit - args.pinch_enter
    paused = show_help = False
    notice_since, status = None, ""
    clock, meter = VideoClock(), FpsMeter()
    capture, exit_reason = None, None
    selector_name = "오른손" if args.keyboard == "left" else "왼손"
    keyboard_name = "왼손" if args.keyboard == "left" else "오른손"
    try:
        with create_detector(args.model) as detector:
            capture = open_capture(args.camera, args.pipeline)
            window = Window(WINDOW, (960, 720))
            while True:
                frame, lost = read_frame(capture)
                if lost:
                    keyboard.reset()
                now = time.monotonic()
                fps = meter.tick(now)
                if not args.no_mirror:
                    frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                detections = [(label, landmarks, world) for label, landmarks, world, _ in
                              detect_hands(detector, frame, clock.ms(now), not args.no_mirror)]
                hands, events, cursor = keyboard.update(detections, w, h, now, enabled=not paused and not show_help)
                for key in events:
                    glyph = ime.preview_label(key, now)
                    if key == "BACK":
                        ime.backspace()
                    else:
                        ime.press(key, now)
                    status = "입력: " + glyph
                cursors = []
                if cursor is not None:
                    x, y, progress, ratio = cursor
                    cursors.append((x, y, progress, f"{ratio:.2f}"))
                help_lines = (
                    "손마디 천지인 · 꼬집기 입력",
                    f"{keyboard_name}: 손바닥을 펴서 자판 · 주먹이면 지움",
                    f"{selector_name}: 엄지·검지 중점을 키 위에 두고 맞닿으면 입력",
                    "같은 자음 키를 빠르게 반복하면 다음 자음으로 바뀝니다.",
                    "[ ] 꼬집기 판정 비율    P 일시정지    S 저장    Q 종료",
                    f"꼬집기 {keyboard.pinch.enter:.2f} / 떼기 {keyboard.pinch.exit:.2f} · {fps:.0f} FPS",
                )
                view = draw_ui(frame, ime, hands, status, fonts, paused, show_help=show_help,
                               notice_since=notice_since, hint="키 위에서 꼬집어 입력 · 자판 손 주먹은 지움",
                               help_lines=help_lines, cursors=cursors)
                window.show(view)
                key_code = window.read_key()
                if key_code in (27, ord("q"), ord("Q")):
                    exit_reason = "Q/ESC 키"
                    break
                if window.closed():
                    exit_reason = "영상 창 닫힘"
                    break
                char = chr(key_code)
                if char.lower() == "p":
                    paused = not paused
                    keyboard.reset()
                elif char.lower() == "h":
                    show_help = not show_help
                    keyboard.reset()
                elif key_code in (8, 127):
                    ime.backspace()
                elif char.lower() == "s":
                    try:
                        args.output.write_text(ime.text, encoding="utf-8")
                        status = "저장됨: " + args.output.name
                    except OSError as exc:
                        status = "저장 실패 (터미널 확인)"
                        print("저장 실패:", exc)
                    notice_since = now
                elif char in "[]":
                    # 꼬집기 판정 비율 조절. 잘 안 잡히면 ], 맞닿기 전에 입력되면 [.
                    enter = min(0.8, max(0.1, round(keyboard.pinch.enter + (0.02 if char == "]" else -0.02), 2)))
                    keyboard.pinch = PinchGate(enter, round(enter + gap, 2))
                    status = f"누르기 판정: 꼬집기 {enter:.2f}"
                    notice_since = now
    finally:
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()
        if exit_reason:
            print("종료 이유: " + exit_reason)
        print(f"꼬집기 판정: enter {keyboard.pinch.enter:.2f}, exit {keyboard.pinch.exit:.2f}")
        print("\n입력 결과:\n" + ime.text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_camera_arguments(parser, "카메라 번호 (macOS: iPhone 연결 시 0)")
    parser.add_argument("--keyboard", choices=("left", "right"), default="left", help="자판으로 쓸 손 (기본 왼손)")
    parser.add_argument("--no-mirror", action="store_true", help="좌우 반전 끄기")
    parser.add_argument("--pinch-enter", type=positive_float, default=PINCH_ENTER,
                        help="엄지·검지 끝 거리 / 손목~중지 뿌리 길이가 이 값 아래면 꼬집은 것으로 본다")
    parser.add_argument("--pinch-exit", type=positive_float, default=PINCH_EXIT,
                        help="이 값 위로 벌려야 다음 꼬집기를 받는다")
    parser.add_argument("--fist-hold", type=positive_float, default=0.18, help="지움에 필요한 자판 손 주먹 유지 시간 (초)")
    parser.add_argument("--cycle", type=positive_float, default=0.85, help="같은 자음 키를 순환하는 시간 (초)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().with_name("pinch_output.txt"))
    args = parser.parse_args()
    if args.pinch_exit <= args.pinch_enter:
        parser.error("--pinch-exit는 --pinch-enter보다 커야 합니다.")
    resolve_pipeline(args)
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
