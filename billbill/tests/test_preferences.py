"""转账开关、界面语言、外观，以及仓库里不能带个人信息。"""
import ast
import os
import re
import sqlite3
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from helpers import FIXTURES, ROOT, Isolated  # noqa: E402
from billbill import config, i18n, ledger, pipeline, template, ui, ui_settings  # noqa: E402
from billbill.i18n import tr  # noqa: E402

CJK = re.compile(r"[一-鿿]")
BANK = """日期,摘要,支出,收入,余额
2026-03-01,转账给 房东,1000.00,,9000.00
2026-03-02,手机银行转账 来自朋友,,50.00,9050.00
2026-03-03,转账至 ···4321,200.00,,8850.00
"""


class TransferSwitch(Isolated):
    def load(self, **settings):
        config.save_settings(settings)
        return config.load_settings()

    def directions(self, settings):
        pipeline.refresh(settings)
        return [r["direction"] for r in ledger.entries()]

    def test_transfers_to_other_people_are_real_money(self):
        settings = self.load()
        pipeline.import_file(self.write("a.csv", BANK), settings)
        self.assertEqual(self.directions(settings), ["expense", "income", "expense"])

    def test_only_own_accounts_are_left_out(self):
        settings = self.load()
        ledger.replace_manual_accounts([{"key": "4321", "label": "储蓄卡"}])
        pipeline.import_file(self.write("a.csv", BANK), settings)
        self.assertEqual(self.directions(settings), ["expense", "income", "transfer"])

    def test_switch_takes_effect_without_reimporting(self):
        settings = self.load()
        pipeline.import_file(self.write("a.csv", BANK), settings)
        self.assertEqual(self.directions(self.load(transfer_words_neutral=True)), ["transfer"] * 3)
        self.assertEqual(self.directions(self.load(transfer_words_neutral=False)), ["expense", "income", "expense"])

    def test_wallet_transfer_keeps_its_real_direction(self):
        text = ("交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态\n"
                "2025-08-03 19:44:01,转账,朋友,转账-AA收款,收入,¥50.00,零钱,已存入零钱\n")
        settings = self.load()
        pipeline.import_file(self.write("wechat.csv", text), settings)
        self.assertEqual(self.directions(settings), ["income"])

    def test_statement_saying_not_counted_is_still_believed(self):
        text = "交易时间,交易对方,收/支,金额,交易分类\n2025-08-08 10:00:00,余额宝,不计收支,2000.00,不计收支\n"
        settings = self.load()
        pipeline.import_file(self.write("a.csv", text), settings)
        self.assertEqual(self.directions(settings), ["transfer"])

    def test_card_repayment_stays_out_of_spending(self):
        text = "日期,摘要,支出,余额\n2026-03-01,信用卡还款,800.00,100.00\n2026-03-02,午饭,20.00,80.00\n"
        settings = self.load()
        pipeline.import_file(self.write("a.csv", text), settings)
        self.assertEqual(self.directions(settings), ["transfer", "expense"])


