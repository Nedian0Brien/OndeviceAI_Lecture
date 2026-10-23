"""정답이 붙은 기록(pinch_cheonjiin.py --collect)을 지금 판정에 다시 흘려 "의도 → 실제 입력" 표를 낸다.

실행:  python 261018/pinch_eval.py 261018/data/collect-*.jsonl [-v] [--drop 0.15 --window 0.5 ...]

- 로그의 프레임 신호(ratios·curled·pose·facing)를 PinchTyper에 그대로 넣는다. 판정을 고친 뒤 같은 기록으로
  다시 돌리면 고친 효과를 바로 비교할 수 있다. 판정값은 옵션으로 바꿔 볼 수 있다.
- 앱과 같게, 새 차례가 시작될 때 직전 접촉을 버린다(pinch_cheonjiin.run의 기록 모드 처리).
- 차례마다 안내한 손·방향(손바닥/손등)에서 나온 꼬집기 입력을 모두 모은다. 판정이 차례를 넘기지 못한 동안 사용자가 여러 번
  꼬집었을 수 있으므로, "첫 입력"과 "모든 입력"을 따로 센다.
- 안내한 손이 EVAL_MIN_FRAMES 프레임 미만 보인 차례는 평가에서 뺀다(차례가 곧바로 넘어간 경우 등).
- --occluded-roll: 엄지 가림으로 손바닥 꼬집기를 버리는 기울기 기준(도). -90이면 버리지 않고 판정 결과를 그대로 본다.
- 단어 기록(--collect-set words)은 단어마다 기대 키 순서(첫 줄 설정)와 실제 꼬집기를 편집 거리로 맞춰
  맞음 · 다른 손가락 · 더 들어감(오입력) · 빠짐을 키별로 센다. 왼손 손등 편집 키(지움 등)는 사용자가 고친 것이라
  맞추기에서 빼고 따로 센다. 지우고 다시 친 키는 맞음으로, 지워진 오입력은 더 들어감으로 잡힌다.
  기록 당시 이 규칙에 막혀 차례가 넘어가지 않았다면(N으로 건너뜀) 그 차례의 꼬집기가 여기서 처음 드러난다.
"""

import argparse
import json
from pathlib import Path

from pinch_collect import FACE_NAMES, FINGER_NAMES, HAND_NAMES, HANDS
from pinch_typer import (HOLD_TIME, OCCLUDED_ROLL, PINCH_DROP, PINCH_ENTER, PINCH_EXIT, PINCH_MARGIN, PINCH_WINDOW,
                         FingerPinch, PinchTyper, signal_from_log)

EVAL_MIN_FRAMES = 15  # 약 0.5초
SHORT = "IMRP"


def replay_prompts(path, occluded_roll=OCCLUDED_ROLL, hold=HOLD_TIME, **pinch):
    """로그 하나를 재생해 {차례 번호: {"hand", "finger", "frames", "taps": [손가락...], "live": 앱이 남긴 결과}}."""
    typer = PinchTyper(occluded_roll=occluded_roll, hold=hold)
    if pinch:
        for state in typer.hands.values():
            old = state.pinch
            state.pinch = FingerPinch(pinch.get("enter", old.enter), pinch.get("exit", old.exit),
                                      pinch.get("margin", old.margin), old.min_contact,
                                      pinch.get("drop", old.drop), pinch.get("window", old.window), old.hold)
    prompts, current = {}, None
    with open(path, encoding="utf-8") as file:
        for line in file:
            row = json.loads(line)
            for event in row.get("events", []):
                if event[0] == "collect" and event[1] in prompts and event[4] != "redo":
                    prompts[event[1]]["live"] = event[4]
            if "hands" not in row:
                continue
            prompt = row.get("prompt")
            if prompt is not None and prompt["id"] != current:  # 새 차례: 앱처럼 직전 접촉을 버린다.
                for state in typer.hands.values():
                    state.pinch.lost()
                    state.contact = None
            current = None if prompt is None else prompt["id"]
            signals = {hand: signal_from_log(v, row.get("size", (1.0, 1.0))) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in HANDS}, row["t"])
            if prompt is None:
                continue
            face = prompt.get("face", "palm")  # 2026-10-23 이전 기록에는 방향이 없다(모두 손바닥).
            item = prompts.setdefault(prompt["id"], {"hand": prompt["hand"], "finger": prompt["finger"], "face": face,
                                                     "frames": 0, "taps": [], "live": None})
            item["frames"] += prompt["hand"] in row["hands"]
            item["taps"] += [finger for hand, finger, side in typer.pinches if (hand, side) == (prompt["hand"], face)]
    return prompts


