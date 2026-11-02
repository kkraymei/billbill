"""记账模板：哪张工作表、从第几行写、每列放什么字段，以及模板自己的分类目录。

配置存在 templates/templates.json，模板文件是用户自己的 .xlsx，导出时保留它的格式和公式。
没有设置模板时用内置的一份。
"""

from __future__ import annotations

import json
import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import config
from .i18n import tr

DIRECTION_LABELS = {"expense": "支出", "income": "收入", "transfer": "不计收支"}
# 多数记账软件的导入模板用「转账」而不是「不计收支」。
DIRECTION_WORDS = {"expense": "支出", "income": "收入", "transfer": "转账"}

FIELDS = {
    "trade_date": "日期", "trade_datetime": "日期时间", "date_cn": "日期(2025年08月01日)",
    "date_cn_time": "日期时间(2025年08月01日 12:00:00)", "trade_time": "交易时间(文本)",
    "year": "年", "month": "月", "day": "日", "year_month": "年月",
    "direction_word": "收支类型(支出/收入/转账)", "direction_label": "收支类型(支出/收入/不计收支)",
    "amount": "金额", "income_amount": "收入金额", "expense_amount": "支出金额", "signed_amount": "带符号金额(支出为负)",
    "category_group": "一级分类", "category": "二级分类", "category_full": "完整分类(一级 / 二级)",
    "account": "账户", "counter_account": "对手账户", "counterparty": "交易对方", "description": "说明",
    "currency": "货币", "tags": "标签", "label": "来源渠道", "raw_category": "原始分类", "status": "交易状态",
    "order_id": "订单号", "remark": "备注原文", "balance": "余额", "source_file": "来源文件",
}

# 模板表头名 -> 字段。
_ALIASES = {
    "日期": "trade_date", "交易日期": "trade_date", "date": "trade_date", "时间": "date_cn_time",
    "日期时间": "date_cn_time", "交易时间": "date_cn_time", "类型": "direction_word", "收支": "direction_word",
    "收支类型": "direction_word", "收/支": "direction_word", "方向": "direction_word", "金额": "amount",
    "amount": "amount", "发生额": "amount", "收入": "income_amount", "支出": "expense_amount",
    "一级分类": "category_group", "大类": "category_group", "分类": "category_group", "二级分类": "category",
    "子分类": "category", "科目": "category", "记账科目": "category", "category": "category", "账户": "account",
    "账户1": "account", "支付账户": "account", "账号": "account", "account": "account", "账户2": "counter_account",
    "对手账户": "counter_account", "转入账户": "counter_account", "对方账户": "counter_account", "标签": "tags",
    "tag": "tags", "tags": "tags",
    # 记账模板的「备注」要的是这笔钱花在什么上；账单原文的附言多数渠道是空的。
    "备注": "description", "说明": "description", "摘要": "description", "note": "description",
    "remark": "description", "附言": "remark", "货币": "currency", "币种": "currency", "currency": "currency",
    "商户": "counterparty", "交易对方": "counterparty", "对方": "counterparty", "来源": "label", "渠道": "label",
    "订单号": "order_id", "流水号": "order_id", "余额": "balance",
}

GROUPS = {"餐饮美食": "餐饮", "交通出行": "交通", "汽车养护": "交通", "日用百货": "购物", "服饰装扮": "购物",
           "数码电器": "购物", "居家家装": "居家", "住房物业": "居家", "水电燃气": "居家", "文化娱乐": "娱乐",
           "运动户外": "娱乐", "旅游度假": "娱乐", "医疗健康": "医疗", "教育培训": "学习", "通讯网络": "通讯",
           "订阅服务": "通讯", "人情往来": "人情", "金融保险": "金融", "宠物": "其他", "其他支出": "其他"}
_INCOME = ("工资薪酬", "奖金补贴", "兼职外快", "投资收益", "退款返现", "红包礼金", "报销入账", "其他收入")

BUILTIN = {
    "name": "内置模板",
    "columns": {"A": "trade_date", "B": "direction_word", "C": "amount", "D": "currency", "E": "category_group",
                "F": "category", "G": "account", "H": "counter_account", "I": "counterparty", "J": "description",
                "K": "tags", "L": "label", "M": "source_file"},
    "start_row": 2,
    "categories": ([{"direction": "expense", "group": g, "category": c} for c, g in GROUPS.items()]
                   + [{"direction": "income", "group": c, "category": ""} for c in _INCOME]
                   + [{"direction": "transfer", "group": "转账", "category": ""}]),
}


