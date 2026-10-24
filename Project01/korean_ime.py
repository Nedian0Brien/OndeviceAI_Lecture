"""한글 입력기: 천지인 자판의 키 입력을 한글 문자열로 조합합니다 (표준 라이브러리만 사용, 카메라와 무관).

키 번호는 휴대폰 천지인 자판을 따릅니다.
  1 ㅣ   2 ㆍ   3 ㅡ
  4 ㄱㅋㄲ  5 ㄴㄹ  6 ㄷㅌㄸ
  7 ㅂㅍㅃ  8 ㅅㅎㅆ  9 ㅈㅊㅉ
  0 ㅇㅁ   SPACE 공백   COMMIT 조합 경계(공백 없음)   ENTER 줄바꿈   BACK 지움
  N ㄴ  L ㄹ   (꼬집기 자판 layout.py가 ㄴㄹ을 두 손가락으로 나눠 씁니다)

- 모음은 ㅣ·ㆍ·ㅡ 획을 이어 붙여 만듭니다(ㅣ+ㆍ = ㅏ, ㆍ+ㅡ = ㅗ …). 아래아 ㆍ·ㆍㆍ는 조합 중에만 보입니다.
- 같은 자음 키를 cycle_seconds 안에 다시 누르면 그 그룹 안에서 다음 자음으로 바뀝니다(ㄱ → ㅋ → ㄲ).
  글자가 하나인 키(N, L)는 순환하지 않고 같은 자음을 하나 더 넣습니다.
- 자모 토큰 목록을 유지하고, 매번 `compose`로 유니코드 한글 문자열을 만듭니다. 받침은 다음 모음이 오면
  다음 글자의 초성으로 옮겨 갑니다.
- 지움(backspace)은 마지막 키 입력 한 번을 되돌립니다(완성 글자를 통째로 지우지 않습니다).

손 동작 판정기(손 방향·주먹)는 common/gestures.py에 있습니다.
"""

from dataclasses import dataclass
from typing import Optional


# 같은 자음 키를 제한 시간 안에 다시 누르면 다음 자음으로 바뀜
CONSONANTS = {
    "4": "ㄱㅋㄲ", "5": "ㄴㄹ", "6": "ㄷㅌㄸ",
    "7": "ㅂㅍㅃ", "8": "ㅅㅎㅆ", "9": "ㅈㅊㅉ", "0": "ㅇㅁ",
    "N": "ㄴ", "L": "ㄹ",  # 꼬집기 자판(2026-10-23): ㄴ·ㄹ을 손가락 하나씩
}
VOWEL_KEYS = {"1": "i", "2": ".", "3": "-"}  # 모음 키 → 획 기호(i ㅣ, . ㆍ, - ㅡ)
# 획 기호 순서 → 모음. 조합 중인 획(ㆍ, ㆍㆍ)도 키로 두어 다음 획을 이어 붙일 수 있게 함
VOWELS = {
    "i": "ㅣ", "-": "ㅡ", ".": "ㆍ", "..": "ㆍㆍ",
    "i.": "ㅏ", "i..": "ㅑ", ".i": "ㅓ", "..i": "ㅕ",
    ".-": "ㅗ", "..-": "ㅛ", "-.": "ㅜ", "-..": "ㅠ",
    "i.i": "ㅐ", "i..i": "ㅒ", ".ii": "ㅔ", "..ii": "ㅖ",
    ".-i.": "ㅘ", ".-i.i": "ㅙ", ".-i": "ㅚ",
    "-..i": "ㅝ", "-..ii": "ㅞ", "-.i": "ㅟ", "-i": "ㅢ",
}
# 유니코드 한글 음절 표의 초성·중성·종성 순서(음절 코드 계산에 씀)
INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
MEDIALS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
FINALS = ("", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ",
          "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ",
          "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ")
