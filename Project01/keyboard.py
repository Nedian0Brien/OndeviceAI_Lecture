"""양손의 핀치와 주먹 동작을 판정해 천지인 방식으로 한글을 입력하는 모듈"""

from dataclasses import dataclass
import math
import statistics


# --- 천지인 한글 조합 ---

CONSONANTS = {
    "4": "ㄱㅋㄲ", "6": "ㄷㅌㄸ", "7": "ㅂㅍㅃ", "8": "ㅅㅎㅆ", "9": "ㅈㅊㅉ", "0": "ㅇㅁ",
    "N": "ㄴ", "L": "ㄹ",
}
# 천지인 획(i는 ㅣ, .은 ㆍ, -는 ㅡ)을 입력한 순서로 이어 붙인 문자열과 그 모음
# 조합 중인 ㆍ와 ㆍㆍ도 넣어 두어 다음 획을 이어 붙일 수 있게 함
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
    """입력된 자모 하나를 나타내는 클래스"""
    kind: str  # 자음 "C", 모음 "V", 공백·문장부호 "B"
    value: str


def compose(tokens):
    """자모 토큰을 한글 음절로 조합하는 함수"""
    output = []
    initial = medial = final = ""

    def flush():
        nonlocal initial, medial, final
        if initial and medial:  # 유니코드 음절 코드는 0xAC00 + (초성×21 + 중성)×28 + 종성임
            code = 0xAC00 + (INITIALS.index(initial) * 21 + MEDIALS.index(medial)) * 28
            output.append(chr(code + FINALS.index(final)))
        else:
            output.append(initial or medial)
        initial = medial = final = ""

    for token in tokens:
        value = token.value
        if token.kind == "C":
            if initial and medial and not final and value in FINALS:
                final = value
            elif initial and medial and final + value in FINAL_PAIRS:
                final = FINAL_PAIRS[final + value]
            else:
                flush()
                initial = value
        elif token.kind == "V":
            vowel = VOWELS[value]
            if vowel not in MEDIALS:  # 아직 조합 중인 아래아는 낱자로 보여 줌
                flush()
                output.append(vowel)
            elif initial and medial and final:  # 받침 뒤에 모음이 오면 받침을 다음 글자의 초성으로 옮김(각 + ㅏ = 가가)
                final, carried = SPLIT_FINALS.get(final, ("", final))
                flush()
                initial, medial = carried, vowel
            elif initial and not medial:
                medial = vowel
            else:
                flush()
                medial = vowel
        else:
            flush()
            output.append(value)
    flush()
    return "".join(output)


class CheonjiinIME:
    """입력된 자모를 쌓아 두고 조합한 한글 문자열을 돌려주는 클래스"""

    def __init__(self):
        self.tokens = []

    @property
    def text(self):
        return compose(self.tokens)

    def put_consonant(self, value, replace=None):
        """자음을 넣는 메서드"""
        # 순환 중이면 마지막 자음(replace)을 다음 자음(value)으로 바꿈
        if replace is not None and self.tokens and self.tokens[-1].value == replace:
            self.tokens[-1].value = value
        else:
            self.tokens.append(Token("C", value))

    def put_stroke(self, atom):
        """천지인 획을 넣는 메서드"""
        # 앞 모음에 이어 붙여 모음이 되면 붙이고(ㅣ에 ㆍ를 붙이면 ㅏ), 아니면 새 모음으로 넣음
        if self.tokens and self.tokens[-1].kind == "V" and self.tokens[-1].value + atom in VOWELS:
            self.tokens[-1].value += atom
        else:
            self.tokens.append(Token("V", atom))

    def put_symbol(self, value, replace=False):
        if replace and self.tokens and self.tokens[-1].kind == "B":
            self.tokens[-1].value = value
        else:
            self.tokens.append(Token("B", value))

    def delete_char(self):
        """화면에 보이는 마지막 자모 하나를 지우는 메서드"""
        # 감을 지우면 가, 가를 지우면 ㄱ이 됨
        text = self.text
        if not text:
            return
        while self.tokens:
            self.tokens.pop()
            if compose(self.tokens) != text:
                break

    def clear(self):
        self.tokens = []


