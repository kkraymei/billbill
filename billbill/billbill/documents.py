"""把任何账单文件读成二维表格，后面的识别只面对这一种结构。

文件类型看内容，不看扩展名：银行导出的「.xls」常常其实是 HTML 或制表符文本，
「.doc」也可能是 RTF。新增格式只需要在 read() 里加一个分支。
"""

from __future__ import annotations

import csv
import email
import io
import re
import statistics
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16")
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"BM", b"II*\x00", b"MM\x00*")
# 文件对话框的筛选项。识别本身看内容，不依赖这张表。
SUFFIXES = (".pdf", ".docx", ".doc", ".rtf", ".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt",
            ".html", ".htm", ".mht", ".mhtml", ".png", ".jpg", ".jpeg", ".webp", ".bmp",
            ".tif", ".tiff")

_GAP = re.compile(r"\s{2,}|\t")
_RULE_LINE = re.compile(r"^[\s─—\-=_*·+|]{6,}$")


class ReadError(Exception):
    """文件读不了。消息直接显示给用户。"""


class PasswordRequired(ReadError):
    pass


@dataclass
class Table:
    rows: list[list[str]] = field(default_factory=list)
    note: str = ""
    ocr: bool = False                       # 数字来自 OCR，需要对照原件
    warnings: list[str] = field(default_factory=list)
    # 版面信息：行号 -> [(x0, x1, 文字)]。账单抬头常把标签和值上下分开摆，靠它对齐。
    spans: dict[int, list[tuple[float, float, str]]] = field(default_factory=dict)


def expand(paths: list[Path]) -> list[Path]:
    """文件夹展开成里面的账单文件。"""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
        elif path.is_file():
            found.append(path)
    return found


