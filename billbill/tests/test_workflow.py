"""从文件到 Excel 的整条路：分类、互转、去重、模板导出。"""
import json
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from helpers import FIXTURES, Isolated
from billbill import classify, config, ledger, model, pipeline, template

ALIPAY = """支付宝交易明细
起始日期:[2025-08-01 00:00:00]    终止日期:[2025-08-31 23:59:59]
---------------------------------
交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
2025-08-01 08:12:33,餐饮美食,肯德基,kfc@alipay,早餐套餐,支出,23.00,招商银行储蓄卡(1234),交易成功,A1,S1,
2025-08-08 10:00:00,不计收支,余额宝-转出到银行卡,,余额宝转出,不计收支,2000.00,余额宝,交易成功,A2,S2,
2025-08-15 09:15:00,退款,肯德基,kfc@alipay,早餐套餐退款,收入,23.00,招商银行储蓄卡(1234),退款成功,A3,S3,
2025-08-28 00:00:00,其他,某商户,x@alipay,已关闭订单,支出,999.00,余额宝,交易关闭,A4,S4,
"""
WECHAT = """微信支付账单明细
起始时间：[2025-08-01 00:00:00] 终止时间：[2025-08-31 23:59:59]
----------------------微信支付账单明细列表--------------------
交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注
2025-08-02 09:12:33,商户消费,星巴克,拿铁咖啡,支出,¥35.00,招商银行信用卡(5678),支付成功,W1,/,/
2025-08-03 19:44:01,转账,张三,转账-AA收款,收入,¥50.00,零钱,已存入零钱,W2,/,/
"""
BANK = """Date,Description,Debit,Credit,Balance
02/08/2025,财付通-星巴克,35.00,,965.00
03/08/2025,SALARY ACME PTY LTD,,2000.00,2965.00
04/08/2025,UNKNOWN THING 77,10.00,,2955.00
"""


def template_book(path):
    book = Workbook()
    sheet = book.active
    sheet.append(["日期", "类型", "金额", "一级分类", "二级分类", "账户1", "账户2", "备注", "货币", "标签"])
    sheet.append(["2011年01月11日 12:00:00", "支出", 40, "餐饮", "三餐", "支付宝", "", "示例", "USD", "#标签1"])
    sheet.append(["2011年01月11日 12:00:00", "收入", 1000, "工资", "", "工资卡", "", "示例", "CNY", "#标签2"])
    sheet["C2"].number_format = "#,##0.00"
    book.save(path)


class Importing(Isolated):
    def imported(self, *files):
        settings = config.load_settings()
        results = [pipeline.import_file(self.write(name, text), settings) for name, text in files]
        pipeline.refresh(settings)
        return results, ledger.entries()

    def test_wallet_csv(self):
        results, rows = self.imported(("支付宝.csv", ALIPAY))
        self.assertEqual((results[0].label, results[0].count), ("支付宝", 3))
        self.assertEqual([r["direction"] for r in rows], ["expense", "transfer", "income"])

    def test_same_file_twice_is_skipped(self):
        settings = config.load_settings()
        path = self.write("a.csv", BANK)
        pipeline.import_file(path, settings)
        self.assertEqual(pipeline.import_file(path, settings).status, "skipped")
        self.assertEqual(len(ledger.entries()), 3)

    def test_unreadable_file_does_not_stop_the_batch(self):
        settings = config.load_settings()
        bad = pipeline.import_file(self.write("bad.pdf", "%PDF-1.4 garbage"), settings)
        self.assertEqual(bad.status, "failed")
        self.assertEqual(pipeline.import_file(self.write("a.csv", BANK), settings).status, "verified")

    def test_unexpected_parser_error_only_fails_that_file(self):
        settings = config.load_settings()
        with patch("billbill.statement.parse_table", side_effect=IndexError("boom")):
            result = pipeline.import_file(self.write("a.csv", BANK), settings)
        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.message)

    def test_non_statement_sheet_does_not_fail_the_workbook(self):
        book = Workbook()
        book.active.append(["这张表只是封面"])
        sheet = book.create_sheet("明细")
        for row in (["日期", "摘要", "金额", "余额"], ["2026-01-02", "咖啡", "-12.00", "88.00"], ["2026-01-03", "午饭", "-8.00", "80.00"]):
            sheet.append(row)
        path = self.root / "book.xlsx"
        book.save(path)
        self.assertEqual(pipeline.import_file(path, config.load_settings()).count, 2)

    def test_every_real_office_format_imports_without_duplicates(self):
        settings = config.load_settings()
        for path in sorted(FIXTURES.iterdir()):
            pipeline.import_file(path, settings)
        pipeline.refresh(settings)
        active = [r for r in ledger.entries() if not r.get("excluded")]
        self.assertEqual(len(active), 4)

    def test_own_account_learned_from_the_header(self):
        self.imported(("a.csv", "账号：6222000011112345\n" + BANK))
        self.assertIn("11112345", [a["key"] for a in ledger.own_accounts()][0] + "11112345")


