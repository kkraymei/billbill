"""表格 -> 交易：列怎么认、方向怎么定、对账能不能发现错。"""
from helpers import Isolated
from billbill import statement as st
from billbill.documents import Table
from billbill.statement import DateStyle, parse_date, parse_money, parse_table


def parse(rows, name="bill.csv"):
    return parse_table(Table(rows=rows), name)


def summary(result):
    return [(r["date"], r["amount"], r["direction"]) for r in result.rows]


class Dates(Isolated):
    def test_written_forms(self):
        style = DateStyle(year=2026)
        for text in ("02Feb", "02 Feb", "02\tFeb", "02 Feb", "02 Feb 2026", "02Feb2026", "2-Feb", "02-Feb-26",
                     "02-Feb-2026", "Feb 2", "2026-02-02", "2026/2/2", "2026.02.02", "2026年2月2日", "2月2日",
                     "20260202", "02/02/2026", "02/02/26"):
            with self.subTest(text):
                self.assertEqual(parse_date(text, style)[1], "2026-02-02")

    def test_time_is_kept(self):
        self.assertEqual(parse_date("2026-02-02 08:30:15")[0], "2026-02-02 08:30:15")
        self.assertEqual(parse_date("2 Feb 2026 08:30", DateStyle())[0], "2026-02-02 08:30:00")

    def test_six_digits_never_become_year_2608(self):
        self.assertEqual(parse_date("260826")[1], "2026-08-26")
        self.assertEqual(parse_date("010226", DateStyle(compact="ddmmyy"))[1], "2026-02-01")
        self.assertEqual(parse_date("260826", DateStyle(compact="mmddyy"))[1], "")

    def test_absurd_dates_are_rejected_not_guessed(self):
        self.assertEqual(parse_date("09May0839")[1], "")
        self.assertEqual(parse_date("31 Feb 2026")[1], "")

    def test_merchant_glued_to_month_is_not_a_date(self):
        self.assertEqual(parse_date("09MayMAPLEMART0839", DateStyle(year=2026))[1], "")

    def test_day_month_order_comes_from_the_file(self):
        self.assertTrue(st._day_first(["15/01/2026", "03/02/2026"]))
        self.assertFalse(st._day_first(["01/15/2026", "02/03/2026"]))
        # 都不超过 12 时看哪种读法日期更集中：一份账单不会跨好几个月。
        self.assertTrue(st._day_first(["01/02/2026", "03/02/2026", "05/02/2026"]))
        self.assertFalse(st._day_first(["02/01/2026", "02/03/2026", "02/05/2026"]))

    def test_year_rolls_over_inside_a_statement(self):
        rows = [["Statement Period 01 Dec 2025 - 15 Jan 2026"], ["Date", "Details", "Debit", "Balance"],
                ["31 Dec", "A", "1.00", "9.00"], ["02 Jan", "B", "1.00", "8.00"]]
        self.assertEqual([r["date"] for r in parse(rows).rows], ["2025-12-31", "2026-01-02"])

    def test_barcode_digits_are_not_a_year(self):
        self.assertEqual(st._year_of([["1301013020122210"], ["Period 2025"]], 2), 2025)

    def test_year_comes_from_the_period_not_the_first_number(self):
        self.assertEqual(st._year_of([["Issued 05 Jul 2026"], ["Statement Period 01 Jan 2025 - 30 Jun 2026"]], 2), 2025)


