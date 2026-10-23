"""Vision Pro 앱(261018/visionos)이 내장 Python으로 부르는 판정 세션 (표준 라이브러리만 사용).

Swift가 프레임마다 ARKit 손 관절을 MediaPipe 21점 순서로 바꿔 JSON으로 넘기면, Mac 앱과 같은 코드
(pinch_typer.hand_signal → PinchTyper.update → 천지인 조합)로 판정하고 화면에 필요한 상태를 JSON으로 돌려준다.
판정 규칙을 Mac에서 고치면 다시 빌드한 앱에 그대로 들어간다(Swift로 다시 쓰지 않는다, 2026-10-23 사용자 결정).

step(JSON) 입력
  {"t": 초, "hands": {"Left"|"Right": {"world": [[x, y, z] × 21], "image": [[x, y, z] × 21]}}}
  - world: 관절 위치(m). 꼬집기 비율·관절 각도·굽힘은 거리·각도만 쓰므로 좌표계와 무관하다.
  - image: 머리(기기) 좌표를 MediaPipe 이미지 축에 맞춘 값(x 오른쪽, y 아래, z 멀어지는 쪽, m).
    손바닥 방향(2D 외적)·기울기(z)·자세(pose_from_image)가 쓴다. 손 이름은 ARKit chirality(실제 손), 반전 없음.
step 출력(Mac 앱 HUD가 그리는 것과 같은 정보, pinch_cheonjiin.draw_hud)
  {"events": [[종류, 값]], "text": 입력 글자, "pending": 마지막 글자가 확정 전 후보인가,
   "pending_punct": 후보가 문장부호인가(밑줄 없음),
   "group": 순환 중인 그룹 글자 목록 또는 null, "group_index": 그룹에서 지금 글자 위치,
   "hold_mode": 누르고 유지 방식인가,
   "hands": {손: {"face", "occluded", "armed", "fist", "closed": 주먹 자세라 꼬집기를 보지 않는가(앱은 손끝 버튼을 접는다),
                  "dragging": 모음 버튼을 꼬집는 중인 손가락 번호 또는 null(그동안 앱이 끄는 궤적을 그린다),
                  "drag_frames": 판정에 쓰는 이번 접촉의 프레임 수(이 손이 보인 마지막 프레임들. 앱은 같은 프레임의
                                 관절로 궤적을 그린다), "drag_radius": 끌기 기준 거리(m, 손 크기 × DRAG_MIN),
                  "drag_preview": 지금 떼면 들어갈 획(ㆍ / ㅡ / ㅣ), "drag_passed": 끌기 기준을 넘었는가,
                  "drag_pointer": 끈 방향 각도(12시 0°에서 시계 방향, 화면 기준) 또는 null,
                  "map": [자판 지도 키 이름 또는 null × 4],
                  "fingers": [{"queue", "active", "approach", "release", "hold"} × 4]}},
   "echo": {"id", "hand", "text"} 마지막으로 입력된 것(앱이 손바닥 가운데에 잠깐 띄운다, id가 바뀌면 새 입력) 또는 null,
   "mode": "free"(자유 타이핑) | "words"(단어 타이핑),
   "word": 단어 모드일 때 {"target": 지금 칠 단어(맞혔으면 그 단어) 또는 null(건너뛰고 쉬는 중),
           "solved": 맞혀서 "다음 단어"(next)를 기다리는 중인가, "matched": 입력 칸 앞에서부터 맞은 글자 수,
           "last": [직전 단어, "done"|"skip"] 또는 null, "done": 다 친 단어 수, "skipped": 건너뛴 수,
           "seconds": 다 친 단어의 평균 시간(초) 또는 null}, 자유 모드면 null,
   "log": 기록 파일 경로 또는 null}
command(JSON): {"cmd": "clear" | "log_start" | "log_stop" | "demo" | "info" | "settings" | "mode" | "skip" | "next"} — 결과는
  step 출력과 같은 모양(+ "notice", "settings").
  mode: {"cmd": "mode", "mode": "free" | "words"}. 단어 모드는 SHORT_WORDS를 무작위 순서로 하나씩 낸다(WordCollector,
  Mac --collect-set words와 같은 판정: 입력 칸이 그 단어가 되면 맞힌 것). 맞히면 저절로 넘어가지 않고 "next"를 기다린다
  (앱은 창을 초록으로 하고 "다음 단어" 버튼을 띄운다, 2026-10-23 사용자). 모드를 바꾸면
  입력 칸을 비우고, 기록 중이면 새 기록 파일을 연다(단어 모드 기록은 첫 줄에 단어 순서, 행마다 지금 단어 → pinch_eval.py).
  next: 맞힌 뒤 입력 칸을 비우고 다음 단어. skip: 지금 단어를 건너뛴다(맞힌 뒤면 next와 같다).
  clear: 단어 모드에서는 지금 단어를 처음부터 다시 친다.
  settings: {"cmd": "settings", "values": {"hold", "settle", "index_sweep", "occlusion": 참/거짓}} 판정 규칙 켜기·끄기
  (Vision Pro 설정 창). hold = 꼬집고 HOLD_SECONDS초 머물러야 입력, settle = 손을 뒤집은 직후 SETTLE초 무시,
  index_sweep = 오른손 검지 지나감 규칙, occlusion = 엄지 가림(기울기) 규칙. 바꾸면 진행 중인 접촉은 버린다.
  info: 내장 Python 버전과 고정 데이터 재생 결과(입력 칸은 바꾸지 않는다). 앱이 시작할 때 자기 점검으로 부른다.

기록(log_start ~ log_stop)은 pinch_cheonjiin.log_row와 같은 필드로 남겨 pinch_eval.py가 그대로 읽는다.
앱은 손 입력을 시작할 때 log_start, 끝낼 때 log_stop을 부른다(늘 기록, 사용자 2026-10-23). 파일은 1분에 약 8MB라
새 기록을 시작할 때 최근 LOG_KEEP개만 남기고 지운다.
"""

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from common.pose import pose_from_image
from pinch_collect import SHORT_WORDS, WordCollector
from pinch_typer import (DRAG_MIN, DRAG_STROKES, KEYS, OCCLUDED_ROLL, SETTLE, VOWEL_LABELS, PinchTyper, cycle_queue,
                         group_of, hand_signal, key_label, replay)