def label(entry: dict) -> str:
    return entry["group"] + (" / " + entry["category"] if entry.get("category") else "")


def _config_path() -> Path:
    return config.TEMPLATE_DIR / "templates.json"


def load_all() -> dict[str, dict]:
    try:
        saved = json.loads(_config_path().read_text("utf-8"))
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def active(settings: dict) -> dict:
    """当前模板：设置里选的那个；没选时用已经配好的第一个，否则用内置的。"""
    templates = load_all()
    chosen = templates.get(settings.get("template", "")) or next(iter(templates.values()), None)
    spec = {**BUILTIN, **(chosen or {})}
    spec["categories"] = spec.get("categories") or BUILTIN["categories"]
    return spec


def validate_categories(entries: list[dict]) -> list[dict]:
    result, seen = [], set()
    for entry in entries:
        group, category = str(entry.get("group") or "").strip(), str(entry.get("category") or "").strip()
        direction = entry.get("direction")
        if direction not in DIRECTION_LABELS or not group:
            raise ValueError("每行都要有方向和一级分类")
        if any(len(v) > 40 or "/" in v or "|" in v or "\n" in v for v in (group, category)):
            raise ValueError("分类名称最多 40 字，不能含 /、| 或换行")
        item = {"direction": direction, "group": group, "category": category}
        if label(item) in seen:
            raise ValueError("分类名称不能重复：" + label(item))
        seen.add(label(item))
        result.append(item)
    if {e["direction"] for e in result} != set(DIRECTION_LABELS):
        raise ValueError("分类目录要包含支出、收入和转账三种方向")
    return result


def inspect(path: Path) -> dict:
    """打开一个 Excel，找到表头行，猜每列的字段。工作簿内容只当数据看。"""
    book = load_workbook(path, data_only=False)
    sheet = book.worksheets[0]
    best_row, best_hits = 1, -1
    for row in range(1, min(sheet.max_row, 20) + 1):
        cells = [str(sheet.cell(row, c).value or "").strip().lower() for c in range(1, sheet.max_column + 1)]
        hits = sum(cell in _ALIASES for cell in cells)
        if hits > best_hits:
            best_row, best_hits = row, hits
    columns = []
    for column in range(1, min(sheet.max_column, 60) + 1):
        title = str(sheet.cell(best_row, column).value or "").strip()
        sample = sheet.cell(best_row + 1, column).value
        field = _ALIASES.get(title.lower(), "")
        if field == "trade_date" and isinstance(sample, str) and "年" in sample and ":" in sample:
            field = "date_cn_time"
        columns.append({"letter": get_column_letter(column), "title": title, "field": field,
                        "sample": "" if sample is None else str(sample)[:30]})
    book.close()
    return {"sheet": sheet.title, "start_row": best_row + 1, "columns": columns}


def save(source: Path, name: str, sheet: str, start_row: int, columns: dict[str, str], categories: list[dict]) -> str:
    """把模板文件复制进 templates/ 并记下配置，返回它的键。"""
    config.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    target = config.TEMPLATE_DIR / source.name
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    templates = load_all()
    key = next((k for k, v in templates.items() if v.get("file") == source.name), f"template-{len(templates) + 1}")
    templates[key] = {"name": name or source.stem, "file": source.name, "sheet": sheet, "start_row": start_row,
                      "style_row": start_row, "columns": columns, "clear_tail": True,
                      "categories": validate_categories(categories)}
    _config_path().write_text(json.dumps(templates, ensure_ascii=False, indent=1), "utf-8")
    return key


def save_categories(settings: dict, categories: list[dict]) -> None:
    """改当前模板的分类目录。内置模板没有配置文件，这时不落盘。"""
    templates = load_all()
    key = settings.get("template") if settings.get("template") in templates else next(iter(templates), None)
    if key is None:
        raise ValueError("请先选择模板文件，再编辑分类")
    templates[key]["categories"] = validate_categories(categories)
    _config_path().write_text(json.dumps(templates, ensure_ascii=False, indent=1), "utf-8")


