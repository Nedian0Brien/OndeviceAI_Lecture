"""시간에 따라 상태가 바뀌는 손 동작 판정기 (표준 라이브러리만 사용).

모든 판정기는 프레임마다 `update(..., now)`를 받습니다. now는 초 단위 시각(time.monotonic)입니다.
손이 화면에서 사라지면 `lost()`를 불러 상태를 지웁니다. 돌아온 손은 손을 다시 펴야 입력을 받습니다.
사라진 순간의 자세로 입력이 나가지 않게 하기 위해서입니다.

- `FistHold`: 주먹을 일정 시간 유지하면 한 번(지움). 반복 간격을 주면 쥐고 있는 동안 계속
- `Orientation`: 손바닥 / 손등 / 회전 중. 순간적인 흔들림을 무시합니다.
"""

from typing import Optional


# 손바닥 방향 값(common.pose.palm_facing)이 ±0.25를 넘으면 손바닥/손등으로 보고, 새 방향은 0.15초 이어져야
# 인정함(2026-10-20 설계값)
FACING_THRESHOLD, FACING_DWELL = 0.25, 0.15


class FistHold:
    """주먹을 hold초 유지하면 한 번 셉니다. 손을 다시 펴야 다음 주먹을 셉니다.

    update(pose, now)는 이번 프레임에서 주먹이 확정되면 True를 돌려줍니다. pose는 common.pose의 자세 이름입니다.
    처음 보이는 손이나 유실 후 돌아온 손은 먼저 펴야 합니다. 주먹을 쥔 채 나타난 손은 세지 않습니다.
    중간에 "closing"이 섞이면 유지 시간을 처음부터 다시 잽니다.
    repeat(초)를 주면 첫 확정 뒤 주먹을 쥐고 있는 동안 repeat초마다 다시 True를 돌려줍니다(연속 지우기).
    쥔 채 잠깐 "closing"으로 읽히는 프레임에서는 내보내지 않지만 반복을 끊지도 않습니다. 손을 펴면 멈춥니다.
    rearm(초)을 주면 "open"까지 펴지 않아도 주먹이 아닌 상태(update의 relaxed)가 rearm초 이어지면 다시 셉니다.
    Vision Pro는 주먹을 연달아 쥘 때 손을 반쯤만 펴서("closing") 두 번째 주먹부터 지워지지 않았습니다(2026-10-24
    기록). 꼬집기 입력기는 카메라·Vision Pro 모두 typer.FIST_REARM을 씁니다.
    """

    def __init__(self, hold=0.18, repeat=None, rearm=None):
        self.hold, self.repeat, self.rearm = hold, repeat, rearm
        self.lost()

    def lost(self):
        """손이 사라졌을 때 상태를 지웁니다. 다시 세려면 손을 펴야 합니다."""
        self.armed = False  # 다음 주먹을 셀 준비가 되었는가(손을 편 뒤 True)
        self.since = None  # 주먹 유지를 재기 시작한 시각
        self.next_repeat = None  # 반복 중이면 다음에 내보낼 시각
        self.progress = 0.0  # 주먹 유지 진행 0~1 (화면 표시용)
        self.relaxed_since = None  # 주먹이 아닌 상태가 시작된 시각(rearm)

    def update(self, pose, now, relaxed=None):
        """프레임 하나를 판정하고, 주먹이 확정된 프레임에 True를 돌려줍니다. relaxed: 주먹이 아닌 상태인가(자세가
        주먹도 아니고 네 손가락이 다 굽지도 않음). rearm을 쓸 때만 봅니다."""
        self.progress = 0.0
        # 1) rearm: 주먹이 아닌 상태가 rearm초 이어지면 편 손과 같이 다시 셀 준비를 함
        if self.rearm is not None:
            if not relaxed:  # 주먹 쪽 자세: 재기 중단
                self.relaxed_since = None
            elif self.relaxed_since is None:  # 주먹이 아닌 상태가 막 시작됨
                self.relaxed_since = now
            elif now - self.relaxed_since >= self.rearm:  # rearm초 이어짐
                self.armed, self.since, self.next_repeat = True, None, None
        # 2) 편 손: 셀 준비를 하고 반복·유지를 모두 끊음
        if pose == "open":
            self.armed, self.since, self.next_repeat = True, None, None
            return False
        # 3) 첫 확정 뒤 쥐고 있는 중: repeat초마다 다시 내보냄
        if self.next_repeat is not None:
            self.progress = min(1.0, 1 - (self.next_repeat - now) / self.repeat)
            if pose == "fist" and now >= self.next_repeat:  # 반복 시각이 됨("closing" 프레임은 내보내지 않음)
                self.next_repeat = now + self.repeat
                return True
            return False
        # 4) 주먹이 아니거나 아직 손을 펴지 않았음: 유지 시간을 처음부터
        if pose != "fist" or not self.armed:
            self.since = None
            return False
        # 5) 주먹 유지 시간을 잼: hold초가 되면 한 번 내보내고, 반복을 쓰면 다음 시각을 정함
        if self.since is None:
            self.since = now
        self.progress = min(1.0, (now - self.since) / self.hold)
        if self.progress >= 1.0:
            self.armed, self.since = False, None
            if self.repeat:
                self.next_repeat = now + self.repeat
            return True
        return False


class Orientation:
    """손바닥(palm) / 손등(back) / 회전 중(turning)을 판정합니다. 새 방향은 dwell초 이어져야 인정합니다.

    update(facing, now): facing은 common.pose.palm_facing 값(-1~1)입니다.
    - facing > threshold → palm, facing < -threshold → back, 그 사이 → turning
    - 히스테리시스: 이미 인정한 방향은 threshold의 절반까지 유지합니다.
    - 인정한 방향은 새 방향(turning 포함)이 dwell초 이어질 때까지 바꾸지 않습니다. landmark가 한 프레임만 튀어
      방향 값이 흔들려도(2026-10-22 로그: +0.6 → -0.23 → +0.6) 입력이 끊기지 않게 하기 위해서입니다.
    - 처음 보이는 손은 dwell초가 지나기 전까지 turning입니다.
    """

    def __init__(self, threshold=FACING_THRESHOLD, dwell=FACING_DWELL):
        self.threshold, self.dwell = threshold, dwell
        self.lost()

    def lost(self):
        """손이 사라졌을 때 상태를 지웁니다."""
        self.state: Optional[str] = None  # 인정한 방향
        self.raw: Optional[str] = None  # 이번 프레임 값만 보고 정한 방향
        self.since: Optional[float] = None  # raw가 지금 값이 된 시각

    def update(self, facing, now):
        """프레임 하나의 손 방향 값으로 인정한 방향을 갱신해 돌려줍니다."""
        if facing is None:  # 손이 없음
            self.lost()
            return None
        # 히스테리시스: 인정한 방향은 threshold의 절반까지 그대로 유지
        if self.state == "palm" and facing > self.threshold / 2:
            return self.state
        if self.state == "back" and facing < -self.threshold / 2:
            return self.state
        raw = "palm" if facing > self.threshold else ("back" if facing < -self.threshold else "turning")
        if raw != self.raw:  # 이번 프레임 방향이 바뀜: 지속 시간을 처음부터 잼
            self.raw, self.since = raw, now
        if now - self.since >= self.dwell:  # 새 방향이 dwell초 이어짐: 인정
            self.state = raw
        elif self.state is None:  # 처음 보이는 손: 인정 전까지 회전 중
            self.state = "turning"
        return self.state