HANDS = ("Left", "Right")
FIXTURES = Path(__file__).resolve().with_name("fixtures") / "pinch-typer-keys.json"
HOLD_SECONDS = 0.3  # 설정 창에서 누르고 유지를 켰을 때 머무는 시간(Mac의 --hold 0.3과 같다)
# image 좌표(m)를 Mac 앱의 픽셀처럼 다루는 배율: 1mm = 1px. 자세 판정(pose_from_image)이 "마디가 2px보다 짧으면 건너뜀"처럼
# 픽셀 기준값을 쓴다. m 그대로 넘기면 모든 마디(약 0.03)가 건너뛰어져 자세가 늘 closing이 되고 주먹(지움)이 판정되지
# 않았다(2026-10-23 기기 기록). 손바닥 방향·기울기는 비율·각도만 써서 배율과 무관하다.
IMAGE_SCALE = 1000.0
LOG_KEEP = 20  # 남겨 둘 기록 파일 수(손 입력 세션 하나가 파일 하나)
# 주먹 지움을 다시 받는 조건: 주먹이 아닌 상태가 FIST_REARM초 이어짐(손끝 버튼이 다시 펼쳐지는 기준과 같다,
# common.gestures.FistHold rearm). Vision Pro 기록에서 주먹을 쥔 채 끊기는 길이는 0.03~0.05초였다.
FIST_REARM = 0.15


def points(rows):
    """[[x, y, z]] → x, y, z 속성을 가진 점 목록(hand_signal이 받는 모양)."""
    return [SimpleNamespace(x=x, y=y, z=z) for x, y, z in rows]


def signal_of(label, joints):
    """Swift가 넘긴 손 하나의 관절 두 벌 → HandSignal. Mac 앱(build_signals)처럼 자세는 이미지 좌표 방식이다."""
    image, world = points(joints["image"]), points(joints["world"])
    return hand_signal(label, image, world, mirrored=False, width=IMAGE_SCALE, height=IMAGE_SCALE,
                       pose=pose_from_image(image, IMAGE_SCALE, IMAGE_SCALE))


