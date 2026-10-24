"""Vision Pro 앱이 내장 Python으로 부르는 판정 세션 모듈"""

# Swift가 프레임마다 ARKit 손 관절을 MediaPipe 21점 순서로 바꿔 JSON으로 넘기고, 이 세션이 카메라 앱과 같은 판정
# (keyboard.hand_signal, keyboard.PinchTyper.update)을 거쳐 화면에 필요한 상태를 JSON으로 돌려줌
# 판정 파일은 빌드할 때 Project01/에서 앱 번들로 복사하므로, 판정 규칙을 고치면 다시 빌드한 앱에 그대로 들어감
#
# step 입력은 {"t": 초, "hands": {"Left" 또는 "Right": {"world": 관절 21개, "image": 관절 21개}}}임
# - world는 관절 위치(m)임. 핀치 거리, 관절 각도, 굽힘은 거리와 각도만 쓰므로 좌표계와 무관함
# - image는 머리(기기) 좌표를 MediaPipe 영상 축에 맞춘 값임(x 오른쪽, y 아래, z 멀어지는 쪽, m)
#   손바닥 방향, 기울기, 자세가 이 값을 씀. 손 이름은 ARKit이 알려 준 실제 손이고 좌우 반전은 없음
# step 출력은 카메라 앱 화면이 그리는 것과 같은 정보이고 필드는 Session.state에 있음
# command 입력은 {"cmd": "clear", "log_start", "log_stop", "demo", "info", "mode", "skip", "next" 가운데 하나}임
# - mode는 {"cmd": "mode", "mode": "free" 또는 "words"}로 자유 입력과 단어 입력을 바꿈
# - 단어 입력에서 단어를 맞히면 저절로 넘어가지 않고 next를 기다림. 앱은 창을 초록으로 하고 "다음 단어" 버튼을 띄움
# - info는 내장 Python 버전과 고정 데이터 재생 결과를 알려 줌. 앱이 시작할 때 자기 점검으로 부름
# 기록(log_start에서 log_stop까지)은 record.log_row와 같은 필드로 남겨 main.py --evaluate가 그대로 읽음
# 앱은 손 입력을 시작할 때 log_start, 끝낼 때 log_stop을 부름(늘 기록, 2026-10-23 사용자 지시)

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

# 기기에서는 판정 파일이 번들 app/에 이 파일과 함께 복사되고, Mac에서는 한 단계 위(Project01/)에 있음
HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "keyboard.py").exists() else HERE.parent
sys.path.insert(0, str(ROOT))

from keyboard import (DRAG_MIN, DRAG_STROKES, HANDS, KEYS, VOWELS, PinchTyper, cycle_queue, group_of,  # noqa: E402
                      hand_signal, key_label, replay)
from record import WordTest  # noqa: E402

FIXTURES = ROOT / "fixtures" / "pinch-typer-keys.json"
# image 좌표(m)를 카메라 앱의 픽셀처럼 다루려고 1mm를 1픽셀로 봄
# 자세 판정은 "마디가 2픽셀보다 짧으면 건너뜀" 같은 픽셀 기준값을 써서, m 그대로 넘기면 모든 마디가 건너뛰어져
# 주먹이 판정되지 않았음(2026-10-23 기기 기록). 손바닥 방향과 기울기는 비율과 각도만 써서 배율과 무관함
IMAGE_SCALE = 1000.0
LOG_KEEP = 20  # 남겨 둘 기록 파일 수. 기록은 1분에 약 8MB임


def points(rows):
    """[x, y, z] 목록을 x, y, z 속성을 가진 관절 객체 목록으로 바꾸는 함수"""
    return [SimpleNamespace(x=x, y=y, z=z) for x, y, z in rows]


def signal_of(label, joints):
    """Swift가 넘긴 손 하나의 관절 두 벌로 HandSignal을 만드는 함수"""
    return hand_signal(label, points(joints["image"]), points(joints["world"]), False, IMAGE_SCALE, IMAGE_SCALE)