class Money(Isolated):
    def test_forms(self):
        for text, want in (("1,234.50", (1234.5, 1, "")), ("-12.00", (12.0, -1, "")), ("+$12.00", (12.0, 1, "")),
                           ("-$1,234.56", (1234.56, -1, "")), ("(12.00)", (12.0, -1, "")), ("12.00-", (12.0, -1, "")),
                           ("¥35.00", (35.0, 1, "")), ("￥35.00", (35.0, 1, "")), ("35.00元", (35.0, 1, "")),
                           ("42.80CR", (42.8, 1, "CR")), ("42.80 DR", (42.8, 1, "DR")), ("RMB 100", (100.0, 1, "")),
                           ("AUD 12.00", (12.0, 1, "")), ("1.234,56", (1234.56, 1, "")), ("１２３.００", (123.0, 1, ""))):
            with self.subTest(text):
                self.assertEqual(parse_money(text), want)

    def test_text_with_digits_is_not_money(self):
        for text in ("Ref 12", "Card 1234 used", "2026-01-02", ""):
            self.assertIsNone(parse_money(text))


class ColumnsByHeader(Isolated):
    def test_account_balance_is_not_taken_as_account(self):
        columns = st.map_by_header(["交易日期", "摘要", "交易金额", "账户余额"])
        self.assertEqual(columns["balance"], 3)
        self.assertNotIn("account", columns)

    def test_income_expense_columns_drop_the_single_amount(self):
        self.assertNotIn("amount", st.map_by_header(["日期", "金额", "收入", "支出", "摘要"]))

    def test_cover_summary_is_not_the_header(self):
        rows = [["BSB", "Account No. Name"], ["Overview of this statement"],
                ["Opening balance", "- Total debits", "+", "Total credits", "=", "Closing balance"],
                ["$100.00", "$20.00", "", "$30.00", "", "$110.00"]]
        rows += [["Disputing a transaction", "please call the number above"]] * 20
        rows += [["Date", "Transaction", "Description", "Debits", "Credits", "Balance"],
                 ["12 Sep 2026", "Funds Transfer", "From linked account", "", "30.00", "130.00 CR"],
                 ["13 Sep 2026", "Card Purchase", "SOME SHOP SYDNEY AU", "20.00", "", "110.00 CR"]]
        result = parse(rows)
        self.assertEqual(summary(result), [("2026-09-12", 30.0, "income"), ("2026-09-13", 20.0, "expense")])
        # 没抢到字段的文字列（商户名就在这里）要并进说明
        self.assertEqual(result.rows[1]["description"], "Card Purchase SOME SHOP SYDNEY AU")

    def test_prose_above_the_header_stays_out_of_it(self):
        rows = [["Your Statement"], ["appears", "on the example app)."],
                ["Date", "Transaction", "Debit", "Credit", "Balance"], ["01 Feb 2026", "Coffee", "5.00", "", "95.00"]]
        self.assertEqual(st.find_header(rows), (2, 1))

    def test_first_header_wins_on_multi_page_statements(self):
        """选中后面某页表头时，前几页的交易全丢，而余额链照样成立。"""
        head = ["Date", "Transaction", "Debit", "Credit", "Balance"]
        rows = [head] + [[f"0{d} Feb 2026", "Shop", "1.00", "", f"{100 - d}.00"] for d in range(1, 9)]
        rows += [["Account Number 12 3456"], head + [""]]
        rows += [[f"1{d} Feb 2026", "Shop", "1.00", "", f"{80 - d}.00"] for d in range(1, 9)]
        self.assertEqual(st.find_header(rows)[0], 0)

    def test_transactions_above_the_header_are_kept(self):
        rows = [["01 Feb 2026", "Shop", "5.00", "95.00"], ["02 Feb 2026", "Shop", "5.00", "90.00"],
                ["Date", "Details", "Debit", "Balance"], ["03 Feb 2026", "Shop", "5.00", "85.00"]]
        self.assertEqual(len(parse(rows).rows), 3)

    def test_multi_line_bilingual_header(self):
        rows = [["Tr.D.", "Val.D.", "Tran. Amount", "Balance"], ["交易日", "起息日", "交易金额", "余额"],
                ["YYMMDD", "YYMMDD", "", ""], ["260826", "260826", "-12.34", "487.66"],
                ["260827", "260827", "100.00", "587.66"]]
        self.assertEqual(summary(parse(rows)), [("2026-08-26", 12.34, "expense"), ("2026-08-27", 100.0, "income")])


