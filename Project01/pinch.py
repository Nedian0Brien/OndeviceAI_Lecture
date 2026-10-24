"""손 하나의 꼬집기 판정(FingerPinch): 엄지가 어느 손끝을 꼬집었다 뗐는지.

프레임마다 손 신호(hand_signal.HandSignal)를 받아, 손끝을 뗀 프레임에 (손가락, 접촉 시간)을 돌려줍니다.

핵심 아이디어: 거리 하나로는 꼬집기를 가를 수 없습니다. 쉬는 자세에서도 엄지는 검지 옆에 있고(거리 0.11~0.28),
실제 꼬집기의 가장 가까운 거리(0.00~0.24)와 겹칩니다(2026-10-22 정답 기록). 그래서 "최근 PINCH_WINDOW초 동안의
자기 값(기준값)보다 PINCH_DROP 이상 다가온 손끝"만 후보로 봅니다. 쉬는 자세에서 원래 가까운 손끝은 다가오지
않았으므로 후보가 되지 않습니다.

판정 순서
  1) 접촉 전: 손가락마다 최근 PINCH_WINDOW초의 최대 거리를 기준값으로 둡니다.
  2) 접촉 시작: 기준값보다 PINCH_DROP 이상 다가왔고 PINCH_ENTER 아래인 손끝 중 가장 가까운 손끝이,
     다른 손끝보다 PINCH_MARGIN 이상 가까우면 그 손끝과 접촉한 것으로 봅니다.
  3) 접촉 중: 손끝마다 가장 가까웠던 거리(low)를 갱신하고, 더 가까이 온 후보가 있으면 그 손끝으로 옮깁니다
     (엄지가 약지로 가는 길에 검지를 먼저 지나가도 약지가 됩니다).
  4) 떼기: 고른 손끝이 low보다 RELEASE_RISE 이상 벌어진 프레임이 RELEASE_FRAMES번 이어지면 입력 1번입니다.
     너무 짧은 접촉, 주먹을 쥐는 도중의 접촉은 버립니다.
"""

from collections import deque
from typing import Optional


# 판정값(엄지 끝 ~ 손가락 끝마디 거리 / 손 크기). 모든 사람에게 같은 고정값임(사용자별 보정 없음)
PINCH_ENTER = 0.30  # 접촉 후보가 되는 거리 상한
PINCH_EXIT = 0.38  # 이 위로 벌리면 항상 뗀 것으로 봄
PINCH_MARGIN = 0.03  # 고른 손끝이 다른 손끝보다 이만큼 가까워야 함(중지를 꼬집으면 약지도 함께 가까워짐)
PINCH_DROP = 0.15  # 기준값보다 이만큼 다가와야 후보. 같은 손가락 빠른 반복은 0.16~0.18만 다가왔음(2026-10-20)
PINCH_WINDOW = 0.5  # 기준값(최근 최대 거리)을 보는 시간(초)
PINCH_MIN_CONTACT = 0.10  # 이보다 짧은 접촉(초)은 엄지가 스친 것으로 보고 버림(실제 꼬집기는 모두 0.13초 이상)
# 얕은 접촉: 고른 손끝이 PINCH_SHALLOW보다 얕게 다가왔다가 벌어지는데 지금 더 가까운 후보가 있으면, 엄지가 그 옆을
# 지나 다른 손가락으로 가는 중임. 떼지 않고 그 손가락으로 옮김(2026-10-23: 중지로 가며 검지가 0.25까지 다가옴)
PINCH_SHALLOW = 0.20
# 떼기: low보다 RELEASE_RISE 이상 벌어진 프레임이 RELEASE_FRAMES번 이어져야 함(관절 값이 한 프레임 튀는 것을 무시)
RELEASE_RISE, RELEASE_FRAMES = 0.12, 2
# 같은 손가락 다시 꼬집기: 방금 뗀 손가락은 REPRESS_WINDOW초 안이면 REPRESS_DROP만 다가와도 후보로 봄
# (덜 벌린 채 다시 꼬집으면 0.08~0.12만 다가옴)
REPRESS_DROP, REPRESS_WINDOW = 0.08, 1.0
# 주먹을 쥐는 도중의 스침: 꼬집는 손가락 말고 다른 손가락이 SPOIL_CURLED개 이상 굽으면 버림(손바닥에서만)
# 소지를 꼬집으면 중지·약지가 함께 굽으므로(0111) 2가 아니라 3임. 손등 꼬집기(모음)는 다른 손가락이 자연스럽게 굽어
# 이 규칙을 쓰지 않음(spoil_curled, typer.py가 손 방향마다 정함)
SPOIL_CURLED = 3
# 검지 겹침: 엄지가 검지 옆에 있다가 중지·약지를 꼬집으면 엄지 옆면이 검지를 눌러 검지 거리도 함께 내려옴
# 검지로 판정된 접촉에서, 다른 손가락의 low가 검지 low + INDEX_TIE 안이고 접촉 동안 굽힘이 접촉 직전 FLEX_BASE초
# 평균보다 검지보다 FLEX_GAIN° 이상 더 늘었으면 그 손가락으로 바꿈(정답 접촉 90개: 맞음 78 → 83)
INDEX_TIE, FLEX_GAIN, FLEX_BASE = 0.05, 10.0, 0.3
# 오른손 검지 지나감: 엄지가 다른 손가락으로 가거나 돌아오며 검지를 지나감. 다른 손가락 입력을 뗀 뒤 INDEX_REPASS초
# 안에 시작했거나, 접촉 동안 검지 굽힘 증가가 INDEX_FLEX_MIN° 미만이면 지나간 것으로 봄(index_sweep, typer.py가 씀)
INDEX_REPASS, INDEX_FLEX_MIN = 0.25, 5.0


