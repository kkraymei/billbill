"""设置：通用、模板、分类、关键词、本人账户、本地模型。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                             QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import classify, config, i18n, ledger, model, template
from .i18n import tr


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("soft")
    label.setWordWrap(True)
    return label


class GeneralTab(QWidget):
    """语言、外观、转账规则。选了就生效，不用再点保存。"""

    def __init__(self, dialog: "SettingsDialog"):
        super().__init__()
        self.dialog = dialog
        settings = dialog.settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language = QComboBox()
        for key, name in i18n.LANGUAGES.items():
            self.language.addItem(name, key)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.get("language"))))
        self.language.currentIndexChanged.connect(lambda: self._reload("language", self.language.currentData()))
        form.addRow(tr("语言"), self.language)
        self.theme = QComboBox()
        for key, name in (("system", "跟随系统"), ("light", "白天"), ("dark", "黑夜")):
            self.theme.addItem(tr(name), key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get("theme"))))
        self.theme.currentIndexChanged.connect(lambda: self._reload("theme", self.theme.currentData()))
        form.addRow(tr("外观"), self.theme)
        self.currency = QLineEdit(settings.get("default_currency", "CNY"))
        self.currency.editingFinished.connect(self._save_numbers)
        form.addRow(tr("默认货币"), self.currency)
        self.large = QDoubleSpinBox()
        self.large.setRange(0, 100_000_000)
        self.large.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.large.setValue(float(settings.get("large_amount") or 0))
        self.large.editingFinished.connect(self._save_numbers)
        form.addRow(tr("「大额」标签阈值"), self.large)
        layout.addLayout(form)
        layout.addSpacing(12)
        layout.addWidget(QLabel(tr("转账规则")))
        self.transfer_words = QCheckBox(tr("摘要写着「转账」的记录也不计收支"))
        self.transfer_words.setChecked(bool(settings.get("transfer_words_neutral")))
        self.transfer_words.toggled.connect(self._toggle_transfer_words)
        layout.addWidget(self.transfer_words)
        layout.addWidget(hint(tr("默认只有本人账户之间的互转不计收支，转给别人的钱算真实支出。"
                                 "本人账户在「本人账户」里登记，可以写账号，也可以写你的姓名。")))
        layout.addStretch()

    def _reload(self, key: str, value: str):
        self.dialog.settings = config.save_settings({key: value})
        self.dialog.done(SettingsDialog.RELOAD)

    def _toggle_transfer_words(self, checked: bool):
        self.dialog.settings = config.save_settings({"transfer_words_neutral": checked})
        self.dialog.changed = True

    def _save_numbers(self):
        self.dialog.settings = config.save_settings({
            "default_currency": self.currency.text().strip().upper() or "CNY", "large_amount": self.large.value()})
        self.dialog.changed = True


class TemplateTab(QWidget):
    """选一个 Excel，告诉程序每列放什么。"""

    def __init__(self, dialog: "SettingsDialog"):
        super().__init__()
        self.dialog = dialog
        self.source: Path | None = None
        self.sheet = ""
        layout = QVBoxLayout(self)
        layout.addWidget(hint(tr("选择你的记账 Excel，程序按表头猜每列放什么，猜错的在下拉框里改。"
                                 "导出时保留模板的格式和公式，示例行会被清掉。")))
        row = QHBoxLayout()
        self.name = QLabel(tr(template.active(dialog.settings).get("name", "")))
        choose = QPushButton(tr("选择 Excel 模板…"))
        choose.clicked.connect(self.choose)
        row.addWidget(self.name, 1)
        row.addWidget(choose)
        layout.addLayout(row)
        form = QHBoxLayout()
        form.addWidget(QLabel(tr("数据从第")))
        self.start = QSpinBox()
        self.start.setRange(1, 1000)
        self.start.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        form.addWidget(self.start)
        form.addWidget(QLabel(tr("行开始写")))
        form.addStretch()
        layout.addLayout(form)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels((tr("列"), tr("表头"), tr("示例"), tr("放什么")))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        save = QPushButton(tr("保存模板"))
        save.clicked.connect(self.save)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        self._show_current()

    def _fill(self, columns: list[dict]):
        self.table.setRowCount(len(columns))
        for index, column in enumerate(columns):
            for position, text in enumerate((column["letter"], column["title"], column["sample"])):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(index, position, item)
            combo = QComboBox()
            combo.addItem(tr("（留空）"), "")
            for key, name in template.FIELDS.items():
                combo.addItem(tr(name), key)
            combo.setCurrentIndex(max(0, combo.findData(column["field"])))
            self.table.setCellWidget(index, 3, combo)

    def _show_current(self):
        spec = template.active(self.dialog.settings)
        self.start.setValue(int(spec.get("start_row", 2)))
        if spec.get("file"):
            path = config.TEMPLATE_DIR / spec["file"]
            if path.is_file():
                self.source, self.sheet = path, spec.get("sheet", "")
                found = template.inspect(path)
                for column in found["columns"]:
                    column["field"] = spec["columns"].get(column["letter"], "")
                self._fill(found["columns"])

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择记账模板"), "", f'{tr("Excel 工作簿")} (*.xlsx *.xlsm)')
        if not path:
            return
        try:
            found = template.inspect(Path(path))
        except Exception as exc:  # noqa: BLE001 - 打不开的文件什么异常都可能抛
            QMessageBox.warning(self, tr("打不开这个文件"), str(exc))
            return
        self.source, self.sheet = Path(path), found["sheet"]
        self.name.setText(Path(path).name)
        self.start.setValue(found["start_row"])
        self._fill(found["columns"])

    def save(self):
        if self.source is None:
            QMessageBox.information(self, tr("还没有模板"), tr("请先选择一个 Excel 模板。"))
            return
        columns = {self.table.item(i, 0).text(): self.table.cellWidget(i, 3).currentData()
                   for i in range(self.table.rowCount()) if self.table.cellWidget(i, 3).currentData()}
        if not columns:
            QMessageBox.warning(self, tr("没有可写的列"), tr("至少给一列选上要放的内容。"))
            return
        current = template.active(self.dialog.settings)
        key = template.save(self.source, self.source.stem, self.sheet, self.start.value(), columns, current["categories"])
        self.dialog.settings = config.save_settings({"template": key})
        self.dialog.changed = True
        self.name.setText(self.source.stem)
        self.dialog.categories.load()


class CategoriesTab(QWidget):
    def __init__(self, dialog: "SettingsDialog"):
        super().__init__()
        self.dialog = dialog
        layout = QVBoxLayout(self)
        layout.addWidget(hint(tr("分类属于模板：导出时分别写进「一级分类」「二级分类」两列。没有二级的留空。")))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels((tr("方向"), tr("一级分类"), tr("二级分类")))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton(tr("添加一行"))
        add.clicked.connect(lambda: self._add({"direction": "expense", "group": "", "category": ""}))
        remove = QPushButton(tr("删除选中行"))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        save = QPushButton(tr("保存分类"))
        save.clicked.connect(self.save)
        for button in (add, remove):
            row.addWidget(button)
        row.addStretch()
        row.addWidget(save)
        layout.addLayout(row)
        self.load()

    def _add(self, entry: dict):
        index = self.table.rowCount()
        self.table.insertRow(index)
        combo = QComboBox()
        for key, name in template.DIRECTION_WORDS.items():
            combo.addItem(tr(name), key)
        combo.setCurrentIndex(combo.findData(entry["direction"]))
        self.table.setCellWidget(index, 0, combo)
        self.table.setItem(index, 1, QTableWidgetItem(entry["group"]))
        self.table.setItem(index, 2, QTableWidgetItem(entry.get("category", "")))

    def load(self):
        self.table.setRowCount(0)
        for entry in template.active(self.dialog.settings)["categories"]:
            self._add(entry)

    def save(self):
        entries = [{"direction": self.table.cellWidget(i, 0).currentData(),
                    "group": (self.table.item(i, 1) or QTableWidgetItem("")).text(),
                    "category": (self.table.item(i, 2) or QTableWidgetItem("")).text()}
                   for i in range(self.table.rowCount())]
        try:
            template.save_categories(self.dialog.settings, entries)
        except ValueError as exc:
            QMessageBox.warning(self, tr("分类没有保存"), tr(str(exc)))
            return
        self.dialog.changed = True


class TextTab(QWidget):
    """一行一条的文本编辑器：关键词和账户共用。"""

    def __init__(self, dialog: "SettingsDialog", note: str, text: str, parse, write):
        super().__init__()
        self.dialog, self.parse, self.write = dialog, parse, write
        layout = QVBoxLayout(self)
        layout.addWidget(hint(note))
        self.editor = QPlainTextEdit(text)
        layout.addWidget(self.editor, 1)
        self.problems = hint("")
        layout.addWidget(self.problems)
        save = QPushButton(tr("保存"))
        save.clicked.connect(self.save)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)

    def save(self):
        items, problems = self.parse(self.editor.toPlainText())
        if problems:
            self.problems.setText("\n".join(tr(p) for p in problems[:5]))
            return
        self.write(items)
        self.problems.setText(tr("已保存 {count} 条", count=len(items)))
        self.dialog.changed = True


class ModelTab(QWidget):
    def __init__(self, dialog: "SettingsDialog"):
        super().__init__()
        self.dialog = dialog
        settings = dialog.settings
        layout = QVBoxLayout(self)
        layout.addWidget(hint(tr("模型是可选的：不选也能用，表格类账单不需要它。文字模型用来归类没见过的商户、"
                                 "认少见的表格；视觉模型用来读扫描件和截图。模型文件留在你自己的电脑上。")))
        self.enable = QCheckBox(tr("启用本地模型"))
        self.enable.setChecked(bool(settings.get("llm_enable")))
        layout.addWidget(self.enable)
        form = QFormLayout()
        self.paths = {}
        for key, name in (("llama_model_path", "文字模型"), ("llama_vision_path", "视觉模型"),
                          ("llama_mmproj_path", "视觉 mmproj")):
            row = QHBoxLayout()
            edit = QLineEdit(settings.get(key, ""))
            browse = QPushButton(tr("选择…"))
            browse.clicked.connect(lambda _, e=edit: self._browse(e))
            row.addWidget(edit, 1)
            row.addWidget(browse)
            form.addRow(tr(name), row)
            self.paths[key] = edit
        layout.addLayout(form)
        row = QHBoxLayout()
        test = QPushButton(tr("加载测试"))
        test.clicked.connect(self.test)
        save = QPushButton(tr("保存"))
        save.clicked.connect(self.save)
        self.state = hint("")
        row.addWidget(self.state, 1)
        row.addWidget(test)
        row.addWidget(save)
        layout.addLayout(row)
        layout.addStretch()

    def _browse(self, edit: QLineEdit):
        start = str(Path(edit.text()).parent) if edit.text() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, tr("选择 GGUF 模型"), start, f'{tr("GGUF 模型")} (*.gguf)')
        if path:
            edit.setText(path)

    def save(self):
        self.dialog.settings = config.save_settings({"llm_enable": self.enable.isChecked(),
                                                     **{k: e.text().strip() for k, e in self.paths.items()}})
        self.dialog.changed = True
        self.state.setText(tr("已保存"))

    def test(self):
        self.save()
        self.state.setText(tr("正在加载…"))
        self.state.repaint()
        try:
            model.runtime.ensure(self.dialog.settings, vision=False)
            self.state.setText(tr("文字模型加载成功"))
        except model.ModelError as exc:
            self.state.setText(tr(str(exc)))


class SettingsDialog(QDialog):
    RELOAD = 2      # 语言或主题变了，主窗口重搭后再打开一次

    def __init__(self, parent=None, tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle(tr("设置"))
        self.resize(760, 600)
        self.settings = config.load_settings()
        self.changed = False
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.general = GeneralTab(self)
        self.categories = CategoriesTab(self)
        self.tabs.addTab(self.general, tr("通用"))
        self.tabs.addTab(TemplateTab(self), tr("记账模板"))
        self.tabs.addTab(self.categories, tr("分类"))
        self.rules = TextTab(self, tr("账单里出现左边的词，就归到右边的分类。一行一条，写在前面的优先：\n"
                                      "EXAMPLE TELCO => 通讯 / 通讯网络"), classify.format_rules(ledger.user_rules()),
                             classify.parse_rules, ledger.replace_user_rules)
        self.tabs.addTab(self.rules, tr("关键词"))
        learned = [a for a in ledger.own_accounts() if a["source"] == "statement"]
        self.accounts = TextTab(self, tr("这些账户之间的转账不算收入和支出。账单抬头的账号会自动记下（目前 {count} 个）；"
                                         "只在流水里露过面的账户和你的姓名写在这里：\n···4321 => 我的储蓄卡", count=len(learned)),
                                classify.format_accounts(ledger.own_accounts()),
                                classify.parse_accounts, ledger.replace_manual_accounts)
        self.tabs.addTab(self.accounts, tr("本人账户"))
        self.tabs.addTab(ModelTab(self), tr("本地模型"))
        self.tabs.setCurrentIndex(tab)
        layout.addWidget(self.tabs)
