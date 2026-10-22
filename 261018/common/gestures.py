"""시간에 따라 상태가 바뀌는 손 동작 판정기와 신호 필터 (표준 라이브러리만 사용).

모든 판정기는 프레임마다 `update(..., now)`를 받는다. now는 초 단위 시각(time.monotonic)이다.
손이 화면에서 사라지면 `lost()`를 불러 상태를 지운다. 돌아온 손은 준비 동작(손 펴기·엄지 떼기)을
다시 거쳐야 입력을 받는다. 사라진 순간의 자세로 입력이 나가지 않게 하기 위해서다.

- `TouchGate`: 같은 키에 일정 시간 머무르면 한 번 입력(엄지 마디 앱의 비교 기준 검출기가 쓴다)
- `FistHold`: 주먹을 일정 시간 유지하면 한 번(지움·확정·공백)
- `Orientation`: 손바닥 / 손등 / 회전 중. 순간적인 흔들림을 무시한다.
- `Kalman1D`: 커서 기준점의 떨림을 줄이는 1차원 칼만 필터
"""

from typing import Optional


# 손바닥 방향 값(common.pose.palm_facing)이 ±0.25를 넘으면 손바닥/손등으로 보고, 새 방향은 0.15초 이어져야
# 인정한다(2026-10-20 설계값).
FACING_THRESHOLD, FACING_DWELL = 0.25, 0.15


class TouchGate:
    """같은 키에 dwell초 머무르면 한 번 입력 → 모든 키에서 충분히 떨어진 상태가 release초 이어져야 재입력.

    update(key, fully_released, now)
      key: 지금 엄지가 닿은 키(없으면 None)
      fully_released: 모든 키에서 충분히 떨어졌는지
    손 유실(lost)은 떨어짐으로 보지 않는다. 시작할 때도 한 번 떨어져야 한다(armed=False에서 시작).
    """

    def __init__(self, dwell=0.12, release=0.10):
        self.dwell = dwell
        self.release = release
        self.armed = False  # 시작할 때도 엄지를 한 번 떼어야 한다.
        self.candidate: Optional[str] = None
        self.since = None
        self.release_since = None
        self.progress = 0.0  # 머무름 진행 0~1 (화면 표시용)

    def lost(self):
        self.armed = False
        self.candidate = self.since = self.release_since = None
        self.progress = 0.0

    def update(self, key, fully_released, now):
        self.progress = 0.0
        if not self.armed:
            # 준비 단계: 떨어진 상태가 release초 이어져야 입력을 받는다.
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
        if key != self.candidate:  # 다른 키로 옮기면 머무름 시간을 다시 잰다.
            self.candidate, self.since = key, now
        self.progress = min(1.0, (now - self.since) / self.dwell)
        if self.progress >= 1.0:
            self.armed = False
            self.candidate = self.since = self.release_since = None
            return key
        return None


class FistHold:
    """주먹을 hold초 유지하면 한 번. 손을 다시 펴야 다음 주먹을 센다.

    update(pose, now)는 이번 프레임에서 주먹이 확정되면 True를 돌려준다. pose는 common.pose의 자세 이름이다.
    처음 보이는 손이나 유실 후 돌아온 손은 먼저 펴야 한다. 주먹을 쥔 채 나타난 손은 세지 않는다.
    중간에 "closing"이 섞이면 유지 시간을 처음부터 다시 잰다.
    """

    def __init__(self, hold=0.18):
        self.hold = hold
        self.lost()

    def lost(self):
        self.armed = False
        self.since = None
        self.progress = 0.0  # 주먹 유지 진행 0~1 (화면 표시용)

    def update(self, pose, now):
        self.progress = 0.0
        if pose == "open":
            self.armed, self.since = True, None
            return False
        if pose != "fist" or not self.armed:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        self.progress = min(1.0, (now - self.since) / self.hold)
        if self.progress >= 1.0:
            self.armed, self.since = False, None
            return True
        return False


