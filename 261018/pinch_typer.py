"""엄지-손끝 꼬집기 횟수로 고르는 양손 천지인 입력의 핵심 상태 머신 (표준 라이브러리만 사용).

카메라·MediaPipe와 분리되어 있다. 손마다 HandSignal(손가락별 꼬집기 비율, 굽힘, 자세, 손 방향)을 받아
한글 입력(CheonjiinIME)을 만든다. visionOS(ARKit)로 옮길 때도 같은 신호만 만들면 된다.

자판: 손 방향(손바닥/손등이 카메라를 향함)마다 다른 키가 놓인다(2026-10-23 모음 한 버튼, 쉬프트 없음).
                  검지      중지      약지      소지
  왼손 손바닥     ㅂㅍㅃ    모음      ㅅㅎㅆ    ㅈㅊㅉ
  오른손 손바닥   ㄱㅋㄲ    ㄴㄹ      ㄷㅌㄸ    ㅇㅁ
  왼손 손등       지움      공백      확정      . , ? ! 줄바꿈   (편집 키)
  오른손 손등     확정/공백 -         -         -     (후보가 있으면 확정, 없으면 공백. 손등은 손가락이 가려 검지만)

- 모음(왼손 중지): 꼬집고 그대로 떼면 ㆍ, 꼬집은 채 가로로 끌면 ㅡ, 세로로 끌면 ㅣ(천지인 획). DRAG_MIN 설명.
- 자음 그룹·문장부호는 같은 손·손가락을 다시 꼬집으면 그룹 안에서 순환하고, 다른 키를 꼬집으면 앞 후보가 확정된다
  (C 규칙). 같은 손가락 자음을 이어 쓸 때(안녕의 ㄴ→ㄴ)는 오른손 손등 검지로 확정한다.
- 주먹(양손): 지움. 마지막 자모 하나를 통째로 지우고(감 → 가 → ㄱ), 쥐고 있으면 FIST_REPEAT초마다 계속 지운다.
- 마우스 모드(오른손 손등 커서)는 2026-10-23 뺐다.
- 오른손 손등 아랫줄 자판(손등에서 네 손가락 꼬집기)을 시험했으나, 손등이 카메라를 향하면 꼬집는 손끝이 카메라
  반대쪽으로 굽고 엄지가 가려져 꼬집기 비율이 0.41~0.51에 머물렀다(2026-10-23 스냅숏). 그래서 오른손 손등에서는 검지만 받는다.
- 예전 자판(모음 세 손가락, 오른손 손등 검지 쉬프트 뒤의 ㅂ·ㅅ·ㅈ 그룹)은 2026-10-23 바꿨다(사용자: 모음은 한 버튼으로,
  자음 절반이 쉬프트 뒤에 있는 문제 해결).

구성
  landmark → 신호   pinch_ratios, hand_signal → HandSignal
  손 하나의 판정기  FingerPinch(어느 손가락을 꼬집었나), common.gestures의 Orientation(손바닥/손등)·FistHold(주먹)
  양손 입력기       PinchTyper.update: 프레임마다 양손 신호를 받아 이벤트 목록을 돌려준다.
  고정 데이터 재생  replay: 카메라 없이 꼬집기·주먹 순서만으로 입력을 재현한다(--demo, 테스트).
"""

from collections import deque
from dataclasses import dataclass
import math
import statistics
from types import SimpleNamespace
from typing import Optional

from korean_ime import CONSONANTS, CheonjiinIME, PreparedKey
from common.gestures import FACING_DWELL, FACING_THRESHOLD, FistHold, Orientation
from common.landmarks import DISTAL_SEGMENTS, MIDDLE_MCP, THUMB_TIP, WRIST, point_segment_distance, xyz
from common.pose import CURL_ANGLE, finger_angles, finger_flex, palm_facing, palm_roll, pose_from_angles


HANDS = ("Left", "Right")
FACES = ("palm", "back")  # 손바닥 / 손등이 카메라를 향함
# 손가락(검지·중지·약지·소지) 순서의 키. 키 이름은 korean_ime의 천지인 키 번호(자음 그룹 4~0, 모음 획 1~3)와 편집 키다.
LEFT_KEYS = ("7", "VOWEL", "8", "9")  # 왼손 손바닥: ㅂㅍㅃ · 모음(꼬집기 ㆍ / 가로 끌기 ㅡ / 세로 끌기 ㅣ) · ㅅㅎㅆ · ㅈㅊㅉ
RIGHT_KEYS = ("4", "5", "6", "0")  # 오른손 손바닥: ㄱㅋㄲ · ㄴㄹ · ㄷㅌㄸ · ㅇㅁ
# 왼손 손등: 편집 키(주먹 지우기가 연속으로 지우기에 느려서 추가). 여러 번 지울 때는 왼손을 뒤집고 검지를 반복해 꼬집는다.
# 소지는 문장부호 그룹(. , ? ! 줄바꿈)이고 같은 손가락을 다시 꼬집으면 순환한다.
EDIT_KEYS = ("BACK", "SPACE", "COMMIT", "PUNCT")
RIGHT_BACK_KEYS = ("COMMIT_SPACE", None, None, None)  # 오른손 손등 검지: 후보가 있으면 확정, 없으면 공백
KEYS = {("Left", "palm"): LEFT_KEYS, ("Left", "back"): EDIT_KEYS,
        ("Right", "palm"): RIGHT_KEYS, ("Right", "back"): RIGHT_BACK_KEYS}
PUNCTUATION = (".", ",", "?", "!", "\n")
VOWEL_LABELS = {"1": "ㅣ", "2": "ㆍ", "3": "ㅡ"}
EDIT_LABELS = {"BACK": "지움", "SPACE": "공백", "COMMIT": "확정", "COMMIT_SPACE": "공백", "VOWEL": "모음"}
# 모음 버튼의 끌기: 꼬집는 동안 꼬집기 지점(엄지 끝과 고른 손끝의 가운데, 이미지 좌표)이 가장 멀리 간 이동이 손 크기
# (이미지 좌표의 손목~중지 뿌리)의 DRAG_MIN배보다 작으면 끌기 없음(ㆍ), 크면 가로·세로 중 큰 쪽(ㅡ / ㅣ).
# 엄지 끝만 보지 않는 것은 떼며 엄지만 벌어지는 것을 끌기로 세지 않기 위해서고, 떼기 기준 위 프레임도 세지 않는다.
# 0.25(손 크기 약 9cm면 약 2.3cm)는 처음 값이다(사용자와 정함, 써 보며 조정).
DRAG_MIN = 0.25
DRAG_STROKES = {None: "2", "h": "3", "v": "1"}  # 끌기 → 천지인 획: 없음 ㆍ, 가로 ㅡ, 세로 ㅣ
# 주먹(양손 모두 지움)을 쥐고 있으면 이 간격(초)마다 한 번씩 더 지운다(2026-10-23 사용자 지정).
FIST_REPEAT = 0.5

