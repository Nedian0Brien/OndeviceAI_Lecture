"""PinchKeyboard: 손끝 꼬집기 한글 입력기 (1인칭 카메라 기준).

카메라를 눈 위치에 두고 손을 내려다본다. 손바닥이 카메라를 향하면 자음 자판, 손을 뒤집어 손등이 보이면 왼손은
모음·편집 키, 오른손은 확정/공백 키다. 자판은 layout.py, 판정 규칙은 pinch.py와 typer.py 머리말을 본다.

실행:  python Project01/project01.py --camera 0
단어:  python Project01/project01.py --words      (단어 입력 시험, 기록은 data/words-날짜-시각.jsonl)
시연:  python Project01/project01.py --demo       (카메라 없이 고정 데이터 재생)
"""

# 한 프레임의 처리 흐름 (run)
#   카메라 프레임 → (--mirror면 좌우 반전) → 손 검출(가로 640 이하로 줄인 영상) → 왼손·오른손 배정
#   → 손마다 HandSignal(hand_signal.py) → PinchTyper.update(typer.py) → 입력 이벤트
#   → (--log) 기록 한 줄 → 화면 그리기(hud.py) → 창 표시 → 단축키
# 이 파일은 카메라·창·단축키만 다룬다.

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from common.camera import FpsMeter, VideoClock, Window, capture_size, open_capture, parse_resolution, read_frame
from common.canvas import load_fonts
from common.cli import add_camera_arguments, resolve_pipeline
from common.hands import assign_hands, create_detector, detect_hands, detection_frame
from common.landmarks import to_pixels
from common.pose import pose_from_image
from hand_signal import hand_signal
from hud import draw_hud
from tools.recording import log_row
from tools.word_test import WordTest
from typer import PinchTyper, replay


WINDOW = "PinchKeyboard"
FIXTURES = Path(__file__).resolve().with_name("fixtures") / "pinch-typer-keys.json"
DATA = Path(__file__).resolve().with_name("data")


def build_signals(hands, width, height, mirrored):
    """{"Left"|"Right": Detection} → {"Left"|"Right": HandSignal}.

    손바닥 방향은 검출의 label(MediaPipe가 본 손)로 계산한다. 같은 라벨 두 손을 화면 위치로 나눈 경우에도
    landmark 배치와 짝이 맞는 것은 원래 label이기 때문이다(common.hands.assign_hands 설명).
    자세(편 손·주먹)는 이미지 좌표 방식(pose_from_image)을 쓴다.
    """
    return {role: hand_signal(hand.label, hand.landmarks, hand.world, mirrored, width, height,
                              pose_from_image(hand.landmarks, width, height))
            for role, hand in hands.items()}


def word_view(test, prompt, last):
    """단어 입력 안내 패널(hud.draw_word_panel). prompt: 지금 단어 (번호, 단어), last: 직전 결과 이벤트."""
    if prompt is not None:
        number, word = prompt
        return {"tone": "prompt", "title": f"「{word}」 입력",
                "detail": f"{number + 1} / {len(test.order)} · 틀리면 지우고 고쳐 치기 · N 건너뛰기"}
    if last is None:
        return None
    _, number, word, result = last
    title, tone = {"done": ("완료", "ok"), "skip": ("건너뜀", "skip")}[result]
    return {"tone": tone, "title": f"{title} · {word}", "detail": f"{number + 1} / {len(test.order)}"}


def copy_text(text):
    """시스템 클립보드에 복사한다(macOS pbcopy, Linux wl-copy / xclip). 도구가 없으면 False."""
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=text.encode("utf-8"), check=False)
            return True
    return False


def demo():
    """카메라 없이 고정 데이터(fixtures/pinch-typer-keys.json)의 첫 시퀀스를 재생한다."""
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
    print(case["name"])
    print(replay(case["steps"]).ime.text)


def is_dark(frame):
    """영상이 거의 검은지(16픽셀 간격 표본의 최댓값 < 8). Continuity Camera가 멈추면 검은 프레임이 들어온다."""
    return frame[::16, ::16].max() < 8