class Dedupe(Isolated):
    def make(self, **values):
        base = {"id": 0, "date": "2026-01-01", "amount": 10.0, "direction": "expense", "label": "Bank",
                "source_file": "a", "fingerprint": "f"}
        return {**base, **values}

    def test_overlapping_exports_keep_one_copy(self):
        rows = [self.make(id=1), self.make(id=2, source_file="b")]
        ledger.deduplicate(rows)
        self.assertEqual(sum(bool(r["excluded"]) for r in rows), 1)

    def test_three_car_washes_in_one_file_are_real(self):
        rows = [self.make(id=i) for i in range(3)]
        ledger.deduplicate(rows)
        self.assertFalse(any(r["excluded"] for r in rows))

    def test_wallet_payment_covers_the_bank_line(self):
        rows = [self.make(id=1, label="微信支付", fingerprint="a", counterparty="星巴克"),
                self.make(id=2, fingerprint="b", description="财付通-星巴克", source_file="b")]
        ledger.deduplicate(rows)
        self.assertTrue(rows[1]["excluded"])
        self.assertIn("星巴克", rows[1]["exclude_reason"])

    def test_bank_line_without_a_gateway_word_is_a_separate_purchase(self):
        rows = [self.make(id=1, label="微信支付", fingerprint="a"), self.make(id=2, fingerprint="b", description="星巴克", source_file="b")]
        ledger.deduplicate(rows)
        self.assertFalse(rows[1]["excluded"])

    def test_manual_decision_survives(self):
        rows = [self.make(id=1, excluded=True, excluded_manual=True, exclude_reason="手动排除")]
        ledger.deduplicate(rows)
        self.assertTrue(rows[0]["excluded"])


class OwnTransfers(Isolated):
    accounts = [{"key": "4321", "label": "储蓄卡"}, {"key": "ZHANGSAN", "label": "我"}]

    def test_counter_account(self):
        self.assertEqual(classify.own_transfer({"counter_account": "···4321"}, self.accounts), "储蓄卡")

    def test_card_used_is_not_the_other_side(self):
        row = {"source_text": "BURGER SHOP Card xx 4321", "description": "BURGER SHOP"}
        self.assertEqual(classify.own_transfer(row, self.accounts), "")

    def test_own_side_is_skipped(self):
        self.assertEqual(classify.own_transfer({"account": "Bank ···4321", "counter_account": "···4321"}, self.accounts), "")

    def test_number_needs_a_transfer_context(self):
        self.assertEqual(classify.own_transfer({"source_text": "invoice 4321 for shop"}, self.accounts), "")
        self.assertEqual(classify.own_transfer({"source_text": "Transfer invoice 4321"}, self.accounts), "")
        self.assertTrue(classify.own_transfer({"source_text": "Transfer to account 88884321"}, self.accounts))

    def test_name_is_exact(self):
        self.assertTrue(classify.own_transfer({"source_text": "Transfer from ZHANG SAN"}, self.accounts))
        self.assertEqual(classify.own_transfer({"source_text": "Transfer from ZHANGSANFENG"}, self.accounts), "")

    def test_short_accounts_are_refused(self):
        self.assertEqual(classify.parse_accounts("123")[1][0][:3], "第 1")

    def test_removing_an_account_restores_the_original_direction(self):
        ledger.replace_manual_accounts([{"key": "4321", "label": "储蓄卡"}])
        settings = config.load_settings()
        pipeline.import_file(self.write("a.csv", "Date,Description,Debit,Balance\n01/02/2026,Transfer to xx4321,5.00,95.00\n"
                                                 "02/02/2026,Transfer to xx4321,5.00,90.00\n"), settings)
        pipeline.refresh(settings)
        self.assertEqual(ledger.entries()[0]["direction"], "transfer")
        ledger.replace_manual_accounts([])
        pipeline.refresh(settings)
        self.assertEqual(ledger.entries()[0]["direction"], "expense")


