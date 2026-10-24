"""프레임 기록을 쓰고 읽으며, 단어 입력 시험을 내고, 정답이 있는 기록을 다시 재생해 채점하는 모듈"""

import json
import random
from types import SimpleNamespace

from keyboard import (CONSONANTS, FINALS, FINGER_NAMES, HANDS, INITIALS, KEYS, MEDIALS, SPLIT_FINALS, VOWELS, HandSignal,
                      PinchTyper, finger_flex, image_tips, palm_roll)


# --- 프레임 기록 ---

def log_row(now, hands, signals, events, w, h, prompt=None, words=False):
    """프레임 하나의 손 신호, 관절, 이벤트를 기록 한 줄(dict)로 만드는 함수"""
    def points(landmarks, digits):
        # 파일 크기를 줄이려고 좌표를 digits 자리로 반올림함
        return [[round(p.x, digits), round(p.y, digits), round(p.z, digits)] for p in landmarks]

    row = {
        "t": round(now, 4), "size": [w, h],
        "hands": {role: {"seen_as": hands[role].label,
                         "ratios": [round(v, 4) for v in sig.ratios], "facing": round(sig.facing, 3),
                         "roll": round(sig.roll, 1), "pose": sig.pose, "curled": list(sig.curled),
                         "image": points(hands[role].landmarks, 4), "world": points(hands[role].world, 5)}
                  for role, sig in signals.items()},
        "events": [list(e) for e in events]}
    if words:  # 채점이 단어 경계를 이 값으로 나누므로 단어 시험 기록에만 둠
        row["prompt"] = prompt
    return row


def signal_from_log(entry, size=(1.0, 1.0)):
    """기록 한 줄의 손 하나를 HandSignal로 되돌리는 함수"""
    # 굽힘은 world 관절로, 손끝 좌표는 영상 관절로 다시 계산함. 예전 기록에는 이 값들이 없을 수 있음
    roll, tips, hand_size = entry.get("roll"), None, 0.0
    if "image" in entry:
        points = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in entry["image"]]
        tips, hand_size = image_tips(points, *size)
        if roll is None:
            roll = palm_roll(points, *size)
    flex = None
    if "world" in entry:
        flex = finger_flex([SimpleNamespace(x=x, y=y, z=z) for x, y, z in entry["world"]])
    return HandSignal(tuple(entry["ratios"]), tuple(entry["curled"]), entry["pose"], entry["facing"], roll or 0.0, flex,
                      tips, hand_size)


def read_config(path):
    """기록 첫 줄에 남긴 시험 설정을 읽는 함수"""
    with open(path, encoding="utf-8") as file:
        first = json.loads(file.readline() or "{}")
    return first.get("collect")


# --- 단어 입력 시험 ---

REST = 0.6  # 단어를 다 친 뒤 다음 단어까지 쉬는 시간(초). 떼는 동작이 다음 단어에 섞이지 않게 함
# 모든 자음 그룹(쌍자음 포함), 복합 모음, 받침이 고루 나오도록 고른 1~3음절 단어
SHORT_WORDS = (
    "가방", "나무", "다리", "라면", "마음", "바다", "사과", "아기", "자리", "차표", "카드", "타조", "파도", "하늘",
    "꿈", "딸기", "빵", "쌀", "짜다", "토끼", "오리", "우유", "의자", "과자", "귀", "뭐", "왜", "예", "얘기", "회사",
    "휴지", "교실", "거미", "너구리", "도로", "버스", "소리", "주스", "커피", "포도", "호박", "구름", "눈", "물", "불",
    "산", "강", "별", "달", "밥", "책", "집", "문", "손", "발", "꽃", "새", "곰", "말", "편지",
)
VOWEL_STROKES = {medial: strokes for strokes, medial in VOWELS.items() if medial in MEDIALS}  # 모음을 치는 획 순서
STROKE_DRAGS = {".": None, "-": "h", "i": "v"}  # 획마다 모음 버튼을 끄는 방향
VOWEL = ("Left", 0, "back")
COMMIT = ("Right", 0, "back")
GROUP_PLACES = {key: (hand, finger) for (hand, face), keys in KEYS.items() if face == "palm"
                for finger, key in enumerate(keys) if key in CONSONANTS}  # 자음 그룹 키가 놓인 손과 손가락


def jamo_of(syllable):
    """한글 음절 하나를 치는 순서의 자모 목록으로 나누는 함수"""
    code = ord(syllable) - 0xAC00
    if not 0 <= code < 11172:
        raise ValueError("한글 음절이 아닙니다: " + syllable)
    initial, rest = divmod(code, 21 * 28)
    medial, final = divmod(rest, 28)
    finals = SPLIT_FINALS.get(FINALS[final], (FINALS[final],) if final else ())  # 겹받침은 두 자음으로 나눔
    return [INITIALS[initial], MEDIALS[medial], *finals]


