"""Vision Pro 앱이 내장 Python으로 부르는 판정 세션(visionos/pinch_vision.py). 합성 손(common.synthetic)은 왼손 손바닥이다."""

import json
from pathlib import Path
import sys
import unittest

from typer import PinchTyper

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "visionos"))


class VisionSessionTest(unittest.TestCase):
    @staticmethod
    def joints(thumb):
        from common import synthetic
        world = synthetic.world_hand(thumb)
        return {"world": world.tolist(), "image": synthetic.to_image(world).tolist()}

    def run_frames(self, session, frames, start=0.0):
        outputs, now = [], start
        for joints in frames:
            now += 1 / 30
            outputs.append(json.loads(session.step(json.dumps({"t": now, "hands": {"Left": joints}}))))
        return outputs, now

    def pinch_frames(self):
        from common import synthetic
        world = synthetic.world_hand(synthetic.AWAY)
        index_pad = tuple((world[7] + world[8]) / 2 + [0.004, 0, 0])  # 검지 끝마디 가운데 옆
        return [self.joints(synthetic.AWAY)] * 25 + [self.joints(index_pad)] * 5 + [self.joints(synthetic.AWAY)] * 3

    def test_step_types_from_joints(self):
        from pinch_vision import Session
        outputs, _ = self.run_frames(Session(), self.pinch_frames())
        self.assertEqual([e for out in outputs for e in out["events"]], [["input", "ㅂ"]])
        self.assertEqual(outputs[-1]["echo"], {"id": 1, "hand": "Left", "text": "ㅂ"})
        last = outputs[-1]
        self.assertEqual((last["text"], last["hands"]["Left"]["face"]), ("ㅂ", "palm"))
        self.assertEqual([f["queue"] for f in last["hands"]["Left"]["fingers"]],
                         [["ㅍ", "ㅃ", "ㅂ"], ["ㄹ"], ["ㅅ", "ㅎ", "ㅆ"], ["ㅈ", "ㅊ", "ㅉ"]])
        self.assertTrue(any(out["hands"]["Left"]["fingers"][0]["active"] for out in outputs))

    def test_fist_deletes_with_meter_coordinates(self):
        # 기기처럼 image를 m 단위로 넘겨도 편 손 / 주먹 자세가 판정되고, 손바닥에서 주먹을 쥐면 지운다.
        # (예전에는 자세 판정의 2px 기준에 모든 마디가 걸려 늘 closing이었다.)
        from common import synthetic
        from pinch_vision import Session, signal_of

        def meters(world):
            return {"world": world.tolist(), "image": world.tolist()}

        open_hand = synthetic.world_hand(synthetic.AWAY)
        fist = synthetic.world_hand(synthetic.AWAY)
        synthetic.fold_fist(fist)
        self.assertEqual(signal_of("Left", meters(open_hand)).pose, "open")
        self.assertEqual(signal_of("Left", meters(fist)).pose, "fist")
        session = Session()
        session.typer.ime.press("1", 0.0)  # ㅣ
        outputs, _ = self.run_frames(session, [meters(open_hand)] * 25 + [meters(fist)] * 10 + [meters(open_hand)] * 3)
        self.assertIn(["delete", None], [e for out in outputs for e in out["events"]])
        self.assertEqual(outputs[-1]["text"], "")
        closed = [out["hands"]["Left"]["closed"] for out in outputs]
        self.assertEqual((closed[24], any(closed[25:35]), closed[-1]), (False, True, False))  # 주먹 동안만 접힘

    def test_vowel_drag_state(self):
        # 모음 버튼(왼손 손등 검지)을 꼬집은 채 아래로 끌면, 꼬집는 동안 끌기 상태(프레임 수·기준 거리 m·미리 볼 획)가
        # 나오고 떼면 ㅣ가 들어간다. 기기처럼 image는 m 단위이고, x를 뒤집어 손등이 보이는 손으로 만든다.
        from common import synthetic
        from pinch_vision import Session

        def meters(world, down=0.0):
            moved = world + [0.0, down, 0.0]
            return {"world": moved.tolist(), "image": (moved * [-1.0, 1.0, 1.0]).tolist()}

        open_hand = synthetic.world_hand(synthetic.AWAY)
        index_pad = tuple((open_hand[7] + open_hand[8]) / 2 + [0.004, 0, 0])
        pinch = synthetic.world_hand(index_pad)
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
                         ("ㅣ", True, 180))  # 아래로 끌었다
        self.assertEqual(outputs[-1]["hands"]["Left"]["dragging"], None)
        self.assertEqual([e for out in outputs for e in out["events"]], [["input", "ㅣ"]])

    def test_log_keeps_recent_files(self):
        # 늘 기록하므로 새 기록을 시작할 때 최근 LOG_KEEP개만 남긴다(새 파일 포함). 다른 파일은 건드리지 않는다.
        import tempfile
        from common import synthetic
        from pinch_vision import LOG_KEEP, Session
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for k in range(LOG_KEEP + 3):
                (folder / f"pinch-log-visionos-20260101-{k:06d}.jsonl").write_text("{}")
            (folder / "other.txt").write_text("x")
            session = Session(folder)
            session.command(json.dumps({"cmd": "log_start"}))
            self.run_frames(session, [self.joints(synthetic.AWAY)] * 3)
            session.command(json.dumps({"cmd": "log_stop"}))
            logs = sorted(folder.glob("pinch-log-visionos-*.jsonl"))
            self.assertEqual(len(logs), LOG_KEEP)
            self.assertNotIn("20260101-000000", " ".join(path.name for path in logs))
            self.assertEqual(len(logs[-1].read_text().splitlines()), 3)
            self.assertTrue((folder / "other.txt").exists())

    def test_word_mode(self):
        # 단어 모드: 짧은 단어를 하나씩 내고, 입력 칸이 그 단어가 되면 다 친 것으로 센 뒤 REST초 쉬고 비운 다음 단어.
        # 건너뛰기, 지우기(지금 단어 다시), 자유 모드로 돌아가기.
        from tools.word_test import REST, SHORT_WORDS, word_steps
        from pinch_vision import Session
        session = Session()

        def step(t):
            return json.loads(session.step(json.dumps({"t": t, "hands": {}})))

        out = json.loads(session.command(json.dumps({"cmd": "mode", "mode": "words"})))
        self.assertEqual(out["mode"], "words")
        first = step(1.0)["word"]
        self.assertIn(first["target"], SHORT_WORDS)
        steps = word_steps(first["target"])
        for k, (_, hand, finger, face, drag) in enumerate(steps[:-1]):
            session.typer.press_finger(hand, finger, 1.0 + 0.1 * k, face, drag)
        partial = step(1.5)["word"]
        self.assertLessEqual(partial["matched"], len(first["target"]))
        _, hand, finger, face, drag = steps[-1]
        session.typer.press_finger(hand, finger, 2.0, face, drag)
        out = step(2.0)
        self.assertIn(["collect", 0, first["target"], "done"], out["events"])
        self.assertEqual((out["word"]["target"], out["word"]["solved"], out["word"]["last"], out["word"]["done"]),
                         (first["target"], True, [first["target"], "done"], 1))
        self.assertEqual(out["word"]["seconds"], 1.0)  # 1.0초에 안내, 2.0초에 다 침
        held = step(2.0 + REST + 1.0)  # 맞히면 저절로 넘어가지 않는다
        self.assertEqual((held["text"], held["word"]["target"], held["word"]["solved"]),
                         (first["target"], first["target"], True))
        nxt = json.loads(session.command(json.dumps({"cmd": "next"})))  # 다음 단어
        self.assertEqual((nxt["text"], nxt["word"]["solved"]), ("", False))
        self.assertIn(nxt["word"]["target"], SHORT_WORDS)
        nxt = step(3.5)
        session.typer.press_finger("Right", 0, 3.6)  # ㄱ
        out = json.loads(session.command(json.dumps({"cmd": "clear"})))
        self.assertEqual(out["text"], "")
        out = json.loads(session.command(json.dumps({"cmd": "skip"})))
        self.assertEqual((out["word"]["last"][1], out["word"]["skipped"]), ("skip", 1))
        out = json.loads(session.command(json.dumps({"cmd": "mode", "mode": "free"})))
        self.assertEqual((out["mode"], out["word"]), ("free", None))

    def test_word_mode_log_for_eval(self):
        # 단어 모드 기록: 파일을 새로 열어 첫 줄에 단어 순서, 행마다 지금 단어(tools/evaluate.py가 읽는 형식).
        import tempfile
        from tools.recording import read_config
        from pinch_vision import Session
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
        from pinch_vision import Session
        session = Session()
        out = json.loads(session.command(json.dumps({"cmd": "demo"})))
        self.assertEqual(out["text"], "안녕하세요")
        self.assertEqual(json.loads(session.command(json.dumps({"cmd": "clear"})))["text"], "")
        info = json.loads(session.command(json.dumps({"cmd": "info"})))
        self.assertIn("'안녕하세요'", info["notice"])
        self.assertEqual(info["text"], "")  # 입력 칸은 그대로

    def test_log_replays_with_same_input(self):
        import tempfile
        from tools.recording import signal_from_log
        from pinch_vision import Session
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            self.assertIn("기록 시작", json.loads(session.command(json.dumps({"cmd": "log_start"})))["notice"])
            self.run_frames(session, self.pinch_frames())
            path = Path(session.log_path)
            session.command(json.dumps({"cmd": "log_stop"}))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), len(self.pinch_frames()))
        self.assertEqual([e for row in rows for e in row["events"]], [["input", "ㅂ"]])
        typer = PinchTyper()  # 기록 행만으로 다시 판정해도 같은 입력
        for row in rows:
            signals = {hand: signal_from_log(v, row["size"]) for hand, v in row["hands"].items()}
            typer.update({hand: signals.get(hand) for hand in ("Left", "Right")}, row["t"])
        self.assertEqual(typer.ime.text, "ㅂ")


if __name__ == "__main__":
    unittest.main()
