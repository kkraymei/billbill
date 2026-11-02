"""界面语言。中文原文就是键，切到英文时查表；后端的提示信息带数字和路径，用正则换。"""

from __future__ import annotations

import re

LANGUAGES = {"zh": "中文", "en": "English"}
_language = "zh"

EN = {
    # 主窗口
    "设置": "Settings", "清空账本": "Clear ledger", "导出 Excel": "Export Excel", "记录": "Records", "收入": "Income",
    "支出": "Expense", "不计收支": "Not counted", "转账": "Transfer", "待确认": "To review", "全部": "All",
    "余额对不上": "Balance mismatch", "已排除": "Excluded", "搜索商户、说明、账户…": "Search merchants, details, accounts…",
    "拖入账单文件，或点击选择": "Drop statements here, or click to choose", "PDF、Word、Excel、CSV、网页、图片": "PDF, Word, Excel, CSV, web pages, images",
    "选择账单文件": "Choose statements", "账单文件": "Statements", "所有文件": "All files", "模板：{name}": "Template: {name}",
    "日期": "Date", "类型": "Type", "金额": "Amount", "分类": "Category", "账户": "Account", "交易对方": "Counterparty",
    "说明": "Details", "标签": "Tags", "已核对": "Verified", "有差异": "Mismatch", "无法核对": "Can't verify",
    "本期无交易": "No transactions", "已导入过": "Already imported", "没读出来": "Couldn't read", "需要密码": "Password needed",
    "{count} 笔": "{count} items", "正在去重和分类…": "Removing duplicates and categorizing…",
    "{name} 有密码，请输入：": "{name} is password protected. Enter the password:",
    "{name}：{message}，请在「余额对不上」里核对": "{name}: {message}. Check it under “Balance mismatch”.",
    "恢复这些记录": "Restore these records", "排除，不写入 Excel": "Exclude from Excel", "手动排除": "Excluded by hand",
    "从账本移除这份账单": "Remove this statement from the ledger",
    "移除所有已导入的账单？关键词、账户和模板都会保留。": "Remove all imported statements? Keywords, accounts and templates are kept.",
    "还有待确认": "Items to review", "还有 {count} 笔没有分类，会以「待确认」写入。继续导出吗？":
        "{count} items have no category and will be written as “To review”. Export anyway?",
    "Excel 工作簿": "Excel workbook", "导出失败": "Export failed", "已导出：{name}": "Exported: {name}", "继续": "Continue", "取消": "Cancel",
    # 标签
    "大额": "Large", "跨境": "Overseas", "手续费": "Fee", "订阅": "Subscription", "自动扣款": "Direct debit", "退款": "Refund",
    "疑似重复": "Possible duplicate", "大额现金": "Cash",
    # 设置：通用
    "通用": "General", "语言": "Language", "外观": "Appearance", "跟随系统": "Follow system", "白天": "Light", "黑夜": "Dark",
    "转账规则": "Transfers", "摘要写着「转账」的记录也不计收支": "Also leave out records marked “transfer”",
    "默认只有本人账户之间的互转不计收支，转给别人的钱算真实支出。本人账户在「本人账户」里登记，可以写账号，也可以写你的姓名。":
        "By default only transfers between your own accounts are left out of income and expense; money sent to anyone else counts. "
        "Add your accounts under “My accounts”, by number or by your name.",
    "默认货币": "Default currency", "「大额」标签阈值": "“Large” tag threshold",
    # 设置：模板
    "记账模板": "Template", "选择你的记账 Excel，程序按表头猜每列放什么，猜错的在下拉框里改。导出时保留模板的格式和公式，示例行会被清掉。":
        "Choose your bookkeeping Excel file. BillBill guesses each column from its header; correct any wrong guess in the list. "
        "Formats and formulas are kept and sample rows are cleared on export.",
    "选择 Excel 模板…": "Choose Excel template…", "数据从第": "Write data from row", "行开始写": "", "列": "Column", "表头": "Header",
    "示例": "Sample", "放什么": "Contents", "保存模板": "Save template", "（留空）": "(empty)", "打不开这个文件": "Can't open this file",
    "选择记账模板": "Choose template", "还没有模板": "No template yet", "请先选择一个 Excel 模板。": "Choose an Excel template first.",
    "没有可写的列": "No columns to write", "至少给一列选上要放的内容。": "Choose the contents of at least one column.",
    "内置模板": "Built-in template",
    # 设置：分类
    "分类属于模板：导出时分别写进「一级分类」「二级分类」两列。没有二级的留空。":
        "Categories belong to the template and are written to its two category columns. Leave the second level empty if there is none.",
    "方向": "Direction", "一级分类": "Category", "二级分类": "Subcategory", "添加一行": "Add row", "删除选中行": "Delete selected row",
    "保存分类": "Save categories", "分类没有保存": "Categories not saved",
    "请先选择模板文件，再编辑分类": "Choose a template file before editing categories",
    "每行都要有方向和一级分类": "Every row needs a direction and a category",
    "分类名称最多 40 字，不能含 /、| 或换行": "Category names are limited to 40 characters and can't contain /, | or line breaks",
    "分类目录要包含支出、收入和转账三种方向": "The list needs expense, income and transfer categories",
    # 设置：关键词、账户
    "关键词": "Keywords", "本人账户": "My accounts", "保存": "Save", "已保存": "Saved", "已保存 {count} 条": "Saved {count} items",
    "账单里出现左边的词，就归到右边的分类。一行一条，写在前面的优先：\nEXAMPLE TELCO => 通讯 / 通讯网络":
        "A record containing the word on the left goes to the category on the right. One per line; earlier lines win:\n"
        "EXAMPLE TELCO => 通讯 / 通讯网络",
    "这些账户之间的转账不算收入和支出。账单抬头的账号会自动记下（目前 {count} 个）；只在流水里露过面的账户和你的姓名写在这里：\n···4321 => 我的储蓄卡":
        "Transfers between these accounts don't count as income or expense. Account numbers on statement headers are remembered "
        "automatically ({count} so far). Add accounts seen only in transactions, and your name, here:\n···4321 => My savings",
    # 设置：模型
    "本地模型": "Local model", "启用本地模型": "Use a local model", "文字模型": "Text model", "视觉模型": "Vision model",
    "视觉 mmproj": "Vision mmproj", "视觉模型配套的 mmproj": "the vision model's mmproj", "选择…": "Choose…",
    "选择 GGUF 模型": "Choose GGUF model", "GGUF 模型": "GGUF model", "加载测试": "Test load", "正在加载…": "Loading…",
    "文字模型加载成功": "Text model loaded",
    "模型是可选的：不选也能用，表格类账单不需要它。文字模型用来归类没见过的商户、认少见的表格；视觉模型用来读扫描件和截图。模型文件留在你自己的电脑上。":
        "A model is optional; table-based statements don't need one. The text model categorizes new merchants and reads unusual "
        "tables. The vision model reads scans and screenshots. Model files stay on your computer.",
    # 模板字段
    "日期时间": "Date and time", "日期(2025年08月01日)": "Date (2025年08月01日)", "日期时间(2025年08月01日 12:00:00)": "Date and time (2025年08月01日 12:00:00)",
    "交易时间(文本)": "Transaction time (text)", "年": "Year", "月": "Month", "日": "Day", "年月": "Year-month",
    "收支类型(支出/收入/转账)": "Type (expense/income/transfer)", "收支类型(支出/收入/不计收支)": "Type (expense/income/not counted)",
    "收入金额": "Income amount", "支出金额": "Expense amount", "带符号金额(支出为负)": "Signed amount (expense negative)",
    "完整分类(一级 / 二级)": "Full category", "对手账户": "Other account", "货币": "Currency", "来源渠道": "Source",
    "原始分类": "Original category", "交易状态": "Status", "订单号": "Order number", "备注原文": "Original note", "余额": "Balance",
    "来源文件": "Source file",
    # 后端提示
    "文件是空的": "The file is empty", "这个压缩格式不是 Word 或 Excel 文档": "This archive isn't a Word or Excel document",
    "MHTML 里没有网页内容": "This MHTML file has no web page", "这份 PDF 有密码": "This PDF is password protected",
    "读不懂这份 .doc 的内部结构，请用 Word 另存为 .docx": "Can't read this .doc file. Save it as .docx in Word",
    "PDF 里没有可读取的文字或图像内容": "This PDF has no readable text or images",
    "OCR 返回了不完整的表格，已停止以免漏账": "OCR returned an incomplete table; stopped so nothing is missed",
    "图片账单需要先在设置里选择 OCR 视觉模型和配套 mmproj": "Image statements need a vision model and its mmproj in Settings",
    "图片过大，请裁切后重试，否则账单小字识别不清": "The image is too large. Crop it and try again",
    "视觉模型没有从图片里识别出内容": "The vision model found nothing in this image", "没有内容": "No content",
    "没有找到日期列和金额列": "No date and amount columns found", "账单写明本期没有交易": "The statement shows no transactions",
    "认出了表头，但没有解析出交易。日期或金额列可能不对，不能据此判定本期无交易":
        "Found the header but no transactions. The date or amount column may be wrong, so this isn't treated as an empty statement",
    "数字来自 OCR，请对照原件复核": "Numbers come from OCR; check them against the original",
    "这个文件已经导入过": "This file was already imported", "没有解析出交易": "No transactions found",
    "没有找到内置的 llama-server，请重新安装，或在设置里指定路径": "Built-in llama-server not found. Reinstall, or set its path in Settings",
    "模型输出被截断，请在设置里调大生成 token 上限": "The model's output was cut off. Raise the token limit in Settings",
    "模型没有返回合法 JSON": "The model didn't return valid JSON",
    "OCR 输出被截断，已停止导入以免漏账。请调大生成 token 上限或裁切图片":
        "OCR output was cut off; stopped so nothing is missed. Raise the token limit or crop the image",
    "OCR 返回空内容，请检查视觉模型和 mmproj 是否配套": "OCR returned nothing. Check that the vision model and mmproj match",
}