# --- 자판 ---

HANDS = ("Left", "Right")
FACES = ("palm", "back")
# 손과 손 방향마다 검지·중지·약지·소지 순서로 놓인 키
KEYS = {
    ("Left", "palm"): ("7", "L", "8", "9"),
    ("Right", "palm"): ("4", "N", "6", "0"),
    ("Left", "back"): ("VOWEL", "SPACE", "COMMIT", "PUNCT"),
    ("Right", "back"): ("COMMIT_SPACE", None, None, None),  # 오른손 손등은 손가락이 서로 가려서 검지만 씀
}
PUNCTUATION = (".", ",", "?", "!", "\n")
EDIT_LABELS = {"SPACE": "공백", "COMMIT": "확정", "COMMIT_SPACE": "공백", "VOWEL": "모음"}
CYCLE_SHOWN = 3  # 손가락 위에 미리 보여 주는 순환 글자 수
# 모음을 핀치한 채 손 크기(손목에서 중지 뿌리까지)의 이 비율보다 멀리 움직이면 끈 것으로 봄
# 손 크기가 약 9cm면 약 2.3cm임
DRAG_MIN = 0.25
DRAG_STROKES = {None: ".", "h": "-", "v": "i"}  # 끌지 않으면 ㆍ, 가로로 끌면 ㅡ, 세로로 끌면 ㅣ


def group_of(key):
    return PUNCTUATION if key == "PUNCT" else CONSONANTS[key]


def is_group_key(key):
    """같은 손가락을 다시 핀치했을 때 다음 글자로 순환하는 키인지 판별하는 함수"""
    # ㄴ과 ㄹ은 글자가 하나라 순환하지 않음
    return key == "PUNCT" or len(CONSONANTS.get(key, "")) > 1


def key_label(key):
    if key == "PUNCT":
        return ".,?!"
    return EDIT_LABELS.get(key) or CONSONANTS[key]


def cycle_queue(typer, hand, finger):
    """손끝에 보여 줄 글자 목록을 돌려주는 함수"""
    # 지금 핀치하면 나올 글자가 맨 앞이고 그 뒤는 순환 순서임
    face = typer.face(hand) or "palm"
    key = KEYS[(hand, face)][finger]
    if key is None:
        return []
    if key == "COMMIT_SPACE":
        return ["확정" if typer.pending is not None else "공백"]
    if not is_group_key(key):
        return [key_label(key)]
    start, pending = 0, typer.pending
    if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, face):
        start = pending.count
    group = ["줄" if char == "\n" else char for char in group_of(key)]
    return [group[(start + i) % len(group)] for i in range(min(len(group), CYCLE_SHOWN))]


# --- 손 방향·주먹 판정 ---

# 손 방향 값이 0.25를 넘으면 손바닥(음수면 손등)으로 보고, 새 방향은 0.15초 이어져야 인정함
FACING_THRESHOLD, FACING_DWELL = 0.25, 0.15
FIST_HOLD = 0.18  # 주먹을 이 시간(초) 동안 쥐면 한 글자 지움
FIST_REPEAT = 0.5  # 주먹을 계속 쥐고 있으면 이 간격(초)마다 한 글자씩 더 지움
# 주먹을 연달아 쥘 때 손을 반쯤만 펴도 다음 주먹을 받게 함. 주먹이 아닌 상태가 이 시간(초) 이어지면 됨
FIST_REARM = 0.15