class Language(Isolated):
    def tearDown(self):
        i18n.set_language("zh")

    def literals(self, name):
        """UI 文件里所有写在 tr(...) 里的中文原文。"""
        tree = ast.parse((ROOT / "billbill" / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr" and isinstance(node.args[0], ast.Constant):
                yield node.args[0].value

    def test_every_interface_string_has_english(self):
        missing = [text for name in ("ui.py", "ui_settings.py") for text in self.literals(name)
                   if text not in i18n.EN and CJK.search(text)]
        self.assertEqual(missing, [])

    def test_constants_shown_in_the_ui_have_english(self):
        i18n.set_language("en")
        names = [word for _, word in ui.STATUS.values()] + [label for _, label in ui.COLUMNS] + [n for n, _ in ui.FILTERS]
        names += [*template.DIRECTION_LABELS.values(), *template.DIRECTION_WORDS.values(), *template.FIELDS.values(), template.BUILTIN["name"]]
        names += ["大额", "跨境", "手续费", "订阅", "自动扣款", "退款", "疑似重复", "大额现金"]
        self.assertEqual([n for n in names if CJK.search(tr(n)) and "2025年" not in tr(n)], [])

    def test_messages_from_the_parser_are_translated(self):
        i18n.set_language("en")
        for text in ("余额逐笔核对通过（3/3）", "余额有 2 处对不上（共 161 处）", "第 3/4 页是图片或文字层损坏，没有配置视觉模型，未读取",
                     "工作表「bank」图片 1 是图片，没有配置视觉模型，未读取", "读取第 2/9 页", "第 2/9 页是图片，正在 OCR…",
                     "请在设置里选择文字模型（GGUF 文件）", "这份 PDF 有密码", "用本地模型分类（复核拿不准的）：1-10 / 20",
                     "3 笔看不出收支方向，暂记为支出，请确认；数字来自 OCR，请对照原件复核", "第 2 行看不懂：x", "没有找到日期列和金额列"):
            with self.subTest(text):
                self.assertIsNone(CJK.search(tr(text)), tr(text))

    def test_chinese_is_the_original(self):
        self.assertEqual(tr("设置"), "设置")
        self.assertEqual(tr("已保存 {count} 条", count=3), "已保存 3 条")

    def test_placeholders_survive_translation(self):
        i18n.set_language("en")
        self.assertEqual(tr("已保存 {count} 条", count=3), "Saved 3 items")

    def test_tags(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tags("#待确认#大额"), "#To review#Large")

    def test_builtin_template_follows_the_language(self):
        i18n.set_language("en")
        settings = config.load_settings()
        pipeline.import_file(self.write("a.csv", BANK), settings)
        pipeline.refresh(settings)
        from openpyxl import load_workbook
        sheet = load_workbook(template.export(ledger.entries(), self.root / "x.xlsx", settings)).active
        self.assertEqual((sheet["A1"].value, sheet["B2"].value), ("Date", "Expense"))


class Appearance(Isolated):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        super().setUp()
        self.window = ui.MainWindow()
        self.addCleanup(self.window.close)
        self.addCleanup(i18n.set_language, "zh")
        self.addCleanup(ui.resolve_theme, "system")

    def test_buttons_are_icons_with_a_name_for_screen_readers(self):
        for button, name in ((self.window.settings_button, "设置"), (self.window.clear_button, "清空账本")):
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())
            self.assertEqual((button.toolTip(), button.accessibleName()), (name, name))

    def test_icons_actually_draw(self):
        for kind in ("gear", "broom"):
            image = ui.line_icon(kind, "#000000").pixmap(48, 48).toImage()
            drawn = sum(image.pixelColor(x, y).alpha() > 0 for x in range(48) for y in range(48))
            self.assertGreater(drawn, 150, kind)

    def test_explicit_themes(self):
        self.assertEqual(ui.resolve_theme("dark"), "dark")
        self.assertEqual(ui.resolve_theme("light"), "light")

    def test_switching_rebuilds_everything(self):
        self.window.settings = config.save_settings({"language": "en", "theme": "dark"})
        self.window.apply_look()
        self.assertEqual(self.window.theme, "dark")
        self.assertEqual(self.window.export_button.text(), "Export Excel")
        self.assertEqual(self.window.settings_button.toolTip(), "Settings")
        self.assertEqual(self.window.table_model.headerData(0, ui.Qt.Orientation.Horizontal), "Date")

    def test_general_tab_saves_and_asks_for_a_reload(self):
        dialog = ui_settings.SettingsDialog(self.window)
        dialog.general.language.setCurrentIndex(dialog.general.language.findData("en"))
        self.assertEqual(config.load_settings()["language"], "en")
        self.assertEqual(dialog.result(), dialog.RELOAD)

    def test_transfer_switch_is_saved_and_refreshes(self):
        dialog = ui_settings.SettingsDialog(self.window)
        dialog.general.transfer_words.setChecked(True)
        self.assertTrue(config.load_settings()["transfer_words_neutral"])
        self.assertTrue(dialog.changed)

    def test_settings_reopen_on_the_same_tab(self):
        self.assertEqual(ui_settings.SettingsDialog(self.window, 3).tabs.currentIndex(), 3)


class Settings(Isolated):
    def test_stale_keys_are_ignored_and_dropped_on_save(self):
        config.SETTINGS_PATH.parent.mkdir(exist_ok=True)
        config.SETTINGS_PATH.write_text('{"llm_base_url": "http://x", "language": "en"}', "utf-8")
        self.assertNotIn("llm_base_url", config.load_settings())
        config.save_settings({"theme": "dark"})
        self.assertNotIn("llm_base_url", config.SETTINGS_PATH.read_text("utf-8"))
        self.assertEqual(config.load_settings()["language"], "en")


class NoPersonalData(Isolated):
    """进仓库、进安装包的默认数据里不能有个人信息。"""
    PERSONAL = re.compile(rb"(?<![a-z])kingr(?![a-z])|kkraymei|kingraymeills|C:[\\/]+Users")

    def texts(self, path):
        raw = path.read_bytes()
        yield raw
        yield raw.decode("utf-16-le", "ignore").encode("utf-8", "ignore")
        if raw[:2] == b"PK":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    yield archive.read(name)

    def test_fixtures_carry_no_author_or_path(self):
        for path in sorted(FIXTURES.iterdir()):
            with self.subTest(path.name):
                self.assertFalse(any(self.PERSONAL.search(text) for text in self.texts(path)))

    def test_source_has_no_personal_paths(self):
        files = [ROOT / "app.py", ROOT / "README.md", ROOT / "billbill.spec", *ROOT.glob("billbill/*.py"), *ROOT.glob("ci/*.py")]
        self.assertEqual([p.name for p in files if self.PERSONAL.search(p.read_bytes())], [])

    def test_a_new_install_starts_empty(self):
        self.assertEqual(ledger.user_rules(), [])
        self.assertEqual(ledger.own_accounts(), [])
        self.assertEqual(ledger.documents(), [])
        self.assertIsNone(template.active(config.load_settings()).get("file"))
        for key in ("llama_model_path", "llama_vision_path", "llama_mmproj_path", "llama_server_path"):
            self.assertEqual(config.DEFAULT_SETTINGS[key], "")
        self.assertFalse(sqlite3.connect(config.DB_PATH).execute(
            "select 1 from sqlite_master where name in ('jobs', 'transactions')").fetchone())
