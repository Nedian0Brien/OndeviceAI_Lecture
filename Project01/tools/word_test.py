"""단어 입력 시험: 짧은 단어를 무작위 순서로 하나씩 내고, 입력 칸이 그 단어가 되면 맞힌 것으로 셉니다.

project01.py --words(카메라)와 Vision Pro 앱의 단어 입력이 씁니다. 기록 파일 첫 줄에 단어 순서와 단어마다의 기대
꼬집기 순서(word_keys)를 남기고, 행마다 지금 단어를 남깁니다. tools/evaluate.py가 이 기록으로 오입력을 셉니다.
"""

import random

from korean_ime import CONSONANTS, FINALS, INITIALS, MEDIALS, SPLIT_FINALS, VOWELS
from layout import KEYS
from typer import PinchTyper


REST = 0.6  # 단어를 다 친 뒤 다음 단어까지 쉬는 시간(초). 떼는 동작이 다음 단어에 섞이지 않게 함
# 1~3음절 일상 단어(띄어쓰기 없음). 모든 자음 그룹(ㄲ·ㄸ·ㅃ·ㅆ·ㅉ 포함), 복합 모음(ㅘ·ㅝ·ㅙ·ㅢ·ㅚ·ㅟ·ㅒ·ㅖ), 받침이
# 고루 나오게 고름. 단어마다 word_steps로 칠 수 있는지 검증함(테스트)
SHORT_WORDS = (
    "가방", "나무", "다리", "라면", "마음", "바다", "사과", "아기", "자리", "차표", "카드", "타조", "파도", "하늘",
    "꿈", "딸기", "빵", "쌀", "짜다", "토끼", "오리", "우유", "의자", "과자", "귀", "뭐", "왜", "예", "얘기", "회사",
    "휴지", "교실", "거미", "너구리", "도로", "버스", "소리", "주스", "커피", "포도", "호박", "구름", "눈", "물", "불",
    "산", "강", "별", "달", "밥", "책", "집", "문", "손", "발", "꽃", "새", "곰", "말", "편지",
)

# 단어 → 기대 꼬집기 단계에 쓰는 자리
VOWEL_STROKES = {medial: strokes for strokes, medial in VOWELS.items() if medial in MEDIALS}  # ㅏ → "i."
STROKE_DRAGS = {".": None, "-": "h", "i": "v"}  # 천지인 획 → 끌기: ㆍ 그대로, ㅡ 가로, ㅣ 세로
VOWEL = ("Left", 0, "back")  # 모음
COMMIT = ("Right", 0, "back")  # 확정/공백
GROUP_PLACES = {key: (hand, finger) for (hand, face), keys in KEYS.items() if face == "palm"
                for finger, key in enumerate(keys) if key in CONSONANTS}  # 자음 그룹 키 → (손, 손가락)


def jamo_of(syllable):
    """한글 음절 하나 → 치는 순서의 자모 [초성, 중성, 종성...](겹받침은 둘로 나눕니다)."""
    code = ord(syllable) - 0xAC00
    if not 0 <= code < 11172:  # 완성형 한글 음절 범위(가~힣) 밖
        raise ValueError("한글 음절이 아닙니다: " + syllable)
    # 음절 코드 = (초성×21 + 중성)×28 + 종성
    initial, rest = divmod(code, 21 * 28)
    medial, final = divmod(rest, 28)
    finals = SPLIT_FINALS.get(FINALS[final], (FINALS[final],) if final else ())
    return [INITIALS[initial], MEDIALS[medial], *finals]