# 꼬집기 거리: 엄지 끝(4)~손가락 끝마디 선분 world 거리 / 손목~중지 뿌리 길이(약 9~10cm).
# landmark는 뼈 축 위에 있어서 살이 맞닿아도 0이 되지 않는다(엄지·손가락 두께만큼 남는다).
# 판정값은 모든 사람에게 같은 고정값이다. 사용자별 보정 단계는 두지 않는다(2026-10-20 결정).
#
# 절대 거리 하나로는 어느 손가락인지 가를 수 없다(2026-10-22 정답 기록 data/collect-*.jsonl).
#   실제 꼬집기의 최저값: 검지·중지 0.00~0.15, 약지 0.02~0.24, 소지 0.17~0.23
#   꼬집지 않은 검지(쉬는 자세, 엄지가 지나갈 때): 0.11~0.28
# 그래서 "직전 PINCH_WINDOW초 동안의 자기 값(기준값)보다 PINCH_DROP 이상 다가온 손가락"만 후보로 본다.
# 쉬는 자세에서 원래 가까운 검지는 다가오지 않으므로 후보가 되지 않는다.
#   PINCH_ENTER: 후보가 되려면 이 값 아래여야 한다(가까움의 상한)   PINCH_EXIT: 이 값 위로 벌리면 항상 뗌
#   PINCH_DROP: 기준값보다 이만큼 다가와야 후보   PINCH_WINDOW: 기준값(최근 최대값)을 보는 시간(초)
#   PINCH_MARGIN: 후보 중 가장 가까운 손끝이 다른 손끝보다 이만큼 가까워야 그 손가락으로 본다
#   PINCH_MIN_CONTACT: 이보다 짧은 접촉(초)은 버린다
# PINCH_DROP 0.15: 같은 손가락을 빠르게 반복할 때 0.36 → 0.18처럼 0.16~0.18만 다가오는 경우가 있다
# (2026-10-20 로그 ㄱㅋㄲㄱ). 0.20이면 이 중 3번을 놓친다. '안녕하세요'에는 같은 손가락 반복이 4번 있다.
# PINCH_MIN_CONTACT 0.10: 2026-10-23 '안녕하세요' 기록(pinch-log-1023)에서 엄지가 검지를 0.07초 스친 ㄱ과,
# 중지 꼬집기 뒤 0.10초 접촉이 한 번 더 잡혀 ㅅ이 ㅎ으로 바뀐 경우가 있었다. 정답 기록의 실제 꼬집기는 모두 0.13초
# 이상이다. 걸러진 0.10초 미만 접촉 15개는 모두 다른 손·다른 손가락이거나 얕은 스침이었다(전체 기록 재생).
# 떼기에 RELEASE_FRAMES 프레임이 필요해 잰 접촉 시간은 최소 2프레임 간격이다. 15fps 이하(Jetson)에서는 짧은 스침도
# 0.13초 이상으로 재져 이 규칙이 거르지 못한다. 실제 꼬집기를 버리는 쪽으로는 움직이지 않는다.
PINCH_ENTER, PINCH_EXIT, PINCH_MARGIN, PINCH_MIN_CONTACT = 0.30, 0.38, 0.03, 0.10
PINCH_DROP, PINCH_WINDOW = 0.15, 0.5
# 얕은 접촉: 접촉 중 최저값이 PINCH_SHALLOW 위에 머문 채 벌어지는 손끝은, 엄지가 그 옆을 지나 다른 손가락으로
# 가는 중일 수 있다. 그때 더 가까운 다른 후보가 있으면 떼지 않고 그 손가락으로 옮긴다.
# 2026-10-23 기록(40차례 중 오입력 1): 오른손 중지를 꼬집으러 가며 검지가 0.25까지 다가왔다가 벌어져 검지로 입력됐다.
# 실제 꼬집기의 최저값은 0.00~0.23(40개 중 0.20 초과 2개)이다. 깊게 꼬집은 뒤 엄지가 돌아가며 이웃 손가락을
# 지나가는 경우(소지 → 약지)에 옮기지 않도록, 0.20보다 얕은 접촉에만 적용한다. 옮겨 갈 손끝은 지금도 enter 아래이고
# margin 이상 더 가까워야 한다(10-22 로그 648594.8: 얕은 중지 꼬집기를 뗄 때 검지가 0.006 더 가까웠던 경우를 제외).
PINCH_SHALLOW = 0.20
# 떼기: 고른 손끝이 접촉 중 최저값보다 RELEASE_RISE 이상 벌어지면(exit 위면 항상) 뗀 것으로 본다.
# 빠르게 연속으로 꼬집을 때는 exit까지 벌리지 않는다(2026-10-22 로그: 0.33~0.37).
# landmark가 한 프레임만 튀는 경우(같은 로그: 0.17 → 0.58 → 0.18)를 떼기로 세지 않도록 RELEASE_FRAMES 연속을 요구한다.
# RELEASE_RISE 0.12 → 0.08(2026-10-23): 눌린 채로 다시 꼬집을 때 사용자는 0.10~0.13만 벌렸고, 0.12를 넘은 것도 1프레임씩이라
# 접촉 하나가 2.8초 이어지는 동안 4~5번의 꼬집기가 입력 1번이 됐다(collect-20261023-181428 36.1~38.9초, 사용자: "핀치를 해도
# 버튼이 눌리지 않는다"). 쥐고 있는 동안 값의 흔들림은 ±0.02 정도다.
# 2026-10-23 저녁: 0.08로 낮췄다가 스침이 더 잘 입력돼(사용자: 살짝 스치기만 해도 입력) C안 결정에 따라 0.12로 되돌렸다.
RELEASE_RISE, RELEASE_FRAMES = 0.12, 2
# 같은 손가락 다시 꼬집기: 방금 뗀 손가락은 REPRESS_WINDOW초 안이면 PINCH_DROP 대신 REPRESS_DROP만 다가와도 후보로 본다.
# 위 기록에서 다시 꼬집은 폭은 0.08~0.12였다(덜 벌린 상태에서 시작하므로). 0.06까지 낮추면 정답 없는 로그의 입력이 더
# 늘어나 확인할 수 없어 0.08로 둔다.
# 시험(정답 기록 117차례): RELEASE_RISE·REPRESS_DROP 0.08, 떼는 중 옮기지 않기를 함께 적용하면 첫 입력 100 그대로, 틀림 4 → 2,
# 긴 접촉 6곳의 입력 1개씩 → 2~3개. 10-22 로그의 손가락별 4회 구간에서 빠졌던 입력 3개가 채워진다.
REPRESS_DROP, REPRESS_WINDOW = 0.08, 1.0
# 주먹을 쥐는 도중 엄지가 손끝을 스치는 것을 버리는 기준: 꼬집는 손가락 말고 다른 손가락이 SPOIL_CURLED개 이상 굽음.
# 예전 값 2는 소지 꼬집기를 막았다. 소지를 꼬집으면 중지·약지가 함께 굽는다(curled 0111). 2026-10-23 기록에서 오른손
# 소지 첫 차례가 0.00~0.05까지 꼬집은 시도 10번을 모두 버려 10.7초 걸렸다. 주먹은 검지까지 넷 다 굽으므로 3으로 둔다.
# 3으로 바꿔 모든 로그를 다시 돌리면 꼬집기 223 → 238개, 주먹(지움) ±0.6초 안의 꼬집기는 그대로 0개다.
SPOIL_CURLED = 3
# 검지 겹침: 엄지가 검지 옆에서 쉬다가 중지·약지를 꼬집으면 엄지 옆면이 검지를 눌러, 검지 값도 꼬집은 손가락만큼
# 내려온다(사용자: "엄지가 검지를 밀어내듯이"). 그러면 접촉이 검지에서 시작해 검지로 입력된다.
# 2026-10-23 오른손 기록(collect-20261023-175304) 20차례 중 4번이 이렇게 틀렸다(중지 3·약지 1).
# 꼬집은 손가락은 엄지 쪽으로 굽고, 눌린 검지는 거의 굽지 않는다. 그래서 검지로 판정된 접촉에서 다른 손가락의
# 최저값이 검지 최저값 + INDEX_TIE 안에 있고, 접촉 동안 굽힘(common.pose.finger_flex)이 접촉 직전 FLEX_BASE초
# 평균보다 검지보다 FLEX_GAIN° 이상 더 늘었으면 그 손가락(여럿이면 최저값이 가장 낮은 손가락)으로 바꾼다.
# 정답이 있는 접촉 90개: 78 → 83개 맞음, 바뀐 5개 모두 맞게 바뀜. 실제 검지 꼬집기 24개는 다른 손가락이
# 검지 최저값 + 0.05 안에 들어온 적이 없어 이 규칙이 작동하지 않았다. 판정값 0.05~0.08, 5~10°에서 결과가 같다.
# 검지 말고 이웃 손가락끼리(중지↔약지, 약지↔소지)는 함께 굽어 굽힘으로 가르면 오히려 틀려(3개) 적용하지 않는다.
INDEX_TIE, FLEX_GAIN, FLEX_BASE = 0.05, 10.0, 0.3
# 오른손 검지 지나감: 엄지가 쉬는 자리가 검지 옆이라, 다른 손가락으로 가거나 돌아올 때 검지를 실제로 지나간다(영상 확인).
# 2026-10-23 자유 입력(pinch-log-1023b, 69초)의 오른손 검지 입력 11개 중 10개가 오입력이었다.
#   되돌아가며 지나감: 다른 손가락 입력을 뗀 뒤 0.10~0.13초 만에 검지 접촉이 시작됨(3개).
#   굽지 않은 채 스침·머묾: 접촉 동안 검지 굽힘 증가 -48~+2°(4개).
# 정답 기록의 실제 오른손 검지 꼬집기는 이전 입력과 0.63초 이상 떨어져 있었고, 굽힘 증가는 +7~+23°였다.
# 그래서 오른손 손바닥 검지 입력은 다른 손가락 입력 뒤 INDEX_REPASS초 안에 시작했거나, 굽힘 증가가 INDEX_FLEX_MIN° 미만이면
# 버린다. 왼손은 ㅓ·ㅕ·ㅔ처럼 ㆍ→ㅣ로 빠르게 넘어가는 입력이 정상이라 적용하지 않는다.
INDEX_REPASS, INDEX_FLEX_MIN = 0.25, 5.0
# 누르고 유지(2026-10-23 사용자 결정 "A안"): 고른 손끝이 HOLD_DEPTH 아래로 HOLD_TIME초 머물면 그 순간 입력한다(뗄 때가 아니라).
# 거리만으로는 스침과 꼬집기가 갈리지 않았다(정답 기록의 의도한 꼬집기 102개 vs 자유 입력에서 바로 지운 스침 14개:
# 최저 거리 중앙 0.08 vs 0.07, 머문 프레임 3 vs 3, 접촉 길이 0.23 vs 0.27초). 스침은 대부분 엄지가 실제로 손끝에 닿았다가
# 곧 떠난다: 0.20 아래에 0.3초 넘게 머문 스침은 14개 중 2개. HOLD_DEPTH 0.20이면 의도한 꼬집기가 손가락마다 거의 다
# 내려온다(검지 25/28, 중지 28/28, 약지 22/23, 소지 22/23; 0.15면 소지 18/23). 값이 흔들려도 HOLD_DEPTH + HOLD_SLACK 위로
# 벌어지기 전에는 유지로 본다. hold가 0이면 예전처럼 뗄 때 입력한다(C안: 되돌릴 때는 RELEASE_RISE도 0.12로).
# 실험 결과(사용자 사용, collect-20261023-185410): 엄지가 쉬거나 돌아오는 자리(검지 옆 0.12~0.2, 누른 손끝의 이웃)에 머문 것도
# 입력돼 더 나빴다. 그래서 기본은 뗄 때 입력(HOLD_TIME 0)이고, --hold로만 켠다(C안, 사용자 결정).
HOLD_TIME, HOLD_DEPTH, HOLD_SLACK = 0.0, 0.20, 0.05
# 검지는 HOLD_INDEX_DEPTH 아래에 머물러야 입력한다. 엄지가 쉬는 자리가 검지 옆이라, 쉬는 동안 검지 값이 0.12~0.2에 머문다
# (2026-10-23 단어 기록 collect-20261023-185410: 중지를 누른 뒤 엄지가 돌아와 쉬자 ㅣ가 입력됨, 138.6초 0.12~0.18).
# 실제 검지 꼬집기의 최저값 중앙값은 왼손 0.08, 오른손 0.06이다. 누르는 동안 유지 진행 호가 차지 않으면 더 깊게 누르면 된다.
HOLD_INDEX_DEPTH = 0.10
# 손 방향이 바뀐 뒤 SETTLE초 동안은 꼬집기 판정에 넣지 않는다. 손을 돌리는 동안 검지 값이 0.5 → 0.2로 내려와 다가온 것으로
# 보이고, 엄지가 검지 옆에서 쉬면 그대로 입력됐다(같은 기록 113.0·134.5초: 왼손 손등으로 지운 뒤 손바닥으로 돌림).
SETTLE = 0.3
# 손 방향 확인: 접촉의 처음 STEADY_FRAMES 프레임과 마지막 STEADY_FRAMES 프레임의 손 방향 값(facing) 중앙값이 모두
# 그 방향(손바닥 ≥ FACING_THRESHOLD, 손등 ≤ -FACING_THRESHOLD)이어야 입력한다.
# Orientation은 새 방향이 FACING_DWELL초 이어져야 바뀌므로, 손을 뒤집는 순간에는 이전 방향으로 판정된다.
# 2026-10-23 기록(pinch-log-1023)에서 이 때문에 5번 잘못 입력됐다: 손등으로 뒤집으며 뗀 손바닥 꼬집기가 ㄱ(2번),
# 손바닥으로 돌아오며 시작한 꼬집기가 손등 검지로 판정되어 쉬프트 꺼짐(2번), 손바닥 쪽 값(+0.48)인데 손등 소지 줄바꿈(1번).
# 접촉 내내 그 방향을 요구하면 facing이 한 프레임만 반대로 튀는 경우(+0.7 → -0.38)에 실제 입력 8개가 막혀(10-22 기록),
# 시작과 끝의 중앙값만 본다. 이 규칙으로 위 5개 중 4개가 막히고, 정답 기록의 결과는 그대로다.
STEADY_FRAMES = 3
# 엄지 가림: 손바닥에서 소지 쪽이 카메라에 가깝게 기울면(palm_roll ≤ OCCLUDED_ROLL) 엄지와 맞닿는 곳이 손바닥·손가락 뒤에
# 가려진다. 그때 MediaPipe는 엄지를 검지 옆에 있다고 추정한다. 2026-10-23 기록(collect-20261023-143853, 왼손)에서
# roll -28° 이하 차례 8개 중 7개가 틀렸다. 중지·약지·소지를 꼬집어도 그 손가락 값은 0.44~0.70에 머물고 검지만
# 0.13~0.18로 내려가 ㄱ(검지)이 입력됐다. 신호에 정보가 없으므로 판정값으로는 고칠 수 없어, 그 자세의 손바닥 꼬집기는
# 받지 않고 화면에 손을 돌리라고 안내한다(사용자 결정). 다른 기록의 손바닥 꼬집기는 가장 기운 것이 -14.2°다.
# 엄지 쪽이 카메라에 가까운 기울기(+32°까지 9차례)는 모두 맞았다. 손등 꼬집기는 이 규칙을 쓰지 않는다.
OCCLUDED_ROLL = -25.0