class FistHold:
    """주먹을 FIST_HOLD초 동안 쥐었는지 판정하는 클래스"""

    def __init__(self):
        self.lost()

    def lost(self):
        self.armed = False  # 손을 편 뒤에 True가 됨
        self.since = None
        self.next_repeat = None
        self.progress = 0.0
        self.relaxed_since = None

    def update(self, pose, now, relaxed):
        self.progress = 0.0
        if not relaxed:
            self.relaxed_since = None
        elif self.relaxed_since is None:
            self.relaxed_since = now
        elif now - self.relaxed_since >= FIST_REARM:
            self.armed, self.since, self.next_repeat = True, None, None
        if pose == "open":
            self.armed, self.since, self.next_repeat = True, None, None
            return False
        if self.next_repeat is not None:  # 이미 한 번 지운 뒤 계속 쥐고 있음
            self.progress = min(1.0, 1 - (self.next_repeat - now) / FIST_REPEAT)
            if pose == "fist" and now >= self.next_repeat:
                self.next_repeat = now + FIST_REPEAT
                return True
            return False
        if pose != "fist" or not self.armed:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        self.progress = min(1.0, (now - self.since) / FIST_HOLD)
        if self.progress >= 1.0:
            self.armed, self.since = False, None
            self.next_repeat = now + FIST_REPEAT
            return True
        return False


class Orientation:
    """손바닥(palm), 손등(back), 회전 중(turning) 가운데 지금 손 방향을 판정하는 클래스"""

    def __init__(self):
        self.lost()

    def lost(self):
        self.state = None  # 인정한 방향
        self.raw = None  # 이번 프레임 값만으로 정한 방향
        self.since = None

    def update(self, facing, now):
        if facing is None:
            self.lost()
            return
        # 이미 인정한 방향은 값이 기준의 절반으로 내려갈 때까지 유지함
        if self.state == "palm" and facing > FACING_THRESHOLD / 2:
            return
        if self.state == "back" and facing < -FACING_THRESHOLD / 2:
            return
        raw = "palm" if facing > FACING_THRESHOLD else ("back" if facing < -FACING_THRESHOLD else "turning")
        if raw != self.raw:
            self.raw, self.since = raw, now
        if now - self.since >= FACING_DWELL:  # 관절 값이 한 프레임만 튀어도 방향이 바뀌지 않게 기다림
            self.state = raw
        elif self.state is None:
            self.state = "turning"


# --- 핀치 판정 ---
# 아래 거리는 엄지 끝에서 손가락 끝마디까지의 거리를 손 크기로 나눈 값임
# 모든 사람에게 같은 값을 쓰고 사람마다 보정하지 않음

PINCH_ENTER = 0.30  # 접촉 후보가 되는 거리 상한
PINCH_EXIT = 0.38  # 이 거리보다 벌리면 언제나 뗀 것으로 봄
PINCH_MARGIN = 0.03  # 중지를 핀치하면 약지도 함께 가까워지므로, 고른 손끝이 다른 손끝보다 이만큼 더 가까워야 함
# 최근 기준값보다 이만큼 다가온 손끝만 핀치 후보로 봄. 쉬는 자세에서도 엄지가 검지 옆에 있기 때문임
# 같은 손가락을 빠르게 반복하면 0.16~0.18만 다가와서 이 값을 넘지 않게 정함(2026-10-20)
PINCH_DROP = 0.15
PINCH_WINDOW = 0.5  # 기준값은 최근 이 시간(초) 동안의 가장 먼 거리임
PINCH_MIN_CONTACT = 0.10  # 이보다 짧은 접촉(초)은 엄지가 스친 것으로 봄. 실제 핀치는 모두 0.13초 이상이었음
PINCH_SHALLOW = 0.20  # 손끝이 이보다 얕게 다가왔다 벌어지면 엄지가 그 옆을 지나 다른 손가락으로 가는 중으로 봄
# 가장 가까웠던 거리보다 0.12 넘게 벌어진 프레임이 2번 이어지면 뗀 것으로 봄. 한 프레임 튄 값은 무시함
RELEASE_RISE, RELEASE_FRAMES = 0.12, 2
# 방금 뗀 손가락은 덜 벌린 채 다시 핀치하므로, 1초 안이면 0.08만 다가와도 후보로 봄
REPRESS_DROP, REPRESS_WINDOW = 0.08, 1.0
# 핀치하는 손가락 말고 다른 손가락이 이 수만큼 굽으면 주먹을 쥐는 중으로 보고 버림
# 소지를 핀치하면 중지와 약지가 함께 굽으므로 2가 아니라 3임
SPOIL_CURLED = 3
# 검지로 잡힌 접촉을 더 굽은 다른 손가락으로 바꾸는 기준. 정답 접촉 90개에서 맞은 수가 78개에서 83개로 늘었음
INDEX_TIE, FLEX_GAIN, FLEX_BASE = 0.05, 10.0, 0.3
# 다른 손가락 입력 뒤 0.25초 안에 시작했거나 검지가 5° 미만으로 굽은 오른손 검지 접촉은 엄지가 지나간 것으로 봄
INDEX_REPASS, INDEX_FLEX_MIN = 0.25, 5.0


