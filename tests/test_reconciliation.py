import unittest

import pandas as pd

from quantfolio.ledger import Ledger
from quantfolio.validation import reconcile


class IndependentAccountingTests(unittest.TestCase):
    def fixture(self, multiplier=1.0, kind="stock", rounding=None):
        s = "sh600001" if kind == "stock" else "sh510001"
        meta = {s: {"kind": kind, "code": s[2:]}}
        book = Ledger(meta)
        curve = []
        last = {}
        dates = pd.bdate_range("2026-01-05", periods=5)
        a = {
            "id": "test",
            "symbol": s,
            "record_date": str(dates[0].date()),
            "ex_date": str(dates[1].date()),
            "share_list_date": str(dates[3].date()),
            "cash_pay_date": str(dates[2].date()),
            "cash_per_share": 0.1,
            "share_multiplier": multiplier,
            "fractional_rounding": rounding,
        }
        for i, d in enumerate(dates):
            prior = book.nav(last)
            book.apply_actions(d, [a] if i == 1 else [])
            px = 10.0 if i == 0 else 9.9 / multiplier
            if i == 0:
                book.buy(d, s, 100, 10.0, d - pd.Timedelta(days=1))
            if i == 4:
                book.sell(d, s, book.quantity(s), round(px, 3 if kind == "etf" else 2), dates[3])
            prices = {s: px} if book.quantity(s) else {}
            values = book.values(prices)
            book.snapshot_record_dates(d, [a] if i == 0 else [])
            curve.append(
                {
                    "date": str(d.date()),
                    "prior_close_nav": prior,
                    "cash": book.cash / 100,
                    "receivables": sum(x["cents"] for x in book.receivables) / 100,
                    "holdings_value": sum(values.values()),
                    "nav": book.nav(prices),
                    "positions": {
                        s: {
                            "quantity": book.quantity(s),
                            "available": book.available(s, d),
                            "close": px,
                            "value": values[s],
                        }
                    }
                    if values
                    else {},
                }
            )
            last = prices
        detail = {"curve": curve, "trades": book.trades, "events": book.events}
        summary = {"return": curve[-1]["nav"] / 1e6 - 1, "fees": sum(x["fee"] for x in book.trades)}
        return detail, summary, meta, [a]

    def test_cash_receivable_and_payment(self):
        self.assertEqual(reconcile(*self.fixture())["status"], "PASS")

    def test_bonus_listing_restrictions(self):
        self.assertEqual(reconcile(*self.fixture(1.5))["status"], "PASS")

    def test_reverse_split_rounding(self):
        self.assertEqual(
            reconcile(*self.fixture(0.333333, kind="etf", rounding="CEILING"))["status"], "PASS"
        )

    def test_corrupt_entitlement_rejected(self):
        d, s, m, a = self.fixture(1.5)
        d["events"][0]["entitled_quantity"] += 100
        self.assertEqual(reconcile(d, s, m, a)["status"], "FAIL")

    def test_false_same_day_availability_rejected(self):
        d, s, m, a = self.fixture()
        next(iter(d["curve"][0]["positions"].values()))["available"] = 100
        self.assertEqual(reconcile(d, s, m, a)["status"], "FAIL")

    def test_early_cash_payment_rejected_even_with_balanced_accounts(self):
        detail, summary, meta, actions = self.fixture()
        actions[0]["cash_available_date"] = detail["curve"][3]["date"]
        result = reconcile(detail, summary, meta, actions)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("cash availability" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
