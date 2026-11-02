"""表格 -> 交易记录。全程确定性：日期、金额、方向都由代码算，模型最多告诉我们哪列是什么。

列怎么认，三层：
1. 表头关键词。
2. 单元格内容：日期样、金额样的格子落在哪几列，不依赖表头语言，也不要求有表头。
3. 算术：上笔余额 ± 本笔金额 = 本笔余额。这条等式成立的那种列分配就是对的——
   既能认出余额列和收支列，也能当场核对解析结果。

每份账单都带核对结论（Statement.check）。解析错了应该被看见，而不是悄悄进账本。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import permutations

from .documents import Table

CENT = 0.005
FIELDS = ("date", "time", "amount", "income", "expense", "direction", "counterparty",
          "description", "category", "account", "counter_account", "status", "order_id",
          "remark", "balance")


class Unrecognized(ValueError):
    """这张表不是账单，或者认不出来。消息直接显示给用户。"""


# ------------------------------------------------------------------ 日期
_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
           "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
           "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11,
           "november": 11, "dec": 12, "december": 12}
_DAY_MONTH = re.compile(r"^(\d{1,2})[\s.-]*([A-Za-z]{3,9})\.?(?:[\s,/.-]*(\d{2,4}))?(?:\s+\d{1,2}:\d{2}\S*)?$", re.A)
_MONTH_DAY = re.compile(r"^([A-Za-z]{3,9})\.?[\s/-]*(\d{1,2})(?:[\s,]+(\d{4}))?(?:\s+\d{1,2}:\d{2}\S*)?$", re.A)
_CN_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_CN_MONTH_DAY = re.compile(r"^(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_YMD = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_ABC = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?!\d)")
_AB = re.compile(r"^(\d{1,2})[-/.](\d{1,2})$")
_CLOCK = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?")


@dataclass
class DateStyle:
    """这份账单的日期写法，整份统一。"""
    year: int | None = None     # 只写月日时用
    day_first: bool = True      # 03/04/2026 读成 4 月 3 日
    compact: str = "yymmdd"     # 6 位纯数字的顺序


def _build(year: int, month: int, day: int) -> str:
    # 年份离谱的一律当解析失败。静默给出公元 839 年比直接说读不出来危险得多。
    if year < 100:
        year += 2000 if year < 70 else 1900
    if not 1980 <= year <= datetime.now().year + 2:
        return ""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_date(value, style: DateStyle | None = None) -> tuple[str, str]:
    """返回 (带时间的文本, YYYY-MM-DD)，读不出来返回 ("", "")。"""
    style = style or DateStyle()
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()
    if not text:
        return "", ""
    day = ""
    if re.fullmatch(r"\d{6}", text):
        # 必须先于通用写法：260826 按 %Y%m%d 会读成公元 2608 年。
        a, b, c = int(text[:2]), int(text[2:4]), int(text[4:])
        order = {"ddmmyy": (c, b, a), "mmddyy": (c, a, b)}.get(style.compact, (a, b, c))
        day = _build(*order)
        return (day, day) if day else ("", "")
    if re.fullmatch(r"\d{8}", text):
        day = _build(int(text[:4]), int(text[4:6]), int(text[6:]))
        return (day, day) if day else ("", "")

    found = _CN_DATE.search(text) or _YMD.search(text)
    if found:
        day = _build(*(int(g) for g in found.groups()))
    elif (found := _ABC.match(text)):
        a, b, c = (int(g) for g in found.groups())
        day = _build(c, b, a) if style.day_first else _build(c, a, b)
        if not day:   # 这一格和整份的写法相反时，13 以上的数字只能是日
            day = _build(c, a, b) if style.day_first else _build(c, b, a)
    elif (found := _DAY_MONTH.match(text) or _MONTH_DAY.match(text)):
        first, second, year = found.groups()
        day_text, month_text = (first, second) if found.re is _DAY_MONTH else (second, first)
        # 必须整词匹配：按前缀的话 09MayCOLES0839 会排到公元 839 年。
        month = _MONTHS.get(month_text.lower())
        if month:
            day = _build(int(year) if year else (style.year or datetime.now().year), month, int(day_text))
    elif (found := _CN_MONTH_DAY.match(text)):
        day = _build(style.year or datetime.now().year, int(found.group(1)), int(found.group(2)))
    elif (found := _AB.match(text)) and style.year:
        a, b = int(found.group(1)), int(found.group(2))
        day = _build(style.year, a, b) if not style.day_first or b > 12 else _build(style.year, b, a)
    if not day:
        return "", ""
    clock = _CLOCK.search(text)
    if clock:
        hour, minute, second = int(clock.group(1)), int(clock.group(2)), int(clock.group(3) or 0)
        if hour < 24 and minute < 60 and second < 60:
            return f"{day} {hour:02d}:{minute:02d}:{second:02d}", day
    return day, day


def is_date(cell: str) -> bool:
    return len(cell) <= 30 and bool(parse_date(cell, DateStyle(year=2000))[1])


# ------------------------------------------------------------------ 金额
_MONEY = re.compile(
    r"^(?P<neg>[-−(+]?)\s*(?:CNY|RMB|USD|AUD|NZD|EUR|GBP|HKD|JPY|SGD|CAD|KRW|THB|TWD|人民币)?\s*[$¥€£]?\s*(?P<neg2>[-−+]?)\s*"
    r"(?P<number>\d[\d,  ]*(?:\.\d+)?|\.\d+)\s*\)?\s*"
    r"(?P<mark>CR|DR|Cr|Dr|cr|dr|-|元)?$", re.A)
_EURO = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$")


def parse_money(value) -> tuple[float, int, str] | None:
    """返回 (绝对值, 原文符号, CR/DR 标记)。不是金额返回 None。

    必须整格匹配。摘要里的「Ref 12」不是 12 块钱。
    """
    if isinstance(value, (int, float)):
        return abs(float(value)), -1 if value < 0 else 1, ""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return None
    number = re.sub(r"[^\d.,]", "", text)
    if _EURO.fullmatch(number):
        text = text.replace(".", "").replace(",", ".")
    found = _MONEY.match(text)
    if not found:
        return None
    try:
        amount = float(re.sub(r"[, ]", "", found.group("number")))
    except ValueError:
        return None
    mark = found.group("mark") or ""
    negative = (found.group("neg") + found.group("neg2")).strip("+") or mark == "-"
    return amount, -1 if negative else 1, mark.upper() if mark.upper() in ("CR", "DR") else ""


def is_money(cell: str) -> bool:
    """像金额：带小数或货币符号。纯整数太像编号，只在表头点名时才认。"""
    return bool(cell) and parse_money(cell) is not None and bool(re.search(r"\.\d{2}\b|[$¥￥€£]", cell))


# ------------------------------------------------------------------ 表头关键词
# 每个字段一组词，越靠前权重越高。
KEYWORDS: dict[str, list[str]] = {
    "date": ["交易时间", "交易日期", "交易创建时间", "记账日期", "入账日期", "发生日期", "交易日", "记账日",
             "日期", "时间", "transactiondate", "trandate", "postingdate", "tr.d.", "date"],
    "time": ["交易时间点", "时间戳"],
    "amount": ["交易金额", "发生额", "人民币金额", "金额(元)", "金额（元）", "金额", "交易额",
               "tran.amount", "transactionamount", "amount", "net", "netamount", "gross"],
    "income": ["收入金额", "贷方发生额", "存入金额", "收入(元)", "收入", "转入金额", "贷方", "creditamount",
               "credit", "credits", "deposit", "deposits", "paidin", "moneyin", "in"],
    "expense": ["支出金额", "借方发生额", "支取金额", "支出(元)", "支出", "转出金额", "借方", "debitamount",
                "debit", "debits", "withdrawal", "withdrawals", "paidout", "moneyout", "out"],
    "direction": ["收/支", "收支", "收付标志", "借贷标志", "收支类型", "借贷方向", "资金动向", "方向", "dr/cr"],
    "counterparty": ["交易对方", "对方户名", "对手户名", "对方账户名称", "对方账户名", "商户名称", "对手信息",
                     "对手方名称", "对方名称", "收款方", "付款方", "对方姓名", "商户", "户名",
                     "payee", "merchant", "counterparty"],
    "description": ["商品说明", "商品名称", "交易说明", "交易摘要", "订单标题", "商品", "摘要", "用途", "备注说明",
                    "说明", "purpose/details", "purpose", "details", "narrative", "transactiondetails",
                    "particulars", "transaction", "description"],
    "category": ["交易分类", "交易类型", "业务类型", "类型", "分类"],
    # 判断自己两个账户之间的划转全靠它，比从说明里抠数字可靠得多。
    "counter_account": ["对方账号", "对方账户", "对方卡号", "对手账号", "对手账户", "收款账号", "付款账号",
                        "转入账号", "转出账号", "counterpartyaccount", "payeeaccount"],
    "account": ["收/付款方式", "支付方式", "付款方式", "账户", "卡号", "卡号末四位", "账号", "银行卡"],
    "status": ["交易状态", "当前状态", "状态"],
    "order_id": ["交易订单号", "交易单号", "商户订单号", "交易号", "订单号", "流水号", "交易流水号", "凭证号码",
                 "凭证号", "业务编号", "vou.no.", "voucherno", "transno", "referenceno"],
    "remark": ["备注", "附言", "留言", "交易地点", "note", "remark"],
    "balance": ["联机余额", "账户余额", "余额", "balance"],
}
_NOISE = re.compile(r"[\s　*'\"`()（）\[\]【】:：]+")
_DECORATION = re.compile(r"^[\s─—\-=_*·+|~#.]+$")


def _norm(text: str) -> str:
    return _NOISE.sub("", text or "").lower()


def _score(cell: str, keyword: str) -> float:
    cell, keyword = _norm(cell), _norm(keyword)
    if not cell or not keyword:
        return 0.0
    if cell == keyword:
        return 1.0
    if len(keyword) <= 3 and keyword.isascii():
        # 「in」「out」不能按子串算：Description 里也有 in。
        return 0.0
    if cell.startswith(keyword) or cell.endswith(keyword):
        return 0.75
    return 0.6 if keyword in cell else 0.0


def _header_score(row: list[str]) -> float:
    cells = [c for c in row if (c or "").strip()]
    if len(cells) < 3:
        return 0.0
    hits = sum(1 for c in cells if any(_score(c, k) >= 0.6 for words in KEYWORDS.values() for k in words))
    short = sum(1 for c in cells if len(c) <= 16) / len(cells)
    numeric = sum(1 for c in cells if re.fullmatch(r"[-+¥￥$]?[\d,.]+", c or "")) / len(cells)
    return hits + short * 2 - numeric * 6


def _data_like(row: list[str]) -> bool:
    cells = [c.strip() for c in row if (c or "").strip()]
    return any(is_date(c) for c in cells) and any(is_money(c) for c in cells)


def _header_line(row: list[str]) -> bool:
    """这一行也是表头的一部分吗：好几格文字，没有数据。"""
    cells = [c for c in row if (c or "").strip()]
    if len(cells) < 2 or any(_DECORATION.match(c) for c in cells):
        return False
    if not any(re.search(r"[A-Za-z一-鿿]", c) for c in cells):
        return False
    if any(re.search(r"[\d,]+\.\d{2}|\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|^\d{6,8}$", c) or is_date(c) for c in cells):
        return False
    # 一句说明文字拆成两三格时也像表头，并进来会把列名污染成「appears Date」。
    if any(len(c.split()) >= 4 or c.rstrip().endswith((").", "。", "）。")) for c in cells):
        return False
    return all(len(c) <= 40 for c in cells)


def find_header(rows: list[list[str]], scan: int = 200) -> tuple[int, int]:
    """定位表头，返回 (起始行, 占几行)，找不到 (-1, 0)。

    封面上的「Opening balance - Total debits + Total credits = Closing balance」按关键词
    打分不低，但底下没有交易，所以先比下面跟着多少行交易，再比关键词。
    """
    found = []
    for index, row in enumerate(rows[:scan]):
        total = _header_score(row)
        if total >= 3:
            found.append((index, min(sum(_data_like(r) for r in rows[index + 1:index + 41]), 8), total))
    if not found:
        return -1, 0
    # 多页账单每页都有表头，取最早的那个，否则前面几页的交易全被跳过。
    top = max(b for _, b, _ in found)
    peak = max(t for _, b, t in found if b == top)
    best = next(i for i, b, t in found if b == top and t >= peak - 1.5)
    start, end = best, best
    # 中英双语表头常叠好几行（Tr.D. / 交易日 / YYMMDD 是同一列）。
    while start > max(0, best - 2) and _header_line(rows[start - 1]):
        start -= 1
    while end + 1 < len(rows) and end < best + 2 and _header_line(rows[end + 1]):
        end += 1
    return start, end - start + 1


def composite_header(rows: list[list[str]], start: int, span: int) -> list[str]:
    block = rows[start:start + span]
    width = max((len(r) for r in block), default=0)
    merged = []
    for column in range(width):
        parts: list[str] = []
        for row in block:
            cell = (row[column] if column < len(row) else "").strip()
            if cell and cell not in parts:
                parts.append(cell)
        merged.append(" ".join(parts))
    return merged


def map_by_header(header: list[str]) -> dict[str, int]:
    """全局分配：「账户余额」既像 account 又像 balance，各挑各的会让 account 拿到余额。"""
    scores = []
    for index, cell in enumerate(header):
        for name, words in KEYWORDS.items():
            best = max((_score(cell, word) - rank * 0.01 for rank, word in enumerate(words)
                        if _score(cell, word)), default=0.0)
            if best > 0:
                scores.append((best, name, index))
    columns: dict[str, int] = {}
    taken: set[int] = set()
    for _, name, index in sorted(scores, key=lambda s: -s[0]):
        if name not in columns and index not in taken:
            columns[name] = index
            taken.add(index)
    # 有收入、支出两列时，单列「金额」多半是误配。
    if "income" in columns or "expense" in columns:
        columns.pop("amount", None)
    return columns


def _extra_text(header: list[str], columns: dict[str, int]) -> list[int]:
    """没抢到字段、但确实是文字的列（Transaction + Description 分两列时，
    商户名常在没抢到的那列），解析时并进说明，否则商户名就丢了。"""
    used = set(columns.values())
    names = ("description", "counterparty", "remark")
    return [i for i, cell in enumerate(header) if i not in used and (cell or "").strip()
            and any(_score(cell, w) >= 0.6 for name in names for w in KEYWORDS[name])]


# ------------------------------------------------------------------ 按内容和算术认列
def _money_of(row: list[str], index: int | None) -> tuple[float, int, str] | None:
    if index is None or index >= len(row):
        return None
    return parse_money(row[index])


def _chain(values: list[tuple[float | None, float | None]], signed: bool) -> tuple[int, int]:
    """(本笔变动, 本笔余额) 序列里有多少对满足余额等式。返回 (成立, 可比)。"""
    good = total = 0
    previous, moved = None, 0.0
    for delta, balance in values:
        if delta is not None:
            moved += delta
        if balance is None:
            continue
        if previous is not None:
            total += 1
            change = balance - previous
            if abs(change - moved) < CENT or (not signed and abs(abs(change) - abs(moved)) < CENT):
                good += 1
        previous, moved = balance, 0.0
    return good, total


def _chain_score(rows, signed_of, balance: int, strict: bool = False) -> float:
    """成立比例 × 覆盖率，正序倒序取好的。余额列只填了几行时，比例再高也不算数。"""
    values = []
    for row in rows:
        found = _money_of(row, balance)
        values.append((signed_of(row), found[0] * found[1] * (-1 if found[2] == "DR" else 1) if found else None))
    if not any(v[0] for v in values):
        return 0.0
    best = 0.0
    for sequence in (values, [(-d if d is not None else None, b) for d, b in reversed(values)]):
        # 收支两列的方向是列本身决定的，只比大小会把标反的两列放过去。
        for signed in (True,) if strict else (True, False):
            good, total = _chain(sequence, signed)
            if total:
                best = max(best, good / total * min(1.0, total / (len(rows) * 0.5)) - (0.001 if not signed else 0))
    return best


def _signed_pair(income: int | None, expense: int | None):
    def signed(row):
        value = 0.0
        found = False
        for index, sign in ((income, 1), (expense, -1)):
            money = _money_of(row, index)
            if money and money[0]:
                value += sign * money[0]
                found = True
        return value if found else None
    return signed


def _signed_single(amount: int):
    def signed(row):
        money = _money_of(row, amount)
        if not money:
            return None
        return money[0] * (-1 if money[1] < 0 or money[2] == "DR" else 1)
    return signed


def solve_money(rows: list[list[str]], columns: dict[str, int]) -> dict[str, int]:
    """用余额等式核对金额列的分配，表头给错或没给时换成算得通的那种。"""
    # 没有余额列就没有等式可解，表头点名的金额列直接算数。
    if "balance" not in columns and _has_money(columns):
        return columns
    candidates = []
    width = max((len(r) for r in rows), default=0)
    for index in range(width):
        if index == columns.get("date"):
            continue
        filled = [r[index] for r in rows if index < len(r) and r[index].strip()]
        if filled and sum(is_money(c) for c in filled) >= len(filled) * 0.9:
            candidates.append(index)
    if len(candidates) < 2 or len(candidates) > 6:
        return columns

    def current() -> float:
        balance = columns.get("balance")
        if balance is None:
            return 0.0
        if "amount" in columns:
            return _chain_score(rows, _signed_single(columns["amount"]), balance)
        # 表头写明了收支时只核对大小，不靠算术翻转：信用卡的「余额」是欠款，
        # 消费时余额上升，看起来和收支两列标反一模一样。
        return _chain_score(rows, _signed_pair(columns.get("income"), columns.get("expense")), balance)

    best_score, best = current(), None
    if best_score >= 0.95:
        return columns
    for balance in candidates:
        others = [c for c in candidates if c != balance]
        # 收支两列先试：单列只比大小，同样算得通时不能抢在前面。
        for income, expense in permutations(others, 2):
            score = _chain_score(rows, _signed_pair(income, expense), balance, strict=True)
            if score > best_score + 0.05:
                best_score, best = score, {"income": income, "expense": expense, "balance": balance}
        for amount in others:
            score = _chain_score(rows, _signed_single(amount), balance)
            if score > best_score + 0.05:
                best_score, best = score, {"amount": amount, "balance": balance}
    if best is None or best_score < 0.8:
        return columns
    result = {k: v for k, v in columns.items() if k not in ("amount", "income", "expense", "balance")}
    used = set(best.values())
    return {**{k: v for k, v in result.items() if v not in used}, **best}


def align_money(columns: dict[str, int], body: list[list[str]]) -> dict[str, int]:
    """表头和数据错开时（版面错位、OCR 合并格），金额列的先后顺序信表头，位置信数据。"""
    names = sorted((n for n in ("amount", "income", "expense", "balance") if n in columns), key=columns.get)
    data = [r for r in body if _data_like(r)]
    width = max((len(r) for r in data), default=0)
    actual = [i for i in range(width) if i != columns.get("date")
              and sum(1 for r in data if i < len(r) and is_money(r[i].strip())) >= max(1, len(data) * 0.02)]
    if len(names) != len(actual) or [columns[n] for n in names] == actual:
        return columns
    moved = {k: v for k, v in columns.items() if k not in names and v not in actual}
    moved.update(zip(names, actual))
    return moved


def map_by_content(rows: list[list[str]]) -> dict[str, int]:
    """没有可用表头时，看格子内容。"""
    data = [r for r in rows if _data_like(r)]
    if not data:
        return {}
    width = max(len(r) for r in data)
    counts = [sum(1 for r in data if i < len(r) and is_date(r[i].strip())) for i in range(width)]
    date_column = max(range(width), key=lambda i: (counts[i], -i))
    columns = {"date": date_column}
    money = [i for i in range(width) if i != date_column
             and sum(1 for r in data if i < len(r) and is_money(r[i].strip())) >= max(1, len(data) * 0.2)]
    if not money:
        return {}
    filled = {i: sum(1 for r in data if i < len(r) and r[i].strip()) for i in money}
    sparse = [i for i in money if filled[i] < len(data) * 0.95]
    dense = [i for i in money if i not in sparse]

    def value(row, index):
        found = _money_of(row, index)
        return found[0] * found[1] if found else 0.0
    # 三列都满：总额 + 手续费 = 净额，等式成立的那列才是实际发生额。
    net = next((c for c in dense for a in dense for b in dense if len({a, b, c}) == 3 and
                all(abs(value(r, a) + value(r, b) - value(r, c)) < CENT for r in data)), None)
    if len(money) == 1:
        columns["amount"] = money[0]
    elif net is not None:
        columns["amount"] = net
    elif len(sparse) == 2 and not any(all(i < len(r) and r[i].strip() for i in sparse) for r in data):
        columns.update(expense=sparse[0], income=sparse[1])
        if dense:
            columns["balance"] = dense[-1]
    else:
        columns.update(amount=money[0], balance=money[-1])
    text = [i for i in range(width) if i != date_column and i not in money]
    if text:
        columns["description"] = max(text, key=lambda i: sum(len(r[i]) for r in data if i < len(r)))
    return columns


# ------------------------------------------------------------------ 账单抬头
_BANKS = (("招商银行", "招商银行"), ("招行", "招商银行"), ("工商银行", "工商银行"), ("建设银行", "建设银行"),
          ("中国银行", "中国银行"), ("农业银行", "农业银行"), ("交通银行", "交通银行"), ("中信银行", "中信银行"),
          ("浦发", "浦发银行"), ("民生银行", "民生银行"), ("光大银行", "光大银行"), ("兴业银行", "兴业银行"),
          ("平安银行", "平安银行"), ("广发银行", "广发银行"), ("邮储", "邮政储蓄银行"), ("华夏银行", "华夏银行"),
          ("支付宝", "支付宝"), ("微信", "微信支付"), ("云闪付", "云闪付"), ("京东", "京东金融"), ("美团", "美团"),
          ("抖音", "抖音支付"), ("PayPal", "PayPal"), ("拼多多", "拼多多"),
          ("Bank of China(Australia)", "中国银行(澳洲)"), ("Bank of China", "中国银行"),
          ("Commonwealth Bank", "澳洲联邦银行"), ("CommBank", "澳洲联邦银行"), ("Westpac", "西太平洋银行"),
          ("Standard Chartered", "渣打银行"), ("HSBC", "汇丰银行"), ("Citibank", "花旗银行"), ("DBS", "星展银行"),
          ("OCBC", "华侨银行"), ("Bank of America", "美国银行"), ("Wells Fargo", "富国银行"), ("Chase", "大通银行"),
          ("Macquarie", "麦格理银行"), ("ANZ Plus", "澳新银行"), ("ANZ", "澳新银行"), ("ubank", "ubank"),
          ("National Australia Bank", "澳洲国民银行"), ("NAB", "澳洲国民银行"), ("Bank of Queensland", "昆士兰银行"),
          ("BOQ", "昆士兰银行"), ("Bendigo", "本迪戈银行"), ("Suncorp", "Suncorp"), ("ING Bank", "ING"),
          ("Revolut", "Revolut"), ("Wise", "Wise"))
# 英文行名按词匹配：直接用 in 的话「eGift Card Purchase」里的 chase 会认成大通银行。
_BANK_PATTERNS = [(label, re.compile(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", re.I)
                   if re.search(r"[A-Za-z]", name) else re.compile(re.escape(name)))
                  for name, label in _BANKS]
_INSTITUTION = re.compile(r"([一-龥]{2,8}?(?:银行|信用社|信用合作社|支付|钱包))"
                          r"|([A-Z][A-Za-z&.' ]{2,28}?\s(?:Bank|Banking Corp\w*|Credit Union))")
# 表头很有辨识度的渠道，文件里没写名字时靠这个。
_HEADER_LABELS = ((("交易时间", "交易对方", "商品说明", "收/支"), "支付宝"), (("交易创建时间", "商品名称", "收/支"), "支付宝"),
                  (("交易类型", "交易对方", "收/支", "支付方式"), "微信支付"), (("商户名称", "交易说明", "收/支"), "京东金融"),
                  (("记账日期", "货币", "交易金额", "联机余额"), "招商银行储蓄卡"),
                  (("交易日", "记账日", "交易摘要", "人民币金额"), "招商银行信用卡"),
                  (("交易日期", "摘要", "收入金额", "支出金额"), "工商银行"),
                  (("交易日期", "交易金额", "账户余额"), "建设银行"))


def guess_label(*chunks) -> str:
    pieces: list[str] = []

    def collect(chunk):
        if isinstance(chunk, (list, tuple)):
            for item in chunk:
                collect(item)
        elif chunk and str(chunk).strip():
            pieces.append(str(chunk))
    collect(chunks)
    blob = " ".join(pieces)
    for label, pattern in _BANK_PATTERNS:
        if pattern.search(blob):
            return label
    # 逐格找机构名。整页拼起来找会把抬头上的姓名一起吃进去。
    for piece in pieces:
        found = _INSTITUTION.search(piece)
        if found:
            return (found.group(1) or found.group(2) or "").strip()
    return ""


# 账号数字之间可能有空格（06 2000 12345678 是 BSB + 账号），紧跟的日期不能吞进来。
_ACCOUNT = re.compile(r"(?:account\s*(?:no|number)|本地账号|账号|帐号|卡号)\s*[.:：/]*\s*"
                      r"([\dxX*•\-]{2,25}(?:[  ][\dxX*•\-]{2,20}(?![/.:\d]))*)", re.I)
_ACCOUNT_LABEL = re.compile(r"account\s*(?:no\.?\b|number\b)|账号|帐号|卡号", re.I)
_ACCOUNT_VALUE = re.compile(r"^\s*([\dxX*•-]{2,25}(?:[  ][\dxX*•-]{2,20})*)(?=\s|$)")
_CURRENCY = re.compile(r"(?:currency|币种)\s*[:：/]*\s*([A-Za-z]{3})\b", re.I)
# 通篇不写币种的账单，只能由发卡行推断。这些标识常印在末页，所以扫全文。
_CURRENCY_REGIONS = ((re.compile(r"\.com\.au\b|\bAustralia\b|Australian Credit Licence", re.I), "AUD"),
                     (re.compile(r"\.co\.nz\b|\bNew Zealand\b", re.I), "NZD"),
                     (re.compile(r"\.com\.sg\b|\bSingapore\b", re.I), "SGD"),
                     (re.compile(r"\.com\.hk\b|\bHong Kong\b", re.I), "HKD"),
                     (re.compile(r"人民币|中国人民银行"), "CNY"))
# 年份必须独立：页首的条码数字里常含 2012，直接搜会把整份账单排到十几年前。
_YEAR = re.compile(r"(?<!\d)(19|20)(\d{2})(?!\d)")
_PERIOD = re.compile(r"起始日期|截止日期|账单周期|账期|period|\bfrom\b", re.I)
_CREDIT_CARD = re.compile(r"信用卡|credit\s*card", re.I)
_OPENING = re.compile(r"(?:上期余额|期初余额|previous\s*period\s*balance|opening\s*balance)\s*[:：]?\s*([-+]?[$¥]?[\d,]+\.\d{2})", re.I)
_CLOSING = re.compile(r"(?:本期余额|期末余额|closing\s*balance)\s*[:：]?\s*([-+]?[$¥]?[\d,]+\.\d{2})", re.I)
_NO_ACTIVITY = re.compile(r"no\s+(?:recent\s+)?transactions|no\s+activity|无交易|没有交易", re.I)
_SIGN_LEGEND = re.compile(r"represents?\s+withdrawal|代表支取|代表支出|负数?\s*代表\s*(?:支出|支取)", re.I)
_TOTALS = {"debit": re.compile(r"total\s*debits?|借方发生额合计|支出合计|总支出", re.I),
           "credit": re.compile(r"total\s*credits?|贷方发生额合计|收入合计|总收入", re.I)}
_TOTAL_MONEY = re.compile(r"[-+]?[¥￥$]?\s*([\d,]+\.\d{2})")


def _joined(row: list[str]) -> str:
    return " ".join(c.strip() for c in row if (c or "").strip())


def _account_of(table: Table, limit: int) -> str:
    rows = table.rows
    for index, row in enumerate(rows[:limit + 1]):
        found = _ACCOUNT.search(_joined(row))
        number = re.sub(r"[\s -]", "", found.group(1)) if found else ""
        # 有的账单把标签和值上下分开摆，只读标签正下方那格，绝不看附近的余额。
        if not number and index + 1 < len(rows):
            labels = table.spans.get(index) or [(i, i + .5, c) for i, c in enumerate(row)]
            below = table.spans.get(index + 1) or [(i, i + .5, c) for i, c in enumerate(rows[index + 1])]
            for x0, x1, text in labels:
                if not _ACCOUNT_LABEL.search(str(text)):
                    continue
                value = next((t for a, b, t in below if a < x1 and b > x0), "")
                candidate = _ACCOUNT_VALUE.match(str(value))
                if candidate:
                    number = re.sub(r"[\s -]", "", candidate.group(1))
                    break
        if len(number) >= 6:
            return f"···{number[-4:]}"     # 导出的表格里不需要完整卡号
    return ""


def _currency_of(rows: list[list[str]], limit: int) -> str:
    for row in rows[:limit + 1]:
        found = _CURRENCY.search(_joined(row))
        if found:
            return found.group(1).upper()
    blob = " ".join(c for row in rows for c in row if c)
    return next((code for pattern, code in _CURRENCY_REGIONS if pattern.search(blob)), "")


def _year_of(rows: list[list[str]], limit: int) -> int:
    """只写月日时用的年份：账单周期的起始年，跨年时解析过程中再进位。"""
    head = rows[:limit + 1]
    for labelled in (True, False):
        for row in head:
            text = _joined(row)
            found = _YEAR.search(text) if not labelled or _PERIOD.search(text) else None
            if found:
                return int(found.group(0))
    return datetime.now().year


def _compact_order(rows: list[list[str]], limit: int) -> str:
    """YYMMDD 之类的格式说明。逐格比：拼起来「YYMMDD YYMMDD」里正好含 mmddyy。"""
    cells = [word.lower() for row in rows[:max(limit + 4, 6)] for c in row for word in c.split()]
    for order, hints in (("ddmmyy", ("ddmmyy", "日月年")), ("mmddyy", ("mmddyy", "月日年")),
                         ("yymmdd", ("yymmdd", "yyyymmdd", "年月日"))):
        if any(hint in cell for cell in cells for hint in hints):
            return order
    return "yymmdd"


def _day_first(cells: list[str]) -> bool:
    """03/04/2026 是 3 月还是 4 月：先看有没有大于 12 的数，否则看哪种读法日期更集中。"""
    first = second = 0
    parsed: dict[bool, list[str]] = {True: [], False: []}
    for cell in cells:
        found = _ABC.match(cell.strip())
        if not found:
            continue
        a, b = int(found.group(1)), int(found.group(2))
        first += a > 12
        second += b > 12
        for flag in parsed:
            day = parse_date(cell, DateStyle(day_first=flag))[1]
            if day:
                parsed[flag].append(day)
    if first or second:
        return first >= second

    def spread(days):
        return (date.fromisoformat(max(days)) - date.fromisoformat(min(days))).days if days else 0
    return spread(parsed[True]) <= spread(parsed[False])


def empty_period(rows: list[list[str]]) -> bool:
    """账单自己的合计栏写明收支都是 0。只认印出来的数字，解析不出来不算空。"""
    totals: dict[str, float] = {}
    for index, row in enumerate(rows):
        line = _joined(row)
        for key, label in _TOTALS.items():
            found = label.search(line)
            if not found:
                continue
            # 值可能排在标签下一行。
            money = (_TOTAL_MONEY.search(line[found.end():])
                     or _TOTAL_MONEY.fullmatch(_joined(rows[index + 1]) if index + 1 < len(rows) else ""))
            if money:
                totals[key] = max(totals.get(key, 0.0), float(money.group(1).replace(",", "")))
    return set(totals) == {"debit", "credit"} and not any(totals.values())


# ------------------------------------------------------------------ 逐行解析
_SUMMARY = ("承前页", "转下页", "本期余额", "上期余额", "期初余额", "期末余额", "笔数合计", "发生额合计", "合计",
            "小计", "总计", "累计", "balance carried", "carried forward", "brought forward",
            "balance for the period", "num of transactions", "total ", "opening balance",
            "closing balance", "subtotal", "grand total", "total debits", "total credits")
_PAGE = re.compile(r"page\s*\d+\s*(?:of|/)\s*\d+|第\s*\d+\s*页|共\s*\d+\s*页", re.I)
_CLOSED = re.compile(r"closing\s*balance|期末余额", re.I)
# 账单抬头的「标签:值」，多页账单每页重复。只认明确的标签：用「重复出现就丢」的规则
# 会把常去商户的续行也删掉，比留点噪声严重得多。
_PREAMBLE = re.compile(r"(账号|帐号|账户名|户名|客户号|开户行|卡号|币种|出账周期|起始日期|截止日期|账单序号"
                       r"|account\s*no|customer\s*no|statement\s*(reference|cycle)|branch|opening\s*balance)\s*[:：]", re.I)
_DISCLAIMER = re.compile(r"represents?\s+(?:withdrawal|deposit)|mark\s+represents|代表支取|代表存入|otherwisely"
                         r"|liability\s+for|disputing|australian?\s+credit\s+licence|\bABN\b|\bAFSL\b|请核对|如有疑问|客服电话", re.I)
_MONTH_NAMES = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
# 「31MayTransferfrom…」日期和摘要挤在一格。
_LEADING_DATE = re.compile(rf"^(\d{{1,2}}\s*(?:{_MONTH_NAMES})(?:\s*\d{{4}}(?!\d))?"
                           r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?!\d)|\d{8}(?!\d)|\d{6}(?!\d))\s*", re.I | re.A)
# 钱还在自己名下，只是换了个地方。「转账」不在这里：转给别人是真实收支，
# 只有对面是本人账户才不计（classify.own_transfer），或用户打开了设置里的开关。
_FUND_MOVES = ("信用卡还款", "还款", "余额宝", "零钱通", "提现", "充值", "基金", "理财", "borrow",
               "自动转入", "自动转出", "亲情卡", "内部", "备用金")
_REFUND_STATUS = ("退款", "已全额退款", "交易关闭", "已退款", "退货")
_SKIP_STATUS = ("交易关闭", "已取消", "支付失败", "交易失败", "已撤销", "冲正")
_INCOME_WORDS = ("收入", "收", "入账", "存入", "贷", "credit", "+")
_EXPENSE_WORDS = ("支出", "支", "付款", "扣款", "借", "debit", "-")
_NEUTRAL_WORDS = ("不计收支", "其他", "中性", "转账")
MAX_CONTINUATION = 5


def _is_summary(text: str) -> bool:
    # 有些 PDF 取出来整行不带空格（CLOSINGBALANCE），去掉空白再比一次。
    lowered = text.lower()
    squeezed = re.sub(r"\s+", "", lowered)
    return any(marker in lowered or marker.replace(" ", "") in squeezed for marker in _SUMMARY)


def _split_leading_date(text: str) -> tuple[str, str]:
    rest = (text or "").strip()
    found = _LEADING_DATE.match(rest)
    if not found:
        return "", ""
    head, rest = found.group(1), rest[found.end():].strip()
    again = _LEADING_DATE.match(rest)
    if again and again.group(1) == head:     # 交易日和起息日并成一格
        rest = rest[again.end():].strip()
    return head, rest


def _strip_value_date(text: str, day: str, style: DateStyle) -> str:
    for _ in range(2):
        head, rest = _split_leading_date(text)
        if not head or not rest or parse_date(head, style)[1] != day:
            break
        text = rest
    return text


def _join_once(*parts: str) -> str:
    """多行交易里同一段文字常被带两次，重复的片段只留一次。"""
    seen: set[str] = set()
    kept: list[str] = []
    for part in parts:
        for chunk in re.split(r"\s{2,}|(?<=\d)\s(?=[A-Z])", (part or "").strip()):
            key = re.sub(r"\s+", "", chunk).lower()
            if key and key not in seen:
                seen.add(key)
                kept.append(chunk.strip())
    return " ".join(kept)


def _tidy(text: str) -> str:
    """去掉占位分隔符（///Purpose 是前两段为空留下的斜杠）。"""
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"^[\s/\\|,;:：，、·.\-–—]+", "", text)
    return re.sub(r"[\s/\\|,;:：，、·\-–—]+$", "", text)


