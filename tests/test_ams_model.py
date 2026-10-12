"""python -m unittest tests/test_ams_model.py"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ams_model as am  # noqa: E402


def feed_rows():
    rows = []
    for wk, meal_lo, meal_avg, oil_lo, oil_avg in (("2026-10-05", 380.0, 390.0, 67.0, 70.0), ("2026-10-12", 382.0, 392.0, 68.0, 71.0)):
        rows += [
            dict(report_date=wk, commodity="Soybean Meal", trade_loc="Illinois", trans_mode="Truck", price_min=meal_lo, avg_price=meal_avg),
            dict(report_date=wk, commodity="Soybean Meal", trade_loc="Illinois", trans_mode="Rail", price_min=meal_lo + 5, avg_price=meal_avg + 5),
            dict(report_date=wk, commodity="Soybean Oil", trade_loc="Illinois", trans_mode="Truck/Rail", price_min=oil_lo, avg_price=oil_avg),
        ]
    return pd.DataFrame(rows)


def bids():
    return pd.DataFrame([
        dict(state="IL", week="2026-10-05", delivery_point="Mills and Processors", bean=12.80),
        dict(state="IL", week="2026-10-12", delivery_point="Mills and Processors", bean=12.90),
        dict(state="IL", week="2026-10-05", delivery_point="Country Elevators", bean=12.40),   # must not be used
    ])


class ModelTests(unittest.TestCase):
    def test_formula(self):
        # 380 x 0.022 + 67 x 0.11 - 12.80 = 8.36 + 7.37 - 12.80
        self.assertAlmostEqual(am.crush(380, 67, 12.80), 2.93, places=6)

    def test_low_end_and_midpoint(self):
        r = am.REGIONS["Illinois"]
        lo = am.region_crush(feed_rows(), bids(), r, basis="min")
        mid = am.region_crush(feed_rows(), bids(), r, basis="avg")
        self.assertAlmostEqual(lo.crush.iloc[0], 2.93, places=6)
        self.assertAlmostEqual(mid.crush.iloc[0], 390 * 0.022 + 70 * 0.11 - 12.80, places=6)
        self.assertGreater(mid.crush.iloc[0], lo.crush.iloc[0])

    def test_truck_is_preferred_and_rail_selectable(self):
        r = am.REGIONS["Illinois"]
        self.assertEqual(am.region_crush(feed_rows(), bids(), r).attrs["meal_mode"], "Truck")
        rail = am.region_crush(feed_rows(), bids(), r, mode="Rail")
        self.assertEqual(rail.meal.iloc[0], 385.0)

    def test_bean_bid_comes_from_the_regions_delivery_point(self):
        r = am.REGIONS["Illinois"]
        self.assertEqual(list(am.weekly_beans(bids(), r)), [12.80, 12.90])

    def test_a_week_missing_either_leg_is_dropped_not_filled(self):
        b = bids().iloc[[0]]
        out = am.region_crush(feed_rows(), b, am.REGIONS["Illinois"])
        self.assertEqual(len(out), 1)

    def test_validate_matches_wednesday_to_its_monday_week(self):
        model = am.region_crush(feed_rows(), bids(), am.REGIONS["Illinois"])
        jsa = pd.DataFrame({"date": ["2026-10-07", "2026-10-14"], "cash_crush": [2.80, 2.95]})
        m, stats = am.validate(model, jsa)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(list(m.week.dt.strftime("%Y-%m-%d")), ["2026-10-05", "2026-10-12"])


if __name__ == "__main__":
    unittest.main()
