"""정답이 붙은 꼬집기 기록의 차례 관리 (표준 라이브러리만 사용, 카메라와 무관).

손가락 하나씩(Collector)과 단어 단위(WordCollector) 두 가지가 있다.

pinch_cheonjiin.py --collect가 쓴다. 화면에 "왼손 약지"처럼 꼬집을 손가락을 차례로 띄우고,
사용자가 꼬집으면 지금 판정이 고른 손가락을 결과로 남긴 뒤 다음 차례로 넘어간다.
로그의 각 프레임에는 그때의 차례(prompt)가 함께 남으므로, 나중에 다른 판정 방법을 같은 기록으로 비교할 수 있다.

차례 순서(kind)
  typing: 손바닥 자판. 손별로 검지 → 중지 → 약지 → 소지를 한 번씩 꼬집는 것을 한 바퀴로 하여,
          왼손 reps바퀴 다음 오른손 reps바퀴. 한 손에 집중할 수 있고, 손가락을 옮겨 가는 실제 입력 동작과 비슷하다.
  left / right: 그 손 손바닥만 검지 → 소지 reps바퀴. 한 손을 특정 자세(예: 카메라가 비스듬히 보는 자세)로
          모아 기록할 때 쓴다(2026-10-23, 가려진 중지 꼬집기 조사).
  edit:   왼손 손등 키(모음·공백·확정·문장부호). 왼손 손등으로 검지 → 소지 reps바퀴.
  words:  단어를 하나씩 안내하고 평소처럼 친다(WordCollector). 손가락을 이어서 옮길 때 생기는 오입력(엄지가 검지를
          지나감 등)은 손가락 하나씩 쉬어 가며 안내하면 나타나지 않아서 만들었다(2026-10-23).

차례 하나의 흐름
  안내(prompt 표시) → 안내한 손·방향에서 꼬집기 입력 → 결과 기록 → REST초 쉼(prompt 없음) → 다음 안내
  - 다른 손이나 다른 방향(손바닥/손등)의 입력은 차례를 넘기지 않는다(로그에는 남는다).
  - skip: 꼬집었는데 입력이 나오지 않을 때 사용자가 넘긴다.
  - redo: 직전 차례를 무효로 하고 다시 한다(잘못 꼬집었을 때).
"""

from korean_ime import CONSONANTS, FINALS, INITIALS, MEDIALS, SPLIT_FINALS, VOWELS
from pinch_typer import KEYS, PinchTyper

HANDS = ("Left", "Right")
FINGER_NAMES = ("검지", "중지", "약지", "소지")
HAND_NAMES = {"Left": "왼손", "Right": "오른손"}
FACE_NAMES = {"palm": "", "back": " 손등"}
KINDS = ("typing", "left", "right", "edit", "words")
REST = 0.6  # 결과를 남긴 뒤 다음 안내까지 쉬는 시간(초). 떼는 동작이 다음 차례에 섞이지 않게 한다.


def make_order(reps, kind="typing"):
    """차례 목록 [(손, 손가락, 방향)]. 방향은 "palm"(손바닥) 또는 "back"(손등)."""
    if kind == "edit":
        return [("Left", finger, "back") for _ in range(reps) for finger in range(4)]
    if kind in ("left", "right"):
        return [(kind.capitalize(), finger, "palm") for _ in range(reps) for finger in range(4)]
    return [(hand, finger, "palm") for hand in HANDS for _ in range(reps) for finger in range(4)]


