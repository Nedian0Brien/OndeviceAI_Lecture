"""main.py의 실행 옵션과 단어 안내 테스트"""

import argparse
import unittest

from main import parse_resolution, word_view
from record import WordTest


class MainTest(unittest.TestCase):
    def test_parse_resolution(self):
        self.assertEqual(parse_resolution("1920X1080"), (1920, 1080))
        self.assertEqual(parse_resolution("auto"), "auto")
        for value in ("720p", "100x100"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_resolution(value)

    def test_word_view(self):
        test = WordTest()
        number, word = test.prompt(0.0)
        self.assertEqual(word_view(test, (number, word), None)["title"], f"「{word}」 입력")
        self.assertEqual(word_view(test, None, ("collect", 0, word, "done"))["tone"], "ok")
        self.assertIsNone(word_view(test, None, None))


if __name__ == "__main__":
    unittest.main()
