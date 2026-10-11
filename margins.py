"""Oilseed crush-margin math and Massive data loading.

Canola (COOPA-style, per the dashboard spec):
  oil  $/mt = (oil ¢/lb / 100) * 2204.62
  meal $/mt = ZM $/short ton * 1.10231
  revenue USD/mt = 0.40 * oil + 0.60 * 0.75 * meal   (0.75 = protein-equivalence on meal)
  margin CAD/mt  = revenue * USDCAD - ICE canola seed (CAD/mt)
  margin ¢/bu    = margin / 55.1155 * 100
Soy (11-44-ish): 11 lb oil + 0.022 st meal - bean, in ¢/bu.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from massive_api import MassiveApiError, get_settlement_history

LBS_PER_MT = 2204.62
ST_TO_MT = 1.10231
OIL_SHARE, MEAL_SHARE, MEAL_PROTEIN = 0.40, 0.60, 0.75
BU_DIVISOR = 55.1155
SOY_OIL_LBS, SOY_MEAL_TONS = 11.0, 44.0 / 2000.0

# bean month letter -> (ZM/ZL letter). Nov beans pair with Dec products.
SOY_PAIRS = {"F": "F", "H": "H", "K": "K", "N": "N", "Q": "Q", "U": "U", "X": "Z"}
SOY_MONTH_NUM = {"F": 1, "H": 3, "K": 5, "N": 7, "Q": 8, "U": 9, "X": 11}
FX_MONTH_NUM = {"H": 3, "M": 6, "U": 9, "Z": 12}


def canola_margin_cad(oil_c_lb, meal_st, usdcad, seed_cad):
    rev = OIL_SHARE * (oil_c_lb / 100.0) * LBS_PER_MT + MEAL_SHARE * MEAL_PROTEIN * meal_st * ST_TO_MT
    return rev * usdcad - seed_cad


def canola_margin_cents_bu(oil_c_lb, meal_st, usdcad, seed_cad):
    return canola_margin_cad(oil_c_lb, meal_st, usdcad, seed_cad) / BU_DIVISOR * 100.0


def soy_margin_cents_bu(oil_c_lb, meal_st, zs_cents):
    return (oil_c_lb / 100.0 * SOY_OIL_LBS + meal_st * SOY_MEAL_TONS - zs_cents / 100.0) * 100.0


def _schedule(months: dict[str, int], start: date, end: date):
    """[(letter, year, roll_date)]: contract is 'front' from the first of the prior
    schedule entry's delivery month until the 1st of its own delivery month."""
    out = []
    for y in range(start.year - 1, end.year + 2):
        for letter, m in sorted(months.items(), key=lambda kv: kv[1]):
            out.append((letter, y, date(y, m, 1)))
    return out


def _front_series(schedule, hist: dict) -> pd.Series:
    """Stitch each contract's settles over [previous roll, own roll)."""
    parts, prev = [], date(1900, 1, 1)
    for key, roll in schedule:
        s = hist.get(key)
        if s is not None and len(s):
            idx = pd.Index(s.index)
            parts.append(s[(idx >= prev) & (idx < roll)])
        prev = roll
    return pd.concat(parts).sort_index() if parts else pd.Series(dtype=float)


def load_history(api_key: str, start: date, end: date) -> pd.DataFrame:
    """Daily front-month ZS (¢/bu), ZM ($/st), ZL (¢/lb), USDCAD from Massive."""
    soy = _schedule(SOY_MONTH_NUM, start, end)
    fx = _schedule(FX_MONTH_NUM, start, end)
    jobs = {}
    for l, y, _ in soy:
        d = y % 10
        jobs[("ZS", l, y)] = f"ZS{l}{d}"
        jobs[("ZM", l, y)] = f"ZM{SOY_PAIRS[l]}{d}"
        jobs[("ZL", l, y)] = f"ZL{SOY_PAIRS[l]}{d}"
    for l, y, _ in fx:
        jobs[("6C", l, y)] = f"6C{l}{y % 10}"

    # only fetch contracts whose front window can touch [start, end]
    wanted = {}
    for prod, sched in (("soy", soy), ("fx", fx)):
        prev = date(1900, 1, 1)
        for l, y, roll in sched:
            if roll >= start and prev <= end:
                for p in (("ZS", "ZM", "ZL") if prod == "soy" else ("6C",)):
                    wanted[(p, l, y)] = jobs[(p, l, y)]
            prev = roll

    def fetch(item):
        k, t = item
        try:
            return k, get_settlement_history(t, api_key)
        except (MassiveApiError, Exception):
            return k, pd.Series(dtype=float)

    with ThreadPoolExecutor(8) as pool:
        got = dict(pool.map(fetch, wanted.items()))

    def build(p, sched):
        s2 = [((p, l, y), roll) for l, y, roll in sched]
        return _front_series(s2, got)

    df = pd.DataFrame({"ZS": build("ZS", soy), "ZM": build("ZM", soy),
                       "ZL": build("ZL", soy), "6C": build("6C", fx)})
    df.index = pd.to_datetime(df.index)
    df = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))].dropna()
    df["USDCAD"] = 1.0 / df["6C"]
    return df.drop(columns="6C")
