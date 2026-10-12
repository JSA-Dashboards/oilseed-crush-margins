"""python -m unittest tests/test_canola_seasonal.py"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import canola_seasonal as cs  # noqa: E402


def series(prices, start="2020-01-01"):
    return pd.Series(prices, index=pd.bdate_range(start, periods=len(prices)), dtype=float)


class SeasonalTests(unittest.TestCase):
    def test_season_labels(self):
        self.assertEqual(cs.season_label(pd.Timestamp("2025-03-05"), 1), "2025")
        self.assertEqual(cs.season_label(pd.Timestamp("2025-09-05"), 8), "2025/26")
        self.assertEqual(cs.season_label(pd.Timestamp("2026-03-05"), 8), "2025/26")
        self.assertEqual(cs.season_label(pd.Timestamp("2025-07-31"), 8), "2024/25")

    def test_prepare_puts_every_season_on_one_reference_calendar(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-02", "2025-01-02", "2025-08-04"]))
        cal = cs.prepare(s, 1)
        self.assertEqual(list(cal.plot_date.dt.strftime("%m-%d")), ["01-02", "01-02", "08-04"])
        crop = cs.prepare(s, 8)
        self.assertEqual(list(crop.season), ["2023/24", "2024/25", "2025/26"])
        self.assertEqual(crop.plot_date.iloc[-1], pd.Timestamp("2000-08-04"))

    def test_roll_day_is_first_trading_day_of_a_delivery_month(self):
        idx = pd.bdate_range("2026-10-26", "2026-11-10")
        self.assertEqual(list(cs.roll_days(idx).strftime("%Y-%m-%d")), ["2026-11-02"])

    def test_monthly_tendency_ignores_the_roll_step(self):
        # flat at 100, a +40 step on the roll day (2021-03-01) and flat after: no real move in March
        idx = pd.bdate_range("2021-01-04", "2021-04-30")
        p = pd.Series(100.0, index=idx)
        p[p.index >= "2021-03-01"] = 140.0
        t = cs.monthly_tendency(p)
        march = t[t.month == 3].iloc[0]
        self.assertAlmostEqual(march.avg_pct, 0.0, places=6)

    def test_prior_band_needs_enough_seasons(self):
        g = pd.DataFrame({"2021": [1, 2, 3], "2022": [2, 3, 4], "2023": [3, 4, 5], "2024": [9, 9, 9]})
        b = cs.prior_band(g, ["2021", "2022", "2023"])
        self.assertEqual(list(b["median"]), [2.0, 3.0, 4.0])
        self.assertTrue(cs.prior_band(g, ["2021", "2022"]).empty)

    def test_position_now_percentile(self):
        rows = []
        for yr, price in ((2021, 100.0), (2022, 200.0), (2023, 300.0), (2024, 250.0)):
            rows.append((pd.Timestamp(f"{yr}-06-15"), price))
        s = pd.Series([r[1] for r in rows], index=[r[0] for r in rows])
        d = cs.prepare(s, 1)
        grid = cs.season_grid(d)
        pos = cs.position_now(d, grid, "2024", ["2021", "2022", "2023"])
        self.assertAlmostEqual(pos["percentile"], 2 / 3 * 100, places=6)       # 250 is above 100 and 200, below 300


if __name__ == "__main__":
    unittest.main()