# ------------------------------------------------------------------ 字段取值
def _date(text: str):
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return text or ""


def _datetime(row: dict):
    raw = row.get("time") or row.get("date") or ""
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19] if " " in raw else raw[:10], pattern)
        except (ValueError, TypeError):
            continue
    return raw


def value(row: dict, field: str, settings: dict):
    day = row.get("date") or ""
    direction = row.get("direction") or "expense"
    amount = float(row.get("amount") or 0)
    simple = {
        "trade_date": lambda: _date(day), "trade_datetime": lambda: _datetime(row),
        "date_cn": lambda: f"{day[:4]}年{day[5:7]}月{day[8:10]}日",
        "date_cn_time": lambda: f"{day[:4]}年{day[5:7]}月{day[8:10]}日 {(row.get('time') or '')[11:19] or '00:00:00'}",
        "trade_time": lambda: row.get("time") or day,
        "year": lambda: int(day[:4]), "month": lambda: int(day[5:7]), "day": lambda: int(day[8:10]),
        "year_month": lambda: day[:7], "direction_word": lambda: DIRECTION_WORDS.get(direction, "支出"),
        "direction_label": lambda: DIRECTION_LABELS.get(direction, "支出"), "amount": lambda: amount,
        "income_amount": lambda: amount if direction == "income" else "",
        "expense_amount": lambda: amount if direction == "expense" else "",
        "signed_amount": lambda: -amount if direction == "expense" else amount,
        "category_group": lambda: row.get("group", ""), "category": lambda: row.get("detail", ""),
        "category_full": lambda: row.get("category", ""),
        "currency": lambda: row.get("currency") or settings.get("default_currency") or "CNY",
    }
    return simple[field]() if field in simple else row.get(field, "") or ""


_DATE_FORMATS = {"trade_date": "yyyy-mm-dd", "trade_datetime": "yyyy-mm-dd hh:mm:ss"}


def export(rows: list[dict], out_path: Path, settings: dict) -> Path:
    """写出记账 Excel。被排除的记录不进文件。"""
    rows = [row for row in rows if not row.get("excluded")]
    spec = active(settings)
    columns = {letter.upper(): field for letter, field in spec["columns"].items()}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.get("file"):
        source = config.TEMPLATE_DIR / spec["file"]
        if not source.exists():
            raise FileNotFoundError(f"模板文件不存在：{source}")
        shutil.copyfile(source, out_path)
        book = load_workbook(out_path)
        sheet = book[spec["sheet"]] if spec.get("sheet") in book.sheetnames else book.worksheets[0]
    else:
        book = Workbook()
        sheet = book.active
        sheet.title = "账单"
        for letter, field in columns.items():
            sheet[f"{letter}1"] = tr(FIELDS.get(field, field))
            sheet[f"{letter}1"].font = Font(bold=True)
            sheet.column_dimensions[letter].width = 34 if field in ("description", "source_file") else 16
        sheet.freeze_panes = "A2"

    start = int(spec.get("start_row", 2))
    style_row = int(spec.get("style_row", start))
    styles = {}
    for letter in columns:
        cell = sheet[f"{letter}{style_row}"]
        styles[letter] = (copy(cell.font), copy(cell.fill), copy(cell.border), copy(cell.alignment), cell.number_format)
    # 模板自带的示例行要整行清掉，「#标签1」这类样例值不能混进真实数据。
    for row_index in range(start, sheet.max_row + 1):
        for column in range(1, sheet.max_column + 1):
            sheet.cell(row=row_index, column=column).value = None
    for offset, row in enumerate(rows):
        for letter, field in columns.items():
            cell = sheet[f"{letter}{start + offset}"]
            cell.value = value(row, field, settings)
            # 内置模板跟随界面语言；用户自己的模板写什么由模板决定。
            if not spec.get("file") and field in ("direction_word", "direction_label"):
                cell.value = tr(cell.value)
            cell.font, cell.fill, cell.border, cell.alignment, cell.number_format = (copy(s) if i < 4 else s
                                                                                     for i, s in enumerate(styles[letter]))
            # 列没设日期格式时，日期会显示成 46260 这样的序列号。
            if field in _DATE_FORMATS and cell.number_format in ("General", "@"):
                cell.number_format = _DATE_FORMATS[field]
    book.save(out_path)
    return out_path