class Categories(Isolated):
    entries = [{"direction": "expense", "group": "餐饮", "category": "三餐"}, {"direction": "expense", "group": "交通", "category": "交通出行"},
               {"direction": "expense", "group": "其他", "category": "其他支出"}, {"direction": "income", "group": "工资", "category": ""},
               {"direction": "income", "group": "其他收入", "category": ""}, {"direction": "transfer", "group": "其他", "category": ""}]

    def setUp(self):
        super().setUp()
        config.TEMPLATE_DIR.mkdir()
        (config.TEMPLATE_DIR / "templates.json").write_text(json.dumps(
            {"mine": {"name": "我的", "columns": {"A": "trade_date"}, "start_row": 2, "categories": self.entries}}, ensure_ascii=False), "utf-8")
        self.settings = config.load_settings()

    def row(self, text, direction="expense", index=1, **extra):
        return {"id": index, "date": "2026-01-01", "amount": 10.0, "direction": direction, "source_text": text,
                "description": text, "counterparty": "", "raw_category": "", "remark": "", **extra}

    def test_your_rules_come_first_and_two_level_names_resolve(self):
        ledger.replace_user_rules([{"keyword": "EXAMPLETEL", "category": "交通 / 交通出行"}, {"keyword": "COMM BANK", "category": "交通出行"}])
        rows = [self.row("EXAMPLETEL PREPAID"), self.row("CommBankapp", index=2)]
        classify.classify(rows, self.settings)
        self.assertEqual([r["category"] for r in rows], ["交通 / 交通出行"] * 2)

    def test_rule_may_turn_spending_into_a_transfer(self):
        ledger.replace_user_rules([{"keyword": "SAVER", "category": "其他"}])
        rows = [self.row("MY SAVER")]
        classify.classify(rows, self.settings)
        self.assertEqual((rows[0]["direction"], rows[0]["category"]), ("transfer", "其他"))

    def test_builtin_table_fits_your_template(self):
        rows = [self.row("肯德基"), self.row("滴滴出行", index=2), self.row("工资代发", "income", 3)]
        classify.classify(rows, self.settings)
        self.assertEqual([r["category"] for r in rows], ["餐饮 / 三餐", "交通 / 交通出行", "工资"])

    def test_substring_collisions_stay_fixed(self):
        rows = [self.row("国家电网"), self.row("手机银行转账", index=2)]
        classify.classify(rows, self.settings)
        self.assertNotIn("数码", rows[0]["category"])

    def test_english_keywords_match_whole_words(self):
        rows = [self.row("ZEPHYR PHARMACY 22 PERTH"), self.row("Target Store", index=2)]
        self.assertEqual(classify._builtin_category(rows[0]), "")
        self.assertEqual(classify._builtin_category(rows[1]), "日用百货")

    def test_unknown_stays_unsure_not_guessed(self):
        rows = [self.row("WHO KNOWS 77")]
        classify.classify(rows, self.settings)
        self.assertEqual(rows[0]["category"], classify.UNSURE)
        self.assertIn("#待确认", rows[0]["tags"])

    def test_only_choice_is_taken_without_asking(self):
        rows = [self.row("whatever", "transfer")]
        classify.classify(rows, self.settings)
        self.assertEqual(rows[0]["category"], "其他")

    def test_manual_choice_survives_and_teaches_the_same_merchant(self):
        row = self.row("WHO KNOWS 77", category="餐饮 / 三餐", cat_source="manual")
        classify.remember_manual(row, self.entries)
        rows = [row, self.row("WHO KNOWS 77", index=2)]
        classify.classify(rows, self.settings)
        self.assertEqual(rows[0]["cat_source"], "manual")
        self.assertEqual((rows[1]["category"], rows[1]["cat_source"]), ("餐饮 / 三餐", "cache"))

    def test_model_asked_once_per_merchant_then_cached(self):
        calls = []

        class Fake:
            can_chat = True

            def chat_json(self, system, user, schema, thinking=False):
                body = json.loads(user)
                calls.append((len(body["transactions"]), thinking))
                return {"items": [{"index": t["index"], "category_id": 0, "counterparty": "Noodle", "description": "午饭",
                                   "tags": []} for t in body["transactions"]]}
        rows = [self.row("KPY*Noodle Bar Sydney AU"), self.row("KPY*Noodle Bar Melbourne AU", index=2)]
        classify.classify(rows, self.settings, Fake())
        self.assertEqual(calls, [(1, False)])
        self.assertEqual({r["category"] for r in rows}, {"餐饮 / 三餐"})
        calls.clear()
        again = [self.row("KPY*Noodle Bar Perth AU", index=3)]
        classify.classify(again, self.settings, Fake())
        self.assertEqual(calls, [])
        self.assertEqual(again[0]["cat_source"], "cache")

    def test_unsure_answers_get_a_second_thinking_pass(self):
        seen = []

        class Fake:
            can_chat = True

            def chat_json(self, system, user, schema, thinking=False):
                seen.append(thinking)
                return {"items": [{"index": 0, "category_id": 0 if thinking else -1, "counterparty": "", "description": "", "tags": []}]}
        rows = [self.row("HARD ONE")]
        classify.classify(rows, self.settings, Fake())
        self.assertEqual(seen, [False, True])
        self.assertEqual(rows[0]["category"], "餐饮 / 三餐")

    def test_model_cannot_turn_spending_into_income(self):
        class Fake:
            can_chat = True

            def chat_json(self, system, user, schema, thinking=False):
                return {"items": [{"index": 0, "category_id": 3, "counterparty": "", "description": "", "tags": []}]}
        rows = [self.row("HARD ONE")]
        classify.classify(rows, self.settings, Fake())
        self.assertEqual((rows[0]["direction"], rows[0]["category"]), ("expense", classify.UNSURE))

    def test_model_failure_leaves_the_rest_intact(self):
        class Fake:
            can_chat = True

            def chat_json(self, *args, **kwargs):
                raise model.ModelError("模型没有响应")
        rows = [self.row("肯德基"), self.row("HARD ONE", index=2)]
        stats = classify.classify(rows, self.settings, Fake())
        self.assertEqual(rows[0]["category"], "餐饮 / 三餐")
        self.assertEqual(rows[1]["category"], classify.UNSURE)
        self.assertIn("模型没有响应", stats["errors"][0])

    def test_tags(self):
        rows = [self.row("Overseas Fee", amount=5000.0), self.row("NETFLIX", index=2), self.row("ATM CASH", index=3)]
        classify.classify(rows, {**self.settings, "large_amount": 1000})
        self.assertIn("#大额", rows[0]["tags"])
        self.assertIn("#跨境", rows[0]["tags"])
        self.assertIn("#订阅", rows[1]["tags"])
        self.assertIn("#大额现金", rows[2]["tags"])

    def test_handling_fee_is_not_a_subscription(self):
        self.assertNotIn("订阅", "".join(classify._tags(self.row("手续费"), self.settings)))

    def test_repeats_within_days_are_only_flagged(self):
        rows = [self.row("COFFEE", counterparty="COFFEE"), self.row("COFFEE", index=2, counterparty="COFFEE", date="2026-01-02")]
        classify.classify(rows, self.settings)
        self.assertIn("#疑似重复", rows[0]["tags"])


