"""정답이 있는 기록을 지금 판정으로 다시 재생해 "의도 → 실제 입력"을 셉니다.

실행:  python tools/evaluate.py data/기록.jsonl [-v]

- 단어 입력 기록(--words, Vision Pro 단어 입력): 단어마다 기대 꼬집기 순서(기록 첫 줄)와 실제 꼬집기를 편집 거리로
  맞춰 맞음 · 다른 손가락 · 더 들어감(오입력) · 빠짐을 셉니다. 왼손 손등 공백·확정·문장부호는 사용자가 고치거나
  끝맺은 키라 맞추기에서 빼고 따로 셉니다. 끝나지 않은 단어는 뺍니다.
- 손가락 차례 기록(예전 정답 기록 도구로 남긴 data/collect-*.jsonl): 차례마다 안내한 손가락과 첫 입력을 비교합니다.
- 앱과 같게, 새 단어(차례)가 시작될 때 직전 접촉을 버립니다.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Project01 폴더(판정 모듈)를 찾게 함

from common.landmarks import FINGER_NAMES  # noqa: E402
from layout import HANDS, KEYS  # noqa: E402
from tools.recording import read_config, signal_from_log  # noqa: E402
from typer import PinchTyper  # noqa: E402

EVAL_MIN_FRAMES = 15  # 안내한 손이 이 프레임(약 0.5초) 미만 보인 차례는 평가에서 뺌
EDIT_NAMES = {"SPACE", "COMMIT", "PUNCT"}  # 단어를 고치거나 끝맺는 키: 기대 키 순서와 맞추지 않고 따로 셈
HAND_NAMES = {"Left": "왼손", "Right": "오른손"}
FACE_NAMES = {"palm": "", "back": " 손등"}


def key_name(key):
    """(손, 손가락, 방향) 키를 "오른손 손등 검지" 같은 이름으로 바꿉니다."""
    hand, finger, face = key
    return f"{HAND_NAMES[hand]}{FACE_NAMES[face]} {FINGER_NAMES[finger]}"


def replay(path, on_prompt):
    """기록을 재생하며 프레임마다 on_prompt(typer, prompt, row)를 부릅니다. 새 차례에서 직전 접촉을 버립니다."""
    typer, current = PinchTyper(), None
    with open(path, encoding="utf-8") as file:
        # 기록 한 줄 = 프레임 하나(또는 결과 이벤트 하나)
        for line in file:
            row = json.loads(line)
            if "hands" not in row:
                on_prompt(None, None, row)  # 결과 이벤트만 있는 줄
                continue
            prompt = row.get("prompt")
            if prompt is not None and prompt["id"] != current:  # 새 차례: 앱처럼 직전 접촉을 버림
                for state in typer.hands.values():
                    state.pinch.lost()
            current = None if prompt is None else prompt["id"]
            # 기록된 손 신호로 지금의 판정을 다시 돌림
            signals = {hand: signal_from_log(v, row.get("size", (1.0, 1.0))) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in HANDS}, row["t"])
            on_prompt(typer, prompt, row)


def replay_words(path):
    """단어 기록 → {단어 번호: {"word", "expected": [키], "taps": [키], "edits": 편집 키 수, "live": 기록 당시 결과}}."""
    config = read_config(path)
    expected = {word: [tuple(key) for key in keys] for word, keys in config["keys"].items()}
    items = {}

    def collect(typer, prompt, row):
        # 기록 당시 단어 결과(맞힘·건너뜀)를 남김
        for event in row.get("events", []):
            if event[0] == "collect" and event[1] in items:
                items[event[1]]["live"] = event[3]
        if typer is None or prompt is None:  # 결과 줄이거나 쉬는 중: 꼬집기를 모으지 않음
            return
        item = items.setdefault(prompt["id"], {"word": prompt["word"], "expected": expected[prompt["word"]],
                                               "taps": [], "edits": 0, "live": None})
        # 이번 프레임에 인정된 꼬집기: 편집 키는 따로 세고, 나머지는 실제 키 순서에 붙임
        for key in typer.pinches:
            if KEYS[(key[0], key[2])][key[1]] in EDIT_NAMES:
                item["edits"] += 1
            else:
                item["taps"].append(key)

    replay(path, collect)
    return items


def replay_prompts(path):
    """손가락 차례 기록 → {차례 번호: {"hand", "finger", "face", "frames", "taps": [손가락...], "live"}}."""
    prompts = {}

    def collect(typer, prompt, row):
        # 기록 당시 차례 결과를 남김(다시 하기 "redo"는 빼고)
        for event in row.get("events", []):
            if event[0] == "collect" and event[1] in prompts and event[4] != "redo":
                prompts[event[1]]["live"] = event[4]
        if typer is None or prompt is None:  # 결과 줄이거나 쉬는 중
            return
        face = prompt.get("face", "palm")  # 2026-10-23 이전 기록에는 방향이 없음(모두 손바닥)
        item = prompts.setdefault(prompt["id"], {"hand": prompt["hand"], "finger": prompt["finger"], "face": face,
                                                 "frames": 0, "taps": [], "live": None})
        item["frames"] += prompt["hand"] in row["hands"]  # 안내한 손이 보인 프레임 수
        item["taps"] += [finger for hand, finger, side in typer.pinches if (hand, side) == (prompt["hand"], face)]

    replay(path, collect)
    return prompts


def align(expected, actual):
    """편집 거리로 맞춘 결과 [("match"|"sub"|"extra"|"miss", 기대 키, 실제 키)]."""
    # 1) 편집 거리 표 채우기: cost[i][j] = 기대 앞 i개와 실제 앞 j개를 맞추는 최소 비용
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
    # 2) 역추적: 표 끝에서 처음으로 돌아가며 각 칸이 어느 연산으로 왔는지 고름
    ops, i, j = [], len(expected), len(actual)
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + (expected[i - 1] != actual[j - 1]):  # 맞음 또는 다른 키
            ops.append(("match" if expected[i - 1] == actual[j - 1] else "sub", expected[i - 1], actual[j - 1]))
            i, j = i - 1, j - 1
        elif j and cost[i][j] == cost[i][j - 1] + 1:  # 실제에만 있는 키(더 들어감)
            ops.append(("extra", None, actual[j - 1]))
            j -= 1
        else:  # 기대에만 있는 키(빠짐)
            ops.append(("miss", expected[i - 1], None))
            i -= 1
    return ops[::-1]


def report_words(items, verbose=False):
    """단어 기록 요약: 키 단위 맞음·다른 손가락·더 들어감·빠짐."""
    totals = {"match": 0, "sub": 0, "extra": 0, "miss": 0}
    finished = {number: item for number, item in items.items() if item["live"] is not None}  # 끝난 단어만
    lines = []
    # 단어마다 기대·실제 키를 맞춰 종류별로 세고 합계에 더함
    for number, item in sorted(finished.items()):
        ops = align(item["expected"], item["taps"])
        counts = {kind: sum(op[0] == kind for op in ops) for kind in totals}
        for kind in totals:
            totals[kind] += counts[kind]
        if verbose:  # 단어별 줄
            extras = ", ".join(key_name(got) for kind, _, got in ops if kind == "extra")
            lines.append(f"  {number:3d} {item['word']}: 기대 {len(item['expected'])} · 맞음 {counts['match']} · "
                         f"다른 손가락 {counts['sub']} · 더 들어감 {counts['extra']} · 빠짐 {counts['miss']}"
                         + (f" · 더 들어간 키: {extras}" if extras else ""))
    expected = sum(len(item["expected"]) for item in finished.values())
    lines.append(f"단어 {len(finished)}개(끝나지 않은 {len(items) - len(finished)}개 제외) · 기대 키 {expected} · "
                 f"맞음 {totals['match']} · 다른 손가락 {totals['sub']} · 더 들어감(오입력) {totals['extra']} · "
                 f"빠짐 {totals['miss']} · 편집 키 {sum(item['edits'] for item in finished.values())}")
    return "\n".join(lines)


def report_prompts(prompts, verbose=False):
    """손가락 차례 기록 요약: 첫 입력 정확도와 모든 입력의 맞음/틀림."""
    valid = {k: v for k, v in prompts.items() if v["frames"] >= EVAL_MIN_FRAMES}  # 손이 충분히 보인 차례만
    lines = []
    if verbose:  # 차례별 줄
        for number, item in sorted(valid.items()):
            taps = " ".join(FINGER_NAMES[f] for f in item["taps"]) or "-"
            lines.append(f"  {number:3d} {key_name((item['hand'], item['finger'], item['face']))} → {taps}")
    first = sum(bool(v["taps"]) and v["taps"][0] == v["finger"] for v in valid.values())
    right = sum(f == v["finger"] for v in valid.values() for f in v["taps"])
    wrong = sum(f != v["finger"] for v in valid.values() for f in v["taps"])
    missed = sum(not v["taps"] for v in valid.values())
    lines.append(f"평가 차례 {len(valid)}개 (제외 {len(prompts) - len(valid)}개) · 첫 입력 맞음 {first} · "
                 f"모든 입력 맞음 {right} / 틀림 {wrong} · 입력 없음 {missed}")
    return "\n".join(lines)


def main():
    """기록 파일마다 종류(단어 / 손가락 차례)를 보고 알맞은 요약을 출력합니다."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true", help="단어(차례)마다 결과")
    args = parser.parse_args()
    for path in args.logs:
        config = read_config(path)
        if config is None:  # 첫 줄에 정답 설정이 없는 기록
            print(f"## {path.name}: 정답이 없는 기록입니다(첫 줄 설정 없음)")
        elif config.get("kind") == "words":  # 단어 입력 기록
            print(f"## {path.name} (단어)\n" + report_words(replay_words(path), args.verbose))
        else:  # 손가락 차례 기록
            print(f"## {path.name} (손가락 차례)\n" + report_prompts(replay_prompts(path), args.verbose))


if __name__ == "__main__":
    main()