def read_config(path):
    """로그 첫 줄의 기록 설정({"reps", "kind", "order", ...}), 없으면 None."""
    with open(path, encoding="utf-8") as file:
        first = json.loads(file.readline() or "{}")
    return first.get("collect")


def replay_words(path, occluded_roll=OCCLUDED_ROLL, hold=HOLD_TIME):
    """단어 기록을 재생해 {차례: {"word", "expected": [키], "taps": [키], "edits": 편집 키 수, "live": 기록 당시 결과}}.

    키는 (손, 손가락, 방향) 튜플이다. 새 단어가 시작될 때 앱처럼 직전 접촉을 버린다.
    """
    config = read_config(path)
    expected = {word: [tuple(key) for key in keys] for word, keys in config["keys"].items()}
    typer, items, current = PinchTyper(occluded_roll=occluded_roll, hold=hold), {}, None
    with open(path, encoding="utf-8") as file:
        for line in file:
            row = json.loads(line)
            for event in row.get("events", []):
                if event[0] == "collect" and event[1] in items and event[3] != "redo":
                    items[event[1]]["live"] = event[3]
            if "hands" not in row:
                continue
            prompt = row.get("prompt")
            if prompt is not None and prompt["id"] != current:
                for state in typer.hands.values():
                    state.pinch.lost()
                    state.contact = None
            current = None if prompt is None else prompt["id"]
            signals = {hand: signal_from_log(v, row.get("size", (1.0, 1.0))) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in HANDS}, row["t"])
            if prompt is None:
                continue
            item = items.setdefault(prompt["id"], {"word": prompt["word"], "expected": expected[prompt["word"]],
                                                   "taps": [], "edits": 0, "live": None})
            for key in typer.pinches:
                if key[0] == "Left" and key[2] == "back":
                    item["edits"] += 1
                else:
                    item["taps"].append(key)
    return items