def group_of(key):
    """키가 순환하는 자모·기호 목록."""
    return PUNCTUATION if key == "PUNCT" else CONSONANTS[key]


def is_group_key(key):
    """같은 손가락을 다시 꼬집으면 순환하는 키(자음 그룹·문장부호)인가."""
    return key == "PUNCT" or key in CONSONANTS


def key_label(key):
    """자판 지도에 쓰는 키 이름."""
    if key == "PUNCT":
        return ".,?!"
    return VOWEL_LABELS.get(key) or EDIT_LABELS.get(key) or CONSONANTS[key]


def drag_of(dx, dy, size):
    """꼬집는 동안의 이동 (dx, dy)와 손 크기 → 끌기: None(없음) / "h"(가로) / "v"(세로). 이미지 좌표(y 아래)."""
    if size <= 0 or math.hypot(dx, dy) < DRAG_MIN * size:
        return None
    return "h" if abs(dx) >= abs(dy) else "v"


CYCLE_SHOWN = 3  # 손가락 위에 보이는 순환 글자 수(문장부호 그룹 5개는 앞 3개만)


# 손끝 라벨(화면 표시용). 앱(pinch_cheonjiin)과 Vision Pro 앱(pinch_vision)이 함께 쓴다.

def tip_text(typer, hand, finger):
    """손끝 라벨: 이 손가락을 지금 방향에서 다음에 꼬집으면 입력될 것(손이 회전 중이면 손바닥 자판). 없으면 None."""
    queue = cycle_queue(typer, hand, finger)
    return queue[0] if queue else None


