"""기록·평가 도구(tools/): 단어 입력 시험, 기록 한 줄, 기대 키 순서 맞추기."""

import json
import unittest

from common.hands import Detection, assign_hands
from common.synthetic import detection
from tools.evaluate import align
from tools.recording import log_row, signal_from_log
from tools.word_test import COMMIT, REST, SHORT_WORDS, WordTest, word_keys, word_steps
from typer import replay


class WordTestTest(unittest.TestCase):
    def test_every_word_has_verified_keys(self):
        for word in SHORT_WORDS:
            self.assertEqual(replay(word_steps(word)).ime.text, word)
        keys = {key for word in SHORT_WORDS for key in word_keys(word)}
        palms = {(hand, finger, "palm") for hand in ("Left", "Right") for finger in range(4)}
        self.assertTrue(palms <= keys)  # 모든 손바닥 키가 들어 있다
        drags = {step[4] for word in SHORT_WORDS for step in word_steps(word)}
        self.assertEqual(drags, {None, "h", "v"})  # 모음의 세 획(ㆍ·ㅡ·ㅣ)
        # 학교: ㅎ(왼손 약지 2번) ㅏ ㄱ, 같은 손가락 ㄱ이 이어지므로 확정을 넣고 ㄱ ㅛ
        index = ("Right", 0, "palm")
        self.assertEqual(word_keys("학교")[4:7], [index, COMMIT, index])

    def test_word_flow_and_config(self):
        test = WordTest(words=("바다", "꽃"), rounds=1, seed=0)
        number, word = test.prompt(0.0)
        self.assertEqual(test.describe((number, word)), {"id": 0, "word": word})
        self.assertEqual(test.update(0.1, "ㄱ"), [])
        self.assertEqual(test.update(0.2, word), [("collect", 0, word, "done")])
        self.assertIsNone(test.prompt(0.2 + REST / 2))  # 쉬는 중
        self.assertEqual(test.skip(0.2 + REST)[0][3], "skip")
        self.assertTrue(test.done)
        config = test.config()["collect"]
        self.assertEqual((config["kind"], sorted(config["order"])), ("words", ["꽃", "바다"]))
        self.assertEqual(config["keys"]["꽃"][0], ["Right", 0, "palm"])

    def test_align_counts_extra_and_wrong_keys(self):
        a, b, c, x = ("Right", 1, "palm"), ("Left", 0, "palm"), ("Right", 3, "palm"), ("Right", 0, "palm")
        ops = align([a, b, c], [a, x, b, ("Right", 2, "palm")])
        self.assertEqual([op[0] for op in ops], ["match", "extra", "match", "sub"])
        self.assertEqual([op[0] for op in align([a, b], [a])], ["match", "miss"])


class RecordingTest(unittest.TestCase):
    def test_log_row_keeps_landmarks_and_replays(self):
        from project01 import build_signals

        image, world = detection()
        hands = assign_hands([Detection("Left", image, world, 0.9)])
        signals = build_signals(hands, 640, 480, mirrored=False)
        row = json.loads(json.dumps(log_row(1.0, hands, signals, [], 640, 480), ensure_ascii=False))
        hand = row["hands"]["Left"]
        self.assertNotIn("prompt", row)
        self.assertEqual((len(hand["image"]), len(hand["world"])), (21, 21))
        self.assertEqual(tuple(hand["ratios"]), tuple(round(v, 4) for v in signals["Left"].ratios))
        self.assertAlmostEqual(signal_from_log(hand, row["size"]).roll, signals["Left"].roll, delta=0.1)
        self.assertEqual(log_row(1.0, {}, {}, [], 640, 480, {"id": 0, "word": "꽃"}, True)["prompt"]["word"], "꽃")


if __name__ == "__main__":
    unittest.main()