_MODEL_LABELS = ("视觉模型配套的 mmproj", "视觉模型", "文字模型")
_LABEL = "|".join(_MODEL_LABELS)
_PATTERNS = (
    (r"工作表「(.+?)」图片 (\d+)", r"sheet “\1” image \2"),
    (r"^第 (\d+)/(\d+) 页是图片或文字层损坏，没有配置视觉模型，未读取$",
     r"Page \1/\2 is an image or has a damaged text layer and no vision model is set up, so it was skipped"),
    (r"^(.+) 是图片，没有配置视觉模型，未读取$", r"\1 is an image and no vision model is set up, so it was skipped"),
    (r"^读取第 (\d+)/(\d+) 页$", r"Reading page \1/\2"),
    (r"^第 (\d+)/(\d+) 页是图片，正在 OCR…$", r"Page \1/\2 is an image, running OCR…"),
    (r"^(.+)：正在 OCR…$", r"\1: running OCR…"),
    (r"^余额逐笔核对通过（(\d+)/(\d+)）$", r"Every balance checks out (\1/\2)"),
    (r"^余额有 (\d+) 处对不上（共 (\d+) 处）$", r"\1 of \2 balances don't match"),
    (r"^(\d+) 笔看不出收支方向，暂记为支出，请确认$", r"\1 items have no clear direction and were recorded as expense; please check"),
    (r"^读取失败：(.*)$", r"Couldn't read the file: \1"),
    (r"^解析出错：(.*)$", r"Couldn't parse the file: \1"),
    (r"^处理中断：(.*)$", r"Processing stopped: \1"),
    (r"^llama-server 不存在：(.*)$", r"llama-server not found: \1"),
    (rf"^请在设置里选择({_LABEL})（GGUF 文件）$", lambda m: f"Choose {tr(m[1])} in Settings (GGUF file)"),
    (rf"^({_LABEL})不是有效的 GGUF 文件：(.*)$", lambda m: f"{tr(m[1])} is not a valid GGUF file: {m[2]}"),
    (r"^模型加载失败（退出码 (.+?)），日志：(.*)$", r"The model failed to load (exit code \1). Log: \2"),
    (r"^模型加载超时，日志：(.*)$", r"The model took too long to load. Log: \1"),
    (r"^模型服务返回 (\d+)：(.*)$", r"The model server returned \1: \2"),
    (r"^模型没有响应：(.*)$", r"The model didn't respond: \1"),
    (r"^用本地模型分类（复核拿不准的）：(.*)$", r"Categorizing with the local model (second look): \1"),
    (r"^用本地模型分类：(.*)$", r"Categorizing with the local model: \1"),
    (r"^第 (\d+) 行看不懂：(.*)$", r"Line \1 can't be read: \2"),
    (r"^第 (\d+) 行关键词重复：(.*)$", r"Line \1 repeats a keyword: \2"),
    (r"^第 (\d+) 行重复：(.*)$", r"Line \1 is a duplicate: \2"),
    (r"^第 (\d+) 行太短，至少 (\d+) 位才不会误伤：(.*)$", r"Line \1 is too short; use at least \2 characters: \3"),
    (r"^分类名称不能重复：(.*)$", r"Category names must be unique: \1"),
    (r"^模板文件不存在：(.*)$", r"Template file not found: \1"),
)
_COMPILED = tuple((re.compile(pattern, re.S), target) for pattern, target in _PATTERNS)


def set_language(language: str) -> None:
    global _language
    _language = language if language in LANGUAGES else "zh"


def language() -> str:
    return _language


def _english(text: str) -> str:
    if text in EN:
        return EN[text]
    for pattern, target in _COMPILED:
        text, count = pattern.subn(target, text)
        if count and pattern.pattern.startswith("^"):
            break
    return text


def tr(text: str, **values) -> str:
    """text 是中文原文。带 {占位符} 的传 values；后端的整句也可以直接传进来。"""
    if _language == "en" and text:
        if text not in EN and ("；" in text or "\n" in text):
            text = "\n".join("; ".join(_english(part) for part in line.split("；")) for line in text.split("\n"))
        else:
            text = _english(text)
    return text.format(**values) if values else text


def tags(text: str) -> str:
    """#待确认#大额 -> #To review#Large"""
    return "".join(f"#{tr(tag)}" for tag in (text or "").split("#") if tag)