def _months_between(earlier: str, later: str) -> int:
    return (int(later[:4]) - int(earlier[:4])) * 12 + int(later[5:7]) - int(earlier[5:7])


def _direction_word(word: str) -> str:
    word = word.strip().lower()
    if not word:
        return ""
    # 「不计收支」要先判，否则会被「收」误伤。
    if any(w in word for w in _NEUTRAL_WORDS) and "不计" in word or word in _NEUTRAL_WORDS:
        return "transfer"
    if any(w in word for w in _INCOME_WORDS) or word in ("cr", "c"):
        return "income"
    if any(w in word for w in _EXPENSE_WORDS) or word in ("dr", "d"):
        return "expense"
    return "transfer" if any(w in word for w in _NEUTRAL_WORDS) else ""


@dataclass
class Statement:
    rows: list[dict] = field(default_factory=list)
    label: str = ""
    account: str = ""
    currency: str = ""
    check: str = "unchecked"        # verified / mismatch / unchecked / empty
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


def _walk(table: Table, columns: dict[str, int], extra: list[int], start: int, span: int,
          style: DateStyle, filename: str) -> tuple[list[dict], int]:
    """逐行读交易。一笔可能跨好几行，金额可能在最后一行；还要避开页眉页脚和合计。"""
    rows = table.rows
    records: list[dict] = []
    skipped = 0
    header_keys = {re.sub(r"\s+", "", _joined(rows[i])) for i in range(max(start, 0), start + span) if 0 <= i < len(rows)}
    split_money = "income" in columns or "expense" in columns
    state = {"last": ""}
    segment = 0

    def cell(row, name):
        index = columns.get(name)
        return (row[index] or "").strip() if index is not None and index < len(row) else ""

    def read_date(row):
        raw, leftover = cell(row, "date"), ""
        stamp, day = parse_date(raw, style) if len(raw) <= 40 else ("", "")
        if not day:
            head, rest = _split_leading_date(raw)
            if head:
                stamp, day = parse_date(head, style)
                if day:
                    raw, leftover = head, rest
        # 不带年份的日期跨年时月份会倒退（12 月 -> 1 月），年份要进位。
        if day and not re.search(r"\d{4}", raw) and not re.fullmatch(r"\d{6}", raw) and not _ABC.match(raw):
            previous = state["last"]
            if previous and day < previous and _months_between(day, previous) >= 6:
                style.year = (style.year or datetime.now().year) + 1
                stamp, day = parse_date(raw, style)
            state["last"] = max(state["last"], day)
        return stamp, day, leftover

    def read_money(row):
        """返回 (金额, 符号, 标记, 收支侧)。没有金额时金额为 0。"""
        if split_money:
            for name, side in (("income", "income"), ("expense", "expense")):
                found = parse_money(cell(row, name))
                if found and found[0]:
                    # 支出列写成 -12.00 是常态。收入列里的负数才是冲正。
                    return found[0], 1, "", "expense" if side == "income" and found[1] < 0 else side
            return 0.0, 1, "", ""
        found = parse_money(cell(row, "amount"))
        return (found[0], found[1], found[2], "") if found else (0.0, 1, "", "")

    def spare(row):
        return " ".join((row[i] or "").strip() for i in extra if i < len(row))

    def emit(date_row, stamp, day, money_row, leftover, line):
        amount, sign, mark, side = read_money(money_row)
        status = cell(date_row, "status") or cell(money_row, "status")
        if not amount or any(hint in status for hint in _SKIP_STATUS):
            return False
        same = money_row is date_row
        balance = parse_money(cell(money_row, "balance") or cell(date_row, "balance"))
        records.append({
            "source_file": filename, "time": stamp or day, "date": day, "amount": round(amount, 2),
            "counterparty": _tidy(cell(date_row, "counterparty") or cell(money_row, "counterparty")),
            "description": _tidy(_join_once(cell(date_row, "description"), spare(date_row),
                                            "" if same else cell(money_row, "description"),
                                            "" if same else spare(money_row), leftover)),
            "raw_category": cell(date_row, "category"),
            "account": cell(date_row, "account") or cell(money_row, "account"),
            "counter_account": _tidy(cell(date_row, "counter_account") or cell(money_row, "counter_account")),
            "status": status, "order_id": cell(date_row, "order_id") or cell(money_row, "order_id"),
            "remark": cell(date_row, "remark") or cell(money_row, "remark"),
            "direction_word": cell(date_row, "direction") or cell(money_row, "direction"),
            "row": line, "_sign": sign, "_mark": mark, "_side": side, "_segment": segment,
            "_balance": balance[0] * balance[1] * (-1 if balance[2] == "DR" else 1) if balance else None,
            "_lines": 0,
        })
        return True

    pending = None
    continuing = True       # 合计行之后的文字是页脚，不再算上一笔的续行
    closed = False          # 期末余额之后的带日期金额的表是手续费统计，不是交易
    # 表头可能不在第一页，它上面的交易同样要读。
    first = next((i for i, r in enumerate(rows[:max(start, 0)]) if _data_like(r)
                  and not _is_summary(_joined(r))), max(start, 0) + span if start >= 0 else 0)
    for line, row in enumerate(rows[first:], first + 1):
        if not any((c or "").strip() for c in row):
            continue
        joined = _joined(row)
        if _is_summary(joined) or _PAGE.search(joined):
            if _CLOSED.search(joined):
                closed = True
            continuing = False
            skipped += 1
            continue
        if _DISCLAIMER.search(joined):
            skipped += 1
            continue
        if re.sub(r"\s+", "", joined) in header_keys:
            if closed:
                segment += 1
            closed = False
            skipped += 1
            continue
        if closed or _PREAMBLE.search(joined):
            skipped += 1
            continue
        stamp, day, leftover = read_date(row)
        if not day:
            if pending is not None:
                if read_money(row)[0] > 0:
                    done = emit(pending["row"], pending["stamp"], pending["day"], row, pending["extra"], pending["line"])
                    pending = None
                    skipped += not done
                    continuing = continuing or done
                    continue
                text = _tidy(cell(row, "description")) or joined
                pending["extra"] = f'{pending["extra"]} {text}'.strip()
                continue
            # 同一天的多笔，后面几笔常不重复写日期。金额落在金额列里，就是一笔交易。
            if continuing and records and read_money(row)[0] > 0:
                last = records[-1]
                if emit(row, last["time"], last["date"], row, "", line):
                    continue
            if continuing and records and records[-1]["_lines"] < MAX_CONTINUATION:
                last = records[-1]
                last["_lines"] += 1
                last["description"] = f'{last["description"]} {joined}'.strip()
                continue
            skipped += 1
            continue
        if pending is not None:
            pending = None
            skipped += 1
        if read_money(row)[0] == 0:
            pending = {"row": row, "stamp": stamp, "day": day, "extra": leftover, "line": line}
            continue
        if emit(row, stamp, day, row, leftover, line):
            continuing = True
        else:
            skipped += 1
    for record in records:
        record["description"] = _tidy(_strip_value_date(record["description"], record["date"], style))
    return records, skipped