def word_steps(word):
    """단어를 치는 기대 핀치 단계 목록을 만드는 함수"""
    # 실제 입력기로 쳐 보고 같은 단어가 나오는지 확인함
    typer, steps = PinchTyper(), []

    def press(key, drag=None):
        typer.press_finger(key[0], key[1], key[2], drag)
        steps.append(["tap", key[0], key[1], key[2], drag])

    for char in word:
        if char == " ":
            if typer.pending is not None:
                press(COMMIT)
            press(COMMIT)
            continue
        for jamo in jamo_of(char):
            if jamo in VOWEL_STROKES:
                for stroke in VOWEL_STROKES[jamo]:
                    press(VOWEL, STROKE_DRAGS[stroke])
                continue
            group = next(key for key in GROUP_PLACES if jamo in CONSONANTS[key])
            hand, finger = GROUP_PLACES[group]
            pending = typer.pending
            # 같은 손가락의 후보가 남아 있으면 순환하지 않게 먼저 확정함(까까의 ㄲ 다음 ㄲ)
            if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, "palm"):
                press(COMMIT)
            for _ in range(CONSONANTS[group].index(jamo) + 1):
                press((hand, finger, "palm"))
    if typer.ime.text != word:
        raise ValueError(f"기대 단계가 {word!r} 대신 {typer.ime.text!r}를 만듭니다")
    return steps


class WordTest:
    """짧은 단어를 무작위 순서로 하나씩 내고 맞힘과 건너뜀을 세는 클래스"""

    def __init__(self):
        self.order = random.Random().sample(list(SHORT_WORDS), len(SHORT_WORDS))
        self.keys = {word: [tuple(step[1:4]) for step in word_steps(word)] for word in SHORT_WORDS}
        self.index = 0
        self.resting_until = None
        self.results = {}  # 단어 번호마다 "done" 또는 "skip"

    @property
    def done(self):
        return self.index >= len(self.order)

    def prompt(self, now):
        """지금 낼 단어의 (번호, 단어)를 돌려주는 메서드"""
        if self.done or (self.resting_until is not None and now < self.resting_until):  # 쉬는 중이면 None
            return None
        self.resting_until = None
        return self.index, self.order[self.index]

    def update(self, now, text):
        """입력 칸이 지금 단어와 같아졌으면 맞힘으로 넘기는 메서드"""
        current = self.prompt(now)
        if current is not None and text == current[1]:
            return self._finish("done", now)
        return []

    def skip(self, now):
        return [] if self.prompt(now) is None else self._finish("skip", now)

    def _finish(self, result, now):
        event = ("collect", self.index, self.order[self.index], result)
        self.results[self.index] = result
        self.index += 1
        self.resting_until = now + REST
        return [event]

    def summary(self):
        done = sum(result == "done" for result in self.results.values())
        skipped = sum(result == "skip" for result in self.results.values())
        return f"단어 입력: 맞힘 {done} · 건너뜀 {skipped} / {len(self.order)} (오입력은 main.py --evaluate로 확인)"

    def config(self):
        """기록 첫 줄에 남길 단어 순서와 단어마다의 기대 핀치 순서를 만드는 메서드"""
        return {"collect": {"reps": 1, "kind": "words", "order": self.order,
                            "keys": {word: [list(key) for key in keys] for word, keys in self.keys.items()}}}


# --- 기록 채점 ---

EVAL_MIN_FRAMES = 15  # 안내한 손이 이 프레임 수(약 0.5초)보다 적게 보인 차례는 채점에서 뺌
EDIT_NAMES = {"SPACE", "COMMIT", "PUNCT"}  # 단어를 고치거나 끝맺는 키라 기대 순서와 맞추지 않고 따로 셈
HAND_NAMES = {"Left": "왼손", "Right": "오른손"}
FACE_NAMES = {"palm": "", "back": " 손등"}


def key_name(key):
    """(손, 손가락, 방향) 키를 "오른손 손등 검지" 같은 이름으로 바꾸는 함수"""
    hand, finger, face = key
    return f"{HAND_NAMES[hand]}{FACE_NAMES[face]} {FINGER_NAMES[finger]}"


def replay_log(path, collect):
    """기록을 지금 판정으로 다시 재생하며 프레임마다 collect(typer, prompt, row)를 부르는 함수"""
    typer, current = PinchTyper(), None
    with open(path, encoding="utf-8") as file:
        for line in file:
            row = json.loads(line)
            if "hands" not in row:  # 결과 이벤트만 있는 줄
                collect(None, None, row)
                continue
            prompt = row.get("prompt")
            if prompt is not None and prompt["id"] != current:  # 앱과 같게 새 차례에서 직전 접촉을 버림
                for state in typer.hands.values():
                    state.pinch.lost()
            current = None if prompt is None else prompt["id"]
            signals = {hand: signal_from_log(v, row.get("size", (1.0, 1.0))) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in HANDS}, row["t"])
            collect(typer, prompt, row)


