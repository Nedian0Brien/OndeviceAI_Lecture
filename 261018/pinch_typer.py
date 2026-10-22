"""엄지-손끝 꼬집기 횟수로 고르는 양손 천지인 입력의 핵심 상태 머신 (표준 라이브러리만 사용).

카메라·MediaPipe와 분리되어 있다. 손마다 HandSignal(손가락별 꼬집기 비율, 굽힘, 자세, 손 방향, 커서 기준점)을
받아 한글 입력(CheonjiinIME)과 마우스 이벤트를 만든다. visionOS(ARKit)로 옮길 때도 같은 신호만 만들면 된다.

자판 (손바닥이 카메라를 향한 자세)
            검지      중지      약지      소지
  왼손      ㅣ        ㆍ        ㅡ        쉬프트(원샷)
  오른손    ㄱㅋㄲ    ㄴㄹ      ㄷㅌㄸ    ㅇㅁ
  +쉬프트   ㅂㅍㅃ    ㅅㅎㅆ    ㅈㅊㅉ    . , ? ! 줄바꿈

- 같은 손가락을 다시 꼬집으면 그룹 안에서 순환하고, 다른 버튼을 꼬집으면 앞 후보가 확정된다(C 규칙).
- 왼손 주먹: 후보가 있으면 확정, 없으면 공백. 오른손 주먹: 지움(후보가 있으면 후보 취소).
- 오른손 손등: 마우스 모드. 오른손 검지 꼬집기 유지 = 커서 이동, 왼손 검지 꼬집기 = 클릭(1회 좌, 2회 우, 유지 = 누름).

구성
  landmark → 신호   pinch_ratios, hand_signal → HandSignal
  손 하나의 판정기  FingerPinch(어느 손가락을 꼬집었나), common.gestures의 Orientation(손바닥/손등)·FistHold(주먹)
  양손 입력기       PinchTyper.update: 프레임마다 양손 신호를 받아 이벤트 목록을 돌려준다.
  고정 데이터 재생  replay: 카메라 없이 꼬집기·주먹 순서만으로 입력을 재현한다(--demo, 테스트).
"""

from dataclasses import dataclass
import math
from typing import Optional

from korean_ime import CONSONANTS, CheonjiinIME, PreparedKey
from common.gestures import FACING_DWELL, FACING_THRESHOLD, FistHold, Kalman1D, Orientation
from common.landmarks import DISTAL_SEGMENTS, INDEX_MCP, MIDDLE_MCP, THUMB_TIP, WRIST, point_segment_distance, xyz
from common.pose import CURL_ANGLE, finger_angles, palm_facing, pose_from_angles


HANDS = ("Left", "Right")
# 손가락(검지·중지·약지·소지) 순서의 키. 왼손은 모음과 쉬프트, 오른손은 자음 그룹이다.
LEFT_KEYS = ("1", "2", "3", "SHIFT")
RIGHT_KEYS = ("4", "5", "6", "0")
RIGHT_SHIFT_KEYS = ("7", "8", "9", "PUNCT")
PUNCTUATION = (".", ",", "?", "!", "\n")
VOWEL_LABELS = {"1": "ㅣ", "2": "ㆍ", "3": "ㅡ"}

# 꼬집기 거리: 엄지 끝(4)~손가락 끝마디 선분 world 거리 / 손목~중지 뿌리 길이(약 9~10cm).
# landmark는 뼈 축 위에 있어서 살이 맞닿아도 0이 되지 않는다(엄지·손가락 두께만큼 남는다).
# 판정값은 모든 사람에게 같은 고정값이다. 사용자별 보정 단계는 두지 않는다(2026-10-20 결정).
#   PINCH_ENTER: 이 값 아래면 접촉 시작   PINCH_EXIT: 이 값 위로 벌리면 항상 뗌
#   PINCH_MARGIN: 가장 가까운 손끝이 두 번째보다 이만큼 가까워야 그 손가락으로 본다
#   PINCH_MIN_CONTACT: 이보다 짧은 접촉(초)은 버린다
# 손가락마다 4번씩 꼬집은 2026-10-22 로그(37초)에서 32/32 입력, 오입력 0이었다.
PINCH_ENTER, PINCH_EXIT, PINCH_MARGIN, PINCH_MIN_CONTACT = 0.25, 0.38, 0.03, 0.04
# 떼기: exit 위로 벌리면 항상 뗌. 빠르게 연속으로 꼬집을 때는 exit까지 벌리지 않으므로(2026-10-22 로그: 0.33~0.37),
# 접촉 중 가장 낮았던 값보다 RELEASE_RISE 이상 올라가고 enter + RELEASE_FLOOR를 넘어도 뗀 것으로 본다.
# landmark가 한 프레임만 튀는 경우(같은 로그: 0.17 → 0.58 → 0.18)를 떼기로 세지 않도록 RELEASE_FRAMES 연속을 요구한다.
RELEASE_RISE, RELEASE_FLOOR, RELEASE_FRAMES = 0.12, 0.05, 2
# 마우스: 꼬집기가 TAP_MAX초보다 짧으면 클릭, 두 번째 클릭이 DOUBLE_WINDOW초 안이면 우클릭.
# 커서는 손 이동량 × CURSOR_GAIN만큼 움직이고, 꼬집은 직후 CURSOR_SETTLE초의 흔들림은 버린다.
TAP_MAX, DOUBLE_WINDOW, CURSOR_GAIN, CURSOR_SETTLE = 0.25, 0.35, 2.0, 0.06