class Session:
    def __init__(self, log_dir=None):
        self.typer = PinchTyper()
        for hand in self.typer.hands.values():
            hand.fist.rearm = FIST_REARM
        self.log_dir = Path(log_dir) if log_dir else None
        self.log, self.log_path = None, None
        self.signals = {}
        self.now = 0.0
        self.collector = None  # 단어 모드의 WordCollector(자유 모드면 None)
        self.clear_next = False  # 다음 단어를 시작할 때 입력 칸을 비울지
        self.last_result = None  # 직전 단어 결과 [단어, "done"|"skip"]
        self.shown_at = {}  # 단어 번호 → 처음 안내한 시각
        self.durations = []  # 다 친 단어마다 걸린 시간(초)
        self.solved = None  # 맞혀서 "다음 단어"를 기다리는 단어

    # -- 프레임 ------------------------------------------------------------
    def step(self, payload):
        data = json.loads(payload)
        now, hands = data["t"], data.get("hands") or {}
        self.now = now
        self.signals = {label: signal_of(label, hands[label]) for label in HANDS if hands.get(label)}
        waiting = self.collector is not None and self.solved is None  # 단어를 치는 중(맞혔으면 다음 단어를 기다림)
        prompt = self.collector.prompt(now) if waiting else None
        if prompt is not None and self.clear_next:
            # 새 단어: 직전 단어의 입력을 지우고 쉬는 동안 시작된 접촉을 버린다(Mac run과 같다).
            self._clear_input()
            self.clear_next = False
        if prompt is not None:
            self.shown_at.setdefault(prompt[0], now)
        events = self.typer.update({label: self.signals.get(label) for label in HANDS}, now)
        if waiting:
            finished = self.collector.update(self.typer.pinches, now, self.typer.ime.text)
            events += finished
            if finished:
                self._finished(finished[-1], now)
        if self.log is not None:
            self._write(now, hands, events, None if prompt is None else self.collector.describe(prompt))
        return json.dumps(self.state(events), ensure_ascii=False)

    # -- 단어 모드 ---------------------------------------------------------
    def _clear_input(self):
        self.typer.ime.clear()
        self.typer.pending = None
        for hand in self.typer.hands.values():
            hand.pinch.lost()
            hand.contact = None
            hand.held = []

    def _finished(self, event, now):
        """단어 하나가 끝났다(event = ("collect", 번호, 단어, "done"|"skip"))."""
        _, number, word, result = event
        if result == "done" and number in self.shown_at:
            self.durations.append(now - self.shown_at[number])
        self.last_result = [word, result]
        if result == "done":
            self.solved = word  # 맞힘: "다음 단어"(next)를 기다린다
        else:
            self.clear_next = True

    def next_word(self):
        """맞힌 단어에서 다음 단어로: 입력 칸을 비우고 바로 다음 단어를 낸다(쉬는 시간 없이)."""
        self.solved, self.clear_next = None, False
        self.collector.resting_until = None
        self._clear_input()

    def set_mode(self, mode):
        if mode not in ("free", "words"):
            raise ValueError(f"알 수 없는 모드: {mode}")
        self.collector = WordCollector.shuffled(SHORT_WORDS, seed=time.time_ns()) if mode == "words" else None
        self.clear_next, self.last_result, self.shown_at, self.durations = False, None, {}, []
        self.solved = None
        self._clear_input()
        if self.log is not None:  # 모드마다 기록 파일을 나눈다(단어 모드 기록은 첫 줄이 단어 순서)
            self.start_log()

    def word_state(self):
        if self.collector is None:
            return None
        prompt = None if self.solved else self.collector.prompt(self.now)
        target = self.solved or (None if prompt is None else prompt[1])
        text = self.typer.ime.text
        matched = 0
        if target is not None:
            while matched < min(len(text), len(target)) and text[matched] == target[matched]:
                matched += 1
        results = self.collector.results.values()
        return {"target": target, "solved": self.solved is not None, "matched": matched, "last": self.last_result,
                "done": sum(result == "done" for result in results),
                "skipped": sum(result == "skip" for result in results),
                "seconds": round(sum(self.durations) / len(self.durations), 1) if self.durations else None}

    def state(self, events=(), notice=None):
        typer = self.typer
        hands = {}
        for label in HANDS:
            hand = typer.hands[label]
            signal = self.signals.get(label)
            approach = hand.pinch.progress(signal.ratios) if signal is not None else [0.0] * 4
            release = hand.pinch.release_progress(signal.ratios) if signal is not None else 0.0
            held = hand.pinch.hold_progress(self.now)
            face = typer.face(label) or "palm"
            keys = KEYS[(label, face)]
            drag = typer.drag_view(label)
            hands[label] = {
                "face": hand.orientation.state,
                "occluded": typer.occluded(label),
                "armed": hand.pinch.armed,
                "fist": hand.fist.progress,
                "closed": hand.pinch.closed,
                "dragging": None if drag is None else drag["finger"],
                "drag_frames": 0 if drag is None else len(drag["points"]),
                "drag_radius": 0.0 if drag is None else round(DRAG_MIN * drag["size"] / IMAGE_SCALE, 4),
                "drag_preview": None if drag is None else VOWEL_LABELS[DRAG_STROKES[drag["drag"]]],
                "drag_passed": drag is not None and drag["drag"] is not None,
                "drag_pointer": None if drag is None else drag["pointer"],
                "map": [None if key is None else key_label(key) for key in keys],
                "fingers": [{"queue": cycle_queue(typer, label, finger), "active": hand.pinch.active == finger,
                             "approach": round(approach[finger], 3),
                             "release": round(release, 3) if hand.pinch.active == finger else 0.0,
                             "hold": round(held, 3) if hand.pinch.active == finger else 0.0}
                            for finger in range(4)],
            }
        pending = typer.pending
        group = None if pending is None else ["줄" if char == "\n" else char for char in group_of(pending.key)]
        return {"events": [list(event) for event in events], "text": typer.ime.text,
                "pending": pending is not None,
                "pending_punct": pending is not None and pending.key == "PUNCT",
                "group": group, "group_index": None if pending is None else (pending.count - 1) % len(group),
                "hold_mode": bool(typer.hands["Left"].pinch.hold), "hands": hands,
                "echo": None if typer.echo is None else dict(zip(("id", "hand", "text"), typer.echo)),
                "mode": "free" if self.collector is None else "words", "word": self.word_state(),
                "settings": self.settings(),
                "log": None if self.log_path is None else str(self.log_path), "notice": notice}

    # -- 명령 ------------------------------------------------------------
    def command(self, payload):
        cmd = json.loads(payload).get("cmd")
        notice = None
        if cmd == "clear":
            self.typer.ime.clear()
            self.typer.pending = None
        elif cmd == "mode":
            self.set_mode(json.loads(payload).get("mode"))
        elif cmd == "next" or (cmd == "skip" and self.solved is not None):
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
        elif cmd == "demo":  # 카메라 없이 고정 데이터 재생(Mac의 --demo와 같은 첫 시퀀스)
            case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
            self.typer.ime = replay(case["steps"]).ime
            self.typer.pending = None
            notice = case["name"]
        elif cmd == "settings":
            self.apply_settings(json.loads(payload).get("values") or {})
        elif cmd == "info":
            case = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"][0]
            notice = f"Python {sys.version.split()[0]} 준비됨 · 고정 데이터 재생 '{replay(case['steps']).ime.text}'"
        else:
            raise ValueError(f"알 수 없는 명령: {cmd}")
        return json.dumps(self.state(notice=notice), ensure_ascii=False)

    # -- 설정 ------------------------------------------------------------
    def settings(self):
        typer = self.typer
        return {"hold": bool(typer.hands["Left"].pinch.hold), "settle": typer.settle > 0,
                "index_sweep": typer.index_sweep_rule, "occlusion": typer.occluded_roll > -90}

    def apply_settings(self, values):
        typer = self.typer
        if "hold" in values:
            for hand in typer.hands.values():
                hand.pinch.hold = HOLD_SECONDS if values["hold"] else 0.0
        if "settle" in values:
            typer.settle = SETTLE if values["settle"] else 0.0
        if "index_sweep" in values:
            typer.index_sweep_rule = bool(values["index_sweep"])
        if "occlusion" in values:
            typer.occluded_roll = OCCLUDED_ROLL if values["occlusion"] else -90.0
        for hand in typer.hands.values():  # 규칙이 바뀌었으니 진행 중인 접촉은 버린다
            hand.pinch.lost()
            hand.contact = None
            hand.held = []

    # -- 기록 ------------------------------------------------------------
    def start_log(self):
        if self.log_dir is None:
            return "기록 폴더가 없습니다"
        self.stop_log()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        old = sorted(self.log_dir.glob("pinch-log-visionos-*.jsonl"))
        for path in old[:max(0, len(old) - (LOG_KEEP - 1))]:  # 새 파일까지 LOG_KEEP개
            path.unlink()
        stamp = time.strftime('%Y%m%d-%H%M%S')
        self.log_path = self.log_dir / f"pinch-log-visionos-{stamp}{'-words' if self.collector else ''}.jsonl"
        self.log = open(self.log_path, "a", encoding="utf-8")
        if self.collector is not None:  # 단어 모드: 첫 줄에 단어 순서(pinch_eval.read_config)
            self.log.write(json.dumps(self.collector.config(), ensure_ascii=False) + "\n")
        return "기록 시작: " + self.log_path.name

    def stop_log(self):
        if self.log is None:
            return None
        self.log.close()
        name = self.log_path.name
        self.log, self.log_path = None, None
        return "기록 끝: " + name

    def _write(self, now, hands, events, prompt=None):
        def rounded(rows, digits):
            return [[round(v, digits) for v in row] for row in rows]
        row = {"t": round(now, 4), "size": [1, 1], "source": "visionos",
               "hands": {label: {"seen_as": label, "ratios": [round(v, 4) for v in sig.ratios],
                                 "facing": round(sig.facing, 3), "roll": round(sig.roll, 1), "pose": sig.pose,
                                 "curled": list(sig.curled), "image": rounded(hands[label]["image"], 5),
                                 "world": rounded(hands[label]["world"], 5)}
                         for label, sig in self.signals.items()},
               "events": [list(event) for event in events]}
        if self.collector is not None:  # 단어 모드: 이 프레임에 안내 중인 단어(쉬는 중이면 null), Mac log_row와 같다
            row["prompt"] = prompt
        self.log.write(json.dumps(row, ensure_ascii=False) + "\n")
