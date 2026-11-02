"""读取层：文件类型看内容，PDF 按金额对齐重建表格。"""
from pathlib import Path
from unittest.mock import Mock

from helpers import FIXTURES, Isolated, image_page, pdf, table_pdf
from billbill import documents, statement

OCR_TABLE = "| Date | Merchant | Amount |\n|---|---|---|\n| 2026-09-01 | Coffee | -35.00 |"
EXPECTED = [("2026-03-01", 50.0, "expense"), ("2026-03-02", 8000.0, "income"),
            ("2026-03-05", 1200.5, "expense"), ("2026-03-09", 0.5, "expense")]


class OfficeFormats(Isolated):
    def test_every_format_gives_the_same_verified_numbers(self):
        """这些文件是真 Word、真 Excel 存出来的，同一份内容。"""
        for path in sorted(FIXTURES.iterdir()):
            with self.subTest(path.name):
                parsed = statement.parse_table(documents.read(path)[0], path.name)
                self.assertEqual([(r["date"], r["amount"], r["direction"]) for r in parsed.rows], EXPECTED)
                self.assertEqual(parsed.check, "verified")
                self.assertEqual(parsed.account, "···2345")

    def test_html_named_xls_is_read_by_content(self):
        self.assertEqual(documents.read(FIXTURES / "masquerade.xls")[0].note, "网页表格")

    def test_wrong_extension_does_not_matter(self):
        target = self.root / "bill.txt"
        target.write_bytes((FIXTURES / "statement.docx").read_bytes())
        self.assertEqual(documents.read(target)[0].note, "Word 文档")

    def test_unknown_zip_is_rejected_clearly(self):
        import zipfile
        path = self.root / "x.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.txt", "hello")
        with self.assertRaisesRegex(documents.ReadError, "不是 Word 或 Excel"):
            documents.read(path)

    def test_empty_file(self):
        with self.assertRaisesRegex(documents.ReadError, "空的"):
            documents.read(self.write("x.csv", ""))


class TextFormats(Isolated):
    def test_gbk_csv(self):
        path = self.write("a.csv", "日期,摘要,金额\n2026-01-02,咖啡,-12.00\n", "gb18030")
        self.assertEqual(documents.read(path)[0].rows[1], ["2026-01-02", "咖啡", "-12.00"])

    def test_fixed_width_text(self):
        text = "\n".join(["Date        Details            Amount      Balance"] +
                         [f"2026-01-0{d}  Shop {d}             -1.00       {100 - d}.00" for d in range(1, 6)])
        rows = documents.read(self.write("a.txt", text))[0].rows
        self.assertEqual(rows[1], ["2026-01-01", "Shop 1", "-1.00", "99.00"])

    def test_html_table_expands_merged_cells(self):
        html = ('<table><tr><th rowspan="2">Date</th><th colspan="2">Money</th></tr>'
                "<tr><td>Income</td><td>Expense</td></tr><tr><td>2026-09-01</td><td></td><td>35.00</td></tr></table>")
        self.assertEqual(documents.read(self.write("a.html", html))[0].rows,
                         [["Date", "Money", "Money"], ["Date", "Income", "Expense"], ["2026-09-01", "", "35.00"]])

    def test_paragraphs_without_a_table_still_parse(self):
        """没有表格的文本：日期、文字、金额用两个以上空格分开。"""
        text = "账单明细\n2026-01-02  咖啡店  12.00\n2026-01-03  公交  3.50\n2026-01-04  午饭  20.00\n"
        parsed = statement.parse_table(documents.read(self.write("a.txt", text))[0], "a.txt")
        self.assertEqual([r["amount"] for r in parsed.rows], [12.0, 3.5, 20.0])