class ColumnsWithoutHeaders(Isolated):
    def test_no_header_at_all(self):
        rows = [["2026-01-02", "Coffee", "-12.00", "88.00"], ["2026-01-03", "Salary", "100.00", "188.00"]]
        self.assertEqual(summary(parse(rows)), [("2026-01-02", 12.0, "expense"), ("2026-01-03", 100.0, "income")])

    def test_header_in_another_language(self):
        rows = [["Fecha", "Concepto", "Importe", "Saldo"], ["02/01/2026", "Cafe", "-12.00", "88.00"],
                ["03/01/2026", "Nomina", "100.00", "188.00"]]
        result = parse(rows)
        self.assertEqual(result.check, "verified")
        self.assertEqual(summary(result), [("2026-01-02", 12.0, "expense"), ("2026-01-03", 100.0, "income")])

    def test_split_columns_without_names(self):
        rows = [["Fecha", "Concepto", "Salida", "Entrada", "Saldo"], ["02/01/2026", "Cafe", "12.00", "", "88.00"],
                ["03/01/2026", "Nomina", "", "100.00", "188.00"]]
        self.assertEqual([r["direction"] for r in parse(rows).rows], ["expense", "income"])

    def test_gross_fee_net_picks_the_net(self):
        """三列都满时靠 a + b = c 认实际发生额，不能把手续费当金额。"""
        rows = [["Date", "Merchant", "Gross", "Fee", "Net"], ["2025-08-04", "Steam", "-68.00", "0.00", "-68.00"],
                ["2025-08-09", "Client", "1200.00", "-36.00", "1164.00"]]
        want = [("2025-08-04", 68.0, "expense"), ("2025-08-09", 1164.0, "income")]
        self.assertEqual(summary(parse(rows)), want)
        rows[0] = ["Fecha", "Tienda", "Bruto", "Cuota", "Neto"]
        self.assertEqual(summary(parse(rows)), want)


