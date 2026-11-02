"""测试工具。全部是合成数据，不读取任何真实账单。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests/fixtures"

from billbill import config, ledger  # noqa: E402


class Isolated(unittest.TestCase):
    """每个测试一个空的数据目录。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        values = {"DATA_DIR": self.root / "data", "DB_PATH": self.root / "data/billbill.db",
                  "SETTINGS_PATH": self.root / "data/settings.json", "TEMPLATE_DIR": self.root / "templates"}
        for name, value in values.items():
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        ledger.init()

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / name
        path.write_bytes(text.encode(encoding))
        return path


def pdf(path, pages, font_size=8, ruled=False, tracking=False):
    """银行风格的 PDF：词间距是坐标偏移，不是空格字符。pages: [[(x, y, 文字), …], …]"""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                                NameObject("/Subtype"): NameObject("/Type1"),
                                                NameObject("/BaseFont"): NameObject("/Courier")}))
    for items in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        commands = []
        for x, y, text in items:
            pieces = " -250 ".join(" -50 ".join(f"({c})" for c in word) if tracking else f"({word})"
                                   for word in text.split())
            commands.append(f"BT /F1 {font_size} Tf {x} {y} Td [{pieces}] TJ ET")
        if ruled:
            commands += [f"{x} 600 m {x} 770 l S" for x in (35, 170, 420, 510)]
            commands += [f"35 {y} m 510 {y} l S" for y in (600, 645, 675, 705, 735, 770)]
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    writer.close()


def table_pdf(path, rows, columns=(40, 105, 350, 420, 490), **options):
    items = [(x, 750 - i * 30, text) for i, row in enumerate(rows) for x, text in zip(columns, row) if text]
    pdf(path, [items], **options)


def image_page(path):
    from PIL import Image
    Image.new("RGB", (1200, 800), "white").save(path)