def _deltas(records: list[dict], opening: float | None) -> dict[int, float]:
    """余额的增减。先定账单是正序还是倒序：|变动| = 金额 的对数多的那个方向。"""
    dated = [r for r in records if r["_balance"] is not None]
    if not dated:
        return {}

    def fits(sequence):
        return sum(abs(abs(b["_balance"] - a["_balance"]) - b["amount"]) < CENT
                   for a, b in zip(sequence, sequence[1:]))
    forward, backward = fits(dated), fits(dated[::-1])
    if forward == backward:
        descending = dated[0]["date"] > dated[-1]["date"]
    else:
        descending = backward > forward
    ordered = dated[::-1] if descending else dated
    deltas = {id(b): b["_balance"] - a["_balance"] for a, b in zip(ordered, ordered[1:])}
    if opening is not None:
        deltas[id(ordered[0])] = ordered[0]["_balance"] - opening
    return deltas


def _assign_directions(records: list[dict], negative_seen: bool, card: bool, legend: bool, opening) -> int:
    """定收支方向，返回无法确定的笔数。优先级：收支两列 > 方向列 > CR/DR > 符号 > 余额增减。"""
    deltas = _deltas(records, opening)
    unknown = 0
    for record in records:
        written = record["direction_word"]
        word = "" if "转账" in written else _direction_word(written)
        delta = deltas.get(id(record))
        if record["_side"]:
            direction = record["_side"]
        elif word:
            direction = word
        elif record["_mark"]:
            direction = "income" if record["_mark"] == "CR" else "expense"
        elif card:
            direction = "income" if record["_sign"] < 0 else "expense"
        elif negative_seen or legend:
            direction = "expense" if record["_sign"] < 0 else "income"
        elif delta is not None and abs(delta) > CENT:
            direction = "income" if delta > 0 else "expense"
        else:
            direction, unknown = "expense", unknown + 1
            record["_unsure"] = True
        blob = " ".join((record["raw_category"], record["description"], record["counterparty"],
                         record["remark"], record["status"]))
        flow = direction
        if (any(hint in blob for hint in _FUND_MOVES) and not written) \
                or any(hint in record["raw_category"] for hint in ("不计收支", "信用卡还款")):
            direction = "transfer"
        # 只记下「写着转账」，算不算收支由设置决定，改开关不用重新导入。
        record["transfer_word"] = direction != "transfer" and (
            "转账" in written or "转账" in record["raw_category"] or ("转账" in blob and not written))
        if any(hint in record["status"] for hint in _REFUND_STATUS) and direction == "expense":
            direction = "income"
        record["direction"] = direction
        record["_flow"] = 1 if flow == "income" else -1
    return unknown