def group_of(key):
    """키가 순환하는 자모·기호 목록."""
    return PUNCTUATION if key == "PUNCT" else CONSONANTS[key]


def key_label(key):
    """자판 지도에 쓰는 키 이름."""
    if key == "SHIFT":
        return "⇧"
    if key == "PUNCT":
        return ".,?!"
    return VOWEL_LABELS.get(key) or CONSONANTS[key]


# ---------------------------------------------------------------------------
# landmark → 신호. x, y, z 속성만 쓰므로 MediaPipe 타입이 아니어도 된다.

@dataclass
class HandSignal:
    """상태 머신이 받는 손 하나의 신호. 카메라·MediaPipe와 무관한 값만 담는다."""
    ratios: tuple  # 검지·중지·약지·소지 끝마디까지의 꼬집기 비율(pinch_ratios)
    curled: tuple = (False, False, False, False)  # 손가락별 굽음(world PIP 각도 ≤ CURL_ANGLE)
    pose: str = "open"  # open / closing / fist
    facing: float = 1.0  # > 0 손바닥, < 0 손등이 카메라를 향함(common.pose.palm_facing)
    anchor: tuple = (0.5, 0.5)  # 커서 기준점(검지 뿌리)의 정규화 이미지 좌표


def pinch_ratios(world):
    """엄지 끝(4)~각 손가락 끝마디 선분(DIP→TIP) 거리 / 손목(0)~중지 뿌리(9) 길이. 검지·중지·약지·소지.

    손끝 점끼리 재면 손가락 지문 면(끝마디 가운데)이 닿아도 거리가 크게 남는다. 선분까지 재면 끝마디
    어디에 닿든 같은 기준이 된다. 손 크기로 나눠 사람·거리에 따른 차이를 없앤다.
    """
    scale = max(math.dist(xyz(world[WRIST]), xyz(world[MIDDLE_MCP])), 1e-6)
    thumb = xyz(world[THUMB_TIP])
    return tuple(point_segment_distance(thumb, xyz(world[a]), xyz(world[b])) / scale for a, b in DISTAL_SEGMENTS)


def hand_signal(label, landmarks, world, mirrored=False, width=1.0, height=1.0, pose=None):
    """한 손의 이미지 landmark(정규화 좌표)와 world landmark로 HandSignal을 만든다.

    label: 실제 왼손/오른손(손바닥 방향 부호에 쓴다). pose를 주지 않으면 world 각도로 자세를 정한다.
    """
    angles = finger_angles(world)
    return HandSignal(
        ratios=pinch_ratios(world),
        curled=tuple(angle <= CURL_ANGLE for angle in angles),
        pose=pose or pose_from_angles(angles),
        facing=palm_facing(landmarks, label, mirrored, width, height),
        anchor=(landmarks[INDEX_MCP].x, landmarks[INDEX_MCP].y),
    )


# ---------------------------------------------------------------------------
# 손 하나의 판정기