def cycle_queue(typer, hand, finger):
    """그룹 키(자음 그룹·문장부호)의 순환 대기열: 손끝(다음 꼬집기로 입력될 글자)부터 그 뒤에 나올 글자 순서.

    같은 손·손가락·방향을 다시 꼬집으면 PinchTyper.press_finger가 group[pending.count % n]을 넣으므로, 순환 중이면
    그룹을 pending.count만큼 돌린 순서다. 그 밖에는 그룹 첫 글자부터.
    모음·편집 키·확정/공백은 글자 하나, 자판에 없는 자리는 빈 목록이다.
    """
    face = typer.face(hand) or "palm"
    key = KEYS[(hand, face)][finger]
    if key is None:
        return []
    if key == "COMMIT_SPACE":  # 오른손 손등 검지: 후보가 있으면 확정, 없으면 공백
        return ["확정" if typer.pending is not None else "공백"]
    if not is_group_key(key):
        return [key_label(key)]
    start, pending = 0, typer.pending
    if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, face):
        start = pending.count
    group = ["줄" if char == "\n" else char for char in group_of(key)]
    return [group[(start + i) % len(group)] for i in range(min(len(group), CYCLE_SHOWN))]


# ---------------------------------------------------------------------------
# landmark → 신호. x, y, z 속성만 쓰므로 MediaPipe 타입이 아니어도 된다.

@dataclass
class HandSignal:
    """상태 머신이 받는 손 하나의 신호. 카메라·MediaPipe와 무관한 값만 담는다."""
    ratios: tuple  # 검지·중지·약지·소지 끝마디까지의 꼬집기 비율(pinch_ratios)
    curled: tuple = (False, False, False, False)  # 손가락별 굽음(world PIP 각도 ≤ CURL_ANGLE)
    pose: str = "open"  # open / closing / fist
    facing: float = 1.0  # > 0 손바닥, < 0 손등이 카메라를 향함(common.pose.palm_facing)
    roll: float = 0.0  # 긴 축 기준 기울기(도). < 0 소지 쪽이 카메라에 가까움(common.pose.palm_roll)
    flex: Optional[tuple] = None  # 손가락별 굽힘(도, common.pose.finger_flex). 없으면 검지 겹침 규칙을 쓰지 않는다
    # 모음 끌기용 이미지 좌표(Mac 픽셀, Vision Pro mm): 엄지 끝과 네 손끝 (x, y), 손 크기(손목~중지 뿌리). 없으면 끌기 없음.
    tips: Optional[tuple] = None
    size: float = 0.0


def image_tips(landmarks, width=1.0, height=1.0):
    """(엄지 끝·검지·중지·약지·소지 끝 이미지 좌표 (x, y), 손 크기 = 손목~중지 뿌리 이미지 거리)."""
    points = [(landmarks[i].x * width, landmarks[i].y * height) for i in (THUMB_TIP, 8, 12, 16, 20)]
    wrist, middle = landmarks[WRIST], landmarks[MIDDLE_MCP]
    size = math.hypot((middle.x - wrist.x) * width, (middle.y - wrist.y) * height)
    return tuple(points), size


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
    tips, size = image_tips(landmarks, width, height)
    return HandSignal(
        ratios=pinch_ratios(world),
        curled=tuple(angle <= CURL_ANGLE for angle in angles),
        pose=pose or pose_from_angles(angles),
        facing=palm_facing(landmarks, label, mirrored, width, height),
        roll=palm_roll(landmarks, width, height),
        flex=finger_flex(world),
        tips=tips,
        size=size,
    )


def signal_from_log(entry, size=(1.0, 1.0)):
    """--log 한 행의 손 하나(hands[손])를 HandSignal로. roll이 없는 예전 기록은 image landmark로,
    굽힘(flex)은 world landmark로, 모음 끌기용 손끝 좌표는 image landmark로 계산한다(없는 기록은 기본값)."""
    roll, tips, hand_size = entry.get("roll"), None, 0.0
    if "image" in entry:
        points = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in entry["image"]]
        tips, hand_size = image_tips(points, *size)
        if roll is None:
            roll = palm_roll(points, *size)
    flex = None
    if "world" in entry:
        flex = finger_flex([SimpleNamespace(x=x, y=y, z=z) for x, y, z in entry["world"]])
    return HandSignal(tuple(entry["ratios"]), tuple(entry["curled"]), entry["pose"], entry["facing"], roll or 0.0, flex,
                      tips, hand_size)