# 받침 두 개 → 겹받침, 그리고 그 역(모음이 오면 겹받침의 뒤 자음만 다음 초성으로 옮김)
FINAL_PAIRS = {
    "ㄱㅅ": "ㄳ", "ㄴㅈ": "ㄵ", "ㄴㅎ": "ㄶ", "ㄹㄱ": "ㄺ", "ㄹㅁ": "ㄻ",
    "ㄹㅂ": "ㄼ", "ㄹㅅ": "ㄽ", "ㄹㅌ": "ㄾ", "ㄹㅍ": "ㄿ", "ㄹㅎ": "ㅀ", "ㅂㅅ": "ㅄ",
}
SPLIT_FINALS = {value: tuple(key) for key, value in FINAL_PAIRS.items()}


@dataclass
class Token:
    """입력된 자모 하나. kind: "C" 자음 · "V" 모음(획 기호 순서) · "B" 경계(공백·줄바꿈·기호·확정 "")."""
    kind: str
    value: str
    key: str = ""  # 이 토큰을 만든 키(자음 순환 판단에 씀)


@dataclass
class PreparedKey:
    """다음 키 입력으로 들어갈 자모를 미리 정해 둔 값입니다. target이 있으면 그 토큰을 label로 바꿉니다(순환)."""
    key: str
    label: str  # 들어갈 자모
    target: Optional[Token] = None  # 순환으로 바꿀 마지막 토큰
    before: str = ""  # 바꾸기 전 target 값(그 사이 토큰이 바뀌었으면 순환하지 않음)


@dataclass
class UndoEdit:
    """키 입력 한 번을 되돌리는 기록입니다. cleared가 있으면 토큰 목록 전체를 그것으로 되돌립니다."""
    length: int  # 입력 전 토큰 수
    last: Optional[Token]  # 입력 전 마지막 토큰의 복사본
    cleared: Optional[list] = None  # 전체 지움·자모 지움 전의 토큰 목록


def compose(tokens):
    """자모 토큰을 유니코드 한글로 조합하고, 모음 앞의 받침을 다음 초성으로 옮깁니다."""
    output = []
    initial = medial = final = ""  # 조합 중인 글자의 초성·중성·종성

    def flush():
        # 조합 중인 글자를 출력에 내보내고 비움
        nonlocal initial, medial, final
        if initial and medial:  # 초성+중성이 있음: 음절 코드 = 0xAC00 + (초성×21 + 중성)×28 + 종성
            code = 0xAC00 + (INITIALS.index(initial) * 21 + MEDIALS.index(medial)) * 28
            output.append(chr(code + FINALS.index(final)))
        else:  # 초성만 또는 중성만 있음: 낱자모 그대로
            output.append(initial or medial)
        initial = medial = final = ""

    # 토큰을 앞에서부터 읽으며 초성 → 중성 → 종성 자리를 채우고, 자리가 맞지 않으면 앞 글자를 내보냄
    for token in tokens:
        value = token.value
        if token.kind == "C":  # 자음 토큰
            if initial and medial:
                # 초성+중성 뒤의 자음: 받침으로 붙이거나, 겹받침으로 합치거나, 새 글자의 초성으로 시작함
                if not final and value in FINALS:
                    final = value
                elif final + value in FINAL_PAIRS:
                    final = FINAL_PAIRS[final + value]
                else:
                    flush()
                    initial = value
            else:
                # 모음 없이 자음이 이어짐: 앞 글자를 내보내고 새 초성으로 시작함
                flush()
                initial = value
        elif token.kind == "V":  # 모음 토큰
            vowel = VOWELS[value]
            if vowel not in MEDIALS:  # 아직 조합 중인 아래아는 임시로 표시함
                flush()
                output.append(vowel)
            elif initial and medial and final:
                # 받침 뒤 모음: 받침(겹받침이면 뒤 자음)을 다음 글자의 초성으로 옮김(각 + ㅏ → 가가)
                remaining, carried = SPLIT_FINALS.get(final, ("", final))
                final = remaining
                flush()
                initial, medial = carried, vowel
            elif initial and not medial:
                medial = vowel  # 초성 뒤 모음: 중성으로 붙임
            else:
                flush()
                medial = vowel  # 초성이 없거나 이미 중성이 있음: 앞 글자를 내보내고 모음으로 새 글자 시작
        else:  # 경계 토큰
            flush()
            output.append(value)  # 확정 경계는 빈 문자열, 공백/줄바꿈은 그대로 출력
    flush()  # 마지막으로 조합 중이던 글자
    return "".join(output)


