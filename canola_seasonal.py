"""ICE canola seed (RS front month, CAD/mt): seasonal chart and month-by-month tendency.

The front-month series rolls at the 1st of each delivery month (Jan, Mar, May, Jul, Nov), and the price steps by the
spread between the two contracts when it does. Levels are shown as traded (a front-month chart); the month-by-month
tendency strips those roll steps out so a roll is not counted as a price move.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROLL_MONTHS = (1, 3, 5, 7, 11)
BASES = {"Calendar year (Jan-Dec)": 1, "Crop year (Aug-Jul)": 8}
BUNDLED = Path(__file__).parent / "canola_seed_history.csv"


def load_bundled() -> pd.Series:
    s = pd.read_csv(BUNDLED, parse_dates=["date"]).set_index("date")["close"].astype(float)
    return s.sort_index()


def season_label(d: pd.Timestamp, start_month: int) -> str:
    if start_month == 1:
        return str(d.year)
    y = d.year if d.month >= start_month else d.year - 1
    return f"{y}/{(y + 1) % 100:02d}"


def prepare(seed: pd.Series, start_month: int) -> pd.DataFrame:
    """One row per trading day: price, season label, day_of_season and plot_date (every season on one reference
    calendar: 2000 for a calendar year, Aug 2000 to Jul 2001 for a crop year)."""
    s = seed.dropna().sort_index()
    idx = pd.DatetimeIndex(s.index)
    ref_year = lambda x: 2001 if (start_month > 1 and x.month < start_month) else 2000

    def to_ref(x: pd.Timestamp) -> pd.Timestamp:         # same month and day on the reference calendar; Feb 29 -> Feb 28
        day = 28 if (x.month == 2 and x.day == 29) else x.day
        return pd.Timestamp(ref_year(x), x.month, day)

    plot = [to_ref(x) for x in idx]
    ref0 = pd.Timestamp(2000, start_month, 1)
    return pd.DataFrame({"date": idx, "price": s.values, "season": [season_label(x, start_month) for x in idx],
                         "day": [(p - ref0).days for p in plot], "plot_date": plot})


def season_grid(d: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    """Daily grid (rows = day of season, columns = seasons), carrying the last price across weekends and holidays."""
    g = d.pivot_table(index="day", columns="season", values="price", aggfunc="last")
    g = g.reindex(range(0, int(g.index.max()) + 1))
    return g.ffill(limit=limit)


def prior_band(grid: pd.DataFrame, prior: list[str], min_seasons: int = 3) -> pd.DataFrame:
    """25th / median / 75th percentile across the chosen prior seasons for each day of the season."""
    sub = grid[[c for c in prior if c in grid.columns]]
    n = sub.notna().sum(axis=1)
    out = pd.DataFrame({"p25": sub.quantile(0.25, axis=1), "median": sub.median(axis=1),
                        "p75": sub.quantile(0.75, axis=1), "n": n})
    return out[out["n"] >= min_seasons]


def position_now(d: pd.DataFrame, grid: pd.DataFrame, current: str, prior: list[str]) -> dict:
    """Where the latest price sits against the same day of earlier seasons."""
    cur = d[d.season == current].iloc[-1]
    day = int(cur["day"])
    vals = grid.loc[day, [c for c in prior if c in grid.columns]].dropna() if day in grid.index else pd.Series(dtype=float)
    yago = d[d["date"] <= cur["date"] - timedelta(days=364)]
    return {"date": cur["date"], "price": float(cur["price"]), "n_prior": int(len(vals)),
            "median": float(vals.median()) if len(vals) else float("nan"),
            "percentile": float((vals <= cur["price"]).mean() * 100) if len(vals) else float("nan"),
            "year_ago": float(yago["price"].iloc[-1]) if len(yago) else float("nan"),
            "year_ago_date": yago["date"].iloc[-1] if len(yago) else None}


def roll_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The first trading day on or after the 1st of each delivery month: the day the front contract changes."""
    out = []
    for y in range(index.min().year, index.max().year + 1):
        for m in ROLL_MONTHS:
            nxt = index[index >= pd.Timestamp(y, m, 1)]
            if len(nxt) and nxt[0].month == m and nxt[0].year == y:
                out.append(nxt[0])
    return pd.DatetimeIndex(out)


def monthly_tendency(seed: pd.Series) -> pd.DataFrame:
    """For each calendar month: the average % change and the share of years that rose, with roll steps removed."""
    s = seed.dropna().sort_index()
    diff = s.diff()
    diff.loc[diff.index.intersection(roll_days(s.index))] = 0.0          # a roll is a spread, not a price move
    adj = diff.fillna(0.0).cumsum()
    me = adj.groupby([adj.index.year, adj.index.month]).last()
    ms = s.groupby([s.index.year, s.index.month]).last()
    rows = []
    for (y, m), end_adj in me.items():
        prev = (y, m - 1) if m > 1 else (y - 1, 12)
        if prev not in me.index:
            continue
        move = end_adj - me.loc[prev]
        pct = move / ms.loc[prev] * 100.0
        rows.append({"year": y, "month": m, "pct": pct})
    d = pd.DataFrame(rows)
    # the latest month is incomplete; keep complete months only
    last = s.index.max()
    d = d[~((d.year == last.year) & (d.month == last.month))]
    out = d.groupby("month").agg(avg_pct=("pct", "mean"), median_pct=("pct", "median"),
                                 up=("pct", lambda v: (v > 0).mean() * 100), years=("pct", "size")).reset_index()
    return out