class TemplateExport(Isolated):
    def prepare_template(self):
        source = self.root / "记账模板.xlsx"
        template_book(source)
        found = template.inspect(source)
        columns = {c["letter"]: c["field"] for c in found["columns"] if c["field"]}
        key = template.save(source, "我的模板", found["sheet"], found["start_row"], columns, template.BUILTIN["categories"])
        return config.save_settings({"template": key})

    def test_headers_are_recognised(self):
        source = self.root / "t.xlsx"
        template_book(source)
        found = template.inspect(source)
        self.assertEqual(found["start_row"], 2)
        self.assertEqual({c["letter"]: c["field"] for c in found["columns"]},
                         {"A": "date_cn_time", "B": "direction_word", "C": "amount", "D": "category_group", "E": "category",
                          "F": "account", "G": "counter_account", "H": "description", "I": "currency", "J": "tags"})

    def test_chinese_datetime_sample_selects_that_format(self):
        source = self.root / "t.xlsx"
        template_book(source)
        book = load_workbook(source)
        book.active["A1"] = "时间"
        book.save(source)
        self.assertEqual(template.inspect(source)["columns"][0]["field"], "date_cn_time")

    def test_header_row_can_be_anywhere(self):
        book = Workbook()
        book.active.append(["我的账本 2026"])
        book.active.append([])
        book.active.append(["日期", "金额", "分类"])
        path = self.root / "t.xlsx"
        book.save(path)
        self.assertEqual(template.inspect(path)["start_row"], 4)

    def test_export_fills_the_template_and_clears_samples(self):
        settings = self.prepare_template()
        pipeline.import_file(self.write("a.csv", BANK), settings)
        pipeline.refresh(settings)
        rows = ledger.entries()
        rows[-1].update(excluded=True)
        out = template.export(rows, self.root / "out" / "账单.xlsx", settings)
        sheet = load_workbook(out).active
        self.assertEqual(sheet.max_row, 3)
        self.assertEqual(sheet["B2"].value, "支出")
        self.assertEqual(sheet["C2"].value, 35.0)
        self.assertEqual(sheet["C2"].number_format, "#,##0.00")   # 模板格式保留
        self.assertEqual(sheet["A2"].value, "2025年08月02日 00:00:00")
        self.assertNotIn("#标签1", [c.value for c in sheet[2]])
        self.assertEqual(sheet["I2"].value, "CNY")

    def test_workbook_totals_match_the_ledger(self):
        settings = self.prepare_template()
        pipeline.import_file(self.write("a.csv", BANK), settings)
        pipeline.import_file(self.write("b.csv", WECHAT), settings)
        pipeline.refresh(settings)
        rows = ledger.entries()
        sheet = load_workbook(template.export(rows, self.root / "x.xlsx", settings)).active
        exported = sum(sheet.cell(i, 3).value for i in range(2, sheet.max_row + 1))
        self.assertEqual(round(exported, 2), round(sum(r["amount"] for r in rows if not r.get("excluded")), 2))

    def test_builtin_template_works_without_any_setup(self):
        settings = config.load_settings()
        pipeline.import_file(self.write("a.csv", BANK), settings)
        pipeline.refresh(settings)
        sheet = load_workbook(template.export(ledger.entries(), self.root / "x.xlsx", settings)).active
        self.assertEqual(sheet["A1"].value, "日期")
        self.assertEqual(sheet.max_row, 4)
        self.assertEqual(sheet["A2"].number_format, "yyyy-mm-dd")  # 不会显示成 46260

    def test_chinese_date_fields(self):
        row = {"date": "2026-02-03", "time": "2026-02-03 08:09:10", "direction": "transfer", "amount": 5.0}
        self.assertEqual(template.value(row, "date_cn_time", {}), "2026年02月03日 08:09:10")
        self.assertEqual(template.value(row, "direction_word", {}), "转账")
        self.assertEqual(template.value(row, "direction_label", {}), "不计收支")
        self.assertEqual(template.value({**row, "direction": "expense"}, "signed_amount", {}), -5.0)

    def test_category_validation(self):
        with self.assertRaisesRegex(ValueError, "方向"):
            template.validate_categories([{"direction": "expense", "group": "餐饮"}])
        with self.assertRaisesRegex(ValueError, "重复"):
            template.validate_categories([{"direction": "expense", "group": "餐饮"}] * 2)


class ModelClient(Isolated):
    def test_not_a_gguf(self):
        path = self.root / "x.gguf"
        path.write_bytes(b"nope")
        with self.assertRaisesRegex(model.ModelError, "不是有效"):
            model._gguf(str(path), "文字模型")

    def test_json_is_found_inside_chatter(self):
        self.assertEqual(model._extract_json('<think>hm</think>好的：\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertIsNone(model._extract_json("没有 JSON"))

    def test_truncated_or_empty_ocr_stops_the_import(self):
        client = model.Model({})
        with patch("billbill.model._data_url", return_value="data:,"):
            for content, finish in (("", "stop"), ("| a |", "length")):
                reply = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
                with patch.object(client, "_post", return_value=reply), self.assertRaises(model.ModelError):
                    client.ocr(self.root / "x.png")

    def test_model_is_optional(self):
        settings = config.load_settings()
        self.assertFalse(settings["llm_enable"])
        self.assertIsNone(pipeline._model(settings))