class FingerPinch:
    """어느 손끝을 꼬집었는지. 닿았다가 떨어지는 순간 (손가락, 지속 시간)을 돌려준다.

    - 가장 가까운 손끝이 enter 아래이고 두 번째 손끝보다 margin 이상 가까워야 접촉을 시작한다.
      중지를 꼬집으면 약지 끝마디도 함께 가까워지므로, 비율이 아니라 거리 차이로 본다.
    - 그 손끝이 떼기 기준(release_level) 위로 release_frames 프레임 연속 벌어지면 1회. 떼기 기준은 exit와,
      접촉 중 최저값 + RELEASE_RISE(단 enter + RELEASE_FLOOR 이상) 중 작은 값이다.
      min_contact보다 짧거나, 접촉 중 주먹이 되거나 다른 두 손가락 이상이 굽으면(주먹을 쥐는 도중) 버린다.
    - 처음 보이는 손이나 유실 후 돌아온 손은 모든 손끝이 enter 밖에 있는 상태를 한 번 거쳐야 한다
      (이미 꼬집은 채 나타난 손을 입력으로 세지 않기 위해).
    """

    def __init__(self, enter=PINCH_ENTER, exit=PINCH_EXIT, margin=PINCH_MARGIN, min_contact=PINCH_MIN_CONTACT):
        if exit <= enter:
            raise ValueError("exit는 enter보다 커야 합니다.")
        self.enter, self.exit, self.margin, self.min_contact = enter, exit, margin, min_contact
        self.lost()

    def lost(self):
        self.armed = False
        self.active: Optional[int] = None
        self.since: Optional[float] = None
        self.spoiled = False
        self.low = 1.0  # 접촉 중 꼬집는 손끝의 최저 비율
        self.opened = 0  # 떼기 기준 위로 연속된 프레임 수

    def release_level(self):
        return min(self.exit, max(self.enter + RELEASE_FLOOR, self.low + RELEASE_RISE))

    def _check(self, signal):
        others = sum(curled for index, curled in enumerate(signal.curled) if index != self.active)
        if signal.pose == "fist" or others >= 2:
            self.spoiled = True

    def update(self, signal, now):
        if signal is None:
            self.lost()
            return None
        ratios = signal.ratios
        if not self.armed:
            self.armed = min(ratios) > self.enter
            return None
        if self.active is None:
            first, second = sorted(range(len(ratios)), key=ratios.__getitem__)[:2]
            if ratios[first] < self.enter and ratios[second] - ratios[first] >= self.margin:
                self.active, self.since, self.spoiled = first, now, False
                self.low, self.opened = ratios[first], 0
                self._check(signal)
            return None
        self._check(signal)
        value = ratios[self.active]
        if value <= self.release_level():
            self.low, self.opened = min(self.low, value), 0
            return None
        self.opened += 1
        if self.opened < RELEASE_FRAMES:
            return None
        finger, duration, spoiled = self.active, now - self.since, self.spoiled
        self.active = self.since = None
        self.spoiled, self.low, self.opened = False, 1.0, 0
        if spoiled or duration < self.min_contact:
            return None
        return finger, duration


# ---------------------------------------------------------------------------
# 양손 입력기

@dataclass
class Pending:
    """확정 전 후보. 같은 손가락을 다시 꼬집으면 순환한다."""
    key: str
    finger: int
    count: int
    label: str


class HandState:
    """손 하나의 판정기 묶음과 직전 신호."""

    def __init__(self, pinch, orientation, fist):
        self.pinch, self.orientation, self.fist = pinch, orientation, fist
        self.signal: Optional[HandSignal] = None
        self.contact: Optional[str] = None  # 접촉을 시작할 때의 손 방향


