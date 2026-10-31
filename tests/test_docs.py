import os
import tempfile
import unittest

from app.core.docs import clean_line, load_pages


class TranslationNotesTests(unittest.TestCase):
    def test_translator_notes_are_dropped_and_place_labels_stripped(self):
        self.assertEqual(clean_line("T/N: đổi từ cháu sang con nghe cho thân thiết"), "")
        self.assertEqual(clean_line("(cái này xóa luôn cũng đc vì k tìm đc sfx nào phù hợp)"), "")
        self.assertEqual(clean_line("chữ dọc: chuyến đi đến suối nước nóng!!"), "chuyến đi đến suối nước nóng!!")
        self.assertEqual(clean_line("chữ trên gáy 3 cuốn sách: hồi ức"), "hồi ức")
        self.assertEqual(clean_line("vé tàu:"), "")
        for line in ("(thì thầm)", "Chặng: Hakone-Yumoto ➔ Shinjuku", "chap 39: Isshiki Rinna đang lo lắng",
                     "Meishi òa khóc sau khi lạc mất cô bạn gái đầu đời (sư phụ)!?"):
            self.assertEqual(clean_line(line), line)

    def test_pages_skip_notes(self):
        text = "Page 1\nmừng con tới!\nT/N: ghi chú\nchữ dọc: Meishi\nPage 2\nvâng\n"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.assertEqual(load_pages(path), {1: ["mừng con tới!", "Meishi"], 2: ["vâng"]})


if __name__ == "__main__":
    unittest.main()