def read(path: Path, ocr=None, password: str = "", progress=None) -> list[Table]:
    """ocr：图片 -> 文字 的函数，没配视觉模型时为 None。"""
    head = path.read_bytes()[:4096]
    if not head:
        raise ReadError("文件是空的")
    if b"%PDF" in head[:1024]:
        return [_read_pdf(path, ocr, password, progress)]
    if head[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        if "word/document.xml" in names:
            return [_read_docx(path, ocr, progress)]
        if "xl/workbook.xml" in names:
            return _read_xlsx(path, ocr, progress)
        raise ReadError("这个压缩格式不是 Word 或 Excel 文档")
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return _read_ole(path)
    if head.startswith(IMAGE_MAGIC):
        return _read_image_file(path, ocr)
    text = _decode(path.read_bytes())
    stripped = text.lstrip()
    if stripped[:5] == "{\\rtf":
        return [_from_lines(_rtf_text(text).splitlines(), "RTF 文档")]
    if stripped[:12].lower().startswith(("mime-version", "from:", "subject:")) or "multipart/related" in text[:2000]:
        text = _mhtml_body(path.read_bytes())
    if re.search(r"<(?:html|table)[\s>]", text[:20000], re.I):
        return [_read_html(text)]
    return [_read_text(text)]


# ------------------------------------------------------------------ 文本
def _decode(raw: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # 解码成功但满屏替换字符，说明猜错了编码。
        if text.count("\ufffd") <= len(text) * 0.001:
            return text
    try:
        import chardet
        guess = chardet.detect(raw[:200_000]).get("encoding")
        if guess:
            return raw.decode(guess, errors="replace")
    except Exception:  # noqa: BLE001 - 探测失败就走下面的兜底
        pass
    return raw.decode("utf-8", errors="replace")


def _read_text(text: str) -> Table:
    head = "\n".join(text.splitlines()[:40])
    try:
        delimiter = csv.Sniffer().sniff(head, delimiters=",\t;|").delimiter
    except csv.Error:
        counts = {d: head.count(d) for d in (",", "\t", ";", "|")}
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    rows = [[cell.strip() for cell in row]
            for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    body = [r for r in rows if any(c.strip() for c in r)]
    # 银行导出的纯文本常是按空格对齐的定宽排版，没有真正的分隔符。
    single = sum(1 for r in body if len(r) <= 1)
    if body and single / len(body) >= 0.9:
        return _from_lines(text.splitlines(), "定宽文本")
    return Table(rows=rows, note="分隔文本")


def _from_lines(lines: list[str], note: str) -> Table:
    rows = []
    for line in lines:
        line = line.strip()
        if line and not _RULE_LINE.match(line):
            rows.append([c.strip() for c in _GAP.split(line) if c.strip()])
    return Table(rows=rows, note=note)


# ------------------------------------------------------------------ 网页 / MHTML
class _HtmlTables(HTMLParser):
    """HTML 表格转行列，展开跨行跨列。表格外的文字也留着，抬头账号就在那里。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.pending, self.col, self.span = {}, 0, (1, 1)
        self.loose = []

    def _fill(self):
        while self.col in self.pending:
            value, remaining = self.pending.pop(self.col)
            self.row.append(value)
            if remaining > 1:
                self.pending[self.col] = (value, remaining - 1)
            self.col += 1

    def _flush_loose(self):
        text = " ".join(" ".join(self.loose).split())
        self.loose = []
        if text:
            self.rows.append([c for c in _GAP.split(text) if c] or [text])

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._flush_loose()
            self.pending = {}
        elif tag == "tr":
            self._flush_loose()
            self.row, self.col = [], 0
            self._fill()
        elif tag in ("td", "th") and self.row is not None:
            self._fill()
            self.cell = []
            attrs = dict(attrs)
            try:
                self.span = tuple(max(1, min(100, int(attrs.get(k) or 1))) for k in ("rowspan", "colspan"))
            except ValueError:
                self.span = (1, 1)
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")
        elif tag in ("p", "div", "br", "h1", "h2", "h3", "li"):
            self._flush_loose()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        elif self.row is None and self.lasttag not in ("style", "script", "title", "head", "meta"):
            self.loose.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            value = " ".join("".join(self.cell).split())
            rowspan, colspan = self.span
            for _ in range(colspan):
                self.row.append(value)
                if rowspan > 1:
                    self.pending[self.col] = (value, rowspan - 1)
                self.col += 1
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self._fill()
            if any(self.row):
                self.rows.append(self.row)
            self.row = None


def _html_rows(text: str) -> list[list[str]]:
    parser = _HtmlTables()
    parser.feed(text)
    parser._flush_loose()
    return parser.rows


def _read_html(text: str) -> Table:
    return Table(rows=_html_rows(text), note="网页表格")


def _mhtml_body(raw: bytes) -> str:
    message = email.message_from_bytes(raw)
    for part in message.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset()
            return payload.decode(charset, "replace") if charset else _decode(payload)
    raise ReadError("MHTML 里没有网页内容")


# ------------------------------------------------------------------ RTF
def _rtf_text(text: str) -> str:
    """只取正文：\\cell 变制表符，\\row、\\par 变换行，字体表之类的控制组整组跳过。"""
    codepage = re.search(r"\\ansicpg(\d+)", text[:2000])
    codepage = f"cp{codepage.group(1)}" if codepage else "cp936"
    output, stack, skip, pending = [], [], 0, bytearray()

    def flush():
        if pending:
            output.append(pending.decode(codepage, "replace"))
            pending.clear()

    tokens = re.compile(r"\\u(-?\d+)\??|\\'([0-9a-fA-F]{2})|\\([a-zA-Z]+)(-?\d+)? ?|\\(.)|([{}])|([^\\{}]+)", re.S)
    for match in tokens.finditer(text):
        unicode_, hexcode, word, _, symbol, brace, plain = match.groups()
        if brace == "{":
            stack.append(skip)
        elif brace == "}":
            skip = stack.pop() if stack else 0
        elif skip:
            continue
        elif hexcode:
            pending.append(int(hexcode, 16))
        else:
            flush()
            if unicode_:
                value = int(unicode_)
                output.append(chr(value + 65536 if value < 0 else value))
            elif word in ("fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer",
                          "generator", "themedata", "datastore", "latentstyles", "listtable",
                          "listoverridetable", "rsidtbl", "xmlnstbl", "mmathPr", "wgrid"):
                skip = 1
            elif word == "cell":
                output.append("\t")
            elif word in ("row", "par", "line", "sect", "page"):
                output.append("\n")
            elif word == "tab":
                output.append("\t")
            elif symbol == "*":
                skip = 1
            elif symbol in ("\\", "{", "}"):
                output.append(symbol)
            elif plain:
                output.append(plain.replace("\r", "").replace("\n", ""))
    flush()
    return "".join(output)


# ------------------------------------------------------------------ Word
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(node) -> str:
    parts = []
    for item in node.iter():
        if item.tag == _W + "t":
            parts.append(item.text or "")
        elif item.tag in (_W + "tab", _W + "br", _W + "cr"):
            parts.append("  ")
    return "".join(parts).strip()


def _docx_rows(xml: bytes) -> list[list[str]]:
    rows = []

    def walk(parent):
        for child in parent:
            if child.tag == _W + "p":
                text = _docx_text(child)
                if text:
                    rows.append([c for c in _GAP.split(text) if c])
            elif child.tag == _W + "tbl":
                for row in child.iter(_W + "tr"):
                    cells = []
                    for cell in row.findall(_W + "tc"):
                        text = " ".join(t for t in (_docx_text(p) for p in cell.iter(_W + "p")) if t)
                        span = cell.find(f"{_W}tcPr/{_W}gridSpan")
                        cells.append(text)
                        cells += [""] * (int(span.get(_W + "val", 1)) - 1 if span is not None else 0)
                    if any(cells):
                        rows.append(cells)
            elif child.tag in (_W + "body", _W + "sdt", _W + "sdtContent"):
                walk(child)

    walk(ET.fromstring(xml))
    return rows


def _read_docx(path: Path, ocr, progress) -> Table:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        rows = []
        # 页眉里常有银行名和账号，排在正文前面，和抬头同等对待。
        for name in sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n)):
            rows += _docx_rows(archive.read(name))
        rows += _docx_rows(archive.read("word/document.xml"))
        table = Table(rows=rows, note="Word 文档")
        # 整页贴图的扫描件：正文几乎没字，图片交给 OCR。
        if sum(len(c) for r in rows for c in r) < 80:
            for name in sorted(n for n in names if n.startswith("word/media/")):
                _ocr_blob(table, archive.read(name), Path(name).suffix, ocr, name, progress)
    return table


def _ocr_blob(table: Table, blob: bytes, suffix: str, ocr, label: str, progress) -> None:
    if ocr is None:
        table.warnings.append(f"{label} 是图片，没有配置视觉模型，未读取")
        return
    if progress:
        progress(f"{label}：正在 OCR…")
    with tempfile.TemporaryDirectory(prefix="billbill-") as folder:
        target = Path(folder) / f"image{suffix}"
        target.write_bytes(blob)
        table.rows += _ocr_rows(ocr(target))
    table.ocr = True


def _ole_doc_text(stream: bytes, tables: dict[str, bytes]) -> str:
    """Word 97-2003 正文：按片段表（piece table）拼出文字。[MS-DOC] 2.8.35。"""
    flags = struct.unpack_from("<H", stream, 0x0A)[0]
    table = tables["1Table" if flags & 0x0200 else "0Table"]
    offset, length = struct.unpack_from("<II", stream, 0x01A2)
    clx = table[offset:offset + length]
    position = 0
    while position < len(clx) and clx[position] == 1:     # 跳过 Prc，后面才是片段表
        position += 3 + struct.unpack_from("<H", clx, position + 1)[0]
    if position >= len(clx) or clx[position] != 2:
        raise ReadError("读不懂这份 .doc 的内部结构，请用 Word 另存为 .docx")
    size = struct.unpack_from("<I", clx, position + 1)[0]
    plc = clx[position + 5:position + 5 + size]
    count = (size - 4) // 12
    marks = struct.unpack_from(f"<{count + 1}I", plc)
    parts = []
    for index in range(count):
        chars = marks[index + 1] - marks[index]
        fc = struct.unpack_from("<I", plc, (count + 1) * 4 + index * 8 + 2)[0]
        if fc & 0x40000000:
            start = (fc & 0x3FFFFFFF) // 2
            parts.append(stream[start:start + chars].decode("cp1252", "replace"))
        else:
            parts.append(stream[fc:fc + chars * 2].decode("utf-16-le", "replace"))
    return "".join(parts)


def _cells_to_rows(cells: list[str]) -> list[list[str]]:
    """.doc 里 \x07 既是单元格结束也是行结束，所以空格子和行尾长得一样。

    办法是看周期：每行都多一个行尾标记（必为空），周期 P 上恰好全空、
    又能整除总数的，就是列数 + 1。
    """
    cleaned = [" ".join(c.split()) for c in cells]
    for period in range(2, min(len(cleaned), 60) + 1):
        if len(cleaned) % period == 0 and not any(cleaned[period - 1::period]):
            return [cleaned[i:i + period - 1] for i in range(0, len(cleaned), period)]
    return [[c] for c in cleaned if c]


def _doc_rows(text: str) -> list[list[str]]:
    text = re.sub(r"\x13[^\x14\x15]*\x14?", "", text).replace("\x15", "")   # 域代码
    rows: list[list[str]] = []
    cells: list[str] = []

    def paragraphs(block: str):
        for line in re.split(r"[\r\x0b\x0c]", block):
            if line.strip():
                rows.append([c for c in _GAP.split(line.strip()) if c])

    parts = text.split("\x07")
    for index, token in enumerate(parts[:-1]):
        # 表格之前（或两张表之间）的段落和第一格粘在同一段里。
        if "\r" in token and (not cells or (not parts[index - 1] and len(cells) > 1)):
            rows += _cells_to_rows(cells)
            cells = []
            lead, _, token = token.rpartition("\r")
            paragraphs(lead)
        cells.append(token)
    rows += _cells_to_rows(cells)
    paragraphs(parts[-1])
    return [r for r in rows if any(r)]


def _read_ole(path: Path) -> list[Table]:
    import olefile
    with olefile.OleFileIO(str(path)) as document:
        if not document.exists("WordDocument"):
            return _read_xls(path)
        tables = {name: document.openstream(name).read() if document.exists(name) else b""
                  for name in ("0Table", "1Table")}
        text = _ole_doc_text(document.openstream("WordDocument").read(), tables)
    return [Table(rows=_doc_rows(text), note="Word 97-2003 文档")]


# ------------------------------------------------------------------ Excel
def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_xlsx(path: Path, ocr, progress) -> list[Table]:
    from openpyxl import load_workbook
    book = load_workbook(path, data_only=True)
    tables = []
    try:
        for sheet in book.worksheets:
            rows = [[_cell_text(c) for c in row] for row in sheet.iter_rows(values_only=True)]
            table = Table(rows=rows, note=f"工作表「{sheet.title}」")
            for index, image in enumerate(sheet._images, 1):
                _ocr_blob(table, image._data(), f".{image.format}", ocr,
                          f"工作表「{sheet.title}」图片 {index}", progress)
            if any(any(row) for row in table.rows) or table.warnings:
                tables.append(table)
    finally:
        book.close()
    return tables


def _read_xls(path: Path) -> list[Table]:
    import xlrd
    book = xlrd.open_workbook(str(path))
    tables = []
    for sheet in book.sheets():
        rows = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                row.append(_cell_text(xlrd.xldate_as_datetime(cell.value, book.datemode)
                                      if cell.ctype == xlrd.XL_CELL_DATE else cell.value))
            rows.append(row)
        if any(any(row) for row in rows):
            tables.append(Table(rows=rows, note=f"工作表「{sheet.name}」"))
    book.release_resources()
    return tables


# ------------------------------------------------------------------ PDF 版面
# 不按列间空隙切列：真实账单的列间距可以只有几 pt，比列内空隙还窄。
# 金额列才是表格的骨架——数字总是对齐的。所以先在有日期的行上找出金额的对齐位置，
# 确定金额列，其余文字归说明。文字列挤在一起也不影响。
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*"
_DATE_WORDS = re.compile(
    r"^\d{6,8}$|^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$|^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$"
    r"|^\d{1,2}\s*月\s*\d{1,2}\s*日$|^\d{1,2}[/-]\d{1,2}$"
    rf"|^\d{{1,2}}[\s.-]*{_MONTH}(?:[\s,.-]*\d{{2,4}})?$|^{_MONTH}\s*\d{{1,2}}(?:\s*,?\s*\d{{4}})?$", re.A | re.I)
_TIME_WORD = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_MONEY_WORD = re.compile(
    r"^[-+(−]?\s*(?:[A-Z]{3})?\s*[-+$¥￥€£]?\s*[-+]?\d[\d,]*\.\d{2}\)?\s*(?:CR|DR|Cr|Dr|-)?$", re.A)
_MONEY_AFFIX = re.compile(r"^(?:[-+(−$¥￥€£]+|CR|DR|Cr|Dr)$")
_MONEY_HEADER = re.compile(r"^(?:debits?|credits?|balance|withdrawals?|deposits?|amount|paid|money|支出|收入|"
                           r"余额|金额|借方|贷方)\b", re.I)
_MAX_OFFSET = 2         # 日期前最多还有几个词（序号之类）


def _merge_money(words: list[dict], width: float) -> list[dict]:
    """把拆开的「- $ 12.00 CR」并回一个词。"""
    merged: list[dict] = []
    tight = width * 0.012
    for word in words:
        word = dict(word)
        if merged:
            last = merged[-1]
            joined = f'{last["text"]}{word["text"]}'
            near = word["x0"] - last["x1"] < tight
            if near and (_MONEY_AFFIX.match(last["text"]) or _MONEY_WORD.match(last["text"])) \
                    and _MONEY_WORD.match(joined):
                # 对齐看的是数字的右边缘：CR 后缀不能把它往右推，否则同一列会裂成两列。
                edge = last.get("edge", last["x1"]) if _MONEY_AFFIX.match(word["text"]) else word["x1"]
                last.update(text=joined, x1=word["x1"], edge=edge)
                continue
        merged.append(word)
    return merged


def _date_span(words: list[dict], column: float | None = None) -> tuple[int, int]:
    """行内日期所占的词范围，没有返回 (0, 0)。日期可能拆成 1～3 个词，后面可能跟时间。

    column 是日期列的 x 位置。知道它以后，日期前面的页边编号、序号都能跳过。
    """
    for start in range(min(_MAX_OFFSET + 1, len(words))):
        if column is not None and abs(words[start]["x0"] - column) > 6:
            continue
        for size in range(min(3, len(words) - start), 0, -1):
            if _DATE_WORDS.fullmatch(" ".join(w["text"] for w in words[start:start + size])):
                end = start + size
                if end < len(words) and _TIME_WORD.match(words[end]["text"]):
                    end += 1
                return start, end
        if column is None and re.search(r"[A-Za-z一-鿿]", words[start]["text"]):
            break
    return 0, 0


def _lines_of(words: list[dict]) -> list[list[dict]]:
    """按纵坐标把词归成行。行距随字号变，容差取词高的中位数。"""
    if not words:
        return []
    heights = sorted(w["bottom"] - w["top"] for w in words)
    tolerance = max(2.0, heights[len(heights) // 2] * 0.6)
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and word["top"] - lines[-1][0]["top"] <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _edge(word: dict) -> float:
    """数字部分的右边缘。「12.00CR」连写时按字符宽度扣掉后缀。"""
    if "edge" in word:
        return word["edge"]
    tail = len(re.search(r"[^\d.)]*$", word["text"]).group())
    return word["x1"] - (word["x1"] - word["x0"]) * tail / max(1, len(word["text"]))


def _money_columns(lines: list[list[dict]], width: float) -> list[dict]:
    """找金额列：有日期的行里，右边缘对得齐的数字。"""
    # 一笔可能跨几行、金额在最后一行，所以日期行之后紧跟的几行也算。
    dated, window = [], 0
    for line in lines:
        window = 4 if _date_span(line)[1] else max(0, window - 1)
        if window and any(_MONEY_WORD.match(w["text"]) for w in line):
            dated.append(line)
    edges = sorted((_edge(w), w["x1"] - w["x0"]) for line in dated for w in line
                   if _MONEY_WORD.match(w["text"]))
    if not edges:
        return []
    groups, tolerance = [[edges[0]]], width * 0.012
    for edge in edges[1:]:
        if edge[0] - groups[-1][-1][0] <= tolerance:
            groups[-1].append(edge)
        else:
            groups.append([edge])
    # 说明里偶尔夹着金额（外币原价），位置是散的，凑不成一列。
    support = max(2, len(dated) * 0.04) if len(dated) >= 4 else 1
    columns = [{"x1": statistics.median(e[0] for e in group), "width": max(e[1] for e in group),
                "count": len(group)} for group in groups]
    # 但真列可能整份只出现一次（只有一笔收入）。这种列要有表头正对着它才算数，
    # 而且那个表头词下面还没有稳定的列。
    anchors = [w for line in lines for w in line if _MONEY_HEADER.search(w["text"])
               and sum(bool(_MONEY_HEADER.search(o["text"])) for o in line) >= 2]

    def under(column, word):
        return word["x0"] - tolerance <= column["x1"] <= word["x1"] + column["width"]
    strong = [c for c in columns if c["count"] >= support]
    claimed = [w for w in anchors if any(under(c, w) for c in strong)]
    return [c for c in columns if c in strong
            or any(under(c, w) for w in anchors if w not in claimed)]


def _column_of(word: dict, columns: list[dict], tolerance: float) -> int | None:
    for index, column in enumerate(columns):
        if abs(_edge(word) - column["x1"]) <= tolerance:
            return index
    return None


def _word_runs(words: list[dict], gap: float) -> list[tuple[float, float, str]]:
    runs: list[list[dict]] = []
    for word in words:
        if runs and word["x0"] - runs[-1][-1]["x1"] < gap:
            runs[-1].append(word)
        else:
            runs.append([word])
    return [(run[0]["x0"], run[-1]["x1"], " ".join(w["text"] for w in run)) for run in runs]


def _layout(lines: list[list[dict]], width: float) -> tuple[list[list[str]], dict]:
    """行 -> 表格：[日期, 文字, 金额列…]。找不到金额列时退回按词距切格。"""
    lines = [_merge_money(line, width) for line in lines]
    columns = _money_columns(lines, width)
    starts = [line[_date_span(line)[0]]["x0"] for line in lines
              if _date_span(line)[1] and any(_MONEY_WORD.match(w["text"]) for w in line)]
    date_x = statistics.median(starts) if len(starts) >= 3 else None
    gap = width * 0.014
    rows, spans = [], {}
    tolerance = width * 0.014
    for line in lines:
        runs = _word_runs(line, gap)
        joined = " ".join(w["text"] for w in line)
        if _RULE_LINE.match(joined):
            continue
        spans[len(rows)] = runs
        if not columns:
            rows.append([run[2] for run in runs])
            continue
        start, end = _date_span(line, date_x)
        if not end:
            start, end = _date_span(line)
        cells = [""] * (2 + len(columns))
        text = []
        rest = line[:start] + line[end:] if end else line
        if end:
            cells[0] = " ".join(w["text"] for w in line[start:end])
        elif not any(_MONEY_WORD.match(w["text"]) for w in line):
            # 表头行没有金额：词落在哪一列的跨度里，就归哪一列。
            hits = {}
            for word in line:
                for index, column in enumerate(columns):
                    if word["x0"] <= column["x1"] + tolerance and word["x1"] >= column["x1"] - column["width"] - tolerance:
                        hits.setdefault(index, []).append(word)
                        break
            taken = {id(w) for group in hits.values() for w in group}
            if len(hits) >= 2 or (hits and len(line) <= 8 and len(taken) == 1 and len(runs) >= 3):
                for index, group in hits.items():
                    cells[2 + index] = " ".join(w["text"] for w in group)
                others = [w for w in line if id(w) not in taken]
                if others:
                    # 表头的第一个词放到日期列，和数据行对齐。
                    cells[0] = others[0]["text"]
                    cells[1] = " ".join(w["text"] for w in others[1:])
                rows.append(cells)
                continue
            rows.append([run[2] for run in runs])
            continue
        for word in rest:
            index = _column_of(word, columns, tolerance) if _MONEY_WORD.match(word["text"]) else None
            if index is None or cells[2 + index]:
                text.append(word["text"])
            else:
                cells[2 + index] = word["text"]
        cells[1] = " ".join(text)
        rows.append(cells)
    return rows, spans


# ------------------------------------------------------------------ PDF
# 银行 PDF 常用坐标偏移表示空格，没有真正的空格字符。默认 3pt 容差会吞掉小字号下的
# 词距（A B -> AB），按字号取容差才保得住。
_TEXT_OPTIONS = {"x_tolerance_ratio": 0.15}


def _needs_ocr(page, text: str) -> bool:
    useful = sum(c.isalnum() and c != "\ufffd" for c in text)
    # 扫描件也可能带一层可选中的页码，所以看的是大图片「里面」有没有文字。
    image_without_text = any(
        max(0, image["x1"] - image["x0"]) * max(0, image["bottom"] - image["top"])
        > page.width * page.height * 0.2
        and sum(1 for c in page.chars if image["x0"] <= c["x0"] <= image["x1"]
                and image["top"] <= c["top"] <= image["bottom"] and c.get("text", "").isalnum()) < 20
        for image in page.images)
    return image_without_text or "\ufffd" in text or "(cid:" in text or (useful < 4 and bool(page.images or page.curves))


def _ocr_page(page, ocr, table: Table) -> None:
    with tempfile.TemporaryDirectory(prefix="billbill-") as folder:
        target = Path(folder) / "page.png"
        # 控制总像素：太大模型吃不下，太小小字糊掉。
        dpi = min(180, max(1, int(72 * (16_000_000 / (page.width * page.height)) ** 0.5)))
        rendered = page.to_image(resolution=dpi)
        try:
            rendered.original.save(target, format="PNG")
        finally:
            rendered.original.close()
        table.rows += _ocr_rows(ocr(target))


def _visible_words(area, page) -> list[dict]:
    """页面范围外的文字用户看不见，有的账单在那里放隐藏字段，不能算成一列。"""
    return [w for w in area.extract_words(**_TEXT_OPTIONS) if w["x0"] < page.width and w["top"] < page.height]


def _read_pdf(path: Path, ocr, password: str, progress) -> Table:
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    try:
        pdf = pdfplumber.open(str(path), password=password)
    except PDFPasswordIncorrect as exc:
        raise PasswordRequired("这份 PDF 有密码") from exc
    except Exception as exc:
        if "PDFPasswordIncorrect" in repr(exc):
            raise PasswordRequired("这份 PDF 有密码") from exc
        raise
    table = Table(note="PDF")
    with pdf:
        total = len(pdf.pages)
        # 文字页的行集中起来一起排版：金额列要用全文的数据来找，单页可能只有一两笔。
        segments: list[tuple[str, object]] = []
        width = 0.0
        for number, page in enumerate(pdf.pages, 1):
            if progress:
                progress(f"读取第 {number}/{total} 页")
            text = page.extract_text(**_TEXT_OPTIONS) or ""
            if _needs_ocr(page, text):
                if ocr is None:
                    table.warnings.append(f"第 {number}/{total} 页是图片或文字层损坏，没有配置视觉模型，未读取")
                    continue
                if progress:
                    progress(f"第 {number}/{total} 页是图片，正在 OCR…")
                part = Table()
                _ocr_page(page, ocr, part)
                segments.append(("rows", part.rows))
                table.ocr = True
                continue
            if not text.strip():
                continue
            width = max(width, float(page.width))
            # 不走 find_tables：有边框的表格它会丢格子（日期格整列变空），按词的坐标读更稳。
            segments.append(("lines", _lines_of(_visible_words(page, page))))
    lines = [line for kind, part in segments if kind == "lines" for line in part]
    laid, spans = _layout(lines, width or 600.0)
    # 排版是全文一起算的，再按原来的先后顺序和 OCR 页拼起来。
    cursor = 0
    for kind, part in segments:
        if kind == "rows":
            table.rows += part
            continue
        count = sum(1 for line in part if not _RULE_LINE.match(" ".join(w["text"] for w in line)))
        for index in range(cursor, cursor + count):
            if index in spans:
                table.spans[len(table.rows)] = spans[index]
            table.rows.append(laid[index])
        cursor += count
    if not table.rows and not table.warnings:
        raise ReadError("PDF 里没有可读取的文字或图像内容")
    return table


# ------------------------------------------------------------------ 图片 / OCR
# DeepSeek-OCR 一类的模型在 grounding 模式下输出带坐标框的文本，
# 坐标比模型自己排的 Markdown 可靠得多，走和 PDF 一样的版面逻辑。
_GROUNDED_BOX = re.compile(
    r"<\|det\|>[^\[\]]*?\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\s*<\|/det\|>[ \t]*([^\n<]*)", re.I)
_OCR_PLACEHOLDER = re.compile(r"^\[(?:non-?text|image|figure|table|chart|graphic|logo)\]$", re.I)


def _grounded_words(text: str) -> list[dict]:
    words: list[dict] = []
    for found in _GROUNDED_BOX.finditer(text):
        x0, top, x1, bottom = (int(found.group(i)) for i in range(1, 5))
        content = found.group(5).strip()
        if not content or _OCR_PLACEHOLDER.match(content):
            continue
        box = {"text": content, "x0": min(x0, x1), "x1": max(x0, x1),
               "top": min(top, bottom), "bottom": max(top, bottom)}
        # 模型有时把同一块内容输出两遍，位置几乎重合的只留文字长的。
        twin = next((w for w in words if _overlap(box, w) >= 0.6), None)
        if twin is None:
            words.append(box)
        elif len(content) > len(twin["text"]):
            twin.update(box)
    return words


def _overlap(one: dict, other: dict) -> float:
    across = min(one["x1"], other["x1"]) - max(one["x0"], other["x0"])
    down = min(one["bottom"], other["bottom"]) - max(one["top"], other["top"])
    if across <= 0 or down <= 0:
        return 0.0
    smaller = min((one["x1"] - one["x0"]) * (one["bottom"] - one["top"]),
                  (other["x1"] - other["x0"]) * (other["bottom"] - other["top"]))
    return across * down / max(1, smaller)


def _ocr_rows(text: str) -> list[list[str]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    words = _grounded_words(text)
    if len(words) >= 2:
        lines = _lines_of(words)
        return _layout(lines, max(w["x1"] for w in words) + 2)[0]
    text = re.sub(r"<\|(?:det|ref)\|>.*?<\|/(?:det|ref)\|>", "", text, flags=re.S)
    text = re.sub(r"<\|[^>]+\|>", "", text)
    if re.search(r"<table[\s>]", text, re.I):
        if not re.search(r"</table\s*>", text, re.I):
            raise ReadError("OCR 返回了不完整的表格，已停止以免漏账")
        return _html_rows(text)
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("```") or set(line) <= set("-|: "):
            continue
        if "|" in line:
            cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        else:
            cells = [c for c in _GAP.split(line) if c]
        if any(cells):
            rows.append(cells)
    return rows


def _read_image_file(path: Path, ocr) -> list[Table]:
    if ocr is None:
        raise ReadError("图片账单需要先在设置里选择 OCR 视觉模型和配套 mmproj")
    from PIL import Image, ImageOps, ImageSequence
    tables = []
    with Image.open(path) as image, tempfile.TemporaryDirectory(prefix="billbill-") as folder:
        for index, frame in enumerate(ImageSequence.Iterator(image), 1):
            normalized = ImageOps.exif_transpose(frame).convert("RGB")
            if normalized.width * normalized.height > 16_000_000:
                raise ReadError("图片过大，请裁切后重试，否则账单小字识别不清")
            target = Path(folder) / f"frame-{index}.png"
            normalized.save(target)
            tables.append(Table(rows=_ocr_rows(ocr(target)), ocr=True, note=f"图片第 {index} 帧"))
    if not any(table.rows for table in tables):
        raise ReadError("视觉模型没有从图片里识别出内容")
    return tables
