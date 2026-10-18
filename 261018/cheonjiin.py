"""카메라와 분리한 천지인 조합기와 접촉 입력 상태 머신 (표준 라이브러리만 사용)."""

from dataclasses import dataclass
from typing import Optional


# 같은 자음 키를 제한 시간 안에 다시 누르면 다음 자음으로 바뀐다.
CONSONANTS = {
    "4": "ㄱㅋㄲ", "5": "ㄴㄹ", "6": "ㄷㅌㄸ",
    "7": "ㅂㅍㅃ", "8": "ㅅㅎㅆ", "9": "ㅈㅊㅉ", "0": "ㅇㅁ",
}
VOWEL_KEYS = {"1": "i", "2": ".", "3": "-"}
VOWELS = {
    "i": "ㅣ", "-": "ㅡ", ".": "ㆍ", "..": "ㆍㆍ",
    "i.": "ㅏ", "i..": "ㅑ", ".i": "ㅓ", "..i": "ㅕ",
    ".-": "ㅗ", "..-": "ㅛ", "-.": "ㅜ", "-..": "ㅠ",
    "i.i": "ㅐ", "i..i": "ㅒ", ".ii": "ㅔ", "..ii": "ㅖ",
    ".-i.": "ㅘ", ".-i.i": "ㅙ", ".-i": "ㅚ",
    "-..i": "ㅝ", "-..ii": "ㅞ", "-.i": "ㅟ", "-i": "ㅢ",
}
INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
MEDIALS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
FINALS = ("", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ",
          "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ",
          "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ")
FINAL_PAIRS = {
    "ㄱㅅ": "ㄳ", "ㄴㅈ": "ㄵ", "ㄴㅎ": "ㄶ", "ㄹㄱ": "ㄺ", "ㄹㅁ": "ㄻ",
    "ㄹㅂ": "ㄼ", "ㄹㅅ": "ㄽ", "ㄹㅌ": "ㄾ", "ㄹㅍ": "ㄿ", "ㄹㅎ": "ㅀ", "ㅂㅅ": "ㅄ",
}
SPLIT_FINALS = {value: tuple(key) for key, value in FINAL_PAIRS.items()}


@dataclass
class Token:
    kind: str
    value: str
    key: str = ""


@dataclass
class UndoEdit:
    length: int
    last: Optional[Token]
    cleared: Optional[list] = None


def compose(tokens):
    """자모 토큰을 유니코드 한글로 조합하고, 모음 앞의 받침을 다음 초성으로 옮긴다."""
    output = []
    initial = medial = final = ""

    def flush():
        nonlocal initial, medial, final
        if initial and medial:
            code = 0xAC00 + (INITIALS.index(initial) * 21 + MEDIALS.index(medial)) * 28
            output.append(chr(code + FINALS.index(final)))
        else:
            output.append(initial or medial)
        initial = medial = final = ""

    for token in tokens:
        value = token.value
        if token.kind == "C":
            if initial and medial:
                if not final and value in FINALS:
                    final = value
                elif final + value in FINAL_PAIRS:
                    final = FINAL_PAIRS[final + value]
                else:
                    flush()
                    initial = value
            else:
                flush()
                initial = value
        elif token.kind == "V":
            vowel = VOWELS[value]
            if vowel not in MEDIALS:  # 아직 조합 중인 아래아는 임시로 표시한다.
                flush()
                output.append(vowel)
            elif initial and medial and final:
                remaining, carried = SPLIT_FINALS.get(final, ("", final))
                final = remaining
                flush()
                initial, medial = carried, vowel
            elif initial and not medial:
                medial = vowel
            else:
                flush()
                medial = vowel
        else:
            flush()
            output.append(value)  # 확정 경계는 빈 문자열, 공백/줄바꿈은 그대로 출력
    flush()
    return "".join(output)


class CheonjiinIME:
    def __init__(self, cycle_seconds=0.85):
        self.cycle_seconds = cycle_seconds
        self.tokens = []
        self.history = []
        self.last_key = None
        self.last_time = float("-inf")

    @property
    def text(self):
        return compose(self.tokens)

    def _remember(self):
        # 키 하나는 마지막 토큰만 수정하거나 새 토큰 하나를 추가한다.
        # 전체 문장을 매번 복사하지 않아 긴 입력에서도 history 크기는 선형이다.
        last = self.tokens[-1] if self.tokens else None
        self.history.append(UndoEdit(len(self.tokens), Token(last.kind, last.value, last.key) if last else None))

    def press(self, key, now):
        if key == "BACK":
            self.backspace()
            return
        if key not in CONSONANTS and key not in VOWEL_KEYS and key not in ("SPACE", "COMMIT", "ENTER"):
            raise ValueError("알 수 없는 키: " + key)
        self._remember()
        if key in CONSONANTS:
            group = CONSONANTS[key]
            if (self.tokens and self.tokens[-1].kind == "C" and self.last_key == key
                    and 0 <= now - self.last_time <= self.cycle_seconds):
                token = self.tokens[-1]
                token.value = group[(group.index(token.value) + 1) % len(group)]
            else:
                self.tokens.append(Token("C", group[0], key))
        elif key in VOWEL_KEYS:
            atom = VOWEL_KEYS[key]
            if (self.tokens and self.tokens[-1].kind == "V"
                    and self.tokens[-1].value + atom in VOWELS):
                self.tokens[-1].value += atom
            else:
                self.tokens.append(Token("V", atom, key))
        else:
            self.tokens.append(Token("B", {"SPACE": " ", "COMMIT": "", "ENTER": "\n"}[key]))
        self.last_key, self.last_time = key, now

    def backspace(self):
        """마지막 한 번의 키 입력을 취소한다 (완성 글자를 통째로 지우지 않는다)."""
        if self.history:
            edit = self.history.pop()
            if edit.cleared is not None:
                self.tokens = edit.cleared
            else:
                del self.tokens[edit.length:]
                if edit.last is not None:
                    self.tokens[-1] = edit.last
        self.last_key, self.last_time = None, float("-inf")

    def clear(self):
        if self.tokens:
            self.history.append(UndoEdit(0, None, self.tokens))
        self.tokens = []
        self.last_key, self.last_time = None, float("-inf")


class TouchGate:
    """눌림 유지 → 한 번 입력 → 충분히 뗀 뒤 재입력. 손 유실은 release로 보지 않는다."""

    def __init__(self, dwell=0.12, release=0.10):
        self.dwell = dwell
        self.release = release
        self.armed = False  # 시작할 때도 엄지를 한 번 떼어야 한다.
        self.candidate: Optional[str] = None
        self.since = None
        self.release_since = None
        self.progress = 0.0

    def lost(self):
        self.armed = False
        self.candidate = self.since = self.release_since = None
        self.progress = 0.0

    def update(self, key, fully_released, now):
        self.progress = 0.0
        if not self.armed:
            if fully_released:
                if self.release_since is None:
                    self.release_since = now
                if now - self.release_since >= self.release:
                    self.armed = True
                    self.release_since = None
            else:
                self.release_since = None
            return None
        if key is None:
            self.candidate = self.since = None
            return None
        if key != self.candidate:
            self.candidate, self.since = key, now
        self.progress = min(1.0, (now - self.since) / self.dwell)
        if self.progress >= 1.0:
            self.armed = False
            self.candidate = self.since = self.release_since = None
            return key
        return None