class PdfLayout(Isolated):
    def parsed(self, path):
        return statement.parse_table(documents.read(path)[0], path.name)

    def test_columns_closer_than_any_gap_still_separate(self):
        """Date Description Credit 粘成一格的那份真实账单：列靠金额对齐找，不靠空隙。"""
        path = self.root / "tight.pdf"
        rows = [["Date", "Description", "Credit", "Debit", "Balance"]]
        balance = 100.0
        for day in range(1, 9):
            balance -= 5
            rows.append([f"0{day} Feb 2026", "Some Long Merchant Name Here", "", "5.00", f"{balance:.2f}"])
        table_pdf(path, rows, columns=(40, 90, 300, 345, 395))
        parsed = self.parsed(path)
        self.assertEqual(parsed.check, "verified")
        self.assertEqual(len(parsed.rows), 8)
        self.assertTrue(all(r["direction"] == "expense" for r in parsed.rows))

    def test_credit_that_appears_only_once_keeps_its_column(self):
        path = self.root / "once.pdf"
        rows = [["Date", "Description", "Debit", "Credit", "Balance"]]
        balance = 100.0
        for day in range(1, 9):
            balance -= 5
            rows.append([f"0{day} Feb 2026", "Shop", "5.00", "", f"{balance:.2f}"])
        rows.append(["09 Feb 2026", "Refund", "", "40.00", f"{balance + 40:.2f}"])
        table_pdf(path, rows)
        parsed = self.parsed(path)
        self.assertEqual(parsed.check, "verified")
        self.assertEqual(parsed.rows[-1]["direction"], "income")

    def test_cr_suffix_does_not_split_a_column(self):
        path = self.root / "cr.pdf"
        table_pdf(path, [["Date", "Description", "Debit", "Credit", "Balance"],
                         ["01 Feb 2026", "Shop", "5.00", "", "95.00"], ["02 Feb 2026", "Shop", "5.00", "", "90.00 CR"],
                         ["03 Feb 2026", "Shop", "5.00", "", "85.00 CR"], ["04 Feb 2026", "Shop", "5.00", "", "80.00"]])
        self.assertEqual(self.parsed(path).check, "verified")

    def test_margin_code_before_the_date_is_skipped(self):
        """账单页边的细小编号排在日期前面，那一行的日期不能丢。"""
        path = self.root / "margin.pdf"
        items = [(40, 750, "Date"), (105, 750, "Transaction"), (350, 750, "Debit"), (490, 750, "Balance")]
        for index in range(6):
            y = 720 - index * 30
            items += [(40, y, f"0{index + 1} Feb 2026"), (105, y, "Shop"), (350, y, "5.00"),
                      (490, y, f"{95 - index * 5}.00")]
        items.append((5, 690, "12.4AB."))
        pdf(path, [items])
        self.assertEqual([r["date"][-2:] for r in self.parsed(path).rows], ["01", "02", "03", "04", "05", "06"])

    def test_text_outside_the_page_is_ignored(self):
        path = self.root / "offpage.pdf"
        rows = [["Date", "Description", "Debit", "Balance"]] + [
            [f"0{d} Feb 2026", "Shop", "5.00", f"{100 - d * 5}.00"] for d in range(1, 6)]
        items = [(x, 750 - i * 30, t) for i, r in enumerate(rows) for x, t in zip((40, 105, 350, 490), r)]
        items += [(700, 720 - i * 30, "9,999.00") for i in range(5)]
        pdf(path, [items])
        self.assertEqual(self.parsed(path).check, "verified")

    def test_names_keep_their_spaces(self):
        for ruled in (False, True):
            for size in (6, 8, 10):
                with self.subTest(ruled=ruled, size=size):
                    path = self.root / "names.pdf"
                    table_pdf(path, [("Date", "Merchant", "Amount"), ("2026-09-01", "A SAMPLE", "-35.00"),
                                     ("2026-09-02", "ACME REALTY PTY LTD", "-12.00"), ("2026-09-03", "IKEA", "-8.50")],
                              columns=(40, 180, 430), font_size=size, ruled=ruled)
                    self.assertEqual([r["description"] or r["counterparty"] for r in self.parsed(path).rows],
                                     ["A SAMPLE", "ACME REALTY PTY LTD", "IKEA"])

    def test_character_tracking_is_not_a_word_gap(self):
        path = self.root / "track.pdf"
        table_pdf(path, [("Date", "Merchant", "Amount"), ("2026-09-01", "A SAMPLE", "-35.00"),
                         ("2026-09-02", "ACME REALTY", "-12.00")], columns=(40, 180, 430), tracking=True)
        self.assertIn("ACME REALTY", " ".join(c for r in documents.read(path)[0].rows for c in r))

    def test_ruled_table_keeps_every_cell(self):
        """有边框时不走 find_tables：真实账单上它把日期格整列丢了。"""
        path = self.root / "ruled.pdf"
        table_pdf(path, [("Date", "Merchant", "Amount"), ("02 Feb 2026", "A SAMPLE", "-35.00"),
                         ("03 Feb 2026", "IKEA", "-8.50")], columns=(40, 180, 430), ruled=True)
        self.assertEqual([r["date"] for r in self.parsed(path).rows], ["2026-02-02", "2026-02-03"])


