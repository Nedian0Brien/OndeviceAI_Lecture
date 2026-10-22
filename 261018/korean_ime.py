"""한글 입력기: 천지인 자판의 키 입력을 한글 문자열로 조합한다 (표준 라이브러리만 사용, 카메라와 무관).

키 번호는 휴대폰 천지인 자판을 따른다.
  1 ㅣ   2 ㆍ   3 ㅡ
  4 ㄱㅋㄲ  5 ㄴㄹ  6 ㄷㅌㄸ
  7 ㅂㅍㅃ  8 ㅅㅎㅆ  9 ㅈㅊㅉ
  0 ㅇㅁ   SPACE 공백   COMMIT 조합 경계(공백 없음)   ENTER 줄바꿈   BACK 지움

- 모음은 ㅣ·ㆍ·ㅡ 획을 이어 붙여 만든다(ㅣ+ㆍ = ㅏ, ㆍ+ㅡ = ㅗ …). 아래아 ㆍ·ㆍㆍ는 조합 중에만 보인다.
- 같은 자음 키를 cycle_seconds 안에 다시 누르면 그 그룹 안에서 다음 자음으로 바뀐다(ㄱ → ㅋ → ㄲ).
- 자모 토큰 목록을 유지하고, 매번 `compose`로 유니코드 한글 문자열을 만든다. 받침은 다음 모음이 오면
  다음 글자의 초성으로 옮겨 간다.
- 지움(backspace)은 마지막 키 입력 한 번을 되돌린다(완성 글자를 통째로 지우지 않는다).

손 동작 판정기(TouchGate·FistHold 등)는 common/gestures.py에 있다.
"""

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
class PreparedKey:
    key: str
    label: str
    target: Optional[Token] = None
    before: str = ""


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

    def preview_label(self, key, now):
        """문장을 바꾸지 않고 다음 입력에서 선택될 자모 한 개를 알려준다."""
        if key in CONSONANTS:
            group = CONSONANTS[key]
            if (self.tokens and self.tokens[-1].kind == "C" and self.last_key == key
                    and 0 <= now - self.last_time <= self.cycle_seconds):
                return group[(group.index(self.tokens[-1].value) + 1) % len(group)]
            return group[0]
        return {"1": "ㅣ", "2": "ㆍ", "3": "ㅡ", "SPACE": "공백", "COMMIT": "확정", "BACK": "지움"}.get(key, "")

    def prepare_key(self, key, now):
        label = self.preview_label(key, now)
        target = None
        if (key in CONSONANTS and self.tokens and self.tokens[-1].kind == "C"
                and self.last_key == key and 0 <= now - self.last_time <= self.cycle_seconds):
            target = self.tokens[-1]
        return PreparedKey(key, label, target, target.value if target is not None else "")

    def press(self, key, now, prepared=None):
        if prepared is not None and prepared.key != key:
            raise ValueError("선택한 키와 확정할 키가 다릅니다.")
        if key == "BACK":
            self.backspace()
            return
        if key not in CONSONANTS and key not in VOWEL_KEYS and key not in ("SPACE", "COMMIT", "ENTER"):
            raise ValueError("알 수 없는 키: " + key)
        self._remember()
        if key in CONSONANTS:
            selection = prepared if prepared is not None else self.prepare_key(key, now)
            if (selection.target is not None and self.tokens and self.tokens[-1] is selection.target
                    and selection.target.value == selection.before):
                self.tokens[-1].value = selection.label
            else:
                self.tokens.append(Token("C", selection.label, key))
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

    def put_symbol(self, value, replace=False):
        """문장부호·줄바꿈을 넣는다. replace면 마지막 기호를 바꾼다(꼬집기 순환). 지움 한 번에 한 단계 되돌린다."""
        self._remember()
        if replace and self.tokens and self.tokens[-1].kind == "B":
            self.tokens[-1].value = value
        else:
            self.tokens.append(Token("B", value))
        self.last_key, self.last_time = None, float("-inf")
