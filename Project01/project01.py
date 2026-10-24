"""PinchKeyboard: 손끝 꼬집기 한글 입력기 (1인칭 카메라 기준).

카메라를 눈 위치에 두고 손을 내려다봅니다. 손바닥이 카메라를 향하면 자음 자판, 손을 뒤집어 손등이 보이면 왼손은
모음·편집 키, 오른손은 확정/공백 키입니다. 자판은 layout.py, 판정 규칙은 pinch.py와 typer.py 머리말을 봅니다.

실행:  python Project01/project01.py --camera 0
단어:  python Project01/project01.py --words      (단어 입력 시험, 기록은 data/words-날짜-시각.jsonl)
시연:  python Project01/project01.py --demo       (카메라 없이 고정 데이터 재생)
"""

# 한 프레임의 처리 흐름 (run)
#   카메라 프레임 → (--mirror면 좌우 반전) → 손 검출(가로 640 이하로 줄인 영상) → 왼손·오른손 배정
#   → 손마다 HandSignal(hand_signal.py) → PinchTyper.update(typer.py) → 입력 이벤트
#   → (--log) 기록 한 줄 → 화면 그리기(hud.py) → 창 표시 → 단축키
# 이 파일은 카메라·창·단축키만 다룸

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from common.camera import Camera, FpsMeter, VideoClock, Window, capture_size, parse_resolution
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

    손바닥 방향은 검출의 label(MediaPipe가 본 손)로 계산합니다. 같은 라벨 두 손을 화면 위치로 나눈 경우에도
    landmark 배치와 짝이 맞는 것은 원래 label이기 때문입니다(common.hands.assign_hands 설명).
    자세(편 손·주먹)는 이미지 좌표 방식(pose_from_image)을 씁니다.
    """
    return {role: hand_signal(hand.label, hand.landmarks, hand.world, mirrored, width, height,
                              pose_from_image(hand.landmarks, width, height))
            for role, hand in hands.items()}


def word_view(test, prompt, last):
    """단어 입력 안내 패널(hud.draw_word_panel)에 넘길 값을 만듭니다.
    prompt: 지금 단어 (번호, 단어), last: 직전 결과 이벤트."""
    if prompt is not None:  # 칠 단어가 있음: 단어와 진행 번호 안내
        number, word = prompt
        return {"tone": "prompt", "title": f"「{word}」 입력",
                "detail": f"{number + 1} / {len(test.order)} · 틀리면 지우고 고쳐 치기 · N 건너뛰기"}
    if last is None:  # 쉬는 중인데 직전 결과도 없음: 패널을 띄우지 않음
        return None
    # 쉬는 중: 직전 단어의 결과(완료 / 건너뜀)를 보여 줌
    _, number, word, result = last
    title, tone = {"done": ("완료", "ok"), "skip": ("건너뜀", "skip")}[result]
    return {"tone": tone, "title": f"{title} · {word}", "detail": f"{number + 1} / {len(test.order)}"}


def copy_text(text):
    """시스템 클립보드에 복사합니다(macOS pbcopy, Linux wl-copy / xclip). 도구가 없으면 False를 돌려줍니다."""
    # 설치된 첫 번째 복사 도구를 씀
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            subprocess.run(command, input=text.encode("utf-8"), check=False)
            return True
    return False


def demo():
    """카메라 없이 고정 데이터(fixtures/pinch-typer-keys.json)의 첫 시퀀스를 재생합니다."""
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
    print(case["name"])
    print(replay(case["steps"]).ime.text)


def is_dark(frame):
    """영상이 거의 검은지(16픽셀 간격 표본의 최댓값 < 8) 돌려줍니다.
    Continuity Camera가 멈추면 검은 프레임이 들어옵니다."""
    return frame[::16, ::16].max() < 8


def run(args):
    """카메라를 열고 프레임마다 검출 → 판정 → 기록 → 화면 → 단축키를 처리합니다. 창을 닫거나 Q/ESC로 끝납니다."""
    import cv2

    # -- 준비: 글꼴, 판정기, 화면 상태, 단어 시험, 기록 파일 --
    fonts = load_fonts(args.font)
    typer = PinchTyper()
    # 화면 상태: draw_hud가 읽고, 단축키가 바꿈
    ui = {"paused": False, "show_help": False, "show_map": False, "status": "", "notice_since": None, "word": None}
    # 단어 입력 시험(--words): 단어를 하나씩 내고, 프레임마다 지금 단어를 기록에 남김(tools/word_test.py)
    test = WordTest() if args.words else None
    last_result, new_word = None, False  # 직전 결과 이벤트, 다음 단어를 시작할 때 입력 칸을 비울지
    if test and args.log is None:  # 단어 시험은 늘 기록함: 경로를 주지 않으면 data/에 시각 이름으로 만듦
        args.log = DATA / f"words-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log = None
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)  # data/는 저장소에 없음(.gitignore)
        log = open(args.log, "a", encoding="utf-8")
    if test:  # 기록 첫 줄: 단어 순서와 단어마다의 기대 꼬집기 순서(tools/evaluate.py가 읽음)
        log.write(json.dumps(test.config(), ensure_ascii=False) + "\n")
        print(f"단어 입력: {len(test.order)}단어 → {args.log}", flush=True)
    dark_since, dark_warned = None, False  # 카메라가 열렸지만 검은 프레임만 들어오는 경우(Continuity Camera 멈춤 등)
    capture, exit_reason = None, None
    clock, meter = VideoClock(), FpsMeter()

    def notice(message):
        # 화면 아래 상태 안내를 띄움(2.5초 동안, hud.draw_hud)
        ui["status"], ui["notice_since"] = message, time.monotonic()

    def finish_word(events):
        # 단어 하나가 끝남(맞힘·건너뜀): 결과를 안내에 남기고, 다음 단어에서 입력 칸을 비우게 함
        nonlocal last_result, new_word
        last_result, new_word = events[-1], True

    try:
        with create_detector(args.model) as detector:
            size = capture_size(args.resolution)
            capture = Camera(args.camera, args.pipeline, size)
            window = Window(WINDOW)
            first_frame = True
            last_frame, waiting_frame = None, None
            # 프레임 루프: 종료 단축키나 창 닫힘까지 반복
            while True:
                frame, gap = capture.read()
                if frame is None:
                    # 검출·입력·단어 시험을 멈추고 창 이벤트를 처리하면서 재연결을 기다림
                    if waiting_frame is None:
                        typer.reset()
                        if last_frame is not None:
                            waiting_ui = dict(ui, reconnecting=True, show_help=False, word=None)
                            waiting_frame = draw_hud(last_frame, typer, {}, waiting_ui, fonts, 0.0)
                    if waiting_frame is not None:
                        window.show(waiting_frame)
                    code = cv2.waitKey(50) & 0xFF
                    if code in (27, ord("q"), ord("Q")):
                        exit_reason = "Q/ESC 키"
                        break
                    if window.closed():
                        exit_reason = "영상 창 닫힘"
                        break
                    continue
                if first_frame:  # 첫 프레임에서 실제 카메라 해상도와 검출 입력 크기를 한 번 출력함
                    fh, fw = frame.shape[:2]
                    small = detection_frame(frame, args.detect_width).shape
                    requested = "GStreamer 파이프라인" if args.pipeline else f"요청 {size[0]}x{size[1]}"
                    print(f"카메라 해상도 {fw}x{fh} ({requested}), 손 검출 입력 {small[1]}x{small[0]}", flush=True)
                    first_frame = False
                if gap:
                    typer.reset()  # 끊긴 동안의 손 상태로 입력하지 않음
                    dark_since, dark_warned = None, False
                    meter = FpsMeter()
                    notice(f"카메라 재연결 완료 ({gap:.1f}초 끊김)")
                now = time.monotonic()
                fps = meter.tick(now)
                # 검은 프레임이 2초 넘게 이어지면 한 번만 경고함. 영상이 돌아오면 경고 상태를 풂
                if is_dark(frame):
                    dark_since = dark_since or now
                    if now - dark_since > 2.0 and not dark_warned:
                        dark_warned = True
                        message = "카메라 영상이 검습니다. iPhone을 잠근 채 거치했는지, 다른 앱이 카메라를 쓰는지 확인하세요."
                        print(message + " (내장 카메라는 --camera 1)", flush=True)
                        notice(message)
                else:
                    dark_since, dark_warned = None, False
                if args.mirror:  # 셀카 배치: 사람이 보는 방향과 같게 좌우 반전
                    frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]

                # 검출 → 신호 → 판정
                detections = detect_hands(detector, detection_frame(frame, args.detect_width), clock.ms(now),
                                          args.mirror)
                hands = assign_hands(detections)
                signals = build_signals(hands, w, h, args.mirror)
                enabled = not ui["paused"] and not ui["show_help"]  # 일시정지·도움말 중에는 입력을 받지 않음
                prompt = test.prompt(now) if test else None
                if prompt is not None and new_word:
                    # 새 단어: 직전 단어에 입력된 글자를 지우고, 쉬는 동안 시작된 접촉을 버림
                    typer.ime.clear()
                    typer.pending, new_word = None, False
                    for state in typer.hands.values():
                        state.pinch.lost()
                events = typer.update(signals, now) if enabled else []
                if test and enabled:  # 단어 시험: 입력 칸이 지금 단어와 같아졌는지 확인
                    finished = test.update(now, typer.ime.text)
                    events += finished
                    if finished:
                        finish_word(finished)
                if log:  # 프레임 기록 한 줄(tools/recording.log_row)
                    row = log_row(now, hands, signals, events, w, h,
                                  None if prompt is None else test.describe(prompt), test is not None)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                if test and test.done:  # 모든 단어를 끝냄: 결과를 출력하고 시험을 닫음
                    print(test.summary(), flush=True)
                    notice("단어 입력 완료 · 결과는 터미널에")
                    test, ui["word"] = None, None
                elif test:  # 시험 진행 중: 안내 패널 갱신
                    ui["word"] = word_view(test, test.prompt(now), last_result)

                # 화면
                points = {role: to_pixels(hand.landmarks, w, h) for role, hand in hands.items()}
                window.show(draw_hud(frame, typer, points, ui, fonts, fps))
                last_frame, waiting_frame = frame, None
                code = window.read_key()
                key = chr(code).lower() if 0 <= code < 128 else ""  # ASCII 키만 문자로 바꿈(대소문자 무시)

                # 단축키
                if code == 27 or key == "q":  # 종료
                    exit_reason = "Q/ESC 키"
                    break
                if window.closed():  # 창 닫힘 버튼
                    exit_reason = "영상 창 닫힘"
                    break
                if key == "n" and test:  # 단어 건너뛰기
                    skipped = test.skip(now)
                    if skipped:
                        finish_word(skipped)
                        if log:  # 키로 남긴 결과도 같은 시각으로 기록함
                            log.write(json.dumps({"t": round(now, 4), "events": [list(e) for e in skipped]},
                                                 ensure_ascii=False) + "\n")
                elif key == "p":  # 일시정지 전환. 다시 시작할 때 이전 손 상태로 입력하지 않게 판정기를 비움
                    ui["paused"] = not ui["paused"]
                    typer.reset()
                elif key == "h":  # 도움말 전환. 도움말을 보는 동안의 손 동작도 버림
                    ui["show_help"] = not ui["show_help"]
                    typer.reset()
                elif key == "c":  # 입력 결과를 클립보드로 복사
                    notice("복사됨" if copy_text(typer.ime.text) else "복사 도구가 없습니다")
                elif key == "x":  # 입력 전체 지움
                    typer.ime.clear()
                    typer.pending = None
                    notice("전체 지움")
                elif key == "k":  # 자판 지도 전환
                    ui["show_map"] = not ui["show_map"]
                elif key == "f":  # 전체 화면 전환
                    window.toggle_fullscreen()
    finally:
        # 정리: 카메라·기록 파일·창을 닫고, 종료 이유와 단어 시험 중간 결과, 입력 결과를 출력함
        if capture is not None:
            capture.release()
        if log:
            log.close()
        cv2.destroyAllWindows()
        if exit_reason:
            print("종료 이유: " + exit_reason)
        if test and test.results:  # 단어 시험을 끝내지 못하고 종료함
            print(f"단어 입력 중단 ({len(test.results)}/{len(test.order)}단어)\n" + test.summary())
        print("\n입력 결과:\n" + typer.ime.text)


def main():
    """실행 옵션을 읽고 시연(--demo) 또는 카메라 입력기(run)를 시작합니다."""
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
    if args.demo:  # 시연은 카메라·MediaPipe 없이 바로 끝남
        demo()
        return
    if args.detect_width < 160:  # 검출 입력 가로 크기 하한 검사
        parser.error("--detect-width는 160 이상으로 지정하세요.")
    resolve_pipeline(args)
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:  # 카메라·모델·글꼴 오류는 트레이스백 없이 문구만 출력
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