class Arithmetic(Isolated):
    def test_card_balance_rising_with_spending_keeps_the_named_columns(self):
        """信用卡的余额是欠款。这和收支两列标反长得一样，表头写明时绝不能翻。"""
        rows = [["Date", "Details", "Debit", "Credit", "Balance"], ["01 Feb 2026", "Shop", "30.00", "", "130.00"],
                ["02 Feb 2026", "Shop", "20.00", "", "150.00"], ["03 Feb 2026", "Repayment", "", "50.00", "100.00"]]
        result = parse(rows)
        self.assertEqual([r["direction"] for r in result.rows], ["expense", "expense", "income"])
        self.assertEqual(result.check, "verified")

    def test_bank_named_in_a_transaction_is_not_the_channel(self):
        rows = [["XX村镇银行个人账户历史明细"], ["记账日", "摘要说明", "借方发生额", "账户余额", "对手户名"],
                ["20250805", "代发工资", "15.00", "85.00", "招商银行信用卡"]]
        self.assertEqual(parse(rows).label, "村镇银行")

    def test_balance_decides_direction_when_amounts_are_unsigned(self):
        rows = [["Date", "Details", "Amount", "Balance"], ["01 Feb 2026", "A", "5.00", "95.00"],
                ["02 Feb 2026", "B", "10.00", "105.00"], ["03 Feb 2026", "C", "5.00", "100.00"]]
        self.assertEqual([r["direction"] for r in parse(rows).rows][1:], ["income", "expense"])

    def test_newest_first_statement(self):
        rows = [["Date", "Details", "Amount", "Balance"], ["03 Feb 2026", "C", "5.00", "100.00"],
                ["02 Feb 2026", "B", "10.00", "105.00"], ["01 Feb 2026", "A", "5.00", "95.00"]]
        result = parse(rows)
        self.assertEqual(result.check, "verified")
        self.assertEqual([r["direction"] for r in result.rows][:2], ["expense", "income"])

    def test_balance_printed_once_per_day(self):
        rows = [["Date", "Details", "Debit", "Credit", "Balance"], ["01 Feb 2026", "A", "5.00", "", ""],
                ["01 Feb 2026", "B", "5.00", "", "90.00"], ["02 Feb 2026", "C", "", "10.00", "100.00"]]
        self.assertEqual(parse(rows).check, "verified")

    def test_a_row_without_a_date_is_a_same_day_transaction(self):
        """旧版把它当说明吞掉了，金额跟着丢失。"""
        rows = [["Date", "Details", "Debit", "Credit", "Balance"], ["01 Feb 2026", "Interest", "", "10.00", "110.00"],
                ["", "Tax withheld", "2.00", "", "108.00"]]
        result = parse(rows)
        self.assertEqual(summary(result), [("2026-02-01", 10.0, "income"), ("2026-02-01", 2.0, "expense")])
        self.assertEqual(result.check, "verified")

    def test_a_wrong_number_is_reported(self):
        rows = [["Date", "Details", "Debit", "Balance"], ["01 Feb 2026", "A", "5.00", "95.00"],
                ["02 Feb 2026", "B", "5.00", "80.00"], ["03 Feb 2026", "C", "5.00", "75.00"]]
        result = parse(rows)
        self.assertEqual(result.check, "mismatch")
        self.assertTrue(result.rows[1]["_broken"])

    def test_lost_rows_at_the_start_are_caught_by_the_printed_opening(self):
        """只看相邻两笔查不出开头漏读，账单印的期初余额能。"""
        rows = [["Opening balance: 500.00"], ["Date", "Details", "Debit", "Balance"],
                ["05 Feb 2026", "A", "5.00", "95.00"], ["06 Feb 2026", "B", "5.00", "90.00"]]
        self.assertEqual(parse(rows).check, "mismatch")

    def test_lost_rows_at_the_end_are_caught_by_the_printed_closing(self):
        rows = [["Date", "Details", "Debit", "Balance"], ["05 Feb 2026", "A", "5.00", "95.00"],
                ["06 Feb 2026", "B", "5.00", "90.00"], ["Closing balance: 10.00"]]
        self.assertEqual(parse(rows).check, "mismatch")


class Direction(Isolated):
    def test_negative_debit_column_is_still_an_expense(self):
        rows = [["Date", "Details", "Debit", "Credit", "Balance"], ["01 Feb 2026", "A", "-5.00", "", "95.00"],
                ["02 Feb 2026", "B", "", "+10.00", "105.00"]]
        self.assertEqual([r["direction"] for r in parse(rows).rows], ["expense", "income"])

    def test_negative_in_the_income_column_is_a_reversal(self):
        rows = [["日期", "摘要", "收入", "支出"], ["2026-02-01", "冲正", "-5.00", ""]]
        self.assertEqual(parse(rows).rows[0]["direction"], "expense")

    def test_cr_dr_marks(self):
        rows = [["Date", "Details", "Amount"], ["01 Feb 2026", "A", "5.00 CR"], ["02 Feb 2026", "B", "5.00 DR"]]
        self.assertEqual([r["direction"] for r in parse(rows).rows], ["income", "expense"])

    def test_credit_card_positive_means_spending(self):
        rows = [["招商银行信用卡对账单"], ["交易日", "记账日", "交易摘要", "人民币金额"],
                ["2025-08-03", "2025-08-04", "超市", "35.00"], ["2025-08-26", "2025-08-27", "退款", "-10.00"]]
        self.assertEqual([r["direction"] for r in parse(rows).rows], ["expense", "income"])

    def test_direction_column_words(self):
        rows = [["交易时间", "交易对方", "收/支", "金额"], ["2026-01-01 08:00:00", "A", "支出", "1.00"],
                ["2026-01-02 08:00:00", "B", "收入", "2.00"], ["2026-01-03 08:00:00", "C", "不计收支", "3.00"]]
        self.assertEqual([r["direction"] for r in parse(rows).rows], ["expense", "income", "transfer"])

    def test_unknown_direction_is_flagged_not_guessed_silently(self):
        result = parse([["Date", "Details", "Amount"], ["01 Feb 2026", "A", "5.00"]])
        self.assertTrue(result.rows[0]["_unsure"])
        self.assertIn("看不出收支方向", result.warnings[0])

    def test_closed_orders_are_skipped(self):
        rows = [["交易时间", "交易对方", "收/支", "金额", "交易状态"], ["2026-01-01 08:00:00", "A", "支出", "1.00", "交易成功"],
                ["2026-01-02 08:00:00", "B", "支出", "2.00", "交易关闭"]]
        self.assertEqual(len(parse(rows).rows), 1)