class Collector:
    def __init__(self, reps=10, kind="typing"):
        self.reps, self.kind = reps, kind
        self.order = make_order(reps, kind)
        self.index = 0  # 지금 차례 번호
        self.resting_until = None  # 쉬는 중이면 끝나는 시각
        self.results = {}  # 차례 번호 → 지금 판정이 고른 손가락(int) 또는 "skip"

    @property
    def done(self):
        return self.index >= len(self.order)

    def prompt(self, now):
        """지금 안내할 차례 (id, 손, 손가락, 방향). 쉬는 중이거나 끝났으면 None."""
        if self.done or (self.resting_until is not None and now < self.resting_until):
            return None
        self.resting_until = None
        return (self.index, *self.order[self.index])

    # 로그 행의 prompt 필드 이름(prompt()가 돌려주는 튜플 순서)
    FIELDS = ("id", "hand", "finger", "face")

    def describe(self, prompt):
        """prompt 튜플 → 로그 행의 prompt 필드(dict)."""
        return dict(zip(self.FIELDS, prompt))

    def _event(self, result):
        hand, finger, face = self.order[self.index]
        return ("collect", self.index, hand, finger, result, face)

    def _finish(self, result, now):
        event = self._event(result)
        self.results[self.index] = result
        self.index += 1
        self.resting_until = now + REST
        return [event]

    def update(self, pinches, now, text=""):
        """pinches: 이번 프레임에 인정된 꼬집기 [(손, 손가락, 방향)]. 안내한 손·방향의 꼬집기가 있으면 차례를 넘긴다.

        text(입력 칸 글자)는 단어 기록(WordCollector)만 쓴다.
        """
        current = self.prompt(now)
        if current is None:
            return []
        for hand, finger, face in pinches:
            if (hand, face) == (current[1], current[3]):
                return self._finish(finger, now)
        return []

    def skip(self, now):
        """꼬집었는데 입력되지 않았다."""
        return [] if self.prompt(now) is None else self._finish("skip", now)

    def redo(self, now):
        """직전 차례를 무효로 하고 다시 한다."""
        if self.index == 0:
            return []
        self.index -= 1
        self.results.pop(self.index, None)
        self.resting_until = now + REST
        return [self._event("redo")]

    def label(self, hand, finger, face="palm"):
        return f"{HAND_NAMES[hand]}{FACE_NAMES[face]} {FINGER_NAMES[finger]}"

    def round_of(self, number):
        """차례 번호 → 그 손에서 몇 번째 바퀴인지(1부터)."""
        return number % (4 * self.reps) // 4 + 1

    def summary(self):
        """지금 판정의 손·방향·손가락별 결과 표(문자열)."""
        lines = ["지금 판정 결과 (맞음 / 다른 손가락 / 건너뜀)"]
        for item in dict.fromkeys(self.order):  # 처음 나온 순서대로 (손, 손가락, 방향)
            ids = [i for i, other in enumerate(self.order) if other == item and i in self.results]
            right = sum(self.results[i] == item[1] for i in ids)
            skipped = sum(self.results[i] == "skip" for i in ids)
            lines.append(f"  {self.label(item[0], item[1], item[2])}: {right} / {len(ids) - right - skipped} / {skipped}")
        return "\n".join(lines)

    def config(self):
        """로그 첫 줄에 남기는 설정."""
        return {"collect": {"reps": self.reps, "kind": self.kind, "order": [list(item) for item in self.order]}}


# ---------------------------------------------------------------------------
# 단어 기록

# 모든 자음 그룹(ㅃ·ㅆ·ㅉ 포함), 모음 획 조합(ㅘ·ㅝ·ㅙ·ㅢ·ㅚ·ㅟ·ㅒ·ㅖ 포함), 같은 손가락 자음 연속(확정),
# 공백이 들어가도록 고른 단어. 단어마다 word_steps가 기대 단계를 만들고 입력기로 검증한다.
WORDS = (
    "안녕하세요", "감사합니다", "사랑해요", "대한민국", "학교", "컴퓨터", "토끼", "딸기", "짜장면", "치킨",
    "우유", "의자", "과일", "뭐해", "왜요", "쌀밥", "빵집", "오늘 날씨", "하늘", "노래", "친구", "바다",
    "좋은 아침", "맛있어요", "행복", "꽃", "뜨거워", "휴대폰", "회사", "귀여워", "예뻐", "얘기",
)
# 단어 타이핑 모드(Vision Pro 앱)에서 무작위로 내는 짧은 단어(1~3음절, 띄어쓰기 없음). 모든 자음 그룹(ㄲ·ㄸ·ㅃ·ㅆ·ㅉ 포함),
# 복합 모음(ㅘ·ㅝ·ㅙ·ㅢ·ㅚ·ㅟ·ㅒ·ㅖ), 받침이 고루 나오게 골랐다. 단어마다 word_steps로 칠 수 있는지 검증한다(테스트).
SHORT_WORDS = (
    "가방", "나무", "다리", "라면", "마음", "바다", "사과", "아기", "자리", "차표", "카드", "타조", "파도", "하늘",
    "꿈", "딸기", "빵", "쌀", "짜다", "토끼", "오리", "우유", "의자", "과자", "귀", "뭐", "왜", "예", "얘기", "회사",
    "휴지", "교실", "거미", "너구리", "도로", "버스", "소리", "주스", "커피", "포도", "호박", "구름", "눈", "물", "불",
    "산", "강", "별", "달", "밥", "책", "집", "문", "손", "발", "꽃", "새", "곰", "말", "편지",
)
VOWEL_STROKES = {medial: strokes for strokes, medial in VOWELS.items() if medial in MEDIALS}
VOWEL = ("Left", 0, "back")  # 왼손 손등 검지: 모음 버튼
STROKE_DRAGS = {".": None, "-": "h", "i": "v"}  # 천지인 획 → 끌기: ㆍ 그대로, ㅡ 가로, ㅣ 세로
COMMIT = ("Right", 0, "back")  # 오른손 손등 검지: 후보가 있으면 확정, 없으면 공백
# 자음 그룹 키 → 손바닥 자리(손, 손가락)
GROUP_PLACES = {key: (hand, finger) for (hand, face), keys in KEYS.items() if face == "palm"
                for finger, key in enumerate(keys) if key in CONSONANTS}