class Ocr(Isolated):
    def scan(self) -> Path:
        from PIL import Image
        png = self.root / "page.png"
        image_page(png)
        path = self.root / "scan.pdf"
        with Image.open(png) as image:
            image.save(path, resolution=150)
        return path

    def test_scan_goes_through_ocr_and_is_flagged(self):
        table = documents.read(self.scan(), ocr=Mock(return_value=OCR_TABLE))[0]
        self.assertTrue(table.ocr)
        self.assertEqual(table.rows[-1], ["2026-09-01", "Coffee", "-35.00"])

    def test_one_image_page_does_not_lose_the_text_pages(self):
        """真实账单只有末页是图片，旧版为此整份失败。"""
        from pypdf import PdfWriter
        text = self.root / "text.pdf"
        table_pdf(text, [("Date", "Merchant", "Amount"), ("2026-09-01", "Coffee", "-35.00"),
                         ("2026-09-02", "Bus", "-12.00")], columns=(40, 180, 430))
        writer = PdfWriter()
        writer.append(text)
        writer.append(self.scan())
        merged = self.root / "mixed.pdf"
        writer.write(merged)
        writer.close()
        table = documents.read(merged)[0]
        self.assertTrue(any("Coffee" in " ".join(r) for r in table.rows))
        self.assertIn("未读取", table.warnings[0])

    def test_scan_without_a_vision_model_says_so(self):
        with self.assertRaisesRegex(statement.Unrecognized, "视觉模型"):
            statement.parse_table(documents.read(self.scan())[0], "scan.pdf")

    def test_grounded_boxes_are_rebuilt_by_coordinates(self):
        text = ("<|det|>text [20, 113, 110, 202]<|/det|>交易时间\n<|det|>text [269, 113, 356, 202]<|/det|>交易对方\n"
                "<|det|>text [816, 113, 862, 202]<|/det|>金额\n<|det|>text [24, 279, 201, 360]<|/det|>2026-08-01\n"
                "<|det|>text [269, 280, 336, 369]<|/det|>肯德基\n<|det|>text [816, 280, 872, 369]<|/det|>23.00\n"
                "<|det|>text [24, 448, 201, 528]<|/det|>2026-08-02\n<|det|>text [269, 448, 356, 538]<|/det|>滴滴出行\n"
                "<|det|>text [816, 448, 872, 538]<|/det|>28.50\n"
                # 同一块内容被输出两遍，第二遍常是占位标记
                "<|det|>text [816, 276, 872, 369]<|/det|>23.00\n<|det|>text [816, 446, 870, 538]<|/det|>[Non-Text]\n")
        parsed = statement.parse_table(documents.Table(rows=documents._ocr_rows(text), ocr=True), "s.png")
        self.assertEqual([(r["date"], r["amount"]) for r in parsed.rows], [("2026-08-01", 23.0), ("2026-08-02", 28.5)])
        self.assertIn("复核", parsed.warnings[-1])

    def test_truncated_html_is_rejected(self):
        with self.assertRaisesRegex(documents.ReadError, "不完整"):
            documents._ocr_rows("<table><tr><td>partial")

    def test_excel_sheets_and_embedded_image(self):
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image
        book = Workbook()
        book.active.title = "empty"
        for title in ("bank", "wallet"):
            sheet = book.create_sheet(title)
            sheet.append(["日期", "金额", "交易对方"])
            sheet.append(["2026-09-01", -35, "Coffee"])
        png = self.root / "bill.png"
        image_page(png)
        book.create_sheet("scan").add_image(Image(str(png)))
        path = self.root / "book.xlsx"
        book.save(path)
        tables = documents.read(path, Mock(return_value=OCR_TABLE))
        self.assertEqual(len(tables), 3)
        self.assertTrue(tables[-1].ocr)

    def test_every_tiff_frame(self):
        from PIL import Image
        path = self.root / "two.tiff"
        with Image.new("RGB", (32, 32), "white") as a, Image.new("RGB", (32, 32), "gray") as b:
            a.save(path, save_all=True, append_images=[b])
        ocr = Mock(return_value=OCR_TABLE)
        self.assertEqual(len(documents.read(path, ocr)), 2)
        self.assertEqual(ocr.call_count, 2)


class Password(Isolated):
    def test_locked_pdf_asks_for_the_password(self):
        from pypdf import PdfReader, PdfWriter
        plain = self.root / "plain.pdf"
        table_pdf(plain, [("Date", "Merchant", "Amount"), ("2026-09-01", "Coffee", "-35.00")], columns=(40, 180, 430))
        writer = PdfWriter(clone_from=PdfReader(plain))
        writer.encrypt("secret", algorithm="AES-256")
        locked = self.root / "locked.pdf"
        writer.write(locked)
        with self.assertRaises(documents.PasswordRequired):
            documents.read(locked)
        self.assertTrue(any("Coffee" in " ".join(r) for r in documents.read(locked, password="secret")[0].rows))
