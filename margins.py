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


def _needed_tickers(start: date, end: date):
    """{(product, letter, year): ticker} for every contract whose front window touches [start, end]."""
    soy = _schedule(SOY_MONTH_NUM, start, end)
    fx = _schedule(FX_MONTH_NUM, start, end)
    wanted = {}
    for prods, sched in ((("ZS", "ZM", "ZL"), soy), (("6C",), fx)):
        prev = date(1900, 1, 1)
        for l, y, roll in sched:
            if roll >= start and prev <= end:
                for p in prods:
                    letter = SOY_PAIRS[l] if p in ("ZM", "ZL") else l
                    wanted[(p, l, y)] = f"{p}{letter}{y % 10}"
            prev = roll
    return soy, fx, wanted


def _assemble(soy, fx, got: dict, start: date, end: date, cont: pd.Series | None = None) -> pd.DataFrame:
    def build(p, sched):
        return _front_series([((p, l, y), roll) for l, y, roll in sched], got)

    six = build("6C", fx)
    if cont is not None and len(cont):       # continuous CAD futures fills the days before the quarterly archive starts
        six = six.combine_first(cont)
    df = pd.DataFrame({"ZS": build("ZS", soy), "ZM": build("ZM", soy), "ZL": build("ZL", soy), "6C": six})
    df.index = pd.to_datetime(df.index)
    df = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))].dropna()
    df["USDCAD"] = 1.0 / df["6C"]
    return df.drop(columns="6C")


def load_history(api_key: str, start: date, end: date) -> pd.DataFrame:
    """Daily front-month ZS (¢/bu), ZM ($/st), ZL (¢/lb), USDCAD straight from Massive."""
    soy, fx, wanted = _needed_tickers(start, end)

    def fetch(item):
        k, t = item
        try:
            return k, get_settlement_history(t, api_key)
        except (MassiveApiError, Exception):
            return k, pd.Series(dtype=float)

    with ThreadPoolExecutor(8) as pool:
        got = dict(pool.map(fetch, wanted.items()))
    return _assemble(soy, fx, got, start, end)


# ZM/ZL carry the pairing letter (Nov beans use Dec meal and oil); this maps it back to the bean month the schedule uses.
_LEGACY_LETTER = {"F": "F", "H": "H", "K": "K", "N": "N", "Q": "Q", "U": "U", "Z": "X"}


def load_history_snowflake(start: date, end: date, fetch_rows, fetch_legacy=None) -> pd.DataFrame:
    """Same frame, from JSA.COST_OF_CARRY.FUTURES_SETTLEMENTS (filled daily by the droplet, from Oct 2021).

    fetch_rows(start) -> DataFrame[ticker, date, settle].
    fetch_legacy(start) -> DataFrame[product_code, month, year, date, settle] from OILSEED_LEGACY_FUTURES: per-contract
    ZS/ZM/ZL back to 2015 and a continuous CAD series, which extend the history to Dec 2015. Optional."""
    soy, fx, wanted = _needed_tickers(start, end)
    rows = fetch_rows(start - pd.Timedelta(days=45).to_pytimedelta())
    by_ticker = {t: g.set_index("date")["settle"].sort_index() for t, g in rows.groupby("ticker")}
    got = {k: by_ticker.get(t, pd.Series(dtype=float)) for k, t in wanted.items()}
    for s in got.values():
        if len(s):
            s.index = [pd.Timestamp(d).date() for d in s.index]
    cont = None
    if fetch_legacy is not None:
        leg = fetch_legacy(start - pd.Timedelta(days=400).to_pytimedelta())
        leg = leg.assign(date=[pd.Timestamp(d).date() for d in leg["date"]])
        for (p, m, y), g in leg[leg["product_code"].isin(["ZS", "ZM", "ZL"])].groupby(["product_code", "month", "year"]):
            letter = m if p == "ZS" else _LEGACY_LETTER.get(m)
            if letter is None:                                   # e.g. October meal/oil: no bean month pairs with it
                continue
            s = g.set_index("date")["settle"].sort_index()
            key = (p, letter, int(y))
            got[key] = s.combine_first(got[key]) if key in got and len(got[key]) else s
        c = leg[leg["product_code"] == "6C"]
        cont = c.set_index("date")["settle"].sort_index() if len(c) else None
    return _assemble(soy, fx, got, start, end, cont)