def word_steps(word):
    """단어를 치는 기대 단계 [["tap", 손, 손가락, 방향, 끌기]](typer.replay 형식)를 만듭니다.
    입력기로 같은 글자가 나오는지 검증합니다.

    - 모음: 모음 버튼을 천지인 획 순서로(ㆍ 그대로, ㅡ 가로 끌기, ㅣ 세로 끌기).
    - 자음: 그룹 손가락을 그룹 안 순서만큼(ㅋ = 오른손 검지 2번). ㄴ·ㄹ은 한 번.
    - 같은 손가락 그룹 자음이 후보로 남아 이어지면(까까의 ㄲ → ㄲ) 사이에 확정을 넣습니다.
    """
    typer, steps, now = PinchTyper(), [], 0.0

    def press(key, drag=None):
        # 꼬집기 한 번을 실제 입력기에 넣고 단계로 남김
        nonlocal now
        now += 0.5
        typer.press_finger(key[0], key[1], now, key[2], drag)
        steps.append(["tap", key[0], key[1], key[2], drag])

    # 글자마다 자모로 나눠 꼬집기 단계를 쌓음
    for char in word:
        if char == " ":  # 공백: 남은 후보를 확정한 뒤 확정/공백 키(후보가 없으면 공백)
            if typer.pending is not None:
                press(COMMIT)
            press(COMMIT)
            continue
        for jamo in jamo_of(char):
            if jamo in VOWEL_STROKES:  # 모음: 획마다 모음 버튼을 끌기 방향과 함께 꼬집음
                for stroke in VOWEL_STROKES[jamo]:
                    press(VOWEL, STROKE_DRAGS[stroke])
                continue
            # 자음: 이 자음이 든 그룹 키와 그 손가락을 찾음
            group = next(key for key in GROUP_PLACES if jamo in CONSONANTS[key])
            hand, finger = GROUP_PLACES[group]
            pending = typer.pending
            if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, "palm"):
                press(COMMIT)  # 같은 손가락의 후보가 남아 있으면 순환하지 않게 먼저 확정
            for _ in range(CONSONANTS[group].index(jamo) + 1):  # 그룹 안 순서만큼 꼬집어 순환
                press((hand, finger, "palm"))
    if typer.ime.text != word:  # 단계를 실제로 쳐 본 결과가 단어와 다르면 단어 목록 오류
        raise ValueError(f"기대 단계가 {word!r} 대신 {typer.ime.text!r}를 만듭니다")
    return steps


def word_keys(word):
    """단어를 치는 기대 꼬집기 순서 [(손, 손가락, 방향)](평가에서 실제 꼬집기와 맞춥니다. 끌기 방향은 빠집니다)."""
    return [tuple(step[1:4]) for step in word_steps(word)]


class WordTest:
    """단어를 하나씩 냅니다. 입력 칸이 그 단어와 같아지면 맞힘("done"), 건너뛰면 "skip"입니다.
    결과마다 REST초 쉽니다."""

    def __init__(self, words=SHORT_WORDS, rounds=1, seed=None):
        rng = random.Random(seed)
        self.rounds = rounds
        self.order = [word for _ in range(rounds) for word in rng.sample(list(words), len(words))]  # 바퀴마다 다른 순서
        self.keys = {word: word_keys(word) for word in words}
        self.index = 0  # 지금 단어 번호
        self.resting_until = None  # 쉬는 중이면 끝나는 시각
        self.results = {}  # 단어 번호 → "done" / "skip"

    @property
    def done(self):
        """모든 단어를 끝냈는지 돌려줍니다."""
        return self.index >= len(self.order)

    def prompt(self, now):
        """지금 낼 단어 (번호, 단어)를 돌려줍니다. 쉬는 중이거나 끝났으면 None입니다."""
        if self.done or (self.resting_until is not None and now < self.resting_until):
            return None
        self.resting_until = None  # 쉬는 시간이 끝남
        return self.index, self.order[self.index]

    def update(self, now, text):
        """입력 칸이 지금 단어와 같으면 맞힘으로 넘깁니다.
        결과 이벤트 [("collect", 번호, 단어, "done")] 또는 []를 돌려줍니다."""
        current = self.prompt(now)
        if current is not None and text == current[1]:
            return self._finish("done", now)
        return []

    def skip(self, now):
        """지금 단어를 건너뜁니다. 쉬는 중이면 아무것도 하지 않습니다."""
        return [] if self.prompt(now) is None else self._finish("skip", now)

    def _finish(self, result, now):
        # 지금 단어의 결과를 남기고 다음 단어로 넘긴 뒤 REST초 쉼
        event = ("collect", self.index, self.order[self.index], result)
        self.results[self.index] = result
        self.index += 1
        self.resting_until = now + REST
        return [event]

    @staticmethod
    def describe(prompt):
        """기록 행의 prompt 필드."""
        return {"id": prompt[0], "word": prompt[1]}

    def summary(self):
        """맞힘·건너뜀 수를 한 줄로 요약합니다."""
        done = sum(result == "done" for result in self.results.values())
        skipped = sum(result == "skip" for result in self.results.values())
        return f"단어 입력: 맞힘 {done} · 건너뜀 {skipped} / {len(self.order)} (오입력은 tools/evaluate.py로 확인)"

    def config(self):
        """기록 첫 줄: 단어 순서와 단어마다의 기대 꼬집기 순서."""
        return {"collect": {"reps": self.rounds, "kind": "words", "order": self.order,
                            "keys": {word: [list(key) for key in keys] for word, keys in self.keys.items()}}}
