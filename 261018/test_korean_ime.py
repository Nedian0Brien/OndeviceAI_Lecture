"""천지인 한글 조합(korean_ime) 테스트."""

import unittest

from korean_ime import CheonjiinIME, CONSONANTS, MEDIALS


class IMETests(unittest.TestCase):
    def setUp(self):
        self.ime = CheonjiinIME()
        self.now = 0.0

    def type(self, keys, step=0.25):
        for key in keys:
            self.ime.press(key, self.now)
            self.now += step

    def consonant(self, char):
        for key, group in CONSONANTS.items():
            if char in group:
                # 같은 그룹의 독립 자음을 입력할 때는 순환 대기 시간을 넘긴다.
                self.now += 1.0
                self.type(key * (group.index(char) + 1))
                return
        self.fail("지원되지 않는 테스트 자음")

    def test_all_21_vowels(self):
        # 아래아를 포함하는 입력 중간 상태뿐 아니라 모든 현대 한글 모음 확인.
        examples = {
            "1": "ㅣ", "3": "ㅡ", "12": "ㅏ", "122": "ㅑ", "21": "ㅓ",
            "221": "ㅕ", "23": "ㅗ", "223": "ㅛ", "32": "ㅜ", "322": "ㅠ",
            "121": "ㅐ", "1221": "ㅒ", "211": "ㅔ", "2211": "ㅖ",
            "2312": "ㅘ", "23121": "ㅙ", "231": "ㅚ", "3221": "ㅝ",
            "32211": "ㅞ", "321": "ㅟ", "31": "ㅢ",
        }
        self.assertEqual(set(examples.values()), set(MEDIALS))
        for keys, expected in examples.items():
            with self.subTest(keys=keys):
                self.setUp()
                self.type(keys)
                self.assertEqual(self.ime.text, expected)

    def test_greeting(self):
        for keys in ("0125", "52210", "8812", "8211", "0223"):
            self.type(keys)
            self.now += 1.0
        self.assertEqual(self.ime.text, "안녕하세요")

    def test_consonant_cycles(self):
        for key, group in CONSONANTS.items():
            if len(group) == 1:
                continue
            with self.subTest(key=key):
                self.setUp()
                for char in group + group[0]:
                    self.type(key)
                    self.assertEqual(self.ime.text, char)

    def test_single_consonant_key_does_not_cycle(self):
        # 글자가 하나인 키(꼬집기 자판의 ㄴ·ㄹ)는 다시 눌러도 같은 자음을 하나 더 넣는다(안녕의 ㄴ→ㄴ).
        self.type("N" "N")
        self.assertEqual(self.ime.text, "ㄴㄴ")
        self.setUp()
        self.type("0" "1" "2" "N" "N" "2" "2" "1" "0")
        self.assertEqual(self.ime.text, "안녕")

    def test_timeout_separates_same_consonant(self):
        self.type("5")
        self.now += 1.0
        self.type("5")
        self.assertEqual(self.ime.text, "ㄴㄴ")

    def test_final_moves_to_next_initial(self):
        self.type("412512")  # ㄱㅏㄴㅏ
        self.assertEqual(self.ime.text, "가나")

    def test_compound_final_and_split(self):
        self.consonant("ㄷ")
        self.type("12")
        self.consonant("ㄹ")
        self.consonant("ㄱ")
        self.assertEqual(self.ime.text, "닭")
        self.type("12")
        self.assertEqual(self.ime.text, "달가")

    def test_all_compound_final_splits(self):
        pairs = ("ㄱㅅ", "ㄴㅈ", "ㄴㅎ", "ㄹㄱ", "ㄹㅁ", "ㄹㅂ", "ㄹㅅ", "ㄹㅌ", "ㄹㅍ", "ㄹㅎ", "ㅂㅅ")
        expected = ("각사", "간자", "간하", "갈가", "갈마", "갈바", "갈사", "갈타", "갈파", "갈하", "갑사")
        for pair, word in zip(pairs, expected):
            with self.subTest(pair=pair):
                self.setUp()
                self.consonant("ㄱ")
                self.type("12")
                self.consonant(pair[0])
                self.consonant(pair[1])
                self.type("12")
                self.assertEqual(self.ime.text, word)

    def test_tense_initial_and_compound_medial(self):
        self.type("44423121")
        self.assertEqual(self.ime.text, "꽤")

    def test_cycling_final_can_form_next_initial(self):
        self.type("412666")  # ㄱㅏ + ㄷ→ㅌ→ㄸ (ㄸ은 받침으로 쓸 수 없다)
        self.assertEqual(self.ime.text, "가ㄸ")
        self.type("12")
        self.assertEqual(self.ime.text, "가따")

    def test_backspace_undoes_each_stroke(self):
        self.type("412")
        self.assertEqual(self.ime.text, "가")
        for expected in ("기", "ㄱ", "", ""):
            self.ime.backspace()
            self.assertEqual(self.ime.text, expected)

    def test_backspace_restores_precycle_consonant(self):
        self.type("444")
        self.assertEqual(self.ime.text, "ㄲ")
        self.ime.backspace()
        self.assertEqual(self.ime.text, "ㅋ")

    def test_delete_char_removes_whole_jamo(self):
        """꼬집기 입력기의 지움: 키 입력이 아니라 자모 하나를 통째로(순환한 자음·획을 이은 모음)."""
        self.type("412" + "000")  # 가 + ㅇ→ㅁ→ㅇ... 0을 세 번: ㅇ ㅁ ㅇ
        self.assertEqual(self.ime.text, "강")
        for expected in ("가", "ㄱ", "", ""):
            self.ime.delete_char()
            self.assertEqual(self.ime.text, expected)
        self.ime.backspace()  # 지움도 되살릴 수 있다
        self.assertEqual(self.ime.text, "ㄱ")

    def test_delete_char_skips_invisible_commit(self):
        self.type(("4", "1", "2", "COMMIT"))
        self.ime.delete_char()
        self.assertEqual(self.ime.text, "ㄱ")
        self.type(("SPACE",))
        self.ime.delete_char()
        self.assertEqual(self.ime.text, "ㄱ")

    def test_commit_space_and_newline(self):
        self.type(("4", "COMMIT", "4", "SPACE", "0", "ENTER"))
        self.assertEqual(self.ime.text, "ㄱㄱ ㅇ\n")

    def test_clear_is_undoable(self):
        self.type("412")
        self.ime.clear()
        self.assertEqual(self.ime.text, "")
        self.ime.backspace()
        self.assertEqual(self.ime.text, "가")

    def test_pending_dot_can_be_undone(self):
        self.type("02")
        self.assertEqual(self.ime.text, "ㅇㆍ")
        self.type("1")
        self.assertEqual(self.ime.text, "어")
        self.ime.backspace()
        self.assertEqual(self.ime.text, "ㅇㆍ")

class PreviewTests(unittest.TestCase):
    def test_jamo_preview_follows_consonant_cycle_without_editing(self):
        ime = CheonjiinIME()
        self.assertEqual(ime.preview_label("4", 0.0), "ㄱ")
        self.assertEqual(ime.text, "")
        ime.press("4", 0.0)
        self.assertEqual(ime.preview_label("4", .5), "ㅋ")
        self.assertEqual(ime.text, "ㄱ")
        self.assertEqual(ime.preview_label("4", 2.0), "ㄱ")
        self.assertEqual(ime.preview_label("2", .5), "ㆍ")
        self.assertEqual(ime.preview_label("BACK", .5), "지움")
        self.assertEqual(ime.preview_label("COMMIT", .5), "확정")

    def test_commit_key_separates_same_consonant(self):
        ime = CheonjiinIME()
        ime.press("4", 0.0)
        ime.press("COMMIT", 0.1)
        ime.press("4", 0.2)
        self.assertEqual(ime.text, "ㄱㄱ")


if __name__ == "__main__":
    unittest.main()