def align(expected, actual):
    """편집 거리로 맞춘 결과 [("match"|"sub"|"extra"|"miss", 기대 키, 실제 키)]."""
    rows, cols = len(expected) + 1, len(actual) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost[i][j] = min(cost[i - 1][j - 1] + (expected[i - 1] != actual[j - 1]),
                             cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    ops, i, j = [], len(expected), len(actual)
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + (expected[i - 1] != actual[j - 1]):
            ops.append(("match" if expected[i - 1] == actual[j - 1] else "sub", expected[i - 1], actual[j - 1]))
            i, j = i - 1, j - 1
        elif j and cost[i][j] == cost[i][j - 1] + 1:
            ops.append(("extra", None, actual[j - 1]))
            j -= 1
        else:
            ops.append(("miss", expected[i - 1], None))
            i -= 1
    return ops[::-1]


def key_name(key):
    hand, finger, face = key
    return f"{HAND_NAMES[hand]}{FACE_NAMES[face]} {FINGER_NAMES[finger]}"


def report_words(items, verbose=False):
    """단어 기록 요약: 키 단위 맞음·다른 손가락·더 들어감·빠짐, 키별 표."""
    lines, per = [], {}
    totals = {"match": 0, "sub": 0, "extra": 0, "miss": 0}
    unfinished = [number for number, item in items.items() if item["live"] is None]  # 기록을 끝낸 단어 등
    items = {number: item for number, item in items.items() if item["live"] is not None}
    for number, item in sorted(items.items()):
        ops = align(item["expected"], item["taps"])
        counts = {kind: sum(op[0] == kind for op in ops) for kind in totals}
        for kind in totals:
            totals[kind] += counts[kind]
        for kind, want, got in ops:
            key = got if kind == "extra" else want
            entry = per.setdefault(key, {"match": 0, "sub": 0, "extra": 0, "miss": 0})
            entry[kind] += 1
        if verbose:
            extras = ", ".join(key_name(got) for kind, _, got in ops if kind == "extra")
            lines.append(f"  {number:3d} {item['word']}: 기대 {len(item['expected'])} · 맞음 {counts['match']} · "
                         f"다른 손가락 {counts['sub']} · 더 들어감 {counts['extra']} · 빠짐 {counts['miss']} · "
                         f"편집 키 {item['edits']} ({item['live']})" + (f" · 더 들어간 키: {extras}" if extras else ""))
    expected = sum(len(item["expected"]) for item in items.values())
    lines.append(f"단어 {len(items)}개(끝나지 않은 {len(unfinished)}개 제외) · 기대 키 {expected} · 맞음 {totals['match']} · "
                 f"다른 손가락 {totals['sub']} · "
                 f"더 들어감(오입력) {totals['extra']} · 빠짐 {totals['miss']} · 편집 키 "
                 f"{sum(item['edits'] for item in items.values())}")
    lines.append("키                  맞음 다른손가락 더들어감 빠짐")
    for key in sorted(per, key=lambda k: (k[2], k[0], k[1])):
        entry = per[key]
        lines.append(f"  {key_name(key):14s} {entry['match']:5d} {entry['sub']:9d} {entry['extra']:8d} {entry['miss']:4d}")
    return "\n".join(lines)


def report(prompts, verbose=False):
    """요약 문자열: 첫 입력 정확도, 모든 입력의 맞음/틀림, 손·손가락별 의도 → 첫 입력 표."""
    valid = {k: v for k, v in prompts.items() if v["frames"] >= EVAL_MIN_FRAMES}
    lines = []
    if verbose:
        for number, item in sorted(valid.items()):
            taps = "".join(SHORT[f] for f in item["taps"]) or "-"
            live = item["live"] if item["live"] in ("skip", None) else SHORT[item["live"]]
            side = "b" if item["face"] == "back" else ""
            lines.append(f"  {number:3d} {item['hand'][0]}{side}{SHORT[item['finger']]} → {taps:10s} (기록 당시: {live})")
    first = sum(bool(v["taps"]) and v["taps"][0] == v["finger"] for v in valid.values())
    right = sum(f == v["finger"] for v in valid.values() for f in v["taps"])
    wrong = sum(f != v["finger"] for v in valid.values() for f in v["taps"])
    missed = sum(not v["taps"] for v in valid.values())
    lines.append(f"평가 차례 {len(valid)}개 (제외 {len(prompts) - len(valid)}개) · 첫 입력 맞음 {first} · "
                 f"모든 입력 맞음 {right} / 틀림 {wrong} · 입력 없음 {missed}")
    lines.append("의도 \\ 첫 입력    검지 중지 약지 소지 없음")
    for hand, face in [(hand, face) for face in FACE_NAMES for hand in HANDS]:
        for finger in range(4):
            items = [v for v in valid.values() if (v["hand"], v["face"], v["finger"]) == (hand, face, finger)]
            if not items:
                continue
            counts = [sum(bool(v["taps"]) and v["taps"][0] == f for v in items) for f in range(4)]
            counts.append(sum(not v["taps"] for v in items))
            name = f"{HAND_NAMES[hand]}{FACE_NAMES[face]} {FINGER_NAMES[finger]}"
            lines.append(f"  {name:8s}      " + " ".join(f"{c:4d}" for c in counts))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true", help="차례마다 의도 → 입력 목록")
    parser.add_argument("--enter", type=float, default=PINCH_ENTER)
    parser.add_argument("--exit", type=float, default=PINCH_EXIT)
    parser.add_argument("--margin", type=float, default=PINCH_MARGIN)
    parser.add_argument("--drop", type=float, default=PINCH_DROP)
    parser.add_argument("--window", type=float, default=PINCH_WINDOW)
    parser.add_argument("--occluded-roll", type=float, default=OCCLUDED_ROLL,
                        help="엄지 가림 기울기 기준(도). -90이면 가림 규칙 없이 본다")
    parser.add_argument("--hold", type=float, default=HOLD_TIME,
                        help="누르고 유지 시간(초). 0이면 뗄 때 입력. 기록할 때와 같은 방식으로 재생해야 의미가 있다")
    args = parser.parse_args()
    pinch = {key: getattr(args, key) for key in ("enter", "exit", "margin", "drop", "window")}
    merged = {}
    for path in args.logs:
        config = read_config(path)
        if config is not None and config.get("kind") == "words":
            print(f"## {path.name} (단어)")
            print(report_words(replay_words(path, args.occluded_roll, args.hold), args.verbose))
            continue
        prompts = replay_prompts(path, args.occluded_roll, args.hold, **pinch)
        print(f"## {path.name}")
        print(report(prompts, args.verbose))
        merged.update({(path.name, k): v for k, v in prompts.items()})
    if len(merged) and len(args.logs) > 1:
        print("## 전체")
        print(report(merged))


if __name__ == "__main__":
    main()
