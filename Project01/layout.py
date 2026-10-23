"""자판 배치와 키 이름, 순환 대기열, 모음 끌기 규칙.

손 방향(손바닥 / 손등이 카메라를 향함)마다 다른 키가 놓인다.
                  검지      중지      약지      소지
  왼손 손바닥     ㅂㅍㅃ    ㄹ        ㅅㅎㅆ    ㅈㅊㅉ
  오른손 손바닥   ㄱㅋㄲ    ㄴ        ㄷㅌㄸ    ㅇㅁ
  왼손 손등       모음      공백      확정      . , ? ! 줄바꿈
  오른손 손등     확정/공백 -         -         -     (후보가 있으면 확정, 없으면 공백)

- 자음 그룹·문장부호: 같은 손가락을 다시 꼬집으면 그룹 안에서 순환하고(ㄱ → ㅋ → ㄲ), 다른 키를 꼬집으면 앞 글자가
  확정된다. ㄴ·ㄹ은 글자가 하나라 순환하지 않고 바로 들어간다.
- 모음(왼손 손등 검지): 꼬집고 그대로 떼면 ㆍ, 꼬집은 채 가로로 끌면 ㅡ, 세로로 끌면 ㅣ(천지인 획).
- 오른손 손등은 손가락이 가려 검지만 쓴다. 지움은 주먹(양손)이다.
"""

import math

from korean_ime import CONSONANTS


HANDS = ("Left", "Right")
FACES = ("palm", "back")  # 손바닥 / 손등이 카메라를 향함
# 손가락(검지·중지·약지·소지) 순서의 키. 키 이름은 korean_ime의 키(자음 그룹 4~0·N·L, 모음 획 1~3)와 편집 키다.
KEYS = {
    ("Left", "palm"): ("7", "L", "8", "9"),  # ㅂㅍㅃ · ㄹ · ㅅㅎㅆ · ㅈㅊㅉ
    ("Right", "palm"): ("4", "N", "6", "0"),  # ㄱㅋㄲ · ㄴ · ㄷㅌㄸ · ㅇㅁ
    ("Left", "back"): ("VOWEL", "SPACE", "COMMIT", "PUNCT"),  # 모음 · 공백 · 확정 · 문장부호
    ("Right", "back"): ("COMMIT_SPACE", None, None, None),  # 확정/공백
}
PUNCTUATION = (".", ",", "?", "!", "\n")
VOWEL_LABELS = {"1": "ㅣ", "2": "ㆍ", "3": "ㅡ"}  # 천지인 획 키 → 글자
EDIT_LABELS = {"SPACE": "공백", "COMMIT": "확정", "COMMIT_SPACE": "공백", "VOWEL": "모음"}
CYCLE_SHOWN = 3  # 손가락 위에 보이는 순환 글자 수(문장부호 그룹 5개는 앞 3개만)

# 모음 끌기: 꼬집는 동안 꼬집기 지점(엄지 끝과 손끝의 가운데)이 가장 멀리 간 거리가 손 크기(손목 ~ 중지 뿌리)의
# DRAG_MIN배보다 작으면 끌기 없음(ㆍ), 크면 가로·세로 중 많이 움직인 쪽(ㅡ / ㅣ). 손 크기 약 9cm면 약 2.3cm.
DRAG_MIN = 0.25
DRAG_STROKES = {None: "2", "h": "3", "v": "1"}  # 끌기 → 천지인 획 키: 없음 ㆍ, 가로 ㅡ, 세로 ㅣ


def group_of(key):
    """키가 순환하는 자모·기호 목록."""
    return PUNCTUATION if key == "PUNCT" else CONSONANTS[key]


def is_group_key(key):
    """같은 손가락을 다시 꼬집으면 순환하는 키인가(글자가 둘 이상인 자음 그룹·문장부호). ㄴ·ㄹ은 아니다."""
    return key == "PUNCT" or len(CONSONANTS.get(key, "")) > 1


def key_label(key):
    """자판 지도에 쓰는 키 이름."""
    if key == "PUNCT":
        return ".,?!"
    return VOWEL_LABELS.get(key) or EDIT_LABELS.get(key) or CONSONANTS[key]


def drag_of(dx, dy, size):
    """꼬집는 동안의 이동 (dx, dy)와 손 크기 → 끌기: None(없음) / "h"(가로) / "v"(세로). 화면 좌표(y 아래)."""
    if size <= 0 or math.hypot(dx, dy) < DRAG_MIN * size:
        return None
    return "h" if abs(dx) >= abs(dy) else "v"


def cycle_queue(typer, hand, finger):
    """손끝 라벨: 이 손가락을 지금 손 방향에서 꼬집으면 나올 글자부터 순환 순서대로(그룹 키), 그 밖은 글자 하나.

    같은 손·손가락·방향을 다시 꼬집으면 PinchTyper가 group[pending.count % n]을 넣으므로, 순환 중이면 그룹을
    pending.count만큼 돌린 순서다. 손이 회전 중이면 손바닥 자판을 보여 준다. 자판에 없는 자리는 빈 목록이다.
    """
    face = typer.face(hand) or "palm"
    key = KEYS[(hand, face)][finger]
    if key is None:
        return []
    if key == "COMMIT_SPACE":  # 후보가 있으면 확정, 없으면 공백
        return ["확정" if typer.pending is not None else "공백"]
    if not is_group_key(key):
        return [key_label(key)]
    start, pending = 0, typer.pending
    if pending is not None and (pending.hand, pending.finger, pending.face) == (hand, finger, face):
        start = pending.count
    group = ["줄" if char == "\n" else char for char in group_of(key)]
    return [group[(start + i) % len(group)] for i in range(min(len(group), CYCLE_SHOWN))]