def run(args):
    import cv2

    fonts = load_fonts(args.font)
    typer = PinchTyper()
    # 화면 상태: draw_hud가 읽고, 단축키가 바꾼다.
    ui = {"paused": False, "show_help": False, "show_map": False, "status": "", "notice_since": None, "word": None}
    # 단어 입력 시험(--words): 단어를 하나씩 내고, 프레임마다 지금 단어를 기록에 남긴다(tools/word_test.py).
    test = WordTest() if args.words else None
    last_result, new_word = None, False  # 직전 결과 이벤트, 다음 단어를 시작할 때 입력 칸을 비울지
    if test and args.log is None:
        args.log = DATA / f"words-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log = None
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)  # data/는 저장소에 없다(.gitignore)
        log = open(args.log, "a", encoding="utf-8")
    if test:
        log.write(json.dumps(test.config(), ensure_ascii=False) + "\n")
        print(f"단어 입력: {len(test.order)}단어 → {args.log}", flush=True)
    dark_since, dark_warned = None, False  # 카메라가 열렸지만 검은 프레임만 들어오는 경우(Continuity Camera 멈춤 등)
    capture, exit_reason = None, None
    clock, meter = VideoClock(), FpsMeter()

    def notice(message):
        ui["status"], ui["notice_since"] = message, time.monotonic()

    def finish_word(events):
        nonlocal last_result, new_word
        last_result, new_word = events[-1], True

    try:
        with create_detector(args.model) as detector:
            size = capture_size(args.resolution)
            capture = open_capture(args.camera, args.pipeline, size)
            window = Window(WINDOW)
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

                # 검출 → 신호 → 판정
                detections = detect_hands(detector, detection_frame(frame, args.detect_width), clock.ms(now),
                                          args.mirror)
                hands = assign_hands(detections)
                signals = build_signals(hands, w, h, args.mirror)
                enabled = not ui["paused"] and not ui["show_help"]
                prompt = test.prompt(now) if test else None
                if prompt is not None and new_word:
                    # 새 단어: 직전 단어에 입력된 글자를 지우고, 쉬는 동안 시작된 접촉을 버린다.
                    typer.ime.clear()
                    typer.pending, new_word = None, False
                    for state in typer.hands.values():
                        state.pinch.lost()
                        state.contact = None
                events = typer.update(signals, now) if enabled else []
                if test and enabled:
                    finished = test.update(now, typer.ime.text)
                    events += finished
                    if finished:
                        finish_word(finished)
                if log:
                    row = log_row(now, hands, signals, events, w, h,
                                  None if prompt is None else test.describe(prompt), test is not None)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                if test and test.done:
                    print(test.summary(), flush=True)
                    notice("단어 입력 완료 · 결과는 터미널에")
                    test, ui["word"] = None, None
                elif test:
                    ui["word"] = word_view(test, test.prompt(now), last_result)

                # 화면
                points = {role: to_pixels(hand.landmarks, w, h) for role, hand in hands.items()}
                window.show(draw_hud(frame, typer, points, ui, fonts, fps))
                code = window.read_key()
                key = chr(code).lower() if 0 <= code < 128 else ""

                # 단축키
                if code == 27 or key == "q":
                    exit_reason = "Q/ESC 키"
                    break
                if window.closed():
                    exit_reason = "영상 창 닫힘"
                    break
                if key == "n" and test:
                    skipped = test.skip(now)
                    if skipped:
                        finish_word(skipped)
                        if log:  # 키로 남긴 결과도 같은 시각으로 기록한다.
                            log.write(json.dumps({"t": round(now, 4), "events": [list(e) for e in skipped]},
                                                 ensure_ascii=False) + "\n")
                elif key == "p":
                    ui["paused"] = not ui["paused"]
                    typer.reset()
                elif key == "h":
                    ui["show_help"] = not ui["show_help"]
                    typer.reset()
                elif key == "c":
                    notice("복사됨" if copy_text(typer.ime.text) else "복사 도구가 없습니다")
                elif key == "x":
                    typer.ime.clear()
                    typer.pending = None
                    notice("전체 지움")
                elif key == "k":
                    ui["show_map"] = not ui["show_map"]
                elif key == "f":
                    window.toggle_fullscreen()
    finally:
        if capture is not None:
            capture.release()
        if log:
            log.close()
        cv2.destroyAllWindows()
        if exit_reason:
            print("종료 이유: " + exit_reason)
        if test and test.results:
            print(f"단어 입력 중단 ({len(test.results)}/{len(test.order)}단어)\n" + test.summary())
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
    parser.add_argument("--log", type=Path, help="프레임별 손 신호·관절과 이벤트를 jsonl로 남긴다 (tools/evaluate.py로 재생)")
    parser.add_argument("--words", action="store_true",
                        help="단어 입력 시험: 짧은 단어를 하나씩 내고 기록한다 (--log가 없으면 data/words-날짜-시각.jsonl)")
    parser.add_argument("--demo", action="store_true", help="카메라 없이 고정 데이터의 '안녕하세요'를 재생")
    args = parser.parse_args()
    if args.demo:
        demo()
        return
    if args.detect_width < 160:
        parser.error("--detect-width는 160 이상으로 지정하세요.")
    resolve_pipeline(args)
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