class Structure(Isolated):
    head = ["Date", "Transaction", "Debit", "Credit", "Balance"]

    def test_multi_page_header_page_number_and_footer(self):
        rows = [["Statement Period 01 Feb 2026 - 28 Feb 2026"], self.head, ["01 Feb", "OPENING BALANCE", "", "", "100.00 CR"],
                ["02 Feb", "Coffee", "10.00", "", "90.00 CR"], ["Page 1 of 2"], self.head,
                ["03 Feb", "Lunch", "20.00", "", "70.00 CR"], ["28 Feb", "CLOSING BALANCE", "", "", "70.00 CR"],
                ["31 Mar", "Fee summary", "3.00", "", "0.00"], ["ABN 12 345 678 901 Disclaimer text"]]
        result = parse(rows)
        self.assertEqual(summary(result), [("2026-02-02", 10.0, "expense"), ("2026-02-03", 20.0, "expense")])
        self.assertEqual(result.check, "verified")

    def test_next_statement_after_closing_balance_continues(self):
        rows = [["Statement Period 2025"], self.head, ["31 Dec", "Coffee", "10.00", "", "90.00 CR"],
                ["31 Dec", "CLOSING BALANCE", "", "", "90.00 CR"], ["31 Mar", "Fee summary", "3.00", "", "0.00"], self.head,
                ["01 Jan", "IKEA", "20.00", "", "70.00 CR"]]
        self.assertEqual([r["amount"] for r in parse(rows).rows], [10.0, 20.0])

    def test_amount_on_a_later_line(self):
        rows = [self.head, ["02 Feb 2026", "Transfer From", "", "", ""], ["", "Someone Name", "", "30.00", "130.00"],
                ["03 Feb 2026", "Shop", "10.00", "", "120.00"]]
        result = parse(rows)
        self.assertEqual(result.rows[0]["amount"], 30.0)
        self.assertIn("Someone Name", result.rows[0]["description"])

    def test_continuation_lines_join_the_description(self):
        rows = [self.head, ["02 Feb 2026", "Shop", "10.00", "", "90.00"], ["", "Second line", "", "", ""],
                ["03 Feb 2026", "Other", "10.00", "", "80.00"]]
        self.assertEqual(parse(rows).rows[0]["description"], "Shop Second line")

    def test_date_glued_to_text(self):
        rows = [["Statement Period 2026"], ["Date", "Details", "Debit", "Balance"], ["31MayTransferfrom xx4321", "", "5.00", "95.00"]]
        result = parse(rows)
        self.assertEqual(result.rows[0]["date"], "2026-05-31")
        self.assertIn("Transferfrom", result.rows[0]["description"])

    def test_value_date_stripped_from_description(self):
        rows = [["Date", "Details", "Amount"], ["260318", "260318 ///Consumption", "-5.00"]]
        self.assertEqual(parse(rows).rows[0]["description"], "Consumption")

    def test_text_without_transactions_is_refused(self):
        with self.assertRaises(st.Unrecognized):
            parse([["这只是一段说明文字"], ["不是账单"]])

    def test_header_found_but_nothing_parsed_is_an_error_not_an_empty_statement(self):
        with self.assertRaises(st.Unrecognized):
            parse([["Date", "Details", "Debit", "Balance"], ["sometime", "A", "n/a", "n/a"]])


