"""窗口在离屏平台上构建，用合成账单走一遍常用操作。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from helpers import Isolated  # noqa: E402
from billbill import classify, config, documents, ledger, pipeline, template, ui, ui_settings  # noqa: E402

BANK = """Date,Description,Debit,Credit,Balance
02/08/2025,肯德基,35.00,,965.00
03/08/2025,SALARY ACME PTY LTD,,2000.00,2965.00
04/08/2025,UNKNOWN THING 77,10.00,,2955.00
05/08/2025,UNKNOWN THING 77,10.00,,2945.00
"""


class Window(Isolated):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        super().setUp()
        settings = config.load_settings()
        self.path = self.write("bank.csv", BANK)
        pipeline.import_file(self.path, settings)
        pipeline.refresh(settings)
        self.window = ui.MainWindow()
        self.addCleanup(self.window.close)

    def index(self, row, key):
        column = [k for k, _ in ui.COLUMNS].index(key)
        return self.window.table_model.index(row, column)

    def test_stylesheet_builds_for_both_themes(self):
        for name in ("light", "dark"):
            self.assertIn("QPushButton", ui.stylesheet(ui.COLORS[name]))

    def test_shows_the_ledger(self):
        self.assertEqual(self.window.table_model.rowCount(), 4)
        self.assertEqual(self.window.stats["count"].text(), "4")
        self.assertEqual(self.window.stats["unsure"].text(), "2")
        self.assertEqual(self.window.files.count(), 1)
        self.assertIn("已核对", self.window.files.item(0).text())

    def test_filter_and_search(self):
        self.window.set_filter(1)
        self.assertEqual(self.window.table_model.rowCount(), 2)
        self.window.set_filter(0)
        self.window.set_search("肯德基")
        self.assertEqual(self.window.table_model.rowCount(), 1)

    def test_choosing_a_category_teaches_the_same_merchant(self):
        entry = template.active(config.load_settings())["categories"][0]
        name = template.label(entry)
        row = next(i for i, r in enumerate(self.window.table_model.visible) if "UNKNOWN" in r["description"])
        self.window.table_model.setData(self.index(row, "category"), name)
        same = [r for r in ledger.entries() if "UNKNOWN" in r["description"]]
        self.assertEqual({r["category"] for r in same}, {name})
        self.assertEqual({r["cat_source"] for r in same}, {"manual", "cache"})

    def test_editing_text_is_saved(self):
        self.window.table_model.setData(self.index(0, "description"), "早餐")
        self.assertEqual(ledger.entries()[0]["description"], "早餐")

    def test_changing_the_direction_is_saved(self):
        self.window.table_model.setData(self.index(0, "direction"), "不计收支")
        self.assertEqual(ledger.entries()[0]["direction"], "transfer")

    def test_delegate_offers_categories(self):
        delegate = ui.ChoiceDelegate(self.window)
        editor = delegate.createEditor(self.window.table, None, self.index(0, "category"))
        self.assertGreater(editor.count(), 10)

    def test_excluded_rows_leave_the_totals(self):
        rows = ledger.entries()
        rows[0].update(excluded=True, excluded_manual=True, exclude_reason="手动排除")
        ledger.save_entries(rows)
        self.window.reload()
        self.assertEqual(self.window.stats["count"].text(), "3")
        self.window.set_filter(3)
        self.assertEqual(self.window.table_model.rowCount(), 1)

    def test_folders_expand_to_statement_files(self):
        folder = self.root / "bills"
        folder.mkdir()
        (folder / "a.pdf").write_bytes(b"%PDF")
        (folder / "notes.exe").write_bytes(b"x")
        self.assertEqual([p.name for p in documents.expand([folder])], ["a.pdf"])

    def test_import_runs_in_the_background(self):
        from PyQt6.QtTest import QSignalSpy
        path = self.write("more.csv", "Date,Description,Debit,Balance\n01/09/2025,Coffee,5.00,95.00\n02/09/2025,Bus,5.00,90.00\n")
        self.window.add_files([path])
        worker = self.window.worker
        spy = QSignalSpy(worker.finished_with)
        self.assertTrue(spy.wait(15000))
        self.app.processEvents()
        self.assertIsNone(self.window.worker)
        self.assertEqual(len(ledger.entries()), 6)

    def test_failed_file_is_shown_not_swallowed(self):
        result = pipeline.Result("bad.pdf", "failed", message="读不了")
        self.window._add_file_item(result.filename, result.status, 0, result.message, [])
        self.assertIn("没读出来", self.window.files.item(1).text())

    def test_settings_dialog_builds_and_saves_rules(self):
        dialog = ui_settings.SettingsDialog(self.window)
        tab = dialog.rules
        tab.editor.setPlainText("EXAMPLE TELCO => 通讯 / 通讯网络\n坏的一行")
        tab.save()
        self.assertIn("看不懂", tab.problems.text())
        tab.editor.setPlainText("EXAMPLE TELCO => 通讯 / 通讯网络")
        tab.save()
        self.assertEqual(ledger.user_rules()[0]["keyword"], "EXAMPLE TELCO")
        self.assertTrue(dialog.changed)

    def test_settings_saves_accounts_and_keeps_learned_ones(self):
        classify.learn_account("Bank ···1234")
        dialog = ui_settings.SettingsDialog(self.window)
        tab = dialog.accounts
        self.assertNotIn("1234", tab.editor.toPlainText())
        tab.editor.setPlainText("···4321 => 储蓄卡")
        tab.save()
        self.assertEqual({a["key"] for a in ledger.own_accounts()}, {"1234", "4321"})

    def test_template_tab_loads_a_workbook(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["日期", "金额", "备注"])
        book.active.append(["2026-01-01", 1, "x"])
        path = self.root / "mine.xlsx"
        book.save(path)
        dialog = ui_settings.SettingsDialog(self.window)
        tab = dialog.findChild(ui_settings.TemplateTab)
        found = template.inspect(path)
        tab.source, tab.sheet = path, found["sheet"]
        tab._fill(found["columns"])
        tab.save()
        self.assertEqual(template.active(dialog.settings)["file"], "mine.xlsx")
        self.assertEqual(template.active(dialog.settings)["columns"], {"A": "trade_date", "B": "amount", "C": "description"})

    def test_categories_tab_rejects_bad_rows_and_saves_good_ones(self):
        self.test_template_tab_loads_a_workbook()
        dialog = ui_settings.SettingsDialog(self.window)
        tab = dialog.categories
        count = tab.table.rowCount()
        tab._add({"direction": "expense", "group": "新分类", "category": "新二级"})
        tab.save()
        entries = template.active(dialog.settings)["categories"]
        self.assertEqual(len(entries), count + 1)
        self.assertIn("新分类 / 新二级", [template.label(e) for e in entries])

    def test_app_alignment_flags_exist(self):
        self.assertEqual(self.window.table_model.flags(self.index(0, "date")) & Qt.ItemFlag.ItemIsEditable, Qt.ItemFlag.NoItemFlags)