class CheonjiinIME:
    """천지인 키 입력을 토큰 목록에 쌓고 text로 조합 결과를 돌려주는 입력기입니다."""

    def __init__(self, cycle_seconds=0.85):
        self.cycle_seconds = cycle_seconds  # 같은 자음 키를 이 시간(초) 안에 다시 누르면 순환
        self.tokens = []  # 입력된 자모 토큰
        self.history = []  # 되돌리기 기록(UndoEdit)
        self.last_key = None  # 직전 키(자음 순환 판단)
        self.last_time = float("-inf")  # 직전 키 시각

    @property
    def text(self):
        """지금까지의 입력을 조합한 한글 문자열입니다."""
        return compose(self.tokens)

    def _remember(self):
        # 키 하나는 마지막 토큰만 수정하거나 새 토큰 하나를 추가함
        # 전체 문장을 매번 복사하지 않아 긴 입력에서도 history 크기는 선형임
        last = self.tokens[-1] if self.tokens else None
        self.history.append(UndoEdit(len(self.tokens), Token(last.kind, last.value, last.key) if last else None))

    def preview_label(self, key, now):
        """문장을 바꾸지 않고 다음 입력에서 선택될 자모 한 개를 알려줍니다."""
        if key in CONSONANTS:  # 자음 키: 순환 조건이면 다음 자음, 아니면 그룹 첫 자음
            group = CONSONANTS[key]
            # 순환 조건: 글자가 둘 이상인 그룹, 마지막 토큰이 이 키의 자음, 제한 시간 안에 다시 누름
            if (len(group) > 1 and self.tokens and self.tokens[-1].kind == "C" and self.last_key == key
                    and 0 <= now - self.last_time <= self.cycle_seconds):
                return group[(group.index(self.tokens[-1].value) + 1) % len(group)]
            return group[0]
        return {"1": "ㅣ", "2": "ㆍ", "3": "ㅡ", "SPACE": "공백", "COMMIT": "확정", "BACK": "지움"}.get(key, "")

    def prepare_key(self, key, now):
        """key를 지금 누르면 들어갈 자모와, 순환이면 바꿀 마지막 토큰을 PreparedKey로 돌려줍니다."""
        label = self.preview_label(key, now)
        target = None
        if (key in CONSONANTS and len(CONSONANTS[key]) > 1 and self.tokens and self.tokens[-1].kind == "C"
                and self.last_key == key and 0 <= now - self.last_time <= self.cycle_seconds):
            target = self.tokens[-1]  # preview_label과 같은 순환 조건: 마지막 토큰을 바꿀 대상으로 둠
        return PreparedKey(key, label, target, target.value if target is not None else "")

    def press(self, key, now, prepared=None):
        """키 하나를 입력합니다. prepared를 주면 미리 정한 자모(꼬집기 순환)를 그대로 넣습니다."""
        # 입력 검증: 미리 정한 키와 다른 키, 자판에 없는 키는 오류
        if prepared is not None and prepared.key != key:
            raise ValueError("선택한 키와 확정할 키가 다릅니다.")
        if key == "BACK":  # 지움 키는 기록을 남기지 않고 되돌리기만 함
            self.backspace()
            return
        if key not in CONSONANTS and key not in VOWEL_KEYS and key not in ("SPACE", "COMMIT", "ENTER"):
            raise ValueError("알 수 없는 키: " + key)
        self._remember()
        if key in CONSONANTS:  # 자음 키
            selection = prepared if prepared is not None else self.prepare_key(key, now)
            # 순환: 바꿀 토큰이 여전히 마지막 토큰이고 값도 그대로면 제자리에서 바꿈. 아니면 새 자음 토큰
            if (selection.target is not None and self.tokens and self.tokens[-1] is selection.target
                    and selection.target.value == selection.before):
                self.tokens[-1].value = selection.label
            else:
                self.tokens.append(Token("C", selection.label, key))
        elif key in VOWEL_KEYS:  # 모음 획 키
            atom = VOWEL_KEYS[key]
            # 앞 모음 토큰에 획을 이어 붙여 아는 모음이 되면 이어 붙임(ㅣ + ㆍ → ㅏ). 아니면 새 모음 토큰
            if (self.tokens and self.tokens[-1].kind == "V"
                    and self.tokens[-1].value + atom in VOWELS):
                self.tokens[-1].value += atom
            else:
                self.tokens.append(Token("V", atom, key))
        else:  # 공백·확정·줄바꿈: 경계 토큰
            self.tokens.append(Token("B", {"SPACE": " ", "COMMIT": "", "ENTER": "\n"}[key]))
        self.last_key, self.last_time = key, now

    def backspace(self):
        """마지막 한 번의 키 입력을 취소합니다 (완성 글자를 통째로 지우지 않습니다)."""
        if self.history:  # 되돌릴 기록이 있을 때만
            edit = self.history.pop()
            if edit.cleared is not None:
                self.tokens = edit.cleared  # 전체 지움·자모 지움을 되돌림
            else:
                del self.tokens[edit.length:]  # 새로 붙은 토큰을 빼고, 고친 마지막 토큰을 원래 값으로 되돌림
                if edit.last is not None:
                    self.tokens[-1] = edit.last
        self.last_key, self.last_time = None, float("-inf")  # 되돌린 뒤에는 순환을 이어 가지 않음

    def delete_char(self):
        """마지막 자모 하나(또는 공백·기호 하나)를 통째로 지웁니다.

        backspace는 키 입력 한 번을 되돌립니다(ㅇ→ㅁ 순환을 ㅇ으로, ㅏ의 ㆍ만 빼 ㅣ로). 꼬집기 입력기의 지움은
        보이는 자모를 지우는 것으로 기대해서(2026-10-23 사용자 시험) 따로 둡니다. 토큰 하나가 자모 하나입니다
        (순환한 자음은 토큰을 제자리에서 바꾸고, 획을 이어 만든 모음은 한 토큰에 모입니다). 감 → 가 → ㄱ.
        화면에 보이지 않는 토큰(확정 경계 "")은 함께 뺍니다.
        지운 뒤에도 backspace로 되살릴 수 있게 history에 남깁니다.
        """
        text = self.text
        if not text:  # 보이는 글자가 없으면 지울 것이 없음
            return
        before = [Token(token.kind, token.value, token.key) for token in self.tokens]  # 순환은 토큰을 제자리에서 바꿈
        # 조합 결과가 달라질 때까지 뒤에서부터 토큰을 뺌(보이지 않는 경계 토큰은 함께 빠짐)
        while self.tokens:
            self.tokens.pop()
            if compose(self.tokens) != text:
                break
        self.history.append(UndoEdit(0, None, before))
        self.last_key, self.last_time = None, float("-inf")

    def clear(self):
        """입력을 모두 지웁니다. backspace 한 번으로 되살릴 수 있게 지우기 전 토큰을 history에 남깁니다."""
        if self.tokens:  # 빈 입력을 지운 것은 기록하지 않음
            self.history.append(UndoEdit(0, None, self.tokens))
        self.tokens = []
        self.last_key, self.last_time = None, float("-inf")

    def put_symbol(self, value, replace=False):
        """문장부호·줄바꿈을 넣습니다. replace면 마지막 기호를 바꿉니다(꼬집기 순환). 지움 한 번에 한 단계 되돌립니다."""
        self._remember()
        if replace and self.tokens and self.tokens[-1].kind == "B":  # 순환: 마지막 기호를 다음 기호로 바꿈
            self.tokens[-1].value = value
        else:  # 새 기호 토큰
            self.tokens.append(Token("B", value))
        self.last_key, self.last_time = None, float("-inf")