class EmptyPeriod(Isolated):
    def test_zero_totals(self):
        self.assertTrue(st.empty_period([["Total credits", "$0.00"], ["Total debits", "$ 0.00"]]))
        self.assertTrue(st.empty_period([["Total credits"], ["$0.00"], ["Total debits"], ["$0.00"]]))

    def test_one_sided_or_nonzero_totals_do_not_count(self):
        self.assertFalse(st.empty_period([["Total credits", "$0.00"], ["Total debits", "$ 12.30"]]))
        self.assertFalse(st.empty_period([["Total credits", "$0.00"]]))

    def test_statement_saying_so(self):
        result = parse([["Example Bank"], ["Date", "Details", "Debit", "Balance"], ["No transactions in this period."]])
        self.assertEqual(result.check, "empty")

    def test_equal_opening_and_closing(self):
        rows = [["Date", "Transaction", "Debit", "Credit", "Balance"], ["01 Feb 2026", "OPENING BALANCE 100.00", "", "", ""],
                ["28 Feb 2026", "CLOSING BALANCE 100.00", "", "", ""]]
        self.assertEqual(parse(rows).check, "empty")


class Header(Isolated):
    def account(self, rows):
        return st._account_of(Table(rows=rows), len(rows))

    def test_spaced_account_number(self):
        self.assertEqual(self.account([["Account", "Number 06", "2000 12345678"]]), "···5678")

    def test_date_after_the_number_is_not_swallowed(self):
        self.assertEqual(self.account([["Account No. 12 3456 7890 01/02/2026"]]), "···7890")

    def test_short_numbers_are_not_accounts(self):
        self.assertEqual(self.account([["Account No: 12"]]), "")

    def test_value_under_the_label(self):
        self.assertEqual(self.account([["BSB", "Account Number", "Opening Balance"], ["062000", "12345678", "$5.00"]]), "···5678")

    def test_labels_match_whole_words(self):
        self.assertEqual(st.guess_label("eGift Card Purchase JB HI-FI"), "")    # 不是大通银行
        self.assertEqual(st.guess_label("SAVINGS ACCOUNT"), "")                  # 不是 ING
        self.assertEqual(st.guess_label("ANZ Plus Everyday"), "澳新银行")

    def test_holder_name_stays_out_of_the_label(self):
        self.assertEqual(st.guess_label(["Someone Example", "Example Bank Limited"]), "Example Bank")

    def test_issuer_in_the_footer(self):
        rows = [["Date", "Description", "Debits", "Credits", "Balance"], ["02 Dec 2025", "Deposit", "", "3,000.00", "3,000.00"],
                ["ubank, part of National Australia Bank Limited ABN 12 345 678 901"]]
        self.assertEqual(parse(rows, "SaveAccount.pdf").label, "ubank")

    def test_currency(self):
        self.assertEqual(st._currency_of([["币种：usd"]], 1), "USD")
        self.assertEqual(st._currency_of([["x"], ["commbank.com.au"]], 0), "AUD")


class ModelFallback(Isolated):
    def test_model_only_says_which_column_and_is_remembered(self):
        from billbill import ledger

        class Fake:
            calls = 0

            def map_columns(self, preview, fields):
                Fake.calls += 1
                return {"date": 0, "description": 1, "amount": 2}
        rows = [["一", "二", "三"], ["2026.1.2", "咖啡", "12"]]
        self.assertEqual(parse_table(Table(rows=rows), "x.csv", Fake(), ledger).rows[0]["amount"], 12.0)
        parse_table(Table(rows=rows), "x.csv", Fake(), ledger)
        self.assertEqual(Fake.calls, 1)
