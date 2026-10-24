"""record.py의 단어 입력 시험, 기록 한 줄, 기대 키 맞추기 테스트"""

import json
import unittest

from hand import Detection, assign_hands, hand_signal
from keyboard import replay
from record import COMMIT, REST, SHORT_WORDS, WordTest, align, log_row, signal_from_log, word_steps
from tests.helpers import detection


class WordTestTest(unittest.TestCase):
    def test_every_word_has_verified_keys(self):
        steps = {word: word_steps(word) for word in SHORT_WORDS}
        for word in SHORT_WORDS:
            self.assertEqual(replay(steps[word]).ime.text, word)
        keys = {tuple(step[1:4]) for word in SHORT_WORDS for step in steps[word]}
        palms = {(hand, finger, "palm") for hand in ("Left", "Right") for finger in range(4)}
        self.assertTrue(palms <= keys)  # 모든 손바닥 키가 들어 있음
        self.assertEqual({step[4] for word in SHORT_WORDS for step in steps[word]}, {None, "h", "v"})
        # 학교는 ㅎ ㅏ ㄱ 뒤에 같은 손가락의 ㄱ이 이어지므로 확정을 넣음
        index = ("Right", 0, "palm")
        self.assertEqual([tuple(step[1:4]) for step in word_steps("학교")][4:7], [index, COMMIT, index])

    def test_word_flow_and_config(self):
        test = WordTest()
        number, word = test.prompt(0.0)
        self.assertEqual(test.update(0.1, "ㄱ"), [])
        self.assertEqual(test.update(0.2, word), [("collect", 0, word, "done")])
        self.assertIsNone(test.prompt(0.2 + REST / 2))  # 쉬는 중
        self.assertEqual(test.skip(0.2 + REST)[0][3], "skip")
        self.assertEqual(test.results, {0: "done", 1: "skip"})
        config = test.config()["collect"]
        self.assertEqual((config["kind"], sorted(config["order"])), ("words", sorted(SHORT_WORDS)))
        self.assertEqual(config["keys"]["꽃"][0], ["Right", 0, "palm"])

    def test_align_counts_extra_and_wrong_keys(self):
        a, b, c, x = ("Right", 1, "palm"), ("Left", 0, "palm"), ("Right", 3, "palm"), ("Right", 0, "palm")
        ops = align([a, b, c], [a, x, b, ("Right", 2, "palm")])
        self.assertEqual([op[0] for op in ops], ["match", "extra", "match", "sub"])
        self.assertEqual([op[0] for op in align([a, b], [a])], ["match", "miss"])


class RecordingTest(unittest.TestCase):
    def test_log_row_keeps_landmarks_and_replays(self):
        image, world = detection()
        hands = assign_hands([Detection("Left", image, world)])
        signals = {role: hand_signal(h.label, h.landmarks, h.world, False, 640, 480) for role, h in hands.items()}
        row = json.loads(json.dumps(log_row(1.0, hands, signals, [], 640, 480), ensure_ascii=False))
        hand = row["hands"]["Left"]
        self.assertNotIn("prompt", row)
        self.assertEqual((len(hand["image"]), len(hand["world"])), (21, 21))
        self.assertEqual(tuple(hand["ratios"]), tuple(round(v, 4) for v in signals["Left"].ratios))
        self.assertAlmostEqual(signal_from_log(hand, row["size"]).roll, signals["Left"].roll, delta=0.1)
        self.assertEqual(log_row(1.0, {}, {}, [], 640, 480, {"id": 0, "word": "꽃"}, True)["prompt"]["word"], "꽃")


if __name__ == "__main__":
    unittest.main()