def replay_words(path):
    """단어 기록을 재생해 단어마다 기대 키, 실제 키, 편집 키 수, 기록 당시 결과를 모으는 함수"""
    config = read_config(path)
    expected = {word: [tuple(key) for key in keys] for word, keys in config["keys"].items()}
    items = {}

    def collect(typer, prompt, row):
        for event in row.get("events", []):
            if event[0] == "collect" and event[1] in items:
                items[event[1]]["live"] = event[3]
        if typer is None or prompt is None:
            return
        item = items.setdefault(prompt["id"], {"word": prompt["word"], "expected": expected[prompt["word"]],
                                               "taps": [], "edits": 0, "live": None})
        for key in typer.pinches:
            if KEYS[(key[0], key[2])][key[1]] in EDIT_NAMES:
                item["edits"] += 1
            else:
                item["taps"].append(key)

    replay_log(path, collect)
    return items


def replay_prompts(path):
    """손가락 차례 기록을 재생해 차례마다 안내한 손가락과 실제 입력을 모으는 함수"""
    prompts = {}

    def collect(typer, prompt, row):
        for event in row.get("events", []):
            if event[0] == "collect" and event[1] in prompts and event[4] != "redo":
                prompts[event[1]]["live"] = event[4]
        if typer is None or prompt is None:
            return
        face = prompt.get("face", "palm")  # 2026-10-23 이전 기록에는 방향이 없고 모두 손바닥임
        item = prompts.setdefault(prompt["id"], {"hand": prompt["hand"], "finger": prompt["finger"], "face": face,
                                                 "frames": 0, "taps": [], "live": None})
        item["frames"] += prompt["hand"] in row["hands"]
        item["taps"] += [finger for hand, finger, side in typer.pinches if (hand, side) == (prompt["hand"], face)]

    replay_log(path, collect)
    return prompts


def align(expected, actual):
    """기대 키와 실제 키를 편집 거리로 맞춰 맞음, 다른 키, 더 들어감, 빠짐으로 나누는 함수"""
    rows, cols = len(expected) + 1, len(actual) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost[i][j] = min(cost[i - 1][j - 1] + (expected[i - 1] != actual[j - 1]),
                             cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    # 표 끝에서 처음으로 거슬러 가며 각 칸이 어느 연산으로 왔는지 고름
    ops, i, j = [], len(expected), len(actual)
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + (expected[i - 1] != actual[j - 1]):
            ops.append(("match" if expected[i - 1] == actual[j - 1] else "sub", expected[i - 1], actual[j - 1]))
            i, j = i - 1, j - 1
        elif j and cost[i][j] == cost[i][j - 1] + 1:
            ops.append(("extra", None, actual[j - 1]))
            j -= 1
        else:
            ops.append(("miss", expected[i - 1], None))
            i -= 1
    return ops[::-1]


def report_words(items, verbose=False):
    """단어 기록의 키 단위 채점 결과를 글로 만드는 함수"""
    totals = {"match": 0, "sub": 0, "extra": 0, "miss": 0}
    finished = {number: item for number, item in items.items() if item["live"] is not None}
    lines = []
    for number, item in sorted(finished.items()):
        ops = align(item["expected"], item["taps"])
        counts = {kind: sum(op[0] == kind for op in ops) for kind in totals}
        for kind in totals:
            totals[kind] += counts[kind]
        if verbose:
            extras = ", ".join(key_name(got) for kind, _, got in ops if kind == "extra")
            lines.append(f"  {number:3d} {item['word']}: 기대 {len(item['expected'])} · 맞음 {counts['match']} · "
                         f"다른 손가락 {counts['sub']} · 더 들어감 {counts['extra']} · 빠짐 {counts['miss']}"
                         + (f" · 더 들어간 키: {extras}" if extras else ""))
    expected = sum(len(item["expected"]) for item in finished.values())
    lines.append(f"단어 {len(finished)}개(끝나지 않은 {len(items) - len(finished)}개 제외) · 기대 키 {expected} · "
                 f"맞음 {totals['match']} · 다른 손가락 {totals['sub']} · 더 들어감(오입력) {totals['extra']} · "
                 f"빠짐 {totals['miss']} · 편집 키 {sum(item['edits'] for item in finished.values())}")
    return "\n".join(lines)


def report_prompts(prompts, verbose=False):
    """손가락 차례 기록의 첫 입력 정확도와 전체 입력 결과를 글로 만드는 함수"""
    valid = {k: v for k, v in prompts.items() if v["frames"] >= EVAL_MIN_FRAMES}
    lines = []
    if verbose:
        for number, item in sorted(valid.items()):
            taps = " ".join(FINGER_NAMES[f] for f in item["taps"]) or "-"
            lines.append(f"  {number:3d} {key_name((item['hand'], item['finger'], item['face']))} → {taps}")
    first = sum(bool(v["taps"]) and v["taps"][0] == v["finger"] for v in valid.values())
    right = sum(f == v["finger"] for v in valid.values() for f in v["taps"])
    wrong = sum(f != v["finger"] for v in valid.values() for f in v["taps"])
    missed = sum(not v["taps"] for v in valid.values())
    lines.append(f"평가 차례 {len(valid)}개 (제외 {len(prompts) - len(valid)}개) · 첫 입력 맞음 {first} · "
                 f"모든 입력 맞음 {right} / 틀림 {wrong} · 입력 없음 {missed}")
    return "\n".join(lines)