class FingerPinch:
    """어느 손끝을 꼬집었는지 판정합니다.
    update()는 손끝을 뗀 프레임에 (손가락, 접촉 시간)을, 그 밖에는 None을 돌려줍니다."""

    def __init__(self):
        self.spoil_curled = True  # 주먹 스침 규칙(SPOIL_CURLED)을 쓰는가. 손바닥에서만 씀(typer.py가 정함)
        self.lost()

    def lost(self):
        """손이 사라졌거나 판정을 쉴 때 모든 상태를 지웁니다. 다시 시작하면 기준값부터 새로 쌓습니다."""
        self.history = deque()  # 접촉 전 최근 (시각, 거리) — 기준값 계산용
        self.flexes = deque()  # 접촉 전 최근 (시각, 손가락별 굽힘) — 검지 겹침 규칙의 굽힘 기준
        self.flex_base: Optional[tuple] = None  # 접촉 직전 FLEX_BASE초 평균 굽힘
        self.flex_top: Optional[list] = None  # 접촉 중 손가락별 최대 굽힘
        self.last_tap: Optional[tuple] = None  # 직전 입력 (손가락, 뗀 시각) — 같은 손가락 다시 꼬집기
        self.released: Optional[dict] = None  # 방금 뗀 접촉: 시작 시각, 손가락별 굽힘 증가, 그 전의 last_tap
        self.active: Optional[int] = None  # 지금 접촉 중인 손가락(0 검지 ~ 3 소지)
        self.since: Optional[float] = None  # 접촉 시작 시각
        self.spoiled = False  # 주먹 스침으로 버릴 접촉인가
        self.base = (1.0,) * 4  # 접촉을 시작할 때의 손가락별 기준값
        self.low = [1.0] * 4  # 접촉 중 손가락별 가장 가까운 거리
        self.opened = 0  # 떼기 기준 위로 연속된 프레임 수
        self.closed = False  # 이번 프레임이 주먹 자세인가(closed_pose)

    @property
    def armed(self):
        """기준값을 잴 프레임이 있는지 돌려줍니다(처음 보이거나 사라졌다 돌아온 손은 한 프레임 뒤부터).
        주먹은 편 손으로 칩니다."""
        return bool(self.history) or self.active is not None or self.closed

    @staticmethod
    def closed_pose(signal):
        """주먹 자세인지 돌려줍니다: 자세 판정이 주먹이거나 네 손가락이 모두 굽음. 주먹인 동안은 꼬집기를 보지
        않습니다(엄지가 손가락 위에 놓여 거리가 가까워집니다).
        소지를 꼬집을 때는 검지가 펴져 있어(0111) 여기 들지 않습니다."""
        return signal.pose == "fist" or all(signal.curled)

    # -- 화면 표시용 진행 --------------------------------------------------
    def release_level(self):
        """고른 손끝을 이 거리보다 벌리면 뗀 것으로 봅니다."""
        return min(PINCH_EXIT, self.low[self.active] + RELEASE_RISE)

    def release_progress(self, ratios):
        """고른 손끝을 얼마나 벌렸는지(0~1) 돌려줍니다. 1이 RELEASE_FRAMES 동안 이어지면 입력됩니다.
        접촉 중이 아니면 0입니다."""
        if self.active is None:  # 접촉 중이 아님
            return 0.0
        low, level = self.low[self.active], self.release_level()
        return min(1.0, max(0.0, (ratios[self.active] - low) / max(level - low, 1e-6)))

    def progress(self, ratios):
        """손가락별 꼬집기 진행(0~1)입니다: 기준값보다 다가온 정도 / PINCH_DROP.
        PINCH_EXIT 위로 떨어져 있거나 주먹이면 0입니다."""
        if self.closed:  # 주먹이면 모든 손가락 0
            return [0.0] * 4
        # 진행을 잴 기준값: 접촉 중이면 접촉 시작 때 값, 접촉 전이면 최근 최대 거리, 기록이 없으면 지금 값(진행 0)
        if self.active is not None:
            base = self.base
        elif self.history:
            base = [max(row[j] for _, row in self.history) for j in range(4)]
        else:
            base = ratios
        return [0.0 if value > PINCH_EXIT else min(1.0, max(0.0, (top - value) / PINCH_DROP))
                for top, value in zip(base, ratios)]

    # -- 판정 규칙 ---------------------------------------------------------
    @staticmethod
    def _candidates(base, values):
        """기준값보다 PINCH_DROP 이상 다가왔고 PINCH_ENTER 아래인 손가락을 가까운 순으로 돌려줍니다."""
        return sorted((j for j in range(4) if base[j] - values[j] >= PINCH_DROP and values[j] <= PINCH_ENTER),
                      key=values.__getitem__)

    def _index_overlap(self, finger):
        """검지 겹침 규칙: 검지로 판정된 접촉을 더 굽은 다른 손가락으로 바꿀지 정합니다(INDEX_TIE 설명)."""
        if finger != 0 or self.flex_base is None or self.flex_top is None:  # 검지가 아니거나 굽힘 값이 없으면 그대로
            return finger
        gain = [top - base for top, base in zip(self.flex_top, self.flex_base)]  # 접촉 동안 손가락별 굽힘 증가
        # 검지만큼 가까웠고(INDEX_TIE) 검지보다 FLEX_GAIN° 이상 더 굽은 손가락
        others = [j for j in (1, 2, 3)
                  if self.low[j] <= self.low[0] + INDEX_TIE and gain[j] >= gain[0] + FLEX_GAIN]
        return min(others, key=self.low.__getitem__) if others else finger

    def index_sweep(self):
        """방금 뗀 검지 접촉이 엄지가 검지를 지나간 것인지 돌려줍니다(INDEX_REPASS 설명). "repass" / "flat" / None."""
        info = self.released
        if info is None:  # 방금 뗀 접촉이 없음
            return None
        previous = info["previous"]
        # 다른 손가락 입력 직후 INDEX_REPASS초 안에 시작한 검지 접촉: 엄지가 돌아오며 지나감
        if previous is not None and previous[0] != 0 and info["start"] - previous[1] < INDEX_REPASS:
            return "repass"
        # 검지가 거의 굽지 않은 접촉: 엄지가 옆을 스쳐 지나감
        if info["gain"] is not None and info["gain"][0] < INDEX_FLEX_MIN:
            return "flat"
        return None

    def undo_tap(self):
        """방금 돌려준 입력을 없던 것으로 합니다(직전 입력 기록을 그 전으로 되돌립니다)."""
        if self.released is not None:
            self.last_tap = self.released["previous"]

    def _emit(self, now):
        """입력 하나를 만듭니다: (손가락, 접촉 시간). 검지 지나감 판정에 쓸 정보를 released에 남깁니다."""
        finger = self._index_overlap(self.active)
        gain = None
        if self.flex_base is not None and self.flex_top is not None:  # 굽힘 값이 있으면 접촉 동안의 증가를 남김
            gain = [top - base for top, base in zip(self.flex_top, self.flex_base)]
        self.released = {"start": self.since, "gain": gain, "previous": self.last_tap}
        self.last_tap = (finger, now)
        return finger, now - self.since

    def _end_contact(self):
        """접촉을 끝냅니다. 기준값 기록도 비웁니다(다음 꼬집기는 뗀 뒤의 값을 기준으로 합니다)."""
        self.active = self.since = None
        self.history.clear()
        self.flexes.clear()
        self.flex_base = self.flex_top = None
        self.spoiled, self.low, self.opened = False, [1.0] * 4, 0

    def _check(self, signal):
        """주먹을 쥐는 도중인지 확인하고, 그러면 이번 접촉을 버릴 것으로 표시합니다(SPOIL_CURLED)."""
        others = sum(curled for index, curled in enumerate(signal.curled) if index != self.active)
        if signal.pose == "fist" or (self.spoil_curled and others >= SPOIL_CURLED):
            self.spoiled = True

    # -- 프레임마다 ---------------------------------------------------------
    def update(self, signal, now):
        """프레임 하나를 판정합니다. 손끝을 뗀 프레임에 (손가락, 접촉 시간)을, 그 밖에는 None을 돌려줍니다."""
        if signal is None:  # 손이 없거나 회전 중: 상태를 모두 지움
            self.lost()
            return None
        if self.closed_pose(signal):  # 주먹인 동안은 꼬집기를 보지 않음. 손을 펴면 기준값을 새로 쌓음
            self.lost()
            self.closed = True
            return None
        self.closed = False
        self.released = None
        if self.active is None:  # 접촉 전: 기준값을 쌓고 접촉 시작을 봄
            self._start_contact(signal, now)
            return None
        return self._update_contact(signal, now)  # 접촉 중: 손끝 옮기기와 떼기를 봄

    def _start_contact(self, signal, now):
        """접촉 전: 기준값을 쌓고, 다가온 손끝이 있으면 접촉을 시작합니다(판정 순서 1, 2)."""
        ratios = signal.ratios
        # 최근 PINCH_WINDOW초의 거리만 남김(기준값 = 이 기간의 최대 거리)
        self.history.append((now, ratios))
        while now - self.history[0][0] > PINCH_WINDOW:
            self.history.popleft()
        earlier = [flex for t, flex in self.flexes if now - t <= FLEX_BASE]  # 이번 프레임 전의 굽힘
        if signal.flex is not None:  # 최근 FLEX_BASE초의 굽힘만 남김(검지 겹침 규칙의 기준)
            self.flexes.append((now, signal.flex))
            while now - self.flexes[0][0] > FLEX_BASE:
                self.flexes.popleft()
        base = tuple(max(row[j] for _, row in self.history) for j in range(4))
        candidates = self._candidates(base, ratios)
        if self.last_tap is not None and now - self.last_tap[1] <= REPRESS_WINDOW:  # 같은 손가락 다시 꼬집기
            again = self.last_tap[0]
            # 방금 뗀 손가락은 REPRESS_DROP만 다가와도 후보에 넣음
            if again not in candidates and base[again] - ratios[again] >= REPRESS_DROP and ratios[again] <= PINCH_ENTER:
                candidates = sorted(candidates + [again], key=ratios.__getitem__)
        if not candidates:  # 다가온 손끝이 없음
            return
        best = candidates[0]
        if min(ratios[j] for j in range(4) if j != best) - ratios[best] < PINCH_MARGIN:  # 다른 손끝과 구분되지 않음
            return
        # 접촉 시작: 고른 손끝, 기준값, 가장 가까운 거리, 굽힘 기준을 정함
        self.active, self.since, self.spoiled, self.opened = best, now, False, 0
        self.base, self.low = base, list(ratios)
        if signal.flex is not None:
            earlier = earlier or [signal.flex]
            self.flex_base = tuple(sum(f[j] for f in earlier) / len(earlier) for j in range(4))
            self.flex_top = list(signal.flex)
        self._check(signal)

    def _update_contact(self, signal, now):
        """접촉 중: 손끝을 옮기거나 떼기를 판정합니다(판정 순서 3, 4).
        뗀 프레임에 (손가락, 접촉 시간)을 돌려줍니다."""
        ratios = signal.ratios
        self.low = [min(a, b) for a, b in zip(self.low, ratios)]  # 손가락별 가장 가까웠던 거리 갱신
        if self.flex_top is not None and signal.flex is not None:  # 손가락별 최대 굽힘 갱신
            self.flex_top = [max(a, b) for a, b in zip(self.flex_top, signal.flex)]
        candidates = self._candidates(self.base, self.low)
        # 더 가까이 온 손끝으로 옮김. 떼는 중(직전 프레임이 떼기 기준 위)에는 옮기지 않음: 떼며 엄지가 옆 손가락으로
        # 미끄러진 것을 그 손가락의 꼬집기로 바꾸지 않기 위함
        if candidates and self.low[self.active] - self.low[candidates[0]] >= PINCH_MARGIN and self.opened == 0:
            self.active = candidates[0]
        # 얕은 접촉 규칙(PINCH_SHALLOW): 얕게 다가왔던 손끝이 벌어지는 중
        if ratios[self.active] > self.release_level() and self.low[self.active] > PINCH_SHALLOW:
            closer = [j for j in candidates if j != self.active and ratios[j] <= PINCH_ENTER
                      and ratios[self.active] - ratios[j] >= PINCH_MARGIN]
            if closer:  # 얕게 다가왔다 벌어지는 손끝 대신, 지금도 닿을 거리에 있는 더 가까운 후보로 옮김
                self.active = min(closer, key=ratios.__getitem__)
        self._check(signal)
        if ratios[self.active] <= self.release_level():  # 아직 닿아 있음: 떼기 프레임 수를 처음부터
            self.opened = 0
            return None
        self.opened += 1
        if self.opened < RELEASE_FRAMES:  # 벌어진 프레임이 아직 RELEASE_FRAMES번 이어지지 않음
            return None
        # 뗌: 입력을 만들고 접촉을 끝냄. 주먹 스침이거나 너무 짧은 접촉이면 입력을 취소함
        spoiled, short = self.spoiled, now - self.since < PINCH_MIN_CONTACT
        tap = self._emit(now)
        self._end_contact()
        if spoiled or short:
            self.undo_tap()
            return None
        return tap
