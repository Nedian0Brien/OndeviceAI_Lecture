"""Vision Pro 앱이 내장 Python으로 부르는 판정 세션(visionos/pinch_vision.py)의 테스트"""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from keyboard import PinchTyper, hand_signal
from record import REST, SHORT_WORDS, read_config, signal_from_log, word_steps
from tests.helpers import AWAY, fold_fist, to_image, world_hand

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "visionos"))
from pinch_vision import IMAGE_SCALE, LOG_KEEP, Session  # noqa: E402


class VisionSessionTest(unittest.TestCase):
    @staticmethod
    def joints(thumb):
        # 합성 손은 왼손 손바닥임
        world = world_hand(thumb)
        return {"world": world.tolist(), "image": to_image(world).tolist()}

    def run_frames(self, session, frames, start=0.0):
        outputs, now = [], start
        for joints in frames:
            now += 1 / 30
            outputs.append(json.loads(session.step(json.dumps({"t": now, "hands": {"Left": joints}}))))
        return outputs, now

    def pinch_frames(self):
        world = world_hand()
        index_pad = tuple((world[7] + world[8]) / 2 + [0.004, 0, 0])  # 검지 끝마디 가운데 옆
        return [self.joints(AWAY)] * 25 + [self.joints(index_pad)] * 5 + [self.joints(AWAY)] * 3

    def test_step_types_from_joints(self):
        outputs, _ = self.run_frames(Session(), self.pinch_frames())
        self.assertEqual([e for out in outputs for e in out["events"]], [["input", "ㅂ"]])
        self.assertEqual(outputs[-1]["echo"], {"id": 1, "hand": "Left", "text": "ㅂ"})
        last = outputs[-1]
        self.assertEqual((last["text"], last["hands"]["Left"]["face"]), ("ㅂ", "palm"))
        self.assertEqual([f["queue"] for f in last["hands"]["Left"]["fingers"]],
                         [["ㅍ", "ㅃ", "ㅂ"], ["ㄹ"], ["ㅅ", "ㅎ", "ㅆ"], ["ㅈ", "ㅊ", "ㅉ"]])
        self.assertTrue(any(out["hands"]["Left"]["fingers"][0]["active"] for out in outputs))

    def test_fist_deletes_with_meter_coordinates(self):
        # 기기처럼 image를 m 단위로 넘겨도 편 손과 주먹이 판정되고, 손바닥에서 주먹을 쥐면 지움

        def meters(world):
            return {"world": world.tolist(), "image": world.tolist()}

        def pose(world):
            points = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in world.tolist()]
            return hand_signal("Left", points, points, IMAGE_SCALE, IMAGE_SCALE).pose

        open_hand = world_hand()
        fist = fold_fist(world_hand())
        self.assertEqual(pose(open_hand), "open")
        self.assertEqual(pose(fist), "fist")
        session = Session()
        session.typer.press_finger("Left", 0, "back", "v")  # ㅣ
        outputs, _ = self.run_frames(session, [meters(open_hand)] * 25 + [meters(fist)] * 10 + [meters(open_hand)] * 3)
        self.assertIn(["delete", None], [e for out in outputs for e in out["events"]])
        self.assertEqual(outputs[-1]["text"], "")
        closed = [out["hands"]["Left"]["closed"] for out in outputs]
        self.assertEqual((closed[24], any(closed[25:35]), closed[-1]), (False, True, False))  # 주먹 동안만 접힘

    def test_vowel_drag_state(self):
        # 모음 버튼을 핀치한 채 아래로 끌면 끌기 상태가 나오고 떼면 ㅣ가 들어감
        # 기기처럼 image는 m 단위이고, x를 뒤집어 손등이 보이는 손으로 만듦

        def meters(world, down=0.0):
            moved = world + [0.0, down, 0.0]
            return {"world": moved.tolist(), "image": (moved * [-1.0, 1.0, 1.0]).tolist()}

        open_hand = world_hand()
        index_pad = tuple((open_hand[7] + open_hand[8]) / 2 + [0.004, 0, 0])
        pinch = world_hand(index_pad)
        frames = ([meters(open_hand)] * 25 + [meters(pinch, 0.006 * k) for k in range(8)]
                  + [meters(open_hand, 0.042)] * 3)
        outputs, _ = self.run_frames(Session(), frames)
        dragging = [out["hands"]["Left"] for out in outputs if out["hands"]["Left"]["dragging"] is not None]
        self.assertTrue(dragging)
        self.assertEqual({hand["dragging"] for hand in dragging}, {0})
        counts = [hand["drag_frames"] for hand in dragging]
        self.assertEqual(counts, sorted(counts))
        self.assertTrue(0.01 < dragging[-1]["drag_radius"] < 0.04, dragging[-1]["drag_radius"])
        self.assertEqual((dragging[0]["drag_preview"], dragging[0]["drag_passed"]), ("ㆍ", False))
        self.assertEqual((dragging[-1]["drag_preview"], dragging[-1]["drag_passed"], dragging[-1]["drag_pointer"]),
                         ("ㅣ", True, 180))  # 아래로 끌었음
        self.assertEqual(outputs[-1]["hands"]["Left"]["dragging"], None)
        self.assertEqual([e for out in outputs for e in out["events"]], [["input", "ㅣ"]])

    def test_log_keeps_recent_files(self):
        # 새 기록을 시작할 때 새 파일을 포함해 최근 LOG_KEEP개만 남기고, 다른 파일은 건드리지 않음
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for k in range(LOG_KEEP + 3):
                (folder / f"pinch-log-visionos-20260101-{k:06d}.jsonl").write_text("{}")
            (folder / "other.txt").write_text("x")
            session = Session(folder)
            session.command(json.dumps({"cmd": "log_start"}))
            self.run_frames(session, [self.joints(AWAY)] * 3)
            session.command(json.dumps({"cmd": "log_stop"}))
            logs = sorted(folder.glob("pinch-log-visionos-*.jsonl"))
            self.assertEqual(len(logs), LOG_KEEP)
            self.assertNotIn("20260101-000000", " ".join(path.name for path in logs))
            self.assertEqual(len(logs[-1].read_text().splitlines()), 3)
            self.assertTrue((folder / "other.txt").exists())

    def test_word_mode(self):
        # 단어를 맞히면 next를 기다리고, 건너뛰기와 지우기, 자유 모드로 돌아가기를 확인함
        session = Session()

        def step(t):
            return json.loads(session.step(json.dumps({"t": t, "hands": {}})))

        out = json.loads(session.command(json.dumps({"cmd": "mode", "mode": "words"})))
        self.assertEqual(out["mode"], "words")
        first = step(1.0)["word"]
        self.assertIn(first["target"], SHORT_WORDS)
        steps = word_steps(first["target"])
        for _, hand, finger, face, drag in steps[:-1]:
            session.typer.press_finger(hand, finger, face, drag)
        partial = step(1.5)["word"]
        self.assertLessEqual(partial["matched"], len(first["target"]))
        _, hand, finger, face, drag = steps[-1]
        session.typer.press_finger(hand, finger, face, drag)
        out = step(2.0)
        self.assertIn(["collect", 0, first["target"], "done"], out["events"])
        self.assertEqual((out["word"]["target"], out["word"]["solved"], out["word"]["last"], out["word"]["done"]),
                         (first["target"], True, [first["target"], "done"], 1))
        self.assertEqual(out["word"]["seconds"], 1.0)  # 1.0초에 안내하고 2.0초에 다 쳤음
        held = step(2.0 + REST + 1.0)  # 맞혀도 저절로 넘어가지 않음
        self.assertEqual((held["text"], held["word"]["target"], held["word"]["solved"]),
                         (first["target"], first["target"], True))
        nxt = json.loads(session.command(json.dumps({"cmd": "next"})))
        self.assertEqual((nxt["text"], nxt["word"]["solved"]), ("", False))
        self.assertIn(nxt["word"]["target"], SHORT_WORDS)
        nxt = step(3.5)
        session.typer.press_finger("Right", 0)  # ㄱ
        out = json.loads(session.command(json.dumps({"cmd": "clear"})))
        self.assertEqual(out["text"], "")
        out = json.loads(session.command(json.dumps({"cmd": "skip"})))
        self.assertEqual((out["word"]["last"][1], out["word"]["skipped"]), ("skip", 1))
        out = json.loads(session.command(json.dumps({"cmd": "mode", "mode": "free"})))
        self.assertEqual((out["mode"], out["word"]), ("free", None))

    def test_word_mode_log_for_eval(self):
        # 단어 입력 기록은 첫 줄에 단어 순서, 행마다 지금 단어를 남김
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            session.command(json.dumps({"cmd": "log_start"}))
            session.command(json.dumps({"cmd": "mode", "mode": "words"}))
            session.step(json.dumps({"t": 1.0, "hands": {}}))
            session.command(json.dumps({"cmd": "log_stop"}))
            words = sorted(Path(folder).glob("*-words.jsonl"))
            self.assertEqual(len(words), 1)
            rows = [json.loads(line) for line in words[0].read_text().splitlines()]
            self.assertEqual(read_config(words[0])["kind"], "words")
            self.assertEqual(rows[1]["prompt"]["id"], 0)
            self.assertEqual(rows[1]["prompt"]["word"], rows[0]["collect"]["order"][0])

    def test_demo_and_clear(self):
        session = Session()
        out = json.loads(session.command(json.dumps({"cmd": "demo"})))
        self.assertEqual(out["text"], "안녕하세요")
        self.assertEqual(json.loads(session.command(json.dumps({"cmd": "clear"})))["text"], "")
        info = json.loads(session.command(json.dumps({"cmd": "info"})))
        self.assertIn("'안녕하세요'", info["notice"])
        self.assertEqual(info["text"], "")  # 입력 칸은 그대로 둠

    def test_log_replays_with_same_input(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            self.assertIn("기록 시작", json.loads(session.command(json.dumps({"cmd": "log_start"})))["notice"])
            self.run_frames(session, self.pinch_frames())
            path = Path(session.log_path)
            session.command(json.dumps({"cmd": "log_stop"}))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), len(self.pinch_frames()))
        self.assertEqual([e for row in rows for e in row["events"]], [["input", "ㅂ"]])
        typer = PinchTyper()  # 기록 행만으로 다시 판정해도 같은 입력이 나와야 함
        for row in rows:
            signals = {hand: signal_from_log(v, row["size"]) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in ("Left", "Right")}, row["t"])
        self.assertEqual(typer.ime.text, "ㅂ")


if __name__ == "__main__":
    unittest.main()