def jamo_of(syllable):
    """한글 음절 하나 → 치는 순서의 자모 [초성, 중성, 종성...](겹받침은 둘로 나눈다)."""
    code = ord(syllable) - 0xAC00
    if not 0 <= code < 11172:
        raise ValueError("한글 음절이 아닙니다: " + syllable)
    initial, rest = divmod(code, 21 * 28)
    medial, final = divmod(rest, 28)
    finals = SPLIT_FINALS.get(FINALS[final], (FINALS[final],) if final else ())
    return [INITIALS[initial], MEDIALS[medial], *finals]


def word_steps(word):
    """단어를 치는 기대 단계 [["tap", 손, 손가락, 방향, 끌기]](pinch_typer.replay 형식). 입력기로 같은 글자가 나오는지 검증한다.

    - 모음: 왼손 손등 검지 모음 버튼을 천지인 획 순서로(ㆍ 그대로, ㅡ 가로 끌기, ㅣ 세로 끌기).
    - 자음: 그룹 손가락을 그룹 안 순서만큼(ㅋ = 오른손 검지 2번, ㅎ = 왼손 약지 2번). ㄴ·ㄹ은 한 번.
    - 같은 손·손가락 그룹 자음이 후보로 남아 이어지면(까까의 ㄲ→ㄲ) 사이에 오른손 손등 검지로 확정한다.
    - 공백: 후보가 있으면 손등 검지 한 번(확정) 더, 그다음 손등 검지(공백).
    """
    typer, steps, now = PinchTyper(), [], 0.0

    def press(key, drag=None):
        nonlocal now
        now += 0.5
        typer.press_finger(key[0], key[1], now, key[2], drag)
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
            group = next(key for key in GROUP_PLACES if jamo in CONSONANTS[key])  # 자판에 있는 키(ㄹ = L)
            hand, finger = GROUP_PLACES[group]
            pending = typer.pending
            if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, "palm"):
                press(COMMIT)
            for _ in range(CONSONANTS[group].index(jamo) + 1):
                press((hand, finger, "palm"))
    if typer.ime.text != word:
        raise ValueError(f"기대 단계가 {word!r} 대신 {typer.ime.text!r}를 만듭니다")
    return steps


def word_keys(word):
    """단어를 치는 기대 꼬집기 순서 [(손, 손가락, 방향)](평가에서 실제 꼬집기와 맞출 때 쓴다. 모음 끌기 방향은 빠진다)."""
    return [tuple(step[1:4]) for step in word_steps(word)]


class WordCollector(Collector):
    """단어 기록: 단어를 하나씩 안내하고, 입력 칸이 그 단어와 같아지면 다음 단어로 넘어간다(지움으로 고쳐도 된다).

    결과: "done"(다 침) / "skip"(N) / "redo"(B). 꼬집기가 맞았는지는 기록 뒤 pinch_eval이 기대 키 순서와 맞춰 센다.
    """

    FIELDS = ("id", "word")

    def __init__(self, reps=1, words=WORDS):
        self.reps, self.kind = reps, "words"
        self.order = [word for _ in range(reps) for word in words]
        self.keys = {word: word_keys(word) for word in words}
        self.index, self.resting_until, self.results = 0, None, {}

    def prompt(self, now):
        """지금 안내할 차례 (id, 단어). 쉬는 중이거나 끝났으면 None."""
        if self.done or (self.resting_until is not None and now < self.resting_until):
            return None
        self.resting_until = None
        return (self.index, self.order[self.index])

    def _event(self, result):
        return ("collect", self.index, self.order[self.index], result)

    def update(self, pinches, now, text=""):
        current = self.prompt(now)
        if current is not None and text == current[1]:
            return self._finish("done", now)
        return []

    @classmethod
    def shuffled(cls, words=SHORT_WORDS, rounds=5, seed=None):
        """단어 타이핑 모드: 단어를 바퀴마다 다른 무작위 순서로(rounds바퀴). 순서는 config로 기록 첫 줄에 남는다."""
        import random
        collector = cls(rounds, words)
        rng = random.Random(seed)
        collector.order = [word for _ in range(rounds) for word in rng.sample(list(words), len(words))]
        return collector

    def round_of(self, number):
        return number // (len(self.order) // self.reps) + 1

    def summary(self):
        done = sum(result == "done" for result in self.results.values())
        skipped = sum(result == "skip" for result in self.results.values())
        return f"단어 기록: 완료 {done} · 건너뜀 {skipped} / {len(self.order)} (오입력은 pinch_eval.py로 확인)"

    def config(self):
        return {"collect": {"reps": self.reps, "kind": self.kind, "order": self.order,
                            "keys": {word: [list(key) for key in keys] for word, keys in self.keys.items()}}}
