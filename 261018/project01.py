"""엄지로 손가락 마디를 누르면 입력하고, 주먹을 쥐면 지우는 천지인 키보드.

실행: python 261018/project01.py
카메라 없이 조합 확인: python 261018/project01.py --demo
"""

# 이전 시도 앱(엄지 마디 누르기). 단안 카메라로는 엄지가 마디에 닿았는지 판정하기 어려워 iPhone 실기 시험에서
# 입력되지 않았다(2026-10-18). 현재 주 버전은 pinch_cheonjiin.py다.
#
# 한 프레임의 처리 흐름 (run)
#   카메라 프레임 → 좌우 반전(셀카 배치) → (E) 명도 보정 → 손 검출 → HandKeyboardTracker
#   → 손마다 엄지 끝~마디 world 거리로 누르기 판정(tap_detect.Seg), 주먹이면 지움(FistHold)
#   → 조합기 입력 → 손 위 키 HUD(common.hand_keys.draw_ui) → 창 표시 → 키 입력 처리
# 키 배치는 common/hand_keys.py 머리말을 본다.

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import time

from korean_ime import CheonjiinIME
from common.camera import FpsMeter, VideoClock, Window, open_capture, read_frame
from common.cli import add_camera_arguments, positive_float, resolve_pipeline
from common.gestures import FistHold
from common.hand_keys import KEY_OF_SEGMENT, HandView, draw_ui, key_targets
from common.hands import create_detector, detect_hands
from common.hud import load_fonts
from common.pose import pose_from_image


WINDOW = "Hand Cheonjiin"
# 누르기 판정 기본값: world 좌표(m)에서 엄지 끝과 마디 선분 사이 거리. tap_detect.Seg 참고.
#   TAP_ENTER 안으로 들어와 TAP_DWELL초 유지하면 입력, TAP_EXIT 밖으로 떨어지면 다음 입력 준비.
TAP_ENTER, TAP_EXIT, TAP_DWELL = 0.012, 0.020, 0.05


class HandKeyboardTracker:
    """왼손·오른손마다 누르기 검출기와 주먹(지움) 상태를 따로 유지한다."""

    def __init__(self, mode="both", tap_enter=TAP_ENTER, tap_exit=TAP_EXIT, tap_dwell=TAP_DWELL, fist_hold=0.18):
        from tap_detect import Seg

        self.mode = "both" if mode == "auto" else mode  # both / left / right
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
        """detections: (좌우 라벨, 이미지 landmark, world landmark). 반환: (HandView 목록, 이벤트).

        이벤트는 (라벨, 키)이고 지움은 "BACK"이다. 같은 라벨 손이 둘 보이면 그 손은 표시만 하고 입력은 보류한다.
        """
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
            pose = pose_from_image(landmarks, width, height)
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


def help_lines(tap_enter, fist_hold, enhanced, fps, hands, ime):
    """H 도움말 줄. 마지막 두 줄은 지금 설정과 인식된 손을 보여준다."""
    return (
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


def enhance_frame(frame, clahe):
    """practice의 LAB 명도 보정과 Gaussian 필터: L 채널에만 CLAHE를 적용해 색은 두고 밝기 대비만 올린다."""
    import cv2

    light, a, b = cv2.split(cv2.cvtColor(frame, cv2.COLOR_BGR2LAB))
    enhanced = cv2.cvtColor(cv2.merge((clahe.apply(light), a, b)), cv2.COLOR_LAB2BGR)
    return cv2.GaussianBlur(enhanced, (3, 3), 0)


def skin_overlay(frame, points):
    """손바닥에서 색을 샘플링해 손 주변의 유사 색 영역/윤곽선을 시각화한다(수업 기술 시연용).

    1) 손바닥 가운데 11x11 영역의 LAB a/b 중앙값을 피부색으로 본다.
    2) 그 색 ±(18, 22) 범위를 마스크로 만들고, landmark 볼록 껍질 밖은 지운다.
    3) opening/closing으로 잡음을 줄이고 외곽선을 그린다.
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


def run(args):
    try:
        import cv2
        import mediapipe  # noqa: F401  설치 여부만 미리 확인한다(검출은 common.hands에서 쓴다).
        from PIL import ImageFont  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("필요 패키지를 확인하세요: mediapipe, numpy, Pillow, OpenCV. " + str(exc)) from exc

    fonts = load_fonts(args.font)
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
    clock, meter = VideoClock(), FpsMeter()
    exit_reason = None
    try:
        with create_detector(args.model) as detector:
            capture = open_capture(args.camera, args.pipeline)
            window = Window(WINDOW, (960, 720))
            while True:
                frame, gap = read_frame(capture)
                if gap:
                    keyboards.reset()  # 끊긴 동안의 엄지 위치로 입력하지 않는다.
                now = time.monotonic()
                fps = meter.tick(now)
                if not args.no_mirror:
                    frame = cv2.flip(frame, 1)  # 1: 좌우 반전. 0은 상하 반전이다.
                if args.flip_vertical:
                    frame = cv2.flip(frame, 0)
                if enhanced:
                    frame = enhance_frame(frame, clahe)
                h, w = frame.shape[:2]
                detections = [(label, landmarks, world) for label, landmarks, world, _ in
                              detect_hands(detector, frame, clock.ms(now), not args.no_mirror)]
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
                current_enter = keyboards.taps["Left"].enter
                view = draw_ui(frame, ime, hands, status, fonts, paused, show_help, notice_since, current_enter,
                               help_lines=help_lines(current_enter, fist_hold, enhanced, fps, hands, ime))
                window.show(view)
                key_code = window.read_key()
                if key_code in (27, ord("q"), ord("Q")):
                    exit_reason = "Q/ESC 키"
                    break
                if window.closed():
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


def demo():
    """카메라 없이 키 번호로 '안녕하세요'를 조합한다."""
    ime = CheonjiinIME()
    now = 0.0
    for syllable_keys in ("0125", "52210", "8812", "8211", "0223"):
        for key in syllable_keys:
            ime.press(key, now)
            now += 0.25
        now += 1.0  # 안/녕 경계의 ㄴ을 ㄹ로 순환시키지 않는다.
    print(ime.text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_camera_arguments(parser, "USB 카메라 번호 (기본 0)")
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
        demo()
        return
    resolve_pipeline(args)
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
