"""프레임 기록(project01.py --log): 한 줄 쓰기(log_row)와 다시 읽기(signal_from_log, read_config).

한 줄이 프레임 하나다. 판정을 고친 뒤 같은 기록을 다시 재생해 결과를 비교하는 데 쓴다(tools/evaluate.py).
재생에는 ratios·curled·pose·facing·roll을 쓰고, 이미지(정규화 x, y, z)·world(m) 관절은 신호를 다시 계산할 때 쓴다.
"""

import json
from types import SimpleNamespace

from common.pose import finger_flex, palm_roll
from hand_signal import HandSignal, image_tips


def log_row(now, hands, signals, events, w, h, prompt=None, words=False):
    """기록 한 줄(dict). hands: {손: Detection}, signals: {손: HandSignal}, size는 이미지 좌표를 픽셀로 바꿀 크기.
    words(단어 입력 시험)면 prompt 필드에 지금 단어({"id", "word"}, 쉬는 중이면 null)를 넣는다."""
    def points(landmarks, digits):
        return [[round(p.x, digits), round(p.y, digits), round(p.z, digits)] for p in landmarks]

    row = {
        "t": round(now, 4), "size": [w, h],
        "hands": {role: {"seen_as": hands[role].label,
                         "ratios": [round(v, 4) for v in sig.ratios], "facing": round(sig.facing, 3),
                         "roll": round(sig.roll, 1), "pose": sig.pose, "curled": list(sig.curled),
                         "image": points(hands[role].landmarks, 4), "world": points(hands[role].world, 5)}
                  for role, sig in signals.items()},
        "events": [list(e) for e in events]}
    if words:
        row["prompt"] = prompt
    return row


def signal_from_log(entry, size=(1.0, 1.0)):
    """기록 한 줄의 손 하나(hands[손])를 HandSignal로. 굽힘(flex)은 world 관절로, 모음 끌기용 손끝 좌표는 이미지
    관절로 다시 계산한다. roll이 없는 예전 기록은 이미지 관절로 계산한다."""
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


def read_config(path):
    """기록 첫 줄의 설정({"kind", "order", ...}: 단어 입력 시험·정답 기록), 없으면 None."""
    with open(path, encoding="utf-8") as file:
        first = json.loads(file.readline() or "{}")
    return first.get("collect")
