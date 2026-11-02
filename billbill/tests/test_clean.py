"""说明拆分。虚构商户。"""
from helpers import Isolated
from billbill import clean


class CardMerchant(Isolated):
    def test_split_from_city_state_country(self):
        cases = {"MAPLEMART 1234 SYDNEY AU": "MAPLEMART", "RIVERMART 5678 PERTH WA AUS": "RIVERMART",
                 "EXAMPLETEL PREPAID MELBOURNE AUS": "EXAMPLETEL PREPAID", "ACME SALES PTY LTD SYDNEY AUS": "ACME SALES PTY LTD",
                 "SQ *CORNER CAFE South Brisban AU": "CORNER CAFE", "KPY*Noodle Bar Sydney AU": "Noodle Bar",
                 "UBER *TRIP HELP.UBER.C Sydney AU AUS": "UBER *TRIP",
                 "EXAMPLE FITNESS CENTRE BONDI JUNCTIO AU": "EXAMPLE FITNESS CENTRE",
                 "DEMO STORE PTY LTD St Lucia AU": "DEMO STORE PTY LTD",
                 "SAMPLE BURGERS Brisbane Eagle Farm AU": "SAMPLE BURGERS Brisbane",
                 "FUEL STOP NORTHSIDE 22541 F NORTHSIDE AUS": "FUEL STOP NORTHSIDE",
                 "STREAMCO LIMITED AU AUS": "STREAMCO LIMITED", "EXAMPLE PAY**6055* Melbourne VI AUS": "EXAMPLE PAY",
                 "Consumption Alipay Shanghai CHN": "Alipay"}
        self.assertEqual({t: clean.card_merchant(t) for t, w in cases.items() if clean.card_merchant(t) != w}, {})

    def test_only_purchases_with_a_country_anchor(self):
        for text in ("CASH DEPOSIT EXAMPLE ATM SYDNEY NSW AUS", "Transfer To J SMITH Pay ID Phone", "Overseas Transaction Fee",
                     "Direct Debit 100200 ACMEREALTY Manag", "EXAMPLETELPREPAIDMELBOURNEAUS",
                     "Pre-Authorization Completed Ebay USA USD"):
            with self.subTest(text):
                self.assertEqual(clean.card_merchant(text), "")


class Prepare(Isolated):
    def row(self, text, **extra):
        record = {"description": text, "counterparty": "", "account": "", "counter_account": "", **extra}
        clean.prepare([record], "Bank", "Bank ···9999", "AUD")
        return record

    def test_merchant_found_description_kept(self):
        record = self.row("MAPLEMART 1234 SYDNEY AU")
        self.assertEqual((record["counterparty"], record["description"]), ("MAPLEMART", "MAPLEMART 1234 SYDNEY AU"))

    def test_own_transfer_has_no_counterparty(self):
        record = self.row("Transfer to xx 4321 Comm Bank app")
        self.assertEqual(record["counterparty"], "")
        self.assertEqual(record["counter_account"], "···4321")

    def test_transfer_to_a_person(self):
        self.assertEqual(self.row("Transfer To A SAMPLE Comm Bank app")["counterparty"], "A SAMPLE")

    def test_glued_text_is_spaced(self):
        self.assertIn("Transferfrom", self.row("Transferfromxx4321CommBankapp")["source_text"].replace(" ", "")[:12])

    def test_card_number_goes_to_the_account(self):
        self.assertEqual(self.row("MAPLEMART SYDNEY AU Card xx9753")["account"], "Bank ···9753")

    def test_noise_is_stripped(self):
        self.assertEqual(self.row("SHOP ValueDate 15/01/2026 Rcpt 300400 12345678901")["source_text"], "SHOP")

    def test_bank_fee_has_no_counterparty(self):
        record = self.row("Charging OVS CUP POS TRANS")
        self.assertEqual((record["counterparty"], record["description"]), ("", "境外交易手续费"))

    def test_statement_account_and_currency_fill_in(self):
        record = self.row("Coffee")
        self.assertEqual((record["account"], record["currency"]), ("Bank ···9999", "AUD"))
