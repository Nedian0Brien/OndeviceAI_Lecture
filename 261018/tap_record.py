"""엄지 누르기 검출기 평가용 데이터를 안내에 따라 기록한다.

예: python 261018/tap_record.py --hand right --placement front
화면 안내(준비 → 수행)에 맞춰 강조된 마디를 한 번 누르거나, 가까이 갔다가 닿지 않고 돌아오거나,
닿지 않게 띄우거나, 주먹을 쥔다.
Space 일시정지 · R 수행 중이면 현재 시행 취소, 아니면 직전 시행 무효 · Q/Esc 저장 후 종료
oblique: iPhone을 엄지 쪽에 두고 손바닥 정면에서 약 45° 돌린 위치. 거리는 front와 같게.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import random
import signal
import time

import numpy as np

from project01 import (GSTREAMER, ROOT, HAND_CONNECTIONS, create_detector, detect_hands, find_font, open_capture,
                       read_frame)
from tap_detect import SEGMENT_NAMES, SEGMENTS


PREPARE = 0.9
DURATIONS = {"tap": 1.5, "nearmiss": 1.5, "hover": 2.0, "fist": 2.0, "idle": 3.0}
MIN_PRESENT = 0.7  # 수행 구간의 70% 이상에서 손이 보여야 유효한 시행이다.
MAX_ATTEMPTS = 3  # 손이 계속 안 보이는 시행은 세 번까지만 다시 한다.
FINGER_NAMES = {"I": "검지", "M": "중지", "R": "약지", "P": "소지"}
ROW_NAMES = {"1": "끝마디", "2": "중간마디", "3": "뿌리마디"}


def segment_label(name):
    return FINGER_NAMES[name[0]] + " " + ROW_NAMES[name[1]]


def instruction(trial):
    if trial["kind"] == "tap":
        return segment_label(trial["target"]) + "를 한 번 누르세요"
    if trial["kind"] == "nearmiss":
        return segment_label(trial["target"]) + " 가까이 갔다가 닿지 말고 돌아오세요"
    if trial["kind"] == "hover":
        return segment_label(trial["target"]) + " 위에 엄지를 띄우고 멈추세요 (닿지 않게)"
    if trial["kind"] == "fist":
        return "주먹을 한 번 쥐었다 펴세요"
    return "누르지 말고 손을 자유롭게 움직이세요"


def make_queue(reps, nearmiss_reps, hover_reps, fist_reps, idle_reps, seed):
    queue = ([{"kind": "tap", "target": name} for name in SEGMENT_NAMES for _ in range(reps)]
             + [{"kind": "nearmiss", "target": name} for name in SEGMENT_NAMES for _ in range(nearmiss_reps)]
             + [{"kind": "hover", "target": name} for name in SEGMENT_NAMES for _ in range(hover_reps)]
             + [{"kind": "fist", "target": None} for _ in range(fist_reps)]
             + [{"kind": "idle", "target": None} for _ in range(idle_reps)])
    random.Random(seed).shuffle(queue)
    return queue


class Recorder:
    """프레임별 landmark를 고정 크기 배열로 모은다. 손이 없는 칸은 NaN과 빈 라벨."""

    def __init__(self):
        self.times, self.image, self.world, self.labels, self.scores = [], [], [], [], []

    def add(self, t, detections):
        image = np.full((2, 21, 3), np.nan, np.float32)
        world = np.full((2, 21, 3), np.nan, np.float32)
        labels, scores = ["", ""], np.full(2, np.nan, np.float32)
        for slot, (label, landmarks, world_landmarks, score) in enumerate(detections[:2]):
            image[slot] = [(p.x, p.y, p.z) for p in landmarks]
            world[slot] = [(p.x, p.y, p.z) for p in world_landmarks]
            labels[slot], scores[slot] = label, score
        self.times.append(t)
        self.image.append(image)
        self.world.append(world)
        self.labels.append(labels)
        self.scores.append(scores)

    def save(self, path):
        np.savez_compressed(path, t=np.array(self.times, np.float64),
                            image=np.array(self.image).reshape(-1, 2, 21, 3),
                            world=np.array(self.world).reshape(-1, 2, 21, 3),
                            labels=np.array(self.labels, dtype="<U5").reshape(-1, 2),
                            scores=np.array(self.scores).reshape(-1, 2))


def own_slot(labels, hand):
    """화면에 손이 하나면 좌우 라벨과 상관없이 그 손을 쓴다 (--hand로 이미 지정했다).

    비스듬한 카메라에서는 MediaPipe 좌우 라벨이 뒤집힐 수 있다. 손이 둘이면 라벨이 맞는 하나만 쓴다.
    """
    present = [slot for slot, label in enumerate(labels) if label]
    if len(present) == 1:
        return present[0]
    matching = [slot for slot in present if labels[slot] == hand]
    return matching[0] if len(matching) == 1 else None


def present_ratio(recorder, hand, start, end):
    inside = [own_slot(labels, hand) is not None
              for t, labels in zip(recorder.times, recorder.labels) if start <= t <= end]
    return sum(inside) / max(1, len(inside))


def draw(frame, hand_points, trial, phase, remaining, header, font):
    from PIL import Image, ImageDraw
    import cv2

    h, w = frame.shape[:2]
    if hand_points is not None:
        pixels = [(int(x * w), int(y * h)) for x, y, _ in hand_points]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, pixels[a], pixels[b], (200, 200, 200), 1, cv2.LINE_AA)
        for name, (a, b) in SEGMENTS.items():
            center = ((pixels[a][0] + pixels[b][0]) // 2, (pixels[a][1] + pixels[b][1]) // 2)
            target = trial is not None and trial["target"] == name
            color = (80, 220, 80) if target and phase == "go" else (0, 200, 255) if target else (255, 255, 255)
            cv2.circle(frame, center, 11 if target else 4, color, 2 if target else -1, cv2.LINE_AA)
    color = (80, 220, 80) if phase == "go" else (190, 190, 190)
    cv2.rectangle(frame, (0, 0), (w, 54), (25, 25, 25), -1)
    if remaining is not None:
        cv2.rectangle(frame, (0, 50), (int(w * remaining), 54), color, -1)
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    canvas = ImageDraw.Draw(image)
    canvas.text((10, 4), header, font=font, fill=(170, 170, 170))
    message = {"prepare": "엄지를 떼고 준비 · ", "go": "지금 · ", "paused": "일시정지 · Space로 다시 시작 · "}.get(phase, "")
    canvas.text((10, 24), message + (instruction(trial) if trial else "끝났습니다. Q로 저장"), font=font,
                fill=tuple(reversed(color)))
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


class interruptible:
    """Ctrl+C를 루프 종료 요청으로 바꿔, 중단해도 finally에서 기록을 저장하게 한다."""

    def __enter__(self):
        self.requested = False
        self.previous = signal.signal(signal.SIGINT, self.request)
        return self

    def request(self, *_):
        self.requested = True

    def __exit__(self, *_):
        signal.signal(signal.SIGINT, self.previous)
        return False


def run(args):
    import cv2
    import mediapipe as mp
    from PIL import ImageFont

    font = ImageFont.truetype(str(find_font(args.font)), 18)
    hand = args.hand.capitalize()
    seed = args.seed if args.seed is not None else int(time.time())
    queue = make_queue(args.reps, args.nearmiss_reps, args.hover_reps, args.fist_reps, args.idle_reps, seed)
    total = len(queue)
    session = args.out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{args.hand}-{args.placement}"
    session.mkdir(parents=True)
    recorder, trials = Recorder(), []
    writer = capture = None
    timestamp, t0 = -1, None
    phase, phase_start, current = "prepare", None, (queue.pop(0) if queue else None)
    exit_reason = "완료"
    try:
        with create_detector(args.model) as detector, interruptible() as stop:
            capture = open_capture(cv2, args)
            cv2.namedWindow("Tap Recorder", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("Tap Recorder", 960, 720)
            while True:
                frame, gap = read_frame(cv2, capture)
                if gap and current is not None and phase == "go":
                    # 끊긴 동안의 시행은 기록하지 않고 준비부터 다시 한다.
                    phase, phase_start = "prepare", None
                now = time.monotonic()
                t0 = now if t0 is None else t0
                t = now - t0
                if not args.no_mirror:
                    frame = cv2.flip(frame, 1)
                if args.flip_vertical:
                    frame = cv2.flip(frame, 0)
                if writer is None:
                    writer = cv2.VideoWriter(str(session / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 30,
                                             (frame.shape[1], frame.shape[0]))
                    size = frame.shape[1], frame.shape[0]
                writer.write(frame)
                timestamp = max(timestamp + 1, int(now * 1000))
                detections = detect_hands(cv2, mp, detector, frame, timestamp, not args.no_mirror)
                recorder.add(t, detections)

                phase_start = t if phase_start is None else phase_start
                remaining = None
                if current is not None and phase == "prepare":
                    remaining = 1 - (t - phase_start) / PREPARE
                    if t - phase_start >= PREPARE:
                        phase, phase_start = "go", t
                if current is not None and phase == "go":
                    duration = DURATIONS[current["kind"]]
                    remaining = 1 - (t - phase_start) / duration
                    if t - phase_start >= duration:
                        ratio = present_ratio(recorder, hand, phase_start, t)
                        attempt = current.get("attempt", 1)
                        reason = ("" if ratio >= MIN_PRESENT else
                                  "hand-missing" if attempt < MAX_ATTEMPTS else "gave-up")
                        trials.append({"id": len(trials), "kind": current["kind"], "target": current["target"],
                                       "attempt": attempt, "start": round(phase_start, 4), "end": round(t, 4),
                                       "present": round(ratio, 3), "valid": not reason, "reason": reason})
                        if reason == "hand-missing":
                            queue.append({**current, "attempt": attempt + 1})
                        current = queue.pop(0) if queue else None
                        phase, phase_start = "prepare", t
                done = sum(trial["valid"] for trial in trials)
                header = (f"{args.hand} · {args.placement} · 유효 {done}/{total} · "
                          f"무효 {len(trials) - done} · R 다시 · Space 정지 · Q 저장")
                slot = own_slot([label for label, _, _, _ in detections], hand)
                points = None if slot is None else [(p.x, p.y, p.z) for p in detections[slot][1]]
                view = draw(frame.copy(), points, current, phase if current else "done",
                            None if remaining is None else max(0.0, remaining), header, font)
                cv2.imshow("Tap Recorder", view)
                key_code = cv2.waitKey(1) & 0xFF
                if stop.requested:
                    exit_reason = "Ctrl+C"
                    break
                if key_code in (27, ord("q"), ord("Q")):
                    exit_reason = "Q/ESC 키" if current is not None else "완료"
                    break
                if key_code == ord(" "):
                    # 다시 시작하면 현재 시행을 준비 단계부터 다시 한다.
                    phase, phase_start = ("prepare" if phase == "paused" else "paused"), t
                elif key_code in (ord("r"), ord("R")) and current is not None and phase == "go":
                    # 수행 중 잘못 누른 것을 알아챘을 때: 현재 시행을 기록하지 않고 준비부터 다시 한다.
                    phase, phase_start = "prepare", t
                elif key_code in (ord("r"), ord("R")) and trials and trials[-1]["valid"]:
                    trials[-1]["valid"], trials[-1]["reason"] = False, "user"
                    queue.append({"kind": trials[-1]["kind"], "target": trials[-1]["target"]})
                    if current is None:
                        current, phase, phase_start = queue.pop(0), "prepare", t
    except Exception as exc:
        exit_reason = "오류: " + str(exc)
        raise
    finally:
        if capture is not None:
            capture.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()
        if recorder.times:
            save_session(session, recorder, trials, total, args, hand, seed, size, exit_reason)
        else:
            # 카메라를 열지 못하는 등 프레임이 하나도 없으면 빈 세션 폴더를 남기지 않는다.
            for leftover in session.iterdir():
                leftover.unlink()
            session.rmdir()
            print("기록된 프레임이 없어 세션을 저장하지 않았습니다.")


def save_session(session, recorder, trials, total, args, hand, seed, size, exit_reason):
    import cv2
    import mediapipe as mp

    recorder.save(session / "frames.npz")
    duration = recorder.times[-1] if recorder.times else 0.0
    (session / "trials.json").write_text(json.dumps({"trials": trials}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    meta = {
        "hand": hand, "placement": args.placement, "seed": seed,
        "camera": None if args.pipeline else args.camera, "pipeline": args.pipeline,
        "width": size[0], "height": size[1],
        "frames": len(recorder.times), "duration": round(duration, 3),
        "fps": round((len(recorder.times) - 1) / duration, 2) if duration > 0 else None,
        "mirrored": not args.no_mirror, "flip_vertical": args.flip_vertical,
        "prepare": PREPARE, "durations": DURATIONS, "min_present": MIN_PRESENT, "max_attempts": MAX_ATTEMPTS,
        "mediapipe": mp.__version__, "opencv": cv2.__version__,
        "created": datetime.now().isoformat(timespec="seconds"), "exit": exit_reason,
        "video_note": "video.mp4는 30fps로 기록하지만 실제 프레임 시각은 frames.npz의 t가 기준이다.",
    }
    (session / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    valid = sum(trial["valid"] for trial in trials)
    print(f"저장: {session}\n유효 시행 {valid}/{total}, 프레임 {len(recorder.times)}, "
          f"실측 {meta['fps']} fps, 종료 이유: {exit_reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hand", choices=("left", "right"), required=True)
    parser.add_argument("--placement", choices=("front", "oblique"), required=True,
                        help="front: 손바닥 정면, oblique: 엄지 쪽에서 약 45° 돌린 위치(거리는 front와 같게)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--pipeline", help="GStreamer 문자열. 'usb'는 practice의 /dev/video0 설정")
    parser.add_argument("--model", type=Path, default=ROOT / "src/models/MediaPipe/hand_landmarker.task")
    parser.add_argument("--font", help="한글 폰트 ttf/ttc/otf 경로")
    parser.add_argument("--no-mirror", action="store_true")
    parser.add_argument("--flip-vertical", action="store_true")
    parser.add_argument("--reps", type=int, default=5, help="마디마다 누르기 횟수")
    parser.add_argument("--nearmiss-reps", type=int, default=2, help="마디마다 가까이 갔다 돌아오기 횟수")
    parser.add_argument("--hover-reps", type=int, default=1, help="마디마다 띄우고 멈추기 횟수")
    parser.add_argument("--fist-reps", type=int, default=5)
    parser.add_argument("--idle-reps", type=int, default=3)
    parser.add_argument("--seed", type=int, help="시행 순서 난수 시드 (기본: 현재 시각)")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().with_name("data") / "tap")
    args = parser.parse_args()
    if args.pipeline == "usb":
        args.pipeline = GSTREAMER
    try:
        run(args)
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
