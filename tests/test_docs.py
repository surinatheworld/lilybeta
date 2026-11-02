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
        self.assertEqual(clean_line("(bỏ)"), "")
        self.assertEqual(clean_line("(bỏ hết)"), "")
        self.assertEqual(clean_line("trans note cho editor: chap này có câu tiếng Anh"), "")
        self.assertEqual(clean_line("sorr… (xin-)"), "sorr… (xin-)")
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


class ExcelScriptTests(unittest.TestCase):
    """Kiểm tra script XLSX có cột Page, Type, Japanese và English."""

    @staticmethod
    def write_xlsx(path, rows):
        import zipfile
        from xml.sax.saxutils import escape

        def cell(ref, value):
            return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        body = "".join(f'<row r="{i + 1}">' + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(row))
                       + "</row>" for i, row in enumerate(rows))
        sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                 + body + "</sheetData></worksheet>")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", sheet)

    def test_pages_types_and_source_lengths(self):
        from app.core.docs import source_length
        from app.core.typeset_profiles import split_mark
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Script.xlsx")
            self.write_xlsx(path, [["Number", "Page", "Speaker", "Type", "Japanese", "English"],
                                   [1, "5.0", "A", "Snow_CuteHappy", "青い箱をここに置いてください！", "Put the blue box here, please!"],
                                   [2, "5.0", "B", "Snow_OutsideText", "赤い小箱は 机の上です。", "The little red box is on the desk."],
                                   [3, "6.0", "News", "Snow_Narration", "次の朝", "The next morning."]])
            pages = load_pages(path)
        self.assertEqual(sorted(pages), [5, 6])
        first = pages[5][0]
        self.assertEqual(split_mark(first), ("!cute", "Put the blue box here, please!"))
        self.assertEqual(source_length(first), 15)
        self.assertEqual(split_mark(pages[5][1])[0], "!free")
        self.assertEqual(source_length(pages[5][1]), 11)  # bỏ dấu cách trong câu gốc
        self.assertEqual(split_mark(pages[6][0]), ("!square", "The next morning."))