# ---------------------------------------------------------------------------
# 손 하나의 판정기

class FingerPinch:
    """어느 손끝을 꼬집었는지. 닿았다가 떨어지는 순간 (손가락, 지속 시간)을 돌려준다.

    1) 접촉 전: 손가락마다 최근 window초의 최대 비율을 기준값으로 둔다.
    2) 후보: 기준값보다 drop 이상 다가왔고 enter 아래인 손끝. 그중 가장 가까운 손끝이 다른 모든 손끝보다
       margin 이상 가까우면 접촉을 시작한다(중지를 꼬집으면 약지도 함께 가까워지므로 거리 차이로 본다).
    3) 접촉 중: 손가락마다 최저값을 갱신하고, 후보 중 가장 가까이 온 손끝으로 옮겨 간다(margin 이상 차이 날 때, 떼는 중이 아닐 때).
       엄지가 약지로 가는 길에 검지 옆을 먼저 지나가도, 실제로 꼬집은 약지가 더 가까워지므로 약지가 된다.
       고른 손끝이 얕게(최저값 > PINCH_SHALLOW) 다가왔다가 벌어지는데 더 가까운 후보가 있으면, 떼지 않고 그쪽으로 옮긴다.
    4) 입력: 누르고 유지 방식(hold > 0, 기본)은 고른 손끝이 HOLD_DEPTH 아래에 hold초 머문 순간 1회, 뗄 때까지 다시 입력하지 않는다.
       뗄 때 입력 방식(hold = 0)은 고른 손끝이 떼기 기준(release_level) 위로 RELEASE_FRAMES 프레임 연속이면 1회.
       min_contact보다 짧거나, 접촉 중 주먹이 되거나 다른 세 손가락이 모두 굽으면(주먹을 쥐는 도중) 버린다.
       검지로 판정됐는데 최저값이 비슷한 다른 손가락이 훨씬 더 굽었으면 그 손가락으로 바꾼다(검지 겹침, INDEX_TIE).
       접촉이 끝나면 기준값 기록을 비운다(다음 꼬집기는 뗀 뒤의 값을 기준으로 한다).
       방금 뗀 손가락은 REPRESS_WINDOW초 안에 REPRESS_DROP만 다가와도 다시 후보가 된다(덜 벌리고 다시 꼬집기).
    - 꼬집은 채로 나타난 손은 기준값이 지금 값과 같아 다가온 손끝이 없으므로 입력되지 않는다.
    """

    def __init__(self, enter=PINCH_ENTER, exit=PINCH_EXIT, margin=PINCH_MARGIN, min_contact=PINCH_MIN_CONTACT,
                 drop=PINCH_DROP, window=PINCH_WINDOW, hold=HOLD_TIME):
        if exit <= enter:
            raise ValueError("exit는 enter보다 커야 합니다.")
        self.enter, self.exit, self.margin, self.min_contact = enter, exit, margin, min_contact
        self.drop, self.window = drop, window
        self.hold = hold  # 누르고 유지 시간(초). 0이면 뗄 때 입력
        self.index_depth = HOLD_DEPTH  # 검지가 머물렀다고 볼 거리(손바닥에서는 HOLD_INDEX_DEPTH)
        self.lost()

    def lost(self):
        self.history = deque()  # 접촉 전 최근 (시각, 비율) — 기준값 계산용
        self.flexes = deque()  # 접촉 전 최근 (시각, 손가락별 굽힘) — 검지 겹침 규칙의 굽힘 기준
        self.flex_base: Optional[tuple] = None  # 접촉 직전 FLEX_BASE초 평균 굽힘
        self.flex_top: Optional[list] = None  # 접촉 중 손가락별 최대 굽힘
        self.last_tap: Optional[tuple] = None  # 직전 입력 (손가락, 뗀 시각) — 같은 손가락 다시 꼬집기
        self.released: Optional[dict] = None  # 방금 뗀 접촉: 시작 시각, 손가락별 굽힘 증가, 그 전의 last_tap
        self.active: Optional[int] = None
        self.since: Optional[float] = None
        self.spoiled = False
        self.base = (1.0,) * 4  # 접촉을 시작할 때의 손가락별 기준값
        self.low = [1.0] * 4  # 접촉 중 손가락별 최저 비율
        self.opened = 0  # 떼기 기준 위로 연속된 프레임 수
        self.hold_finger: Optional[int] = None  # 유지 시간을 재는 손가락
        self.hold_since: Optional[float] = None  # 그 손가락이 HOLD_DEPTH 아래로 내려온 시각
        self.fired: Optional[int] = None  # 이번 접촉에서 유지로 입력된 손가락(뗄 때까지 다시 입력하지 않는다)
        self.counted = False  # 그 입력이 받아들여졌는가(PinchTyper가 버리면 undo_tap이 False로)

    @property
    def armed(self):
        """기준값을 잴 프레임이 있는가(처음 보이거나 유실 후 돌아온 손은 한 프레임 뒤부터)."""
        return bool(self.history) or self.active is not None

    def release_level(self):
        return min(self.exit, self.low[self.active] + RELEASE_RISE)

    def hold_depth(self, finger):
        """그 손끝이 머물렀다고 볼 거리. 검지는 손바닥 방향에서만 더 깊게(index_depth, PinchTyper가 정한다)."""
        return self.index_depth if finger == 0 else HOLD_DEPTH

    def hold_progress(self, now):
        """화면 표시용: 누르고 유지 진행(0~1). 유지 방식이 아니거나 접촉 중이 아니면 0, 이미 입력됐으면 1."""
        if not self.hold or self.active is None:
            return 0.0
        if self.fired == self.active:
            return 1.0
        if self.hold_since is None or self.hold_finger != self.active:
            return 0.0
        return min(1.0, (now - self.hold_since) / self.hold)

    def release_progress(self, ratios):
        """화면 표시용: 고른 손끝을 얼마나 벌렸는지(0~1). 1이 되면(RELEASE_FRAMES 동안) 입력된다. 접촉 중이 아니면 0."""
        if self.active is None:
            return 0.0
        low, level = self.low[self.active], self.release_level()
        return min(1.0, max(0.0, (ratios[self.active] - low) / max(level - low, 1e-6)))

    def progress(self, ratios):
        """손가락별 꼬집기 진행(0~1, 화면 표시용): 기준값보다 다가온 정도 / drop. exit 위로 떨어져 있으면 0.

        쉬는 자세에서 원래 가까운 손가락은 다가오지 않았으므로 0에 머문다(판정 규칙과 같은 기준).
        """
        if self.active is not None:
            base = self.base
        elif self.history:
            base = [max(row[j] for _, row in self.history) for j in range(4)]
        else:
            base = ratios
        return [0.0 if value > self.exit else min(1.0, max(0.0, (top - value) / self.drop))
                for top, value in zip(base, ratios)]

    def _candidates(self, base, values):
        """기준값보다 drop 이상 다가왔고 enter 아래인 손가락, 가까운 순."""
        return sorted((j for j in range(4) if base[j] - values[j] >= self.drop and values[j] <= self.enter),
                      key=values.__getitem__)

    def _index_overlap(self, finger):
        """검지 겹침 규칙: 검지로 판정된 접촉을 더 굽은 다른 손가락으로 바꿀지(INDEX_TIE 설명)."""
        if finger != 0 or self.flex_base is None or self.flex_top is None:
            return finger
        gain = [top - base for top, base in zip(self.flex_top, self.flex_base)]
        others = [j for j in (1, 2, 3)
                  if self.low[j] <= self.low[0] + INDEX_TIE and gain[j] >= gain[0] + FLEX_GAIN]
        return min(others, key=self.low.__getitem__) if others else finger

    def index_sweep(self):
        """방금 뗀 검지 접촉이 엄지가 검지를 지나간 것인가(INDEX_REPASS 설명). "repass" / "flat" / None."""
        info = self.released
        if info is None:
            return None
        previous = info["previous"]
        if previous is not None and previous[0] != 0 and info["start"] - previous[1] < INDEX_REPASS:
            return "repass"
        if info["gain"] is not None and info["gain"][0] < INDEX_FLEX_MIN:
            return "flat"
        return None

    def undo_tap(self):
        """방금 돌려준 입력을 없던 것으로 한다(직전 입력 기록을 그 전으로 되돌린다)."""
        if self.released is not None:
            self.last_tap = self.released["previous"]
        self.counted = False

    def _emit(self, now, start=None):
        """입력 하나를 만든다: (손가락, 접촉 시간). 검지 지나감 판정용 정보를 released에 남긴다.

        start: 검지 지나감의 '시작' 시각(유지 방식은 그 손끝에 머물기 시작한 시각, 뗄 때 방식은 접촉 시작).
        """
        finger = self._index_overlap(self.active)
        gain = None
        if self.flex_base is not None and self.flex_top is not None:
            gain = [top - base for top, base in zip(self.flex_top, self.flex_base)]
        self.released = {"start": self.since if start is None else start, "gain": gain, "previous": self.last_tap}
        self.last_tap, self.counted = (finger, now), True
        return finger, now - self.since

    def _end_contact(self):
        self.active = self.since = None
        self.history.clear()
        self.flexes.clear()
        self.flex_base = self.flex_top = None
        self.spoiled, self.low, self.opened = False, [1.0] * 4, 0
        self.hold_finger = self.hold_since = self.fired = None

    def _hold_update(self, signal, now):
        """누르고 유지: 접촉 중에는 매 프레임 가장 가까운 손끝에 머문 시간을 잰다. hold초 머물면 그 손끝을 입력한다.

        - 엄지가 검지를 거쳐 중지에 머물면 중지가 입력된다(거쳐 간 손끝은 머문 시간이 짧다).
          지금 손끝보다 margin 이상 가까운 손끝이 있을 때만 대상을 옮긴다(중지를 누르면 약지도 비슷하게 가깝다).
        - 입력된 손끝은 그 손끝에서 조금 떨어졌다가(hold_depth + HOLD_SLACK 위) 다시 머물면 다시 입력된다.
        - 주먹을 쥐는 중(주먹 자세이거나 다른 세 손가락이 굽음)인 프레임은 머문 시간을 잇지 않는다.
        - 모든 손끝이 exit 위로 RELEASE_FRAMES 프레임 멀어지면 접촉을 끝낸다.
        """
        ratios = signal.ratios
        target = min(range(4), key=ratios.__getitem__)
        if self.hold_finger is not None and ratios[self.hold_finger] - ratios[target] < self.margin:
            target = self.hold_finger  # 지금 손끝보다 margin 이상 가까운 손끝이 있을 때만 옮긴다(이웃 손끝 값이 엇갈림)
        depth = self.hold_depth(target)
        others = sum(curled for index, curled in enumerate(signal.curled) if index != target)
        if signal.pose == "fist" or others >= SPOIL_CURLED:
            self.hold_finger, self.hold_since = target, None
        elif target != self.hold_finger:
            self.hold_finger, self.hold_since = target, (now if ratios[target] <= depth else None)
        elif ratios[target] > depth + HOLD_SLACK:
            self.hold_since = None
        elif self.hold_since is None and ratios[target] <= depth:
            self.hold_since = now
        if self.fired is not None and ratios[self.fired] > self.hold_depth(self.fired) + HOLD_SLACK:
            self.fired = None  # 입력된 손끝에서 떨어졌다: 다시 누를 수 있다
        self.active = target
        if self.hold_since is not None and now - self.hold_since >= self.hold and self.fired != target:
            self.fired = target
            return self._emit(now, self.hold_since)
        if min(ratios) <= self.exit:
            self.opened = 0
            return None
        self.opened += 1
        if self.opened < RELEASE_FRAMES:
            return None
        if self.counted and self.last_tap is not None:  # 같은 손가락 다시 꼬집기 기준은 뗀 시각
            self.last_tap = (self.last_tap[0], now)
        self._end_contact()
        return None

    def _check(self, signal):
        others = sum(curled for index, curled in enumerate(signal.curled) if index != self.active)
        if signal.pose == "fist" or others >= SPOIL_CURLED:
            self.spoiled = True

    def update(self, signal, now):
        if signal is None:
            self.lost()
            return None
        self.released = None
        ratios = signal.ratios
        if self.active is None:
            self.history.append((now, ratios))
            while now - self.history[0][0] > self.window:
                self.history.popleft()
            earlier = [flex for t, flex in self.flexes if now - t <= FLEX_BASE]  # 이번 프레임 전의 굽힘
            if signal.flex is not None:
                self.flexes.append((now, signal.flex))
                while now - self.flexes[0][0] > FLEX_BASE:
                    self.flexes.popleft()
            base = tuple(max(row[j] for _, row in self.history) for j in range(4))
            candidates = self._candidates(base, ratios)
            if self.last_tap is not None and now - self.last_tap[1] <= REPRESS_WINDOW:  # 같은 손가락 다시 꼬집기
                again = self.last_tap[0]
                if again not in candidates and base[again] - ratios[again] >= REPRESS_DROP \
                        and ratios[again] <= self.enter:
                    candidates = sorted(candidates + [again], key=ratios.__getitem__)
            if candidates:
                best = candidates[0]
                if min(ratios[j] for j in range(4) if j != best) - ratios[best] >= self.margin:
                    self.active, self.since, self.spoiled, self.opened = best, now, False, 0
                    self.base, self.low = base, list(ratios)
                    self.hold_finger, self.hold_since = best, (now if ratios[best] <= HOLD_DEPTH else None)
                    if signal.flex is not None:
                        earlier = earlier or [signal.flex]
                        self.flex_base = tuple(sum(f[j] for f in earlier) / len(earlier) for j in range(4))
                        self.flex_top = list(signal.flex)
                    self._check(signal)
            return None
        self.low = [min(a, b) for a, b in zip(self.low, ratios)]
        if self.flex_top is not None and signal.flex is not None:
            self.flex_top = [max(a, b) for a, b in zip(self.flex_top, signal.flex)]
        if self.hold:
            return self._hold_update(signal, now)
        candidates = self._candidates(self.base, self.low)
        # 고른 손끝을 떼는 중(직전 프레임이 떼기 기준 위)에는 다른 손가락으로 옮기지 않는다. 떼며 엄지가 옆 손가락으로
        # 미끄러진 것을 그 손가락의 꼬집기로 바꾸지 않기 위해서다(collect-20261023-175304 차례 17: 중지를 떼는 중 검지 0.03).
        if candidates and self.low[self.active] - self.low[candidates[0]] >= self.margin and self.opened == 0:
            self.active = candidates[0]
        if ratios[self.active] > self.release_level() and self.low[self.active] > PINCH_SHALLOW:
            closer = [j for j in candidates if j != self.active and ratios[j] <= self.enter
                      and ratios[self.active] - ratios[j] >= self.margin]
            if closer:  # 얕게 다가왔다 벌어지는 손끝 대신, 지금도 닿을 거리에 있는 더 가까운 후보로 옮긴다.
                self.active = min(closer, key=ratios.__getitem__)
        self._check(signal)
        if ratios[self.active] <= self.release_level():
            self.opened = 0
            return None
        self.opened += 1
        if self.opened < RELEASE_FRAMES:
            return None
        spoiled, short = self.spoiled, now - self.since < self.min_contact
        tap = self._emit(now)
        self._end_contact()
        if spoiled or short:
            self.undo_tap()
            return None
        return tap


