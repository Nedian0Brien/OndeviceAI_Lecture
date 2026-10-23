"""양손 꼬집기 → 한글 입력(PinchTyper).

프레임마다 양손 신호(hand_signal.HandSignal)를 받아 손마다 판정기를 돌리고, 입력된 꼬집기를 자판(layout.py)의 키로
바꿔 천지인 입력기(korean_ime.CheonjiinIME)에 넣는다.

손 하나의 판정기
  Orientation  손바닥 / 손등 / 회전 중(common/gestures.py)
  FingerPinch  어느 손끝을 꼬집었다 뗐는지(pinch.py)
  FistHold     주먹을 쥐었는지(common/gestures.py) — 지움

입력으로 인정하는 조건(update)
  - 손 방향이 손바닥이나 손등이다(회전 중에는 꼬집기를 보지 않는다).
  - 접촉의 처음·마지막 STEADY_FRAMES 프레임의 손 방향 값이 뗄 때의 손 방향이다(steady).
    손을 뒤집는 도중의 꼬집기를 입력으로 세지 않기 위해서다. 접촉을 시작할 때의 손 방향 판정(Orientation)과는
    비교하지 않는다: 뒤집자마자 꼬집으면 그 판정이 아직 이전 방향이다(frame_face).
  - 손바닥 꼬집기는 소지 쪽으로 OCCLUDED_ROLL° 넘게 기울지 않았다(그 자세는 엄지가 가려 손가락을 믿을 수 없다).
  - 오른손 손바닥 검지는 엄지가 지나간 것이 아니다(pinch.FingerPinch.index_sweep).

꼬집는 도중 손이 HAND_GAP초 안에서 사라졌다 돌아오면 사라진 프레임만 건너뛰고 그 접촉을 이어서 판정한다.
"""

from dataclasses import dataclass
import math
import statistics
from typing import Optional

from common.gestures import FACING_THRESHOLD, FistHold, Orientation
from korean_ime import CONSONANTS, CheonjiinIME, PreparedKey
from layout import DRAG_STROKES, FACES, HANDS, KEYS, VOWEL_LABELS, drag_of, group_of, is_group_key
from pinch import RELEASE_FRAMES, FingerPinch


FIST_HOLD = 0.18  # 주먹을 이 시간(초) 쥐면 지움
FIST_REPEAT = 0.5  # 주먹을 쥐고 있으면 이 간격(초)마다 한 번씩 더 지운다
# 주먹 지움을 다시 받는 조건: 주먹이 아닌 상태가 FIST_REARM초 이어짐(손끝 버튼이 다시 펼쳐지는 기준과 같다).
# 주먹을 연달아 쥘 때 손을 반쯤만 펴도 다음 주먹으로 지운다.
FIST_REARM = 0.15
# 손 방향 확인: 접촉의 처음·마지막 STEADY_FRAMES 프레임의 손 방향 값 중앙값이 모두 그 방향이어야 입력한다.
# 손을 뒤집으며 뗀 꼬집기 5개 중 4개를 막고, 정답 기록의 결과는 그대로였다(2026-10-23).
STEADY_FRAMES = 3
# 엄지 가림: 손바닥에서 소지 쪽이 카메라에 가깝게 OCCLUDED_ROLL°보다 기울면 엄지와 맞닿는 곳이 가려져, MediaPipe가
# 엄지를 검지 옆에 있다고 추정한다(기운 차례 8개 중 7개가 검지로 틀림). 그 자세의 손바닥 꼬집기는 받지 않는다.
OCCLUDED_ROLL = -25.0
# 꼬집는 도중 손 유실: 접촉 중인 손이 HAND_GAP초 이하로 사라지면 그 프레임들은 판정하지 않고 접촉을 이어 간다.
# 카메라는 손등 꼬집기 87번 중 27번에서 손을 0.03~0.6초 놓쳤다(Vision Pro 53번 중 0번). 기록 24개 재생에서
# 0.2초면 입력 12개, 0.4초면 20개, 0.6초면 21개가 되살아났다(2026-10-24, words-20261024-042026 등).
HAND_GAP = 0.4
ECHO_NAMES = {" ": "공백", "\n": "줄바꿈"}  # 손바닥 가운데 입력 표시(echo)에서 보이지 않는 글자의 이름