class Session:
    """앱이 실행되는 동안 하나만 두는 판정 세션 클래스"""

    def __init__(self, log_dir=None):
        self.typer = PinchTyper()
        self.log_dir = Path(log_dir) if log_dir else None  # 앱 Documents 폴더. 없으면 기록하지 않음
        self.log, self.log_path = None, None
        self.signals = {}  # 이번 프레임의 손 신호
        self.now = 0.0  # 마지막 프레임 시각. 명령을 처리할 때 씀
        self.collector = None  # 단어 입력이면 WordTest, 자유 입력이면 None
        self.clear_next = False  # 다음 단어를 시작할 때 입력 칸을 비울지
        self.last_result = None  # 직전 단어의 [단어, "done" 또는 "skip"]
        self.shown_at = {}  # 단어 번호마다 처음 안내한 시각
        self.durations = []  # 맞힌 단어마다 걸린 시간(초)
        self.solved = None  # 맞혀서 "다음 단어"를 기다리는 단어

    def step(self, payload):
        """프레임 하나를 판정하고 화면 상태를 JSON으로 돌려주는 메서드"""
        data = json.loads(payload)
        now, hands = data["t"], data.get("hands") or {}
        self.now = now
        self.signals = {label: signal_of(label, hands[label]) for label in HANDS if hands.get(label)}
        waiting = self.collector is not None and self.solved is None  # 맞혔으면 다음 단어를 기다리는 중임
        prompt = self.collector.prompt(now) if waiting else None
        if prompt is not None and self.clear_next:
            # 카메라 앱과 같게 새 단어에서는 직전 단어의 입력을 지우고 쉬는 동안 시작된 접촉을 버림
            self._clear_input()
            self.clear_next = False
        if prompt is not None:
            self.shown_at.setdefault(prompt[0], now)
        events = self.typer.update({label: self.signals.get(label) for label in HANDS}, now)
        if waiting:
            finished = self.collector.update(now, self.typer.ime.text)
            events += finished
            if finished:
                self._finished(finished[-1], now)
        if self.log is not None:
            self._write(now, hands, events, None if prompt is None else {"id": prompt[0], "word": prompt[1]})
        return json.dumps(self.state(events), ensure_ascii=False)

    def _clear_input(self):
        """입력 칸과 후보, 진행 중인 접촉을 모두 비우는 메서드"""
        self.typer.ime.clear()
        self.typer.pending = None
        for hand in self.typer.hands.values():
            hand.pinch.lost()
            hand.held = []

    def _finished(self, event, now):
        """단어 하나가 끝났을 때 결과를 반영하는 메서드"""
        _, number, word, result = event
        if result == "done" and number in self.shown_at:
            self.durations.append(now - self.shown_at[number])
        self.last_result = [word, result]
        if result == "done":
            self.solved = word
        else:
            self.clear_next = True  # 건너뛰었으면 쉬는 시간 뒤 다음 단어에서 입력 칸을 비움

    def next_word(self):
        """맞힌 단어에서 쉬는 시간 없이 다음 단어로 넘어가는 메서드"""
        self.solved, self.clear_next = None, False
        self.collector.resting_until = None
        self._clear_input()

    def set_mode(self, mode):
        """자유 입력과 단어 입력을 바꾸고 입력 칸과 단어 진행을 처음부터 시작하는 메서드"""
        if mode not in ("free", "words"):
            raise ValueError(f"알 수 없는 모드: {mode}")
        self.collector = WordTest() if mode == "words" else None
        self.clear_next, self.last_result, self.shown_at, self.durations = False, None, {}, []
        self.solved = None
        self._clear_input()
        if self.log is not None:  # 단어 입력 기록은 첫 줄이 단어 순서라 모드마다 기록 파일을 나눔
            self.start_log()

    def word_state(self):
        """단어 입력의 진행 상태를 만드는 메서드"""
        if self.collector is None:
            return None
        prompt = None if self.solved else self.collector.prompt(self.now)
        target = self.solved or (None if prompt is None else prompt[1])  # 쉬는 중이면 None
        text = self.typer.ime.text
        matched = 0  # 입력 칸 앞에서부터 단어와 같은 글자 수
        if target is not None:
            while matched < min(len(text), len(target)) and text[matched] == target[matched]:
                matched += 1
        results = self.collector.results.values()
        return {"target": target, "solved": self.solved is not None, "matched": matched, "last": self.last_result,
                "done": sum(result == "done" for result in results),
                "skipped": sum(result == "skip" for result in results),
                "seconds": round(sum(self.durations) / len(self.durations), 1) if self.durations else None}

    def state(self, events=(), notice=None):
        """화면에 필요한 상태를 만드는 메서드"""
        typer = self.typer
        hands = {}
        for label in HANDS:  # 손이 보이지 않아도 판정기 상태는 있음
            hand = typer.hands[label]
            signal = self.signals.get(label)
            approach = hand.pinch.progress(signal.ratios) if signal is not None else [0.0] * 4
            release = hand.pinch.release_progress(signal.ratios) if signal is not None else 0.0
            face = typer.face(label) or "palm"  # 회전 중이면 손바닥 자판을 보여 줌
            keys = KEYS[(label, face)]
            drag = typer.drag_view(label)
            hands[label] = {
                "face": hand.orientation.state,
                "occluded": typer.occluded(label),
                "armed": hand.pinch.armed(),
                "fist": hand.fist.progress,
                "closed": hand.pinch.closed,  # 주먹 자세라 핀치를 보지 않는 중이면 앱이 손끝 버튼을 접음
                "dragging": None if drag is None else drag["finger"],
                # 앱은 판정이 쓰는 이번 접촉의 프레임 수만큼 같은 프레임의 관절로 끌기 궤적을 그림
                "drag_frames": 0 if drag is None else len(drag["points"]),
                "drag_radius": 0.0 if drag is None else round(DRAG_MIN * drag["size"] / IMAGE_SCALE, 4),  # 픽셀을 m로 바꿈
                "drag_preview": None if drag is None else VOWELS[DRAG_STROKES[drag["drag"]]],
                "drag_passed": drag is not None and drag["drag"] is not None,
                "drag_pointer": None if drag is None else drag["pointer"],
                "map": [None if key is None else key_label(key) for key in keys],
                "fingers": [{"queue": cycle_queue(typer, label, finger), "active": hand.pinch.active == finger,
                             "approach": round(approach[finger], 3),
                             "release": round(release, 3) if hand.pinch.active == finger else 0.0}
                            for finger in range(4)],
            }
        pending = typer.pending
        group = None if pending is None else ["줄" if char == "\n" else char for char in group_of(pending.key)]
        return {"events": [list(event) for event in events], "text": typer.ime.text,
                "pending": pending is not None,
                "pending_punct": pending is not None and pending.key == "PUNCT",  # 문장부호 후보에는 밑줄을 긋지 않음
                "group": group, "group_index": None if pending is None else (pending.count - 1) % len(group),
                "hands": hands,
                "echo": None if typer.echo is None else dict(zip(("id", "hand", "text"), typer.echo)),
                "mode": "free" if self.collector is None else "words", "word": self.word_state(),
                "log": None if self.log_path is None else str(self.log_path), "notice": notice}

    def command(self, payload):
        """앱의 버튼과 모드 명령을 처리하고 화면 상태를 JSON으로 돌려주는 메서드"""
        cmd = json.loads(payload).get("cmd")
        notice = None
        if cmd == "clear":
            self.typer.ime.clear()
            self.typer.pending = None
        elif cmd == "mode":
            self.set_mode(json.loads(payload).get("mode"))
        elif cmd == "next" or (cmd == "skip" and self.solved is not None):  # 맞힌 뒤의 skip은 next와 같음
            if self.solved is not None:
                self.next_word()
        elif cmd == "skip":
            if self.collector is not None:
                skipped = self.collector.skip(self.now)
                if skipped:
                    self._finished(skipped[-1], self.now)
        elif cmd == "log_start":
            notice = self.start_log()
        elif cmd == "log_stop":
            notice = self.stop_log()
        elif cmd == "demo":
            case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
            self.typer.ime = replay(case["steps"]).ime
            self.typer.pending = None
            notice = case["name"]
        elif cmd == "info":
            case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
            notice = f"Python {sys.version.split()[0]} 준비됨 · 고정 데이터 재생 '{replay(case['steps']).ime.text}'"
        else:
            raise ValueError(f"알 수 없는 명령: {cmd}")
        return json.dumps(self.state(notice=notice), ensure_ascii=False)

    def start_log(self):
        """새 기록 파일을 열고 화면 안내 문구를 돌려주는 메서드"""
        if self.log_dir is None:
            return "기록 폴더가 없습니다"
        self.stop_log()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        old = sorted(self.log_dir.glob("pinch-log-visionos-*.jsonl"))  # 파일 이름에 시각이 있어 오래된 순으로 정렬됨
        for path in old[:max(0, len(old) - (LOG_KEEP - 1))]:  # 새 파일까지 LOG_KEEP개만 남김
            path.unlink()
        stamp = time.strftime('%Y%m%d-%H%M%S')
        self.log_path = self.log_dir / f"pinch-log-visionos-{stamp}{'-words' if self.collector else ''}.jsonl"
        self.log = open(self.log_path, "a", encoding="utf-8")
        if self.collector is not None:
            self.log.write(json.dumps(self.collector.config(), ensure_ascii=False) + "\n")
        return "기록 시작: " + self.log_path.name

    def stop_log(self):
        """기록 파일을 닫고 화면 안내 문구를 돌려주는 메서드"""
        if self.log is None:
            return None
        self.log.close()
        name = self.log_path.name
        self.log, self.log_path = None, None
        return "기록 끝: " + name

    def _write(self, now, hands, events, prompt=None):
        """프레임 하나를 record.log_row와 같은 필드로 기록하는 메서드"""
        def rounded(rows, digits):
            return [[round(v, digits) for v in row] for row in rows]
        row = {"t": round(now, 4), "size": [1, 1], "source": "visionos",
               "hands": {label: {"seen_as": label, "ratios": [round(v, 4) for v in sig.ratios],
                                 "facing": round(sig.facing, 3), "roll": round(sig.roll, 1), "pose": sig.pose,
                                 "curled": list(sig.curled), "image": rounded(hands[label]["image"], 5),
                                 "world": rounded(hands[label]["world"], 5)}
                         for label, sig in self.signals.items()},
               "events": [list(event) for event in events]}
        if self.collector is not None:
            row["prompt"] = prompt
        self.log.write(json.dumps(row, ensure_ascii=False) + "\n")