# ---------------------------------------------------------------------------
# 양손 입력기

@dataclass
class Pending:
    """확정 전 후보. 같은 손·손가락·방향을 다시 꼬집으면 순환한다."""
    key: str
    hand: str
    finger: int
    face: str
    count: int
    label: str


class HandState:
    """손 하나의 판정기 묶음과 직전 신호."""

    def __init__(self, pinch, orientation, fist):
        self.pinch, self.orientation, self.fist = pinch, orientation, fist
        self.signal: Optional[HandSignal] = None
        self.contact: Optional[str] = None  # 접촉을 시작할 때의 손 방향
        self.face_state: Optional[str] = None  # 직전 프레임의 손 방향(바뀐 시각을 재는 용도)
        self.face_since = 0.0  # 손 방향이 지금 값이 된 시각
        self.held: list = []  # 이번 접촉 동안의 프레임별 신호(HandSignal)


class PinchTyper:
    """양손 신호 → 한글 입력.

    update()가 돌려주는 이벤트(튜플)
      ("input", 글자)  ("commit", None)  ("delete", None)
    """

    def __init__(self, ime=None, enter=PINCH_ENTER, exit=PINCH_EXIT, margin=PINCH_MARGIN,
                 min_contact=PINCH_MIN_CONTACT, fist_hold=0.18, facing_threshold=FACING_THRESHOLD,
                 facing_dwell=FACING_DWELL, fist_repeat=FIST_REPEAT, occluded_roll=OCCLUDED_ROLL, hold=HOLD_TIME):
        self.ime = ime if ime is not None else CheonjiinIME()
        self.hands = {label: HandState(FingerPinch(enter, exit, margin, min_contact, hold=hold),
                                       Orientation(facing_threshold, facing_dwell),
                                       FistHold(fist_hold, fist_repeat))
                      for label in HANDS}
        self.pending: Optional[Pending] = None
        self.pinches = []  # 직전 update에서 입력으로 인정된 꼬집기 [(손, 손가락, "palm"|"back")] (기록 모드가 읽는다)
        self.occluded_roll = occluded_roll  # 엄지 가림 기울기 기준(도). pinch_eval이 바꿔 가며 비교한다.
        self.settle = SETTLE  # 손 방향이 바뀐 뒤 판정을 쉬는 시간(초). 0이면 쉬지 않는다(Vision Pro 설정 창).
        self.index_sweep_rule = True  # 오른손 검지 지나감 규칙(INDEX_REPASS)을 쓰는가(Vision Pro 설정 창).

    # -- 판정 임계값 ------------------------------------------------------
    @property
    def enter(self):
        return self.hands["Left"].pinch.enter

    def set_pinch(self, enter, exit):
        if exit <= enter:
            raise ValueError("exit는 enter보다 커야 합니다.")
        for hand in self.hands.values():
            hand.pinch.enter, hand.pinch.exit = enter, exit

    def face(self, hand):
        """그 손의 지금 방향: "palm", "back", 또는 판정 전·회전 중이면 None."""
        state = self.hands[hand].orientation.state
        return state if state in FACES else None

    # -- 키 단위 동작 (고정 데이터·키보드 시험에서도 직접 부른다) ----------
    def press_finger(self, hand, finger, now, face="palm", drag=None):
        """hand의 finger(0 검지 ~ 3 소지)를 face(손바닥 "palm" / 손등 "back") 방향에서 한 번 꼬집었다.

        drag: 모음 버튼을 꼬집은 채 끈 방향(None 없음 → ㆍ, "h" 가로 → ㅡ, "v" 세로 → ㅣ). 다른 키에서는 쓰지 않는다.
        자판에 없는 자리(오른손 손등 중지~소지)는 무시하고 빈 목록을 돌려준다.
        """
        key = KEYS[(hand, face)][finger]
        if key is None:
            return []
        if key == "COMMIT_SPACE":  # 오른손 손등 검지: 후보가 있으면 확정, 없으면 공백
            return self.edit_key("COMMIT" if self.pending is not None else "SPACE", now)
        if key == "VOWEL":  # 모음: 앞 후보를 확정하고 천지인 획을 바로 넣는다.
            stroke = DRAG_STROKES[drag]
            self.pending = None
            self.ime.press(stroke, now)
            return [("input", VOWEL_LABELS[stroke])]
        if not is_group_key(key):  # 왼손 손등 편집 키(지움·공백·확정)
            return self.edit_key(key, now)
        pending = self.pending
        if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, face):
            group = group_of(pending.key)  # 같은 손·손가락 다시: 순환
            label = group[pending.count % len(group)]
            self._put(pending.key, label, now, replace=pending.label)
            pending.count, pending.label = pending.count + 1, label
            return [("input", label)]
        label = group_of(key)[0]
        self._put(key, label, now)
        self.pending = Pending(key, hand, finger, face, 1, label)
        return [("input", label)]

    def make_fist(self, hand, now):
        """손바닥 자세에서 주먹을 쥐었다(양손 모두 지움, 2026-10-23 사용자 지정)."""
        return self.delete()

    def delete(self):
        """지움: 마지막 자모 하나를 통째로 지운다. 순환한 자음(ㅇ→ㅁ)은 ㅁ이, 획을 이은 모음(ㅏ)은 ㅏ가 지워진다.

        예전에는 키 입력 한 번을 되돌려 ㅁ이 ㅇ으로, 가가 기로 돌아갔다(사용자가 지움으로 기대한 동작이 아니었다).
        """
        self.pending = None
        self.ime.delete_char()
        return [("delete", None)]

    def edit_key(self, key, now):
        """편집 키: "BACK" 지움 · "SPACE" 공백 · "COMMIT" 확정 · "ENTER" 줄바꿈. 공백·줄바꿈은 앞 후보를 확정한다."""
        if key == "BACK":
            return self.delete()
        self.pending = None
        if key == "COMMIT":
            return [("commit", None)]
        self.ime.press(key, now)
        return [("input", " " if key == "SPACE" else "\n")]

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
            hand.held = []
        self.pending = None

    @staticmethod
    def steady(held, face):
        """접촉의 처음·마지막 STEADY_FRAMES 프레임 손 방향 값 중앙값이 모두 face 쪽인가."""
        sign = 1 if face == "palm" else -1
        facings = [signal.facing for signal in held]
        return bool(facings) and all(sign * statistics.median(part) >= FACING_THRESHOLD
                                     for part in (facings[:STEADY_FRAMES], facings[-STEADY_FRAMES:]))

    def hidden(self, held, face):
        """손바닥 접촉 동안 기울기(roll) 중앙값이 occluded_roll 이하인가(엄지가 가려 손가락 값을 믿을 수 없음)."""
        return face == "palm" and bool(held) and statistics.median(s.roll for s in held) <= self.occluded_roll

    @staticmethod
    def pinch_path(held, finger):
        """꼬집기 지점(엄지 끝과 finger 손끝의 가운데, 이미지 좌표) 목록과 손 크기(중앙값). 손끝 좌표가 없으면 ([], 0)."""
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

    @classmethod
    def drag(cls, held, finger):
        """이번 접촉의 끌기(None / "h" / "v"): 꼬집기 지점이 접촉 시작부터 가장 멀리 간 이동.

        떼며 벌어지는 마지막 RELEASE_FRAMES 프레임은 세지 않는다(DRAG_MIN 설명).
        """
        return cls.drag_of_path(*cls.pinch_path(held[:-RELEASE_FRAMES] if len(held) > RELEASE_FRAMES else held, finger))

    def drag_view(self, hand):
        """화면용: 그 손이 모음 버튼을 꼬집는 중이면 {"finger", "points"(꼬집기 지점, 이미지 좌표), "size", "drag"(지금
        떼면 들어갈 끌기)}. 아니면 None. 사용자가 끄는 모양을 그리고, 끌기 기준 원과 들어갈 획을 미리 보여 주는 데 쓴다."""
        state, face = self.hands[hand], self.face(hand)
        finger = state.pinch.active
        if finger is None or face is None or KEYS[(hand, face)][finger] != "VOWEL":
            return None
        points, size = self.pinch_path(state.held, finger)
        if not points:
            return None
        return {"finger": finger, "points": points, "size": size, "drag": self.drag_of_path(points, size)}

    def occluded(self, hand):
        """화면 안내용: 그 손이 지금 손바닥이고 엄지가 가려질 만큼 소지 쪽으로 기울었는가."""
        state = self.hands[hand]
        return state.signal is not None and state.orientation.state == "palm" and state.signal.roll <= self.occluded_roll

    def update(self, signals, now):
        """signals: {"Left": HandSignal|None, "Right": HandSignal|None}. 이번 프레임의 이벤트 목록을 돌려준다.

        1) 손마다 방향(손바닥/손등)을 갱신한다.
        2) 꼬집기(FingerPinch)와 주먹(FistHold)을 갱신한다.
        3) 꼬집기는 접촉을 시작할 때와 뗄 때 손 방향이 같고, 접촉의 처음·마지막 몇 프레임의 손 방향 값도 그 방향이어야
           그 방향의 키로 입력한다(steady). 손을 뒤집는 도중의 꼬집기를 입력으로 세지 않기 위해서다.
           손바닥 꼬집기는 접촉 동안 소지 쪽으로 기울어 엄지가 가려졌으면 버린다(hidden).
           오른손 손바닥 검지는 엄지가 지나간 것으로 보이면 버린다(FingerPinch.index_sweep).
        4) 주먹(지움)은 손바닥일 때만 센다. 쥐고 있는 동안 FIST_REPEAT초마다 다시 지운다.
        """
        events, self.pinches = [], []
        for label, hand in self.hands.items():
            hand.signal = signals.get(label)
            hand.orientation.update(None if hand.signal is None else hand.signal.facing, now)
            signal, state = hand.signal, hand.orientation.state
            if state != hand.face_state:  # 손 방향이 바뀐 시각(SETTLE)
                hand.face_state, hand.face_since = state, now
            settling = state not in FACES or now - hand.face_since < self.settle
            # 회전 중이거나 방향이 막 바뀌었으면 꼬집기 판정을 쉰다. 다시 시작할 때 기준값은 자리 잡은 자세부터 쌓인다.
            hand.pinch.index_depth = HOLD_INDEX_DEPTH if state == "palm" else HOLD_DEPTH  # 손등은 손가락이 가려 얕다
            tap = hand.pinch.update(None if settling else signal, now)  # 입력된 프레임에서 (손가락, 접촉 시간)
            if tap is not None and tap[0] == 0 and label == "Right" and state == "palm" and self.index_sweep_rule \
                    and hand.pinch.index_sweep():
                hand.pinch.undo_tap()  # 오른손 검지 지나감: 입력하지 않는다(INDEX_REPASS).
                tap = None
            if signal is not None and (tap is not None or hand.pinch.active is not None):
                hand.held.append(signal)
            contact = hand.contact  # 이번 접촉을 시작할 때의 손 방향
            if tap is not None or hand.pinch.active is None:
                hand.contact = None
            elif hand.contact is None:
                hand.contact = contact = state
            if tap is not None and contact == state and state in FACES and self.steady(hand.held, state) \
                    and not self.hidden(hand.held, state) \
                    and KEYS[(label, state)][tap[0]] is not None:  # 접촉 내내 같은 방향이고 자판에 있는 자리
                self.pinches.append((label, tap[0], state))
                drag = self.drag(hand.held, tap[0]) if KEYS[(label, state)][tap[0]] == "VOWEL" else None
                events += self.press_finger(label, tap[0], now, state, drag)
            if tap is not None or hand.pinch.active is None:
                hand.held = []
            if signal is None:
                hand.fist.lost()
            elif hand.fist.update(signal.pose, now) and state == "palm":
                events += self.make_fist(label, now)
        return events


def replay(steps, typer=None, step=0.5):
    """고정 데이터 단계(["tap", 손, 손가락, 방향?, 끌기?] / ["fist", 손])를 순서대로 재생하고 입력기를 돌려준다.

    방향을 생략하면 손바닥("palm"), 끌기(모음 버튼: "h" 가로 ㅡ, "v" 세로 ㅣ)를 생략하면 끌기 없음(ㆍ)이다.
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