class FingerPinch:
    """손 하나에서 엄지가 어느 손끝을 핀치했다 뗐는지 판정하는 클래스"""

    def __init__(self):
        self.spoil_curled = True  # 손등에서는 SPOIL_CURLED 규칙을 쓰지 않음
        self.lost()

    def lost(self):
        self.history = []  # 기준값을 구할 최근 (시각, 거리) 기록
        self.flexes = []  # 접촉 직전 굽힘을 구할 최근 (시각, 굽힘) 기록
        self.flex_base = None
        self.flex_top = None
        self.last_tap = None  # 직전 입력의 (손가락, 뗀 시각)
        self.released = None  # 이번 프레임에 뗀 접촉의 정보
        self.active = None  # 접촉 중인 손가락. 0이 검지, 3이 소지임
        self.since = None
        self.spoiled = False
        self.base = (1.0,) * 4
        self.low = [1.0] * 4  # 접촉하는 동안 손가락마다 가장 가까웠던 거리
        self.opened = 0
        self.closed = False

    @staticmethod
    def closed_pose(signal):
        """주먹 자세인지 판별하는 메서드"""
        # 주먹을 쥐면 엄지가 손가락 위에 놓여 거리가 가까워지므로 핀치를 보지 않음
        return signal.pose == "fist" or all(signal.curled)

    def release_level(self):
        return min(PINCH_EXIT, self.low[self.active] + RELEASE_RISE)

    def release_progress(self, ratios):
        """고른 손끝을 뗀 정도를 0에서 1 사이 값으로 돌려주는 메서드"""
        if self.active is None:
            return 0.0
        low, level = self.low[self.active], self.release_level()
        return min(1.0, max(0.0, (ratios[self.active] - low) / max(level - low, 1e-6)))

    def progress(self, ratios):
        """손가락마다 기준값보다 다가온 정도를 0에서 1 사이 값으로 돌려주는 메서드"""
        if self.closed:
            return [0.0] * 4
        if self.active is not None:
            base = self.base
        elif self.history:
            base = [max(row[j] for _, row in self.history) for j in range(4)]
        else:
            base = ratios
        return [0.0 if value > PINCH_EXIT else min(1.0, max(0.0, (top - value) / PINCH_DROP))
                for top, value in zip(base, ratios)]

    @staticmethod
    def _candidates(base, values):
        return sorted((j for j in range(4) if base[j] - values[j] >= PINCH_DROP and values[j] <= PINCH_ENTER),
                      key=values.__getitem__)

    def _index_overlap(self, finger):
        """검지로 잡힌 접촉의 실제 손가락을 돌려주는 메서드"""
        # 엄지 옆면이 검지를 함께 누르면 검지 거리도 내려가므로, 더 굽은 손가락으로 바꿈
        if finger != 0 or self.flex_base is None or self.flex_top is None:
            return finger
        gain = [top - base for top, base in zip(self.flex_top, self.flex_base)]
        others = [j for j in (1, 2, 3)
                  if self.low[j] <= self.low[0] + INDEX_TIE and gain[j] >= gain[0] + FLEX_GAIN]
        return min(others, key=self.low.__getitem__) if others else finger

    def index_sweep(self):
        """방금 뗀 검지 접촉이 엄지가 검지 옆을 지나간 것인지 판별하는 메서드"""
        info = self.released
        if info is None:
            return False
        previous = info["previous"]
        if previous is not None and previous[0] != 0 and info["start"] - previous[1] < INDEX_REPASS:
            return True  # 다른 손가락을 입력한 직후 엄지가 돌아오며 검지를 지나감
        return info["gain"] is not None and info["gain"][0] < INDEX_FLEX_MIN  # 검지가 거의 굽지 않았으면 스친 것임

    def undo_tap(self):
        if self.released is not None:
            self.last_tap = self.released["previous"]

    def update(self, signal, now):
        if signal is None:
            self.lost()
            return None
        if self.closed_pose(signal):
            self.lost()
            self.closed = True
            return None
        self.closed = False
        self.released = None
        if self.active is None:
            self._start_contact(signal, now)
            return None
        return self._update_contact(signal, now)

    def _start_contact(self, signal, now):
        ratios = signal.ratios
        self.history = [(t, row) for t, row in self.history if now - t <= PINCH_WINDOW] + [(now, ratios)]
        earlier = [flex for t, flex in self.flexes if now - t <= FLEX_BASE]
        if signal.flex is not None:
            self.flexes = [(t, flex) for t, flex in self.flexes if now - t <= FLEX_BASE] + [(now, signal.flex)]
        base = tuple(max(row[j] for _, row in self.history) for j in range(4))
        candidates = self._candidates(base, ratios)
        if self.last_tap is not None and now - self.last_tap[1] <= REPRESS_WINDOW:
            again = self.last_tap[0]
            if again not in candidates and base[again] - ratios[again] >= REPRESS_DROP and ratios[again] <= PINCH_ENTER:
                candidates = sorted(candidates + [again], key=ratios.__getitem__)
        if not candidates:
            return
        best = candidates[0]
        if min(ratios[j] for j in range(4) if j != best) - ratios[best] < PINCH_MARGIN:
            return
        self.active, self.since, self.spoiled, self.opened = best, now, False, 0
        self.base, self.low = base, list(ratios)
        if signal.flex is not None:
            earlier = earlier or [signal.flex]
            self.flex_base = tuple(sum(f[j] for f in earlier) / len(earlier) for j in range(4))
            self.flex_top = list(signal.flex)
        self._check(signal)

    def _update_contact(self, signal, now):
        ratios = signal.ratios
        self.low = [min(a, b) for a, b in zip(self.low, ratios)]
        if self.flex_top is not None and signal.flex is not None:
            self.flex_top = [max(a, b) for a, b in zip(self.flex_top, signal.flex)]
        candidates = self._candidates(self.base, self.low)
        # 더 가까이 온 손끝으로 옮김. 떼는 중에는 엄지가 옆 손가락으로 미끄러진 것이므로 옮기지 않음
        if candidates and self.low[self.active] - self.low[candidates[0]] >= PINCH_MARGIN and self.opened == 0:
            self.active = candidates[0]
        # 얕게 다가왔다 벌어지는 손끝이면 지금 닿아 있는 더 가까운 손끝으로 옮김
        if ratios[self.active] > self.release_level() and self.low[self.active] > PINCH_SHALLOW:
            closer = [j for j in candidates if j != self.active and ratios[j] <= PINCH_ENTER
                      and ratios[self.active] - ratios[j] >= PINCH_MARGIN]
            if closer:
                self.active = min(closer, key=ratios.__getitem__)
        self._check(signal)
        if ratios[self.active] <= self.release_level():
            self.opened = 0
            return None
        self.opened += 1
        if self.opened < RELEASE_FRAMES:
            return None
        spoiled, short = self.spoiled, now - self.since < PINCH_MIN_CONTACT
        tap = self._emit(now)
        self._end_contact()
        if spoiled or short:
            self.undo_tap()
            return None
        return tap

    def _check(self, signal):
        """주먹을 쥐는 도중인 접촉을 버릴 것으로 표시하는 메서드"""
        others = sum(curled for index, curled in enumerate(signal.curled) if index != self.active)
        if signal.pose == "fist" or (self.spoil_curled and others >= SPOIL_CURLED):
            self.spoiled = True

    def _emit(self, now):
        finger = self._index_overlap(self.active)
        gain = None
        if self.flex_base is not None and self.flex_top is not None:
            gain = [top - base for top, base in zip(self.flex_top, self.flex_base)]
        self.released = {"start": self.since, "gain": gain, "previous": self.last_tap}
        self.last_tap = (finger, now)
        return finger, now - self.since

    def _end_contact(self):
        """접촉을 끝내는 메서드"""
        # 다음 핀치가 뗀 뒤의 거리를 기준으로 삼도록 기준값 기록도 비움
        self.active = self.since = None
        self.history, self.flexes = [], []
        self.flex_base = self.flex_top = None
        self.spoiled, self.low, self.opened = False, [1.0] * 4, 0


