"""카메라로 손끝 핀치를 인식해 한글을 입력하는 PinchKeyboard의 실행 파일"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import cv2

from hand import assign_hands, create_detector, detect_hands, detection_frame
from hud import draw_hud, load_fonts
from keyboard import PinchTyper, hand_signal, replay
from record import WordTest, log_row, read_config, replay_prompts, replay_words, report_prompts, report_words


WINDOW = "PinchKeyboard"
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "pinch-typer-keys.json"
DATA = HERE / "data"
DEFAULT_MODEL = HERE.parent / "src/models/MediaPipe/hand_landmarker.task"
# Jetson USB 카메라(practice 설정)를 YUY2 640x480 30fps로 받아 BGR로 바꿈
# 처리가 늦을 때 오래된 프레임을 버려 지연이 쌓이지 않게 queue와 appsink에 drop을 둠
GSTREAMER_USB = (
    "v4l2src device=/dev/video0 ! "
    "video/x-raw,format=YUY2,width=640,height=480,framerate=30/1 ! "
    "videoconvert ! video/x-raw,format=BGR ! "
    "queue leaky=downstream max-size-buffers=1 ! "
    "appsink drop=true max-buffers=1 sync=false"
)
# 카메라 번호로 열 때 요청하는 해상도. 시연 장비(Jetson) 기준 640x480이 기본이고 macOS만 1280x720을 씀
# 저해상도 USB 카메라에 720p를 요청하면 fps가 크게 떨어지는 경우가 있어 Jetson에서는 올리지 않음
DEFAULT_RESOLUTION = (640, 480)
AUTO_RESOLUTION = {"darwin": (1280, 720)}


def parse_resolution(value):
    """--resolution 값("auto" 또는 "1280x720")을 읽는 함수"""
    if value == "auto":
        return value
    # int 변환에 실패하면 argparse가 사용자에게 보여 줄 문구로 바꾸기 위해 예외를 잡음
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("auto 또는 1280x720 꼴로 지정하세요.") from None
    if width < 160 or height < 120:
        raise argparse.ArgumentTypeError("너무 작은 해상도입니다.")
    return width, height


def read_frame(capture, wait=10.0):
    """카메라 프레임과 끊겼던 시간(초)을 돌려주는 함수"""
    # iPhone Continuity Camera는 기기를 움직이거나 잠금을 풀면 잠시 멈추므로 wait초까지 다시 기다림
    ok, frame = capture.read()
    if ok:
        return frame, 0.0
    since = time.monotonic()
    print("카메라 프레임이 끊겼습니다. 다시 들어오기를 기다립니다.", flush=True)
    while time.monotonic() - since <= wait:
        cv2.waitKey(50)
        ok, frame = capture.read()
        if ok:
            gap = time.monotonic() - since
            print(f"카메라 프레임이 다시 들어옵니다 ({gap:.1f}초 끊김).", flush=True)
            return frame, gap
    raise RuntimeError(f"카메라 프레임을 {wait:.0f}초 동안 읽지 못했습니다. 연결/파이프라인을 확인하세요.")


def word_view(test, prompt, last):
    """단어 입력 안내 패널에 보여 줄 제목, 설명, 색을 만드는 함수"""
    if prompt is not None:
        number, word = prompt
        return {"tone": "prompt", "title": f"「{word}」 입력",
                "detail": f"{number + 1} / {len(test.order)} · 틀리면 지우고 고쳐 치기 · N 건너뛰기"}
    if last is None:
        return None
    _, number, word, result = last  # 쉬는 동안에는 직전 단어의 결과를 보여 줌
    title, tone = {"done": ("완료", "ok"), "skip": ("건너뜀", "skip")}[result]
    return {"tone": tone, "title": f"{title} · {word}", "detail": f"{number + 1} / {len(test.order)}"}


def run(args):
    """카메라를 열고 프레임마다 손 검출, 판정, 기록, 화면, 단축키를 처리하는 함수"""
    fonts = load_fonts(args.font)
    typer = PinchTyper()
    ui = {"paused": False, "show_help": False, "show_map": False, "status": "", "notice_since": None, "word": None}
    test = WordTest() if args.words else None
    last_result, new_word = None, False  # 직전 단어 결과, 다음 단어를 시작할 때 입력 칸을 비울지
    if test and args.log is None:  # 단어 시험은 늘 기록함
        args.log = DATA / f"words-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log = None
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        log = open(args.log, "a", encoding="utf-8")
    if test:
        log.write(json.dumps(test.config(), ensure_ascii=False) + "\n")
        print(f"단어 입력: {len(test.order)}단어 → {args.log}", flush=True)
    dark_since, dark_warned = None, False
    capture, exit_reason = None, None
    stamp, fps, previous = -1, 0.0, time.monotonic()
    size = args.resolution if args.resolution != "auto" else AUTO_RESOLUTION.get(sys.platform, DEFAULT_RESOLUTION)

    def notice(message):
        ui["status"], ui["notice_since"] = message, time.monotonic()

    try:
        with create_detector(args.model) as detector:
            capture = cv2.VideoCapture(args.pipeline, cv2.CAP_GSTREAMER) if args.pipeline else cv2.VideoCapture(args.camera)
            if not capture.isOpened():
                raise RuntimeError("카메라를 열 수 없습니다. --camera 또는 --pipeline 값을 확인하세요.")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            first_frame, fullscreen = True, False
            while True:
                frame, gap = read_frame(capture)
                if first_frame:
                    fh, fw = frame.shape[:2]
                    small = detection_frame(frame, args.detect_width).shape
                    requested = "GStreamer 파이프라인" if args.pipeline else f"요청 {size[0]}x{size[1]}"
                    print(f"카메라 해상도 {fw}x{fh} ({requested}), 손 검출 입력 {small[1]}x{small[0]}", flush=True)
                    # 640x480은 960x720으로 키우고, 큰 영상은 화면에 들어오게 줄여 처음 창 크기를 정함
                    scale = min(max(960 / fw, 1.0), 1440 / fw, 900 / fh)
                    cv2.resizeWindow(WINDOW, round(fw * scale), round(fh * scale))
                    first_frame = False
                if gap:
                    typer.reset()  # 끊긴 동안의 손 상태로 입력하지 않음
                now = time.monotonic()
                fps = 0.9 * fps + 0.1 / max(now - previous, 1e-6)
                previous = now
                # Continuity Camera가 멈추면 검은 프레임이 들어옴. 2초 넘게 이어지면 한 번만 경고함
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

                stamp = max(stamp + 1, int(now * 1000))  # MediaPipe VIDEO 모드는 시각(ms)이 엄격히 늘어나야 함
                hands = assign_hands(detect_hands(detector, detection_frame(frame, args.detect_width), stamp,
                                                  args.mirror))
                # 손바닥 방향은 배정된 자리가 아니라 MediaPipe가 본 라벨로 계산해야 관절 배치와 맞음
                signals = {role: hand_signal(hand.label, hand.landmarks, hand.world, args.mirror, w, h)
                           for role, hand in hands.items()}
                enabled = not ui["paused"] and not ui["show_help"]
                prompt = test.prompt(now) if test else None
                if prompt is not None and new_word:
                    # 새 단어에서는 직전 단어의 글자를 지우고 쉬는 동안 시작된 접촉을 버림
                    typer.ime.clear()
                    typer.pending, new_word = None, False
                    for state in typer.hands.values():
                        state.pinch.lost()
                events = typer.update(signals, now) if enabled else []
                if test and enabled:
                    finished = test.update(now, typer.ime.text)
                    events += finished
                    if finished:
                        last_result, new_word = finished[-1], True
                if log:
                    described = None if prompt is None else {"id": prompt[0], "word": prompt[1]}
                    row = log_row(now, hands, signals, events, w, h, described, test is not None)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                if test and test.done:
                    print(test.summary(), flush=True)
                    notice("단어 입력 완료 · 결과는 터미널에")
                    test, ui["word"] = None, None
                elif test:
                    ui["word"] = word_view(test, test.prompt(now), last_result)

                points = {role: [(p.x * w, p.y * h) for p in hand.landmarks] for role, hand in hands.items()}
                cv2.imshow(WINDOW, draw_hud(frame, typer, points, ui, fonts, fps))
                code = cv2.waitKey(1) & 0xFF
                key = chr(code).lower() if 0 <= code < 128 else ""

                if code == 27 or key == "q":
                    exit_reason = "Q/ESC 키"
                    break
                # 일부 GTK·Wayland 환경은 창 표시 여부 조회를 지원하지 않아 예외를 냄. 그때는 닫히지 않은 것으로 봄
                try:
                    closed = cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) == 0
                except cv2.error:
                    closed = False
                if closed:
                    exit_reason = "영상 창 닫힘"
                    break
                if key == "n" and test:
                    skipped = test.skip(now)
                    if skipped:
                        last_result, new_word = skipped[-1], True
                        if log:
                            log.write(json.dumps({"t": round(now, 4), "events": [list(e) for e in skipped]},
                                                 ensure_ascii=False) + "\n")
                elif key == "p":  # 다시 시작할 때 이전 손 상태로 입력하지 않게 판정을 비움
                    ui["paused"] = not ui["paused"]
                    typer.reset()
                elif key == "h":
                    ui["show_help"] = not ui["show_help"]
                    typer.reset()
                elif key == "c":
                    tool = next((c for c in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"])
                                 if shutil.which(c[0])), None)
                    if tool:
                        subprocess.run(tool, input=typer.ime.text.encode("utf-8"), check=False)
                    notice("복사됨" if tool else "복사 도구가 없습니다")
                elif key == "x":
                    typer.ime.clear()
                    typer.pending = None
                    notice("전체 지움")
                elif key == "k":
                    ui["show_map"] = not ui["show_map"]
                elif key == "f":
                    fullscreen = not fullscreen
                    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN,
                                          cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
    finally:
        # 오류로 끝나도 카메라와 기록 파일을 닫고 그때까지의 입력 결과를 출력함
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
    """실행 옵션을 읽고 시연, 채점, 카메라 입력기 가운데 하나를 시작하는 함수"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=0, help="카메라 번호 (macOS: iPhone 연결 시 0)")
    parser.add_argument("--pipeline", help="GStreamer 문자열. 'usb'는 practice의 /dev/video0 설정")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--font", help="한글 폰트 ttf/ttc/otf 경로")
    parser.add_argument("--mirror", action="store_true", help="좌우 반전 (카메라가 사람을 마주 보는 셀카 배치)")
    parser.add_argument("--resolution", type=parse_resolution, default="auto",
                        help="카메라 번호로 열 때 요청할 해상도. auto: macOS 1280x720, 그 밖(Jetson) 640x480. "
                             "--pipeline이면 파이프라인 문자열이 정한다")
    parser.add_argument("--detect-width", type=int, default=640,
                        help="손 검출에 넣는 영상의 최대 가로 크기. 화면은 원래 해상도로 그린다")
    parser.add_argument("--log", type=Path, help="프레임별 손 신호·관절과 이벤트를 jsonl로 남긴다 (--evaluate로 재생)")
    parser.add_argument("--words", action="store_true",
                        help="단어 입력 시험: 짧은 단어를 하나씩 내고 기록한다 (--log가 없으면 data/words-날짜-시각.jsonl)")
    parser.add_argument("--demo", action="store_true", help="카메라 없이 고정 데이터의 '안녕하세요'를 재생")
    parser.add_argument("--evaluate", nargs="+", type=Path, metavar="LOG",
                        help="정답이 있는 기록을 지금 판정으로 다시 재생해 맞음·오입력·빠짐을 센다")
    parser.add_argument("-v", "--verbose", action="store_true", help="(--evaluate) 단어(차례)마다 결과")
    args = parser.parse_args()
    if args.demo:
        case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
        print(case["name"])
        print(replay(case["steps"]).ime.text)
        return
    if args.evaluate:
        for path in args.evaluate:
            config = read_config(path)
            if config is None:
                print(f"## {path.name}: 정답이 없는 기록입니다(첫 줄 설정 없음)")
            elif config.get("kind") == "words":
                print(f"## {path.name} (단어)\n" + report_words(replay_words(path), args.verbose))
            else:
                print(f"## {path.name} (손가락 차례)\n" + report_prompts(replay_prompts(path), args.verbose))
        return
    if args.detect_width < 160:
        parser.error("--detect-width는 160 이상으로 지정하세요.")
    if args.pipeline == "usb":
        args.pipeline = GSTREAMER_USB
    # 카메라·모델·글꼴 오류는 트레이스백 없이 문구만 보여 주기 위해 예외를 잡음
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