def verify(records: list[dict], opening: float | None, closing: float | None = None) -> tuple[int, int]:
    """逐段核对余额等式，返回 (成立, 可比)。对不上的笔记下 _broken。

    账单印的期初、期末余额也算进来：只看相邻两笔的话，开头或结尾整段漏读时查不出来。
    """
    good = total = 0
    segments: dict[int, list[dict]] = {}
    for record in records:
        segments.setdefault(record["_segment"], []).append(record)
    for rows in segments.values():
        outcome = None
        for sequence, polarity in ((rows, 1), (rows[::-1], 1), (rows, -1), (rows[::-1], -1)):
            broken: list[dict] = []
            matched = compared = 0
            single = len(segments) == 1
            previous, moved = (opening if single else None), 0.0
            for record in sequence:
                moved += polarity * record["_flow"] * record["amount"]
                balance = record["_balance"]
                if balance is None:
                    continue
                if previous is not None:
                    compared += 1
                    if abs(balance - previous - moved) < CENT:
                        matched += 1
                    else:
                        broken.append(record)
                previous, moved = balance, 0.0
            if single and closing is not None and previous is not None:
                compared += 1
                if abs(previous + moved - closing) < CENT:
                    matched += 1
                else:
                    broken.append(sequence[-1])
            if outcome is None or matched > outcome[0]:
                outcome = (matched, compared, broken)
        good += outcome[0]
        total += outcome[1]
        for record in outcome[2]:
            record["_broken"] = True
    return good, total