# --- 양손 입력기 ---

# 접촉의 처음 3프레임과 마지막 3프레임에서 손 방향이 같아야 입력함
# 손을 뒤집으며 뗀 핀치 5개 중 4개를 막았음(2026-10-23)
STEADY_FRAMES = 3
# 손바닥이 소지 쪽으로 이 각도보다 기울면 엄지가 가려져 MediaPipe가 검지로 잘못 읽음(8번 중 7번)
OCCLUDED_ROLL = -25.0
# 핀치하는 도중 손을 이 시간(초)보다 짧게 놓치면 접촉을 이어서 판정함
# 기록 24개를 다시 재생했을 때 놓쳤던 입력 20개가 되살아났음(2026-10-24)
HAND_GAP = 0.4
ECHO_NAMES = {" ": "공백", "\n": "줄바꿈"}


@dataclass
class Pending:
    """아직 확정하지 않은 후보 글자를 나타내는 클래스"""
    # 같은 손, 같은 손가락, 같은 방향으로 다시 핀치하면 다음 글자로 순환함
    key: str
    hand: str
    finger: int
    face: str
    count: int  # 지금까지 핀치한 횟수. 다음 글자는 group[count % len(group)]임
    label: str  # 지금 입력 칸에 있는 글자


class HandState:
    """손 하나의 판정기와 이번 접촉 동안 모은 신호를 묶은 클래스"""

    def __init__(self):
        self.pinch = FingerPinch()
        self.orientation = Orientation()
        self.fist = FistHold()
        self.signal = None
        self.seen = None  # 마지막으로 손이 보인 시각(HAND_GAP)
        self.held = []  # 이번 접촉 동안의 프레임별 신호


