"""主窗口：左边放账单文件，右边看整理结果，右上角导出。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap,
                         QPolygonF)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                             QMessageBox, QPushButton, QProgressBar, QStyledItemDelegate, QTableView, QVBoxLayout,
                             QWidget)

from . import classify, config, documents, i18n, ledger, pipeline, template
from .i18n import tr

# ------------------------------------------------------------------ 外观
COLORS = {
    "light": dict(window="#FFFFFF", side="#F5F5F7", line="#D2D2D7", text="#1D1D1F", soft="#6E6E73", accent="#007AFF",
                  accent_soft="#E8F1FE", hover="#EBEBF0", income="#248A3D", expense="#D70015", transfer="#6E6E73",
                  warn="#B25000", warn_soft="#FFF4E5"),
    "dark": dict(window="#1C1C1E", side="#232325", line="#3A3A3C", text="#F5F5F7", soft="#AEAEB2", accent="#0A84FF",
                 accent_soft="#1C3049", hover="#3A3A3C", income="#30D158", expense="#FF453A", transfer="#AEAEB2",
                 warn="#FF9F0A", warn_soft="#3A2A12"),
}
STATUS = {"verified": ("income", "已核对"), "mismatch": ("warn", "有差异"), "unchecked": ("soft", "无法核对"),
          "empty": ("soft", "本期无交易"), "skipped": ("soft", "已导入过"), "failed": ("expense", "没读出来"),
          "locked": ("expense", "需要密码")}


def resolve_theme(setting: str) -> str:
    """设置里的 system / light / dark -> 实际用哪套颜色。标准控件也跟着切。"""
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):        # Qt 6.8 起才有
        hints.setColorScheme({"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(setting, Qt.ColorScheme.Unknown))
    if setting in ("light", "dark"):
        return setting
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def line_icon(kind: str, color: str) -> QIcon:
    """线条图标。直接用画笔画，不依赖 SVG 插件，颜色跟随主题。"""
    scale = 4
    pixmap = QPixmap(24 * scale, 24 * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath()
    if kind == "gear":
        points = []
        for tooth in range(8):
            for offset, radius in ((-0.30, 7.7), (-0.17, 10.2), (0.17, 10.2), (0.30, 7.7)):
                angle = (tooth + offset) * math.tau / 8
                points.append(QPointF(12 + radius * math.cos(angle), 12 + radius * math.sin(angle)))
        path.addPolygon(QPolygonF(points + points[:1]))
        path.addEllipse(QPointF(12, 12), 3, 3)
    else:
        path.moveTo(20.5, 3.5)                  # 把手
        path.lineTo(13.6, 10.4)
        path.moveTo(10.4, 8.6)                  # 箍
        path.lineTo(15.4, 13.6)
        path.moveTo(9.9, 9.1)                   # 扫帚头
        path.cubicTo(8.4, 11.6, 6.4, 13.2, 3.5, 14)
        path.lineTo(10, 20.5)
        path.cubicTo(12.4, 18.8, 14.2, 16.8, 14.9, 14.1)
        path.moveTo(6.8, 17.3)                  # 扫帚丝
        path.lineTo(8.8, 15.3)
        path.moveTo(9.6, 19)
        path.lineTo(11.2, 17.4)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def apply_appearance(settings: dict) -> str:
    """把语言和主题用到整个程序，返回实际的主题名。"""
    i18n.set_language(settings.get("language", "zh"))
    name = resolve_theme(settings.get("theme", "system"))
    QApplication.instance().setStyleSheet(stylesheet(COLORS[name]))
    return name


def ask(parent, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    accept = box.addButton(tr("继续"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is accept


def stylesheet(c: dict) -> str:
    chevron = (config.BUNDLE_DIR / "assets/chevron-down.png").as_posix()
    return f"""
    QWidget {{ color: {c['text']}; font-size: 13px; }}
    QMainWindow, QDialog, #content {{ background: {c['window']}; }}
    #side {{ background: {c['side']}; border-right: 1px solid {c['line']}; }}
    #title {{ font-size: 20px; font-weight: 600; }}
    #soft, QLabel[soft="true"] {{ color: {c['soft']}; font-size: 12px; }}
    #stat {{ font-size: 18px; font-weight: 600; }}
    #drop {{ background: {c['window']}; border: 1.5px dashed {c['line']}; border-radius: 10px; }}
    #drop[active="true"] {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}
    #notice {{ background: {c['warn_soft']}; color: {c['warn']}; border-radius: 6px; padding: 6px 10px; }}
    QPushButton {{ background: {c['window']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 5px 14px; }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton:focus {{ border-color: {c['accent']}; }}
    QPushButton:disabled {{ color: {c['soft']}; }}
    QPushButton#primary {{ background: {c['accent']}; color: #FFFFFF; border: none; font-weight: 600; padding: 6px 18px; }}
    QPushButton#icon {{ background: transparent; border: none; padding: 6px; }}
    QPushButton#icon:hover {{ background: {c['hover']}; }}
    QCheckBox {{ spacing: 8px; }}
    QPushButton[segment="true"] {{ border: none; background: transparent; color: {c['soft']}; padding: 4px 12px; }}
    QPushButton[segment="true"]:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {c['window']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 4px 8px; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 10px; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ padding: 6px 4px; border-bottom: 1px solid {c['line']}; }}
    QListWidget::item:selected {{ background: {c['hover']}; color: {c['text']}; }}
    QTableView, QTableWidget {{ background: {c['window']}; border: 1px solid {c['line']}; border-radius: 8px;
        gridline-color: {c['line']}; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
    QHeaderView::section {{ background: {c['side']}; color: {c['soft']}; border: none;
        border-bottom: 1px solid {c['line']}; padding: 6px 8px; font-size: 12px; }}
    QTabBar::tab {{ padding: 6px 14px; background: transparent; color: {c['soft']}; border: none; }}
    QTabBar::tab:selected {{ color: {c['accent']}; font-weight: 600; border-bottom: 2px solid {c['accent']}; }}
    QTabWidget::pane {{ border: none; }}
    QProgressBar {{ background: {c['line']}; border: none; border-radius: 2px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 2px; }}
    QMenu {{ background: {c['window']}; border: 1px solid {c['line']}; padding: 4px; }}
    QMenu::item {{ padding: 5px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {c['accent']}; color: #FFFFFF; }}
    """


def money(value: float) -> str:
    return f"{value:,.2f}"


# ------------------------------------------------------------------ 后台任务
class Worker(QThread):
    progress = pyqtSignal(str, float)
    finished_with = pyqtSignal(object)

    def __init__(self, paths: list[Path], settings: dict, passwords: dict[str, str] | None = None):
        super().__init__()
        self.paths, self.settings, self.passwords = paths, settings, passwords or {}

    def run(self):
        results, stats = [], {}
        try:
            for index, path in enumerate(self.paths):
                share = index / max(len(self.paths), 1) * 0.9
                self.progress.emit(f"[{index + 1}/{len(self.paths)}] {path.name}", share)
                results.append(pipeline.import_file(
                    path, self.settings, self.passwords.get(str(path), ""),
                    lambda text, name=path.name, share=share: self.progress.emit(f"{name}: {tr(text)}", share)))
            self.progress.emit(tr("正在去重和分类…"), 0.92)
            stats = pipeline.refresh(self.settings, lambda text: self.progress.emit(tr(text), 0.95))
        except Exception as exc:  # noqa: BLE001 - 后台线程的异常要送回界面显示
            stats = {"errors": [f"处理中断：{exc}"]}
        self.finished_with.emit((results, stats))


# ------------------------------------------------------------------ 明细表
COLUMNS = (("date", "日期"), ("direction", "类型"), ("amount", "金额"), ("category", "分类"), ("account", "账户"),
           ("counterparty", "交易对方"), ("description", "说明"), ("tags", "标签"))
WIDTHS = (92, 56, 88, 128, 128, 150, 260, 112)
EDITABLE = {"direction", "category", "counterparty", "description"}
FILTERS = (("全部", lambda r: not r.get("excluded")),
           ("待确认", lambda r: not r.get("excluded") and (r.get("cat_source") == "fallback" or r.get("unsure_direction"))),
           ("余额对不上", lambda r: r.get("broken")),
           ("已排除", lambda r: r.get("excluded")))


class EntriesModel(QAbstractTableModel):
    edited = pyqtSignal(dict, str)

    def __init__(self, colors: dict):
        super().__init__()
        self.rows: list[dict] = []
        self.visible: list[dict] = []
        self.colors = colors
        self.keep = FILTERS[0][1]
        self.search = ""

    def load(self, rows: list[dict]):
        self.rows = rows
        self.apply_filter()

    def apply_filter(self):
        self.beginResetModel()
        needle = self.search.lower()
        self.visible = [r for r in self.rows if self.keep(r) and (not needle or needle in " ".join(
            str(r.get(k) or "") for k in ("date", "category", "account", "counterparty", "description",
                                          "source_text", "tags")).lower())]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):      # noqa: N802
        return 0 if parent.isValid() else len(self.visible)

    def columnCount(self, parent=QModelIndex()):   # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):   # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(COLUMNS[section][1])
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return base | Qt.ItemFlag.ItemIsEditable if COLUMNS[index.column()][0] in EDITABLE else base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row, key = self.visible[index.row()], COLUMNS[index.column()][0]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if key == "amount":
                return money(row["amount"])
            if key == "direction":
                return tr(template.DIRECTION_LABELS.get(row.get("direction"), ""))
            if key == "tags":
                return i18n.tags(row.get("tags"))
            if key == "category" and row.get("category") == classify.UNSURE:
                return tr(classify.UNSURE)
            return row.get(key) or ""
        if role == Qt.ItemDataRole.ForegroundRole:
            if key == "amount":
                return QColor(self.colors.get(row.get("direction"), self.colors["text"]))
            if key == "category" and row.get("cat_source") == "fallback":
                return QColor(self.colors["warn"])
        if role == Qt.ItemDataRole.TextAlignmentRole and key == "amount":
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            return "\n".join(t for t in (row.get("source_text"), row.get("source_file"), row.get("exclude_reason")) if t)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):   # noqa: N802
        if role != Qt.ItemDataRole.EditRole:
            return False
        row, key = self.visible[index.row()], COLUMNS[index.column()][0]
        value = str(value).strip()
        if key == "direction":
            value = next((k for k, v in template.DIRECTION_LABELS.items() if tr(v) == value), row["direction"])
        if value == row.get(key):
            return False
        self.edited.emit(row, f"{key}\n{value}")
        return True


class ChoiceDelegate(QStyledItemDelegate):
    """类型、分类两列在格子里直接选。"""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window = window

    def createEditor(self, parent, option, index):   # noqa: N802
        key = COLUMNS[index.column()][0]
        if key not in ("direction", "category"):
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent)
        if key == "direction":
            combo.addItems(tr(label) for label in template.DIRECTION_LABELS.values())
        else:
            row = self.window.table_model.visible[index.row()]
            entries = template.active(self.window.settings)["categories"]
            # 同方向的排前面，转账总能选：一笔收支可以改判成转账。
            ordered = sorted(entries, key=lambda e: (e["direction"] != row.get("direction"), e["direction"] != "transfer"))
            combo.addItems(template.label(e) for e in ordered)
        combo.activated.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor, index):          # noqa: N802
        if isinstance(editor, QComboBox):
            editor.setCurrentText(str(index.data(Qt.ItemDataRole.EditRole)))
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):    # noqa: N802
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
            return
        super().setModelData(editor, model, index)


class DropArea(QFrame):
    """拖文件进来，或点一下选文件。"""
    dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(96)
        layout = QVBoxLayout(self)
        heading = QLabel(tr("拖入账单文件，或点击选择"))
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = QLabel(tr("PDF、Word、Excel、CSV、网页、图片"))
        note.setObjectName("soft")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addStretch()

    def _active(self, value: bool):
        self.setProperty("active", value)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):   # noqa: N802
        if event.mimeData().hasUrls():
            self._active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):   # noqa: N802
        self._active(False)

    def dropEvent(self, event):        # noqa: N802
        self._active(False)
        self.dropped.emit([Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()])

    def mouseReleaseEvent(self, event):   # noqa: N802
        names = " ".join(f"*{s}" for s in documents.SUFFIXES)
        files, _ = QFileDialog.getOpenFileNames(self, tr("选择账单文件"), "",
                                                f'{tr("账单文件")} ({names});;{tr("所有文件")} (*)')
        if files:
            self.dropped.emit([Path(f) for f in files])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = config.load_settings()
        self.worker: Worker | None = None
        self.setWindowTitle("BillBill")
        self.resize(1280, 800)
        self.setMinimumSize(980, 600)
        self.apply_look()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)

    def apply_look(self):
        """语言或主题变了就整个重搭：所有文字和颜色都在搭界面时取。"""
        self.theme = apply_appearance(self.settings)
        self.colors = COLORS[self.theme]
        root = QWidget()
        root.setObjectName("content")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_side())
        layout.addWidget(self._build_main(), 1)
        self.setCentralWidget(root)
        self.reload()
        self._set_busy(self.worker is not None)

    def _system_theme_changed(self, scheme):
        wanted = "dark" if scheme == Qt.ColorScheme.Dark else "light"
        if self.settings.get("theme") == "system" and wanted != self.theme:
            QTimer.singleShot(0, self.apply_look)

    # -------------------------------------------------------------- 搭界面
    def _build_side(self) -> QWidget:
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(300)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        title = QLabel("BillBill")
        title.setObjectName("title")
        layout.addWidget(title)
        self.drop = DropArea()
        self.drop.dropped.connect(self.add_files)
        layout.addWidget(self.drop)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("soft")
        self.progress_text.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        layout.addWidget(self.progress_text)
        layout.addWidget(self.progress_bar)
        self.files = QListWidget()
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._file_menu)
        self.files.setWordWrap(True)
        self.files.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.files, 1)
        row = QHBoxLayout()
        self.settings_button = self._icon_button("gear", "设置", self.open_settings)
        self.clear_button = self._icon_button("broom", "清空账本", self.clear_ledger)
        row.addWidget(self.settings_button)
        row.addStretch()
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        return side

    def _icon_button(self, kind: str, name: str, action) -> QPushButton:
        button = QPushButton()
        button.setObjectName("icon")
        button.setIcon(line_icon(kind, self.colors["text"]))
        button.setIconSize(QSize(22, 22))
        button.setToolTip(tr(name))
        button.setAccessibleName(tr(name))      # 没有文字，读屏软件靠它
        button.clicked.connect(action)
        return button

    def _build_main(self) -> QWidget:
        main = QWidget()
        main.setObjectName("content")
        layout = QVBoxLayout(main)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        top = QHBoxLayout()
        self.stats = {}
        for key, name in (("count", tr("记录")), ("income", tr("收入")), ("expense", tr("支出")), ("unsure", tr("待确认"))):
            box = QVBoxLayout()
            box.setSpacing(0)
            value = QLabel("—")
            value.setObjectName("stat")
            label = QLabel(name)
            label.setObjectName("soft")
            box.addWidget(value)
            box.addWidget(label)
            top.addLayout(box)
            top.addSpacing(28)
            self.stats[key] = value
        top.addStretch()
        self.template_name = QLabel("")
        self.template_name.setObjectName("soft")
        top.addWidget(self.template_name)
        self.export_button = QPushButton(tr("导出 Excel"))
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.export)
        top.addWidget(self.export_button)
        layout.addLayout(top)

        self.notice = QLabel("")
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)

        bar = QHBoxLayout()
        self.segments = []
        for index, (name, _) in enumerate(FILTERS):
            button = QPushButton(tr(name))
            button.setCheckable(True)
            button.setProperty("segment", True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _, i=index: self.set_filter(i))
            bar.addWidget(button)
            self.segments.append(button)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索商户、说明、账户…"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.set_search)
        bar.addWidget(self.search)
        layout.addLayout(bar)

        self.table_model = EntriesModel(self.colors)
        self.table_model.edited.connect(self.on_edit)
        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setItemDelegate(ChoiceDelegate(self))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for index, width in enumerate(WIDTHS):
            self.table.setColumnWidth(index, width)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        return main

    # -------------------------------------------------------------- 刷新
    def reload(self):
        rows = ledger.entries()
        self.table_model.load(rows)
        active = [r for r in rows if not r.get("excluded")]
        self.stats["count"].setText(f"{len(active):,}")
        self.stats["income"].setText(money(sum(r["amount"] for r in active if r.get("direction") == "income")))
        self.stats["expense"].setText(money(sum(r["amount"] for r in active if r.get("direction") == "expense")))
        self.stats["unsure"].setText(str(sum(1 for r in active if r.get("cat_source") == "fallback")))
        self.export_button.setEnabled(bool(active))
        self.template_name.setText(tr("模板：{name}", name=tr(template.active(self.settings).get("name", ""))))
        self.files.clear()
        for document in ledger.documents():
            self._add_file_item(document["filename"], document.get("status", ""), document.get("count", 0),
                                document.get("detail", ""), document.get("warnings") or [], document["id"])

    def _add_file_item(self, name: str, status: str, count: int, detail: str, warnings: list[str], document=None):
        tone, word = STATUS.get(status, ("soft", status))
        item = QListWidgetItem(f"{name}\n{tr(word)}" + (" · " + tr("{count} 笔", count=count) if count else ""))
        item.setForeground(QColor(self.colors[tone if status != "unchecked" else "text"]))
        item.setToolTip("\n".join(tr(t) for t in (detail, *warnings) if t))
        item.setData(Qt.ItemDataRole.UserRole, document)
        self.files.addItem(item)

    def set_filter(self, index: int):
        for position, button in enumerate(self.segments):
            button.setChecked(position == index)
        self.table_model.keep = FILTERS[index][1]
        self.table_model.apply_filter()

    def set_search(self, text: str):
        self.table_model.search = text.strip()
        self.table_model.apply_filter()

    def show_notice(self, text: str):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    # -------------------------------------------------------------- 导入
    def add_files(self, paths: list[Path], passwords: dict[str, str] | None = None):
        paths = documents.expand(paths)
        if not paths or self.worker is not None:
            return
        self.show_notice("")
        self.worker = Worker(paths, self.settings, passwords)
        self._set_busy(True)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_with.connect(self._on_imported)
        self.worker.start()

    def _set_busy(self, busy: bool):
        self.progress_bar.setVisible(busy)
        for widget in (self.drop, self.clear_button, self.settings_button):
            widget.setEnabled(not busy)
        if busy:
            self.export_button.setEnabled(False)

    def _on_progress(self, text: str, share: float):
        self.progress_text.setText(text)
        self.progress_bar.setValue(int(share * 100))

    def _on_imported(self, outcome):
        results, stats = outcome
        paths = self.worker.paths
        self.worker.wait()
        self.worker = None
        self._set_busy(False)
        self.progress_text.setText("")
        self.reload()
        problems = []
        for result in results:
            if result.status in ("failed", "skipped"):
                self._add_file_item(result.filename, result.status, 0, result.message, result.warnings)
            if result.status == "failed":
                problems.append(f"{result.filename}: {tr(result.message)}")
            elif result.warnings:
                problems.append(f"{result.filename}: {tr('；'.join(result.warnings))}")
            if result.status == "mismatch":
                problems.append(tr("{name}：{message}，请在「余额对不上」里核对", name=result.filename, message=tr(result.message)))
        problems += [tr(error) for error in stats.get("errors", [])]
        self.show_notice("\n".join(problems[:6]))
        locked = [p for p, r in zip(paths, results) if r.status == "locked"]
        if locked:
            passwords = {}
            for path in locked:
                text, ok = QInputDialog.getText(self, tr("需要密码"), tr("{name} 有密码，请输入：", name=path.name),
                                                QLineEdit.EchoMode.Password)
                if ok and text:
                    passwords[str(path)] = text
            if passwords:
                self.add_files([Path(p) for p in passwords], passwords)

    # -------------------------------------------------------------- 编辑
    def on_edit(self, row: dict, change: str):
        key, value = change.split("\n", 1)
        if key == "category":
            entry = next((e for e in template.active(self.settings)["categories"] if template.label(e) == value), None)
            if entry is None:
                return
            row.update(category=value, group=entry["group"], detail=entry["category"],
                       direction=entry["direction"], direction_raw=entry["direction"], cat_source="manual")
            classify.remember_manual(row, template.active(self.settings)["categories"])
            ledger.save_entries([row])
            # 同一商户的其余记录跟着改；模型不参与，这一步要立刻返回。
            pipeline.refresh({**self.settings, "llm_enable": False})
            self.reload()
            return
        if key == "direction":
            row.update(direction=value, direction_raw=value, unsure_direction=False, category="", cat_source="")
            ledger.save_entries([row])
            pipeline.refresh({**self.settings, "llm_enable": False})
            self.reload()
            return
        row[key] = value
        ledger.save_entries([row])
        self.table_model.apply_filter()

    def _row_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        rows = [self.table_model.visible[i.row()] for i in self.table.selectionModel().selectedRows()]
        rows = rows or [self.table_model.visible[index.row()]]
        menu = QMenu(self)
        excluded = all(r.get("excluded") for r in rows)
        action = QAction(tr("恢复这些记录") if excluded else tr("排除，不写入 Excel"), self)

        def toggle():
            for row in rows:
                row.update(excluded=not excluded, excluded_manual=True, exclude_reason="" if excluded else "手动排除")
            ledger.save_entries(rows)
            self.reload()
        action.triggered.connect(toggle)
        menu.addAction(action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _file_menu(self, position):
        item = self.files.itemAt(position)
        document = item.data(Qt.ItemDataRole.UserRole) if item else None
        if document is None:
            return
        menu = QMenu(self)
        action = QAction(tr("从账本移除这份账单"), self)

        def remove():
            ledger.remove_document(document)
            pipeline.refresh({**self.settings, "llm_enable": False})
            self.reload()
        action.triggered.connect(remove)
        menu.addAction(action)
        menu.exec(self.files.viewport().mapToGlobal(position))

    def clear_ledger(self):
        if ask(self, tr("清空账本"), tr("移除所有已导入的账单？关键词、账户和模板都会保留。")):
            ledger.clear()
            self.show_notice("")
            self.reload()

    def export(self):
        target, _ = QFileDialog.getSaveFileName(self, tr("导出 Excel"), str(Path.home() / pipeline.default_output_name()),
                                                f'{tr("Excel 工作簿")} (*.xlsx)')
        if not target:
            return
        unsure = sum(1 for r in self.table_model.rows if not r.get("excluded") and r.get("cat_source") == "fallback")
        if unsure and not ask(self, tr("还有待确认"), tr("还有 {count} 笔没有分类，会以「待确认」写入。继续导出吗？", count=unsure)):
            return
        try:
            path = template.export(self.table_model.rows, Path(target), self.settings)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, tr("导出失败"), tr(str(exc)))
            return
        self.show_notice("")
        self.progress_text.setText(tr("已导出：{name}", name=path.name))

    def open_settings(self):
        from .ui_settings import SettingsDialog
        tab, changed = 0, False
        while True:
            dialog = SettingsDialog(self, tab)
            result = dialog.exec()
            self.settings = config.load_settings()
            changed = changed or dialog.changed
            if result != SettingsDialog.RELOAD:
                break
            # 换语言或主题：主窗口和设置页都按新样子重搭，停在原来那一页。
            self.apply_look()
            tab = dialog.tabs.currentIndex()
        if changed:
            pipeline.refresh({**self.settings, "llm_enable": False})
            self.reload()


def run() -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("BillBill")
    application.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["SF Pro Text", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSize(10)
    application.setFont(font)
    icon = config.BUNDLE_DIR / "assets/billbill.ico"
    if icon.is_file():
        application.setWindowIcon(QIcon(str(icon)))
    pipeline.data_ready()
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-window":
        QTimer.singleShot(1500, application.quit)
    return application.exec()