def _ask_model(table: Table, model, signature: str, store) -> dict[str, int]:
    """内容和算术都认不出来时才问模型，只问列的含义，同格式下次不再问。"""
    saved = store.learned_mapping(signature) if store else None
    if saved:
        return saved
    if model is None:
        return {}
    preview = [[c[:40] for c in row] for row in table.rows[:40] if any(row)][:20]
    columns = model.map_columns(preview, FIELDS)
    if columns and store:
        store.save_mapping(signature, columns)
    return columns


def _has_money(columns: dict[str, int]) -> bool:
    return "amount" in columns or "income" in columns or "expense" in columns


def parse_table(table: Table, filename: str, model=None, store=None) -> Statement:
    rows = table.rows
    if not any(any(c.strip() for c in row) for row in rows):
        raise Unrecognized("；".join(table.warnings) or "没有内容")
    start, span = find_header(rows)
    if start >= 0:
        header = composite_header(rows, start, span)
        columns = map_by_header(header)
    else:
        header, columns = [], {}
    limit = start + span if start >= 0 else 0

    # 内容比表头可靠：表头上方的标题粘进来时，列名会错开一格。
    guessed = map_by_content(rows[limit:])
    if guessed:
        if columns.get("date") != guessed["date"]:
            displaced = next((k for k, v in columns.items() if v == guessed["date"]), None)
            if displaced:
                columns[displaced] = columns.get("date", -1)
                if columns[displaced] < 0:
                    del columns[displaced]
            columns["date"] = guessed["date"]
        body = rows[limit:]
        columns = align_money(columns, body)
        # 表头点名的金额列在数据行里全是空的（比如只有一笔、排版错开），信内容。
        named = [v for k, v in columns.items() if k in ("amount", "income", "expense")]
        if not any(i < len(r) and is_money(r[i]) for r in body for i in named):
            for name in ("amount", "income", "expense", "balance"):
                columns.pop(name, None)
            for name in ("amount", "income", "expense", "balance"):
                if name in guessed:
                    columns = {k: v for k, v in columns.items() if v != guessed[name]}
                    columns[name] = guessed[name]
    if "date" not in columns or not _has_money(columns):
        signature = hashlib.sha1("|".join(_norm(c) for row in rows[:12] for c in row if c).encode()).hexdigest()[:16]
        columns = _ask_model(table, model, signature, store) or columns
    if "date" not in columns or not _has_money(columns):
        if empty_period(rows) or _NO_ACTIVITY.search(" ".join(_joined(r) for r in rows)):
            return Statement(label=guess_label(filename, rows[:30]), account=_account_of(table, len(rows)),
                             currency=_currency_of(rows, len(rows)), check="empty", detail="账单写明本期没有交易")
        raise Unrecognized("没有找到日期列和金额列")

    extra = [i for i in _extra_text(header, columns) if i not in columns.values()]
    style = DateStyle(year=_year_of(rows, max(limit, 12)), compact=_compact_order(rows, max(start, 0)))
    date_cells = [r[columns["date"]] for r in rows if columns["date"] < len(r)]
    style.day_first = _day_first(date_cells)
    probe = [r for r in rows[limit:] if parse_date(r[columns["date"]] if columns["date"] < len(r) else "", style)[1]]
    columns = solve_money(probe, columns)
    records, skipped = _walk(table, columns, extra, start, span, style, filename)

    head = rows[:limit] if start >= 0 else rows[:12]    # 抬头只到表头为止，交易行里的银行名不算
    label = next((name for words, name in _HEADER_LABELS if all(w in _norm(" ".join(header)) for w in map(_norm, words))), "")
    label = label or guess_label(header, filename, head) or guess_label(rows[-12:])
    statement = Statement(label=label, account=_account_of(table, len(head)),
                          currency=_currency_of(rows, len(head)), warnings=list(table.warnings))
    if not records:
        whole = " ".join(_joined(r) for r in rows)
        opening, closing = _printed(_OPENING, whole), _printed(_CLOSING, whole)
        # 只认账单印出来的证据：写明无交易、合计为 0、或期初期末余额相等且金额列没有非零数字。
        body = rows[limit:]
        body = body[:next((i for i, r in enumerate(body) if _CLOSED.search(_joined(r))), len(body))]
        amounts = [m for r in body if not _is_summary(_joined(r)) for k in ("amount", "income", "expense") if k in columns
                   for m in [_money_of(r, columns[k])] if m and m[0]]
        if empty_period(rows) or _NO_ACTIVITY.search(whole) or (opening is not None and opening == closing and not amounts):
            statement.check, statement.detail = "empty", "账单写明本期没有交易"
            return statement
        raise Unrecognized("认出了表头，但没有解析出交易。日期或金额列可能不对，不能据此判定本期无交易")

    whole = " ".join(_joined(r) for r in rows)
    # 封面可能列着好几个账户，表格里的期初行才是这张表的。
    table_text = " ".join(_joined(r) for r in rows[max(start, 0):])
    opening = _printed(_OPENING, table_text) or _printed(_OPENING, whole)
    negative = any(r["_sign"] < 0 for r in records)
    card = bool(_CREDIT_CARD.search(" ".join((filename, " ".join(_joined(r) for r in head))))) and "balance" not in columns
    unknown = _assign_directions(records, negative, card, bool(_SIGN_LEGEND.search(whole)), opening)
    good, total = verify(records, opening, _printed(_CLOSING, table_text) or _printed(_CLOSING, whole))

    statement.rows = records
    if total and good == total:
        statement.check, statement.detail = "verified", f"余额逐笔核对通过（{good}/{total}）"
    elif total:
        statement.check, statement.detail = "mismatch", f"余额有 {total - good} 处对不上（共 {total} 处）"
    if unknown:
        statement.warnings.append(f"{unknown} 笔看不出收支方向，暂记为支出，请确认")
    if table.ocr:
        statement.warnings.append("数字来自 OCR，请对照原件复核")
    return statement


def _printed(pattern: re.Pattern, text: str) -> float | None:
    found = pattern.search(text)
    money = parse_money(found.group(1)) if found else None
    return money[0] * money[1] if money else None


def fingerprint(record: dict, label: str) -> str:
    key = "|".join((record["date"], f'{record["amount"]:.2f}', record.get("direction", ""),
                    record.get("order_id") or record.get("counterparty") or "", label))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