class PinchTyper:
    """양손 신호를 판정해 한글을 입력하는 클래스"""

    def __init__(self):
        self.ime = CheonjiinIME()
        self.hands = {label: HandState() for label in HANDS}
        self.pending = None
        self.pinches = []  # 이번 프레임에 입력으로 인정한 핀치의 (손, 손가락, 방향) 목록
        self.echo = None  # 마지막 입력의 (일련번호, 손, 글자)

    def face(self, hand):
        state = self.hands[hand].orientation.state
        return state if state in FACES else None

    def press_finger(self, hand, finger, face="palm", drag=None):
        """핀치 한 번을 자판의 키로 바꿔 입력하는 메서드"""
        # drag는 모음을 끈 방향이며 None(끌지 않음), "h"(가로), "v"(세로) 중 하나임
        events = self._press(hand, finger, face, drag)
        shown = [ECHO_NAMES.get(value, value) if kind == "input" else "확정"
                 for kind, value in events if kind in ("input", "commit")]
        if shown:
            self.echo = ((self.echo[0] + 1) if self.echo else 1, hand, shown[-1])
        return events

    def _press(self, hand, finger, face, drag):
        key = KEYS[(hand, face)][finger]
        if key is None:
            return []
        if key == "COMMIT_SPACE":
            key = "COMMIT" if self.pending is not None else "SPACE"
        if key == "VOWEL":
            atom = DRAG_STROKES[drag]
            self.pending = None
            self.ime.put_stroke(atom)
            return [("input", VOWELS[atom])]
        if key == "COMMIT":
            self.pending = None
            return [("commit", None)]
        if key == "SPACE":
            self.pending = None
            self.ime.put_symbol(" ")
            return [("input", " ")]
        if not is_group_key(key):  # ㄴ·ㄹ은 후보로 두지 않고 바로 넣음
            self.pending = None
            self.ime.put_consonant(CONSONANTS[key])
            return [("input", CONSONANTS[key])]
        pending = self.pending
        if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, face):
            group = group_of(key)
            label = group[pending.count % len(group)]
            self._put(key, label, replace=pending.label)
            pending.count, pending.label = pending.count + 1, label
            return [("input", label)]
        label = group_of(key)[0]
        self._put(key, label)
        self.pending = Pending(key, hand, finger, face, 1, label)
        return [("input", label)]

    def _put(self, key, value, replace=None):
        if key == "PUNCT":
            self.ime.put_symbol(value, replace=replace is not None)
        else:
            self.ime.put_consonant(value, replace)

    def make_fist(self):
        self.pending = None
        self.ime.delete_char()
        return [("delete", None)]

    @staticmethod
    def pinch_path(held, finger):
        """접촉 동안의 핀치 지점 목록과 손 크기 중앙값을 돌려주는 메서드"""
        # 핀치 지점은 엄지 끝과 손끝의 가운데임
        frames = [s for s in held if s.tips]
        points = [((s.tips[0][0] + s.tips[finger + 1][0]) / 2, (s.tips[0][1] + s.tips[finger + 1][1]) / 2)
                  for s in frames]
        return points, (statistics.median(s.size for s in frames) if frames else 0.0)

    @staticmethod
    def drag_of_path(points, size):
        """핀치 지점의 이동으로 모음 끌기 방향을 정하는 메서드"""
        # 처음 지점에서 가장 멀리 간 이동을 보고 None(끌지 않음), "h"(가로), "v"(세로) 중 하나를 고름
        if not points:
            return None
        ox, oy = points[0]
        dx, dy = max(((x - ox, y - oy) for x, y in points), key=lambda d: math.hypot(*d))
        if size <= 0 or math.hypot(dx, dy) < DRAG_MIN * size:
            return None
        return "h" if abs(dx) >= abs(dy) else "v"

    def drag_view(self, hand):
        """모음을 핀치하는 중인 손의 끌기 궤적과 방향을 돌려주는 메서드"""
        state, face = self.hands[hand], self.face(hand)
        finger = state.pinch.active
        if finger is None or face is None or KEYS[(hand, face)][finger] != "VOWEL":
            return None
        points, size = self.pinch_path(state.held, finger)
        if not points:
            return None
        drag = self.drag_of_path(points, size)
        pointer = None  # 끈 방향의 각도. 12시가 0°이고 시계 방향으로 잼
        if drag is not None:
            (ox, oy), (fx, fy) = points[0], max(points, key=lambda q: math.dist(q, points[0]))
            pointer = (90 if fx >= ox else 270) if drag == "h" else (180 if fy >= oy else 0)
        return {"finger": finger, "points": points, "size": size, "drag": drag, "pointer": pointer}

    @staticmethod
    def steady(held, face):
        sign = 1 if face == "palm" else -1
        facings = [signal.facing for signal in held]
        return bool(facings) and all(sign * statistics.median(part) >= FACING_THRESHOLD
                                     for part in (facings[:STEADY_FRAMES], facings[-STEADY_FRAMES:]))

    @staticmethod
    def frame_face(signal, state):
        """이번 프레임 값만으로 정한 손 방향을 돌려주는 메서드"""
        # 손을 뒤집자마자 핀치하면 Orientation은 아직 이전 방향이므로 이 값도 함께 봄
        if signal is not None and state in FACES:
            if signal.facing >= FACING_THRESHOLD:
                return "palm"
            if signal.facing <= -FACING_THRESHOLD:
                return "back"
        return state

    @staticmethod
    def hidden(held, face):
        return face == "palm" and bool(held) and statistics.median(s.roll for s in held) <= OCCLUDED_ROLL

    def occluded(self, hand):
        """손이 엄지가 가려질 만큼 소지 쪽으로 기울었는지 판별하는 메서드"""
        state = self.hands[hand]
        return state.signal is not None and state.orientation.state == "palm" and state.signal.roll <= OCCLUDED_ROLL

    def reset(self):
        """양손의 판정 상태를 모두 지우는 메서드"""
        for hand in self.hands.values():
            hand.pinch.lost()
            hand.fist.lost()
            hand.orientation.lost()
            hand.held = []
            hand.seen = None
        self.pending = None

    def update(self, signals, now):
        """한 프레임의 양손 신호를 판정하고 이번 프레임의 이벤트 목록을 돌려주는 메서드"""
        events, self.pinches = [], []  # 이벤트는 ("input", 글자), ("commit", None), ("delete", None) 세 가지임
        for label, hand in self.hands.items():
            hand.signal = signals.get(label)
            if hand.signal is None and hand.pinch.active is not None and hand.seen is not None \
                    and now - hand.seen <= HAND_GAP:
                continue
            if hand.signal is not None:
                hand.seen = now
            hand.orientation.update(None if hand.signal is None else hand.signal.facing, now)
            signal, state = hand.signal, hand.orientation.state
            # 손등 핀치는 다른 손가락이 자연스럽게 굽으므로 SPOIL_CURLED를 쓰지 않음
            hand.pinch.spoil_curled = "back" not in (state, self.frame_face(signal, state))
            tap = hand.pinch.update(signal if state in FACES else None, now)
            if tap is not None and tap[0] == 0 and label == "Right" and state == "palm" and hand.pinch.index_sweep():
                hand.pinch.undo_tap()
                tap = None
            if signal is not None and (tap is not None or hand.pinch.active is not None):
                hand.held.append(signal)
            if tap is not None and state in FACES and self.steady(hand.held, state) \
                    and not self.hidden(hand.held, state) and KEYS[(label, state)][tap[0]] is not None:
                self.pinches.append((label, tap[0], state))
                drag = None
                if KEYS[(label, state)][tap[0]] == "VOWEL":
                    # 뗄 때 엄지만 벌어지는 마지막 프레임은 끈 거리에서 뺌
                    held = hand.held[:-RELEASE_FRAMES] if len(hand.held) > RELEASE_FRAMES else hand.held
                    drag = self.drag_of_path(*self.pinch_path(held, tap[0]))
                events += self.press_finger(label, tap[0], state, drag)
            if tap is not None or hand.pinch.active is None:
                hand.held = []
            if signal is None:
                hand.fist.lost()
            elif hand.fist.update(signal.pose, now, relaxed=not FingerPinch.closed_pose(signal)) and state == "palm":
                events += self.make_fist()
        return events


def replay(steps):
    """핀치와 주먹 단계 목록을 차례로 입력하고 입력기를 돌려주는 함수"""
    typer = PinchTyper()
    for item in steps:
        if item[0] == "tap":
            face = item[3] if len(item) > 3 else "palm"
            typer.press_finger(item[1], item[2], face, item[4] if len(item) > 4 else None)
        elif item[0] == "fist":
            typer.make_fist()
        else:
            raise ValueError("알 수 없는 단계: " + str(item))
    return typer