class Orientation:
    """손바닥(palm) / 손등(back) / 회전 중(turning). 새 방향은 dwell초 이어져야 인정한다.

    update(facing, now): facing은 common.pose.palm_facing 값(-1~1)이다.
    - facing > threshold → palm, facing < -threshold → back, 그 사이 → turning
    - 히스테리시스: 이미 인정한 방향은 threshold의 절반까지 유지한다.
    - 인정한 방향은 새 방향(turning 포함)이 dwell초 이어질 때까지 바꾸지 않는다. landmark가 한 프레임만 튀어
      방향 값이 흔들려도(2026-10-22 로그: +0.6 → -0.23 → +0.6) 입력이 끊기지 않게 하기 위해서다.
    - 처음 보이는 손은 dwell초가 지나기 전까지 turning이다.
    """

    def __init__(self, threshold=FACING_THRESHOLD, dwell=FACING_DWELL):
        self.threshold, self.dwell = threshold, dwell
        self.lost()

    def lost(self):
        self.state: Optional[str] = None  # 인정한 방향
        self.raw: Optional[str] = None  # 이번 프레임 값만 보고 정한 방향
        self.since: Optional[float] = None  # raw가 지금 값이 된 시각

    def update(self, facing, now):
        if facing is None:
            self.lost()
            return None
        if self.state == "palm" and facing > self.threshold / 2:
            return self.state
        if self.state == "back" and facing < -self.threshold / 2:
            return self.state
        raw = "palm" if facing > self.threshold else ("back" if facing < -self.threshold else "turning")
        if raw != self.raw:
            self.raw, self.since = raw, now
        if now - self.since >= self.dwell:
            self.state = raw
        elif self.state is None:
            self.state = "turning"
        return self.state


class Kalman1D:
    """위치·속도 상태의 1차원 칼만 필터(등속도 모델). 커서 기준점의 떨림을 줄인다.

    수업 위키 docs/wiki/Kalman-Filter.md의 예측(predict)·보정(update) 두 단계를 그대로 쓴다.
    q: 가속도 잡음 세기(클수록 빠른 움직임을 따라감), r: 측정 잡음 분산(클수록 부드러움).
    x 좌표와 y 좌표에 하나씩 따로 쓴다.
    """

    def __init__(self, q=2.0, r=4e-5):
        self.q, self.r = q, r
        self.reset(0.0, None)

    def reset(self, value, now):
        """필터를 value 위치, 속도 0에서 다시 시작한다."""
        self.x, self.v = value, 0.0
        self.p = [[self.r, 0.0], [0.0, 1.0]]  # 오차 공분산: 위치는 측정 잡음만큼, 속도는 모름(1)
        self.t = now

    def update(self, z, now):
        """측정값 z를 반영한 위치 추정값을 돌려준다."""
        dt = 1 / 30 if self.t is None else max(now - self.t, 1e-3)
        self.t = now
        q, p = self.q, self.p
        # 예측: x' = x + v·dt, P' = F P Fᵀ + Q (Q는 가속도 잡음에서 나온 연속 백색 잡음 모델)
        x = self.x + self.v * dt
        p00 = p[0][0] + dt * (p[1][0] + p[0][1]) + dt * dt * p[1][1] + q * dt ** 3 / 3
        p01 = p[0][1] + dt * p[1][1] + q * dt * dt / 2
        p10 = p[1][0] + dt * p[1][1] + q * dt * dt / 2
        p11 = p[1][1] + q * dt
        # 보정: 위치만 측정한다(H = [1, 0]). k0·k1은 위치·속도의 칼만 이득이다.
        s = p00 + self.r
        k0, k1 = p00 / s, p10 / s
        error = z - x
        self.x, self.v = x + k0 * error, self.v + k1 * error
        self.p = [[(1 - k0) * p00, (1 - k0) * p01], [p10 - k1 * p00, p11 - k1 * p01]]
        return self.x