class PinchTyper:
    """양손 신호 → 한글 입력과 마우스 이벤트.

    update()가 돌려주는 이벤트(튜플)
      ("input", 글자)  ("shift", 켜짐)  ("commit", None)  ("cancel", None)  ("delete", None)
      ("mode", "keyboard"|"mouse")  ("move", dx, dy)  ("click", "left"|"right")  ("down", None)  ("up", None)
    """

    def __init__(self, ime=None, enter=PINCH_ENTER, exit=PINCH_EXIT, margin=PINCH_MARGIN,
                 min_contact=PINCH_MIN_CONTACT, fist_hold=0.18, facing_threshold=FACING_THRESHOLD,
                 facing_dwell=FACING_DWELL, tap_max=TAP_MAX, double_window=DOUBLE_WINDOW, gain=CURSOR_GAIN):
        self.ime = ime if ime is not None else CheonjiinIME()
        self.hands = {label: HandState(FingerPinch(enter, exit, margin, min_contact),
                                       Orientation(facing_threshold, facing_dwell), FistHold(fist_hold))
                      for label in HANDS}
        self.tap_max, self.double_window, self.gain = tap_max, double_window, gain
        self.pending: Optional[Pending] = None
        self.shift = False
        self.mode = "keyboard"
        self.cursor = [0.5, 0.5]
        self.button_down = False
        self.taps = []
        self.clutch = None
        self.filters = (Kalman1D(), Kalman1D())

    # -- 판정 임계값 ------------------------------------------------------
    @property
    def enter(self):
        return self.hands["Left"].pinch.enter

    def set_pinch(self, enter, exit):
        if exit <= enter:
            raise ValueError("exit는 enter보다 커야 합니다.")
        for hand in self.hands.values():
            hand.pinch.enter, hand.pinch.exit = enter, exit

    # -- 키 단위 동작 (고정 데이터·키보드 시험에서도 직접 부른다) ----------
    def press_finger(self, hand, finger, now):
        """손바닥 자세에서 hand의 finger(0 검지 ~ 3 소지)를 한 번 꼬집었다."""
        if hand == "Left":
            key = LEFT_KEYS[finger]
            if key == "SHIFT":
                self.pending = None  # 쉬프트 꼬집기는 앞 후보를 확정하는 구분자이기도 하다.
                self.shift = not self.shift
                return [("shift", self.shift)]
            self.pending, self.shift = None, False
            self.ime.press(key, now)
            return [("input", VOWEL_LABELS[key])]
        pending = self.pending
        if pending is not None and pending.finger == finger and not self.shift:
            group = group_of(pending.key)
            label = group[pending.count % len(group)]
            self._put(pending.key, label, now, replace=pending.label)
            pending.count, pending.label = pending.count + 1, label
            return [("input", label)]
        key = (RIGHT_SHIFT_KEYS if self.shift else RIGHT_KEYS)[finger]
        label = group_of(key)[0]
        self._put(key, label, now)
        self.pending, self.shift = Pending(key, finger, 1, label), False
        return [("input", label)]

    def make_fist(self, hand, now):
        """손바닥 자세에서 주먹을 쥐었다. 왼손: 확정/공백, 오른손: 지움."""
        if hand == "Left":
            if self.pending is not None:
                self.pending, self.shift = None, False
                return [("commit", None)]
            self.shift = False
            self.ime.press("SPACE", now)
            return [("input", " ")]
        self.shift = False
        if self.pending is not None:
            for _ in range(self.pending.count):
                self.ime.backspace()
            self.pending = None
            return [("cancel", None)]
        self.ime.backspace()
        return [("delete", None)]

    def _put(self, key, value, now, replace=None):
        """조합기에 자모(또는 문장부호)를 넣는다. replace가 있으면 마지막 후보를 value로 바꾼다(순환)."""
        if key == "PUNCT":
            self.ime.put_symbol(value, replace=replace is not None)
            return
        target = self.ime.tokens[-1] if replace is not None and self.ime.tokens else None
        self.ime.press(key, now, PreparedKey(key, value, target, replace or ""))

    # -- 프레임 단위 -------------------------------------------------------
    def reset(self):
        """일시정지·도움말 등으로 손 입력을 멈출 때. 다시 시작하면 손을 펴야 입력을 받는다."""
        for hand in self.hands.values():
            hand.pinch.lost()
            hand.fist.lost()
            hand.orientation.lost()
            hand.contact = None
        self.pending, self.shift = None, False
        self.taps, self.clutch, self.button_down = [], None, False

    def update(self, signals, now):
        """signals: {"Left": HandSignal|None, "Right": HandSignal|None}. 이번 프레임의 이벤트 목록을 돌려준다.

        1) 양손의 손바닥 방향을 갱신하고, 오른손이 손등이면 마우스 모드, 아니면 키보드 모드로 둔다.
        2) 손마다 꼬집기(FingerPinch)와 주먹(FistHold)을 갱신한다.
        3) 꼬집기는 접촉을 시작할 때와 뗄 때 모두 기대 방향(키보드=손바닥, 마우스=손등)이어야 입력으로 인정한다.
           손을 뒤집는 도중의 꼬집기를 입력으로 세지 않기 위해서다.
        4) 키보드 모드: 꼬집기 → press_finger, 손바닥 주먹 → make_fist
           마우스 모드: 오른손 → 커서 이동(_move), 왼손 → 클릭(_click)
        """
        events = []
        for label, hand in self.hands.items():
            hand.signal = signals.get(label)
            hand.orientation.update(None if hand.signal is None else hand.signal.facing, now)
        mode = "mouse" if self.hands["Right"].orientation.state == "back" else "keyboard"
        if mode != self.mode:
            self._switch(mode, events)
        expected = "palm" if self.mode == "keyboard" else "back"
        for label, hand in self.hands.items():
            signal, state = hand.signal, hand.orientation.state
            tap = hand.pinch.update(signal, now)  # 손가락을 뗀 프레임에서 (손가락, 접촉 시간)
            contact = hand.contact  # 이번 접촉을 시작할 때의 손 방향
            if tap is not None or hand.pinch.active is None:
                hand.contact = None
            elif hand.contact is None:
                hand.contact = contact = state
            valid = tap is not None and contact == expected and state == expected
            if signal is None:
                hand.fist.lost()
                fist = False
            else:
                fist = hand.fist.update(signal.pose, now)
            if self.mode == "keyboard":
                if valid:
                    events += self.press_finger(label, tap[0], now)
                if fist and state == "palm":
                    events += self.make_fist(label, now)
            elif label == "Right":
                self._move(hand, now, events)
            else:
                self._click(hand, tap if valid else None, now, events)
        # 마우스: 첫 클릭 뒤 double_window초 안에 두 번째가 없으면 좌클릭으로 확정한다.
        if self.mode == "mouse" and len(self.taps) == 1 and now - self.taps[0] > self.double_window:
            self.taps = []
            events.append(("click", "left"))
        return events

    def _switch(self, mode, events):
        """키보드 ↔ 마우스 전환. 확정 전 후보·쉬프트·클릭 대기·누른 버튼을 모두 정리한다."""
        self.mode = mode
        self.pending, self.shift = None, False
        self.taps, self.clutch = [], None
        if self.button_down:
            self.button_down = False
            events.append(("up", None))
        events.append(("mode", mode))

    def _move(self, hand, now, events):
        """오른손 손등 + 검지 꼬집기 유지 = 커서 상대 이동(트랙패드처럼 놓고 다시 잡는다)."""
        signal = hand.signal
        # 떼기 확인(연속 프레임)을 기다리는 동안에도 손가락은 이미 벌어졌으므로 이동을 멈춘다.
        if signal is None or hand.orientation.state != "back" or hand.pinch.active != 0 or hand.pinch.opened:
            self.clutch = None
            return
        x, y = signal.anchor
        fx_filter, fy_filter = self.filters
        if self.clutch is None:
            fx_filter.reset(x, now)
            fy_filter.reset(y, now)
            self.clutch = (x, y, now)
            return
        fx, fy = fx_filter.update(x, now), fy_filter.update(y, now)
        px, py, start = self.clutch
        self.clutch = (fx, fy, start)
        if now - start < CURSOR_SETTLE:  # 오므리는 동작에 따른 튐은 버린다.
            return
        dx, dy = (fx - px) * self.gain, (fy - py) * self.gain
        self.cursor = [min(1.0, max(0.0, self.cursor[0] + dx)), min(1.0, max(0.0, self.cursor[1] + dy))]
        if dx or dy:
            events.append(("move", dx, dy))

    def _click(self, hand, tap, now, events):
        """왼손 손등 + 검지 꼬집기: 짧게 1회 좌클릭, 2회 우클릭, 유지하면 버튼 누름."""
        pinch = hand.pinch
        holding = pinch.active == 0 and hand.contact == "back" and hand.orientation.state == "back"
        if holding and not self.button_down and now - pinch.since >= self.tap_max:
            self.button_down, self.taps = True, []
            events.append(("down", None))
            return
        if self.button_down and not holding:
            self.button_down = False
            events.append(("up", None))
            return
        if tap is not None and tap[0] == 0 and tap[1] < self.tap_max:
            self.taps.append(now)
            if len(self.taps) >= 2:
                self.taps = []
                events.append(("click", "right"))


def replay(steps, typer=None, step=0.5):
    """고정 데이터 단계(["tap", 손, 손가락] / ["fist", 손])를 순서대로 재생하고 입력기를 돌려준다."""
    typer = typer or PinchTyper()
    now = 0.0
    for item in steps:
        now += step
        if item[0] == "tap":
            typer.press_finger(item[1], item[2], now)
        elif item[0] == "fist":
            typer.make_fist(item[1], now)
        else:
            raise ValueError("알 수 없는 단계: " + str(item))
    return typer
