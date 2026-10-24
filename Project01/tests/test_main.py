"""main.py의 단어 안내 테스트"""

import unittest

from main import word_view
from record import WordTest


class MainTest(unittest.TestCase):
    def test_word_view(self):
        test = WordTest()
        number, word = test.prompt(0.0)
        self.assertEqual(word_view(test, (number, word), None)["title"], f"「{word}」 입력")
        self.assertEqual(word_view(test, None, ("collect", 0, word, "done"))["tone"], "ok")
        self.assertIsNone(word_view(test, None, None))


if __name__ == "__main__":
    unittest.main()