@dataclass
class Pending:
    """확정 전 후보 글자. 같은 손·손가락·방향을 다시 꼬집으면 순환한다."""
    key: str
    hand: str
    finger: int
    face: str
    count: int
    label: str


class HandState:
    """손 하나의 판정기 묶음과 이번 접촉의 기록."""

    def __init__(self):
        self.pinch = FingerPinch()
        self.orientation = Orientation()
        self.fist = FistHold(FIST_HOLD, FIST_REPEAT, FIST_REARM)
        self.signal = None  # 이번 프레임의 HandSignal
        self.seen: Optional[float] = None  # 마지막으로 신호가 있던 시각(HAND_GAP)
        self.held: list = []  # 이번 접촉 동안의 프레임별 신호(손 방향 확인, 모음 끌기)


class PinchTyper:
    """양손 신호 → 한글 입력. update()가 돌려주는 이벤트: ("input", 글자) / ("commit", None) / ("delete", None)."""

    def __init__(self):
        self.ime = CheonjiinIME()
        self.hands = {label: HandState() for label in HANDS}
        self.pending: Optional[Pending] = None
        self.pinches = []  # 이번 프레임에 입력으로 인정된 꼬집기 [(손, 손가락, 방향)] (단어 시험 기록이 읽는다)
        self.echo: Optional[tuple] = None  # 마지막 입력 (번호, 손, 표시 글자): 화면이 손바닥 가운데에 잠깐 띄운다

    def face(self, hand):
        """그 손의 지금 방향: "palm", "back", 또는 판정 전·회전 중이면 None."""
        state = self.hands[hand].orientation.state
        return state if state in FACES else None

    # -- 키 단위 동작(프레임 판정과 고정 데이터 재생이 함께 쓴다) ---------------
    def press_finger(self, hand, finger, now, face="palm", drag=None):
        """hand의 finger(0 검지 ~ 3 소지)를 face 방향에서 한 번 꼬집었다. 입력된 것은 echo에 남긴다."""
        events = self._press(hand, finger, now, face, drag)
        shown = [ECHO_NAMES.get(value, value) if kind == "input" else "확정"
                 for kind, value in events if kind in ("input", "commit")]
        if shown:
            self.echo = ((self.echo[0] + 1) if self.echo else 1, hand, shown[-1])
        return events

    def _press(self, hand, finger, now, face, drag):
        """키 하나를 처리한다. drag: 모음을 꼬집은 채 끈 방향(None → ㆍ, "h" → ㅡ, "v" → ㅣ)."""
        key = KEYS[(hand, face)][finger]
        if key is None:  # 자판에 없는 자리(오른손 손등 중지~소지)
            return []
        if key == "COMMIT_SPACE":  # 후보가 있으면 확정, 없으면 공백
            return self._edit("COMMIT" if self.pending is not None else "SPACE", now)
        if key == "VOWEL":  # 모음: 앞 후보를 확정하고 천지인 획을 넣는다
            stroke = DRAG_STROKES[drag]
            self.pending = None
            self.ime.press(stroke, now)
            return [("input", VOWEL_LABELS[stroke])]
        if key in CONSONANTS and not is_group_key(key):  # ㄴ·ㄹ: 순환하지 않으니 후보로 두지 않고 바로 넣는다
            self.pending = None
            self._put(key, CONSONANTS[key], now)
            return [("input", CONSONANTS[key])]
        if not is_group_key(key):  # 왼손 손등 공백·확정
            return self._edit(key, now)
        pending = self.pending
        if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, face):
            group = group_of(pending.key)  # 같은 손·손가락 다시: 순환
            label = group[pending.count % len(group)]
            self._put(pending.key, label, now, replace=pending.label)
            pending.count, pending.label = pending.count + 1, label
            return [("input", label)]
        label = group_of(key)[0]  # 새 그룹: 첫 글자를 후보로 넣는다(앞 후보는 확정)
        self._put(key, label, now)
        self.pending = Pending(key, hand, finger, face, 1, label)
        return [("input", label)]

    def make_fist(self, hand, now):
        """주먹: 마지막 자모 하나를 통째로 지운다(감 → 가 → ㄱ, 순환한 ㅁ은 ㅇ으로 되돌리지 않고 ㅁ째)."""
        self.pending = None
        self.ime.delete_char()
        return [("delete", None)]

    def _edit(self, key, now):
        """편집 키: "SPACE" 공백 · "COMMIT" 확정. 앞 후보를 확정한다."""
        self.pending = None
        if key == "COMMIT":
            return [("commit", None)]
        self.ime.press(key, now)
        return [("input", " ")]

    def _put(self, key, value, now, replace=None):
        """입력기에 자모(또는 문장부호)를 넣는다. replace가 있으면 마지막 후보를 value로 바꾼다(순환)."""
        if key == "PUNCT":
            self.ime.put_symbol(value, replace=replace is not None)
            return
        target = self.ime.tokens[-1] if replace is not None and self.ime.tokens else None
        self.ime.press(key, now, PreparedKey(key, value, target, replace or ""))

    # -- 모음 끌기 -----------------------------------------------------------
    @staticmethod
    def pinch_path(held, finger):
        """꼬집기 지점(엄지 끝과 finger 손끝의 가운데, 화면 좌표) 목록과 손 크기(중앙값)."""
        frames = [s for s in held if s.tips]
        points = [((s.tips[0][0] + s.tips[finger + 1][0]) / 2, (s.tips[0][1] + s.tips[finger + 1][1]) / 2)
                  for s in frames]
        return points, (statistics.median(s.size for s in frames) if frames else 0.0)

    @staticmethod
    def drag_of_path(points, size):
        """꼬집기 지점 목록 → 끌기(None / "h" / "v"): 처음 지점에서 가장 멀리 간 이동."""
        if not points:
            return None
        ox, oy = points[0]
        dx, dy = max(((x - ox, y - oy) for x, y in points), key=lambda d: math.hypot(*d))
        return drag_of(dx, dy, size)

    def drag(self, held, finger):
        """이번 접촉의 끌기. 떼며 엄지만 벌어지는 마지막 RELEASE_FRAMES 프레임은 세지 않는다."""
        return self.drag_of_path(*self.pinch_path(held[:-RELEASE_FRAMES] if len(held) > RELEASE_FRAMES else held,
                                                  finger))

    def drag_view(self, hand):
        """화면용: 그 손이 모음을 꼬집는 중이면 {"finger", "points"(꼬집기 지점), "size", "drag"(지금 떼면 들어갈 끌기),
        "pointer"(끈 방향 각도: 12시 0°에서 시계 방향, 끌기 없으면 None)}. 아니면 None."""
        state, face = self.hands[hand], self.face(hand)
        finger = state.pinch.active
        if finger is None or face is None or KEYS[(hand, face)][finger] != "VOWEL":
            return None
        points, size = self.pinch_path(state.held, finger)
        if not points:
            return None
        drag = self.drag_of_path(points, size)
        pointer = None
        if drag is not None:  # 가장 멀리 간 지점 쪽(가로면 오른쪽 90° / 왼쪽 270°, 세로면 아래 180° / 위 0°)
            (ox, oy), (fx, fy) = points[0], max(points, key=lambda q: math.dist(q, points[0]))
            pointer = (90 if fx >= ox else 270) if drag == "h" else (180 if fy >= oy else 0)
        return {"finger": finger, "points": points, "size": size, "drag": drag, "pointer": pointer}

    # -- 입력 조건 -----------------------------------------------------------
    @staticmethod
    def steady(held, face):
        """접촉의 처음·마지막 STEADY_FRAMES 프레임 손 방향 값 중앙값이 모두 face 쪽인가."""
        sign = 1 if face == "palm" else -1
        facings = [signal.facing for signal in held]
        return bool(facings) and all(sign * statistics.median(part) >= FACING_THRESHOLD
                                     for part in (facings[:STEADY_FRAMES], facings[-STEADY_FRAMES:]))

    @staticmethod
    def frame_face(signal, state):
        """이번 프레임의 손 방향: 손 방향 값이 FACING_THRESHOLD를 넘으면 그 방향, 아니면 인정된 방향(state).

        Orientation은 새 방향이 FACING_DWELL초 이어져야 바꾸므로, 손을 뒤집자마자 꼬집으면 접촉 시작 프레임이 아직
        이전 방향이다. 손등 모음을 손바닥 규칙(SPOIL_CURLED)으로 버리지 않게, 굽음 규칙은 이 값도 본다
        (2026-10-24 Vision Pro 기록: 뒤집고 0.14초 뒤 시작한 ㅣ 3개가 모두 버려짐). 회전 중·판정 전에는 state 그대로다.
        """
        if signal is not None and state in FACES:
            if signal.facing >= FACING_THRESHOLD:
                return "palm"
            if signal.facing <= -FACING_THRESHOLD:
                return "back"
        return state

    @staticmethod
    def hidden(held, face):
        """손바닥 접촉 동안 기울기 중앙값이 OCCLUDED_ROLL 이하인가(엄지가 가려 손가락 값을 믿을 수 없음)."""
        return face == "palm" and bool(held) and statistics.median(s.roll for s in held) <= OCCLUDED_ROLL

    def occluded(self, hand):
        """화면 안내용: 그 손이 지금 손바닥이고 엄지가 가려질 만큼 소지 쪽으로 기울었는가."""
        state = self.hands[hand]
        return state.signal is not None and state.orientation.state == "palm" and state.signal.roll <= OCCLUDED_ROLL

    # -- 프레임마다 ------------------------------------------------------------
    def reset(self):
        """일시정지 등으로 손 입력을 멈출 때. 다시 시작하면 손을 펴야 입력을 받는다."""
        for hand in self.hands.values():
            hand.pinch.lost()
            hand.fist.lost()
            hand.orientation.lost()
            hand.held = []
            hand.seen = None
        self.pending = None

    def update(self, signals, now):
        """signals: {"Left": HandSignal|None, "Right": HandSignal|None}. 이번 프레임의 이벤트 목록을 돌려준다."""
        events, self.pinches = [], []
        for label, hand in self.hands.items():
            hand.signal = signals.get(label)
            if hand.signal is None and hand.pinch.active is not None and hand.seen is not None \
                    and now - hand.seen <= HAND_GAP:
                continue  # 꼬집는 도중 손을 잠깐 놓침: 이 프레임은 건너뛰고 손이 돌아오면 접촉을 이어서 판정한다
            if hand.signal is not None:
                hand.seen = now
            hand.orientation.update(None if hand.signal is None else hand.signal.facing, now)
            signal, state = hand.signal, hand.orientation.state
            # 손등 꼬집기는 다른 손가락이 굽어도 버리지 않는다. 인정된 방향이나 이번 프레임 방향 중 하나라도 손등이면
            # 쓰지 않는다: 뒤집자마자 꼬집은 접촉도, 손등 접촉 중 손 방향 값이 한 프레임 튄 것도 버리지 않는다.
            hand.pinch.spoil_curled = "back" not in (state, self.frame_face(signal, state))
            tap = hand.pinch.update(signal if state in FACES else None, now)  # 회전 중에는 꼬집기를 보지 않는다
            if tap is not None and tap[0] == 0 and label == "Right" and state == "palm" and hand.pinch.index_sweep():
                hand.pinch.undo_tap()  # 오른손 검지 지나감: 입력하지 않는다
                tap = None
            if signal is not None and (tap is not None or hand.pinch.active is not None):
                hand.held.append(signal)
            if tap is not None and state in FACES and self.steady(hand.held, state) \
                    and not self.hidden(hand.held, state) and KEYS[(label, state)][tap[0]] is not None:
                self.pinches.append((label, tap[0], state))
                drag = self.drag(hand.held, tap[0]) if KEYS[(label, state)][tap[0]] == "VOWEL" else None
                events += self.press_finger(label, tap[0], now, state, drag)
            if tap is not None or hand.pinch.active is None:
                hand.held = []
            if signal is None:
                hand.fist.lost()
            elif hand.fist.update(signal.pose, now, relaxed=not FingerPinch.closed_pose(signal)) and state == "palm":
                events += self.make_fist(label, now)  # 주먹(지움)은 손바닥일 때만 센다
        return events


def replay(steps, typer=None, step=0.5):
    """고정 데이터 단계(["tap", 손, 손가락, 방향?, 끌기?] / ["fist", 손])를 순서대로 재생하고 입력기를 돌려준다.

    방향을 생략하면 손바닥("palm"), 끌기(모음: "h" 가로 ㅡ, "v" 세로 ㅣ)를 생략하면 끌기 없음(ㆍ)이다.
    """
    typer = typer or PinchTyper()
    now = 0.0
    for item in steps:
        now += step
        if item[0] == "tap":
            typer.press_finger(item[1], item[2], now, item[3] if len(item) > 3 else "palm",
                               item[4] if len(item) > 4 else None)
        elif item[0] == "fist":
            typer.make_fist(item[1], now)
        else:
            raise ValueError("알 수 없는 단계: " + str(item))
    return typer
