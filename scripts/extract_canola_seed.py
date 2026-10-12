"""Stitch ICE canola (RS) per-contract closes from Canola Prices.xlsx into a front-month CSV.

Usage: python scripts/extract_canola_seed.py "<Canola Prices.xlsx>"  -> canola_seed_history.csv

Roll rule: a contract is front from the 1st of the PREVIOUS RS delivery month (F Jan, H Mar, K May, N Jul,
X Nov) to the 1st of its own delivery month. The window comes from the calendar, not from which contracts
happen to be in the workbook, so a contract's earlier deferred prices are never mistaken for front-month
ones; a month whose contract is missing from the workbook leaves a gap instead of a wrong price.

The contract year is read from each column's own data (its last print falls in its delivery month), not the
header: the sheet has had mislabeled columns (RSH2). Columns that infer the same contract (the workbook repeats
F20/H20/K20/N20) are merged and must agree on shared days.
"""
import sys
from pathlib import Path

import openpyxl
import pandas as pd

MONTH = {"F": 1, "H": 3, "K": 5, "N": 7, "X": 11}
ORDER = ["F", "H", "K", "N", "X"]


def front_window(letter: str, year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    i = ORDER.index(letter)
    pl, py = (ORDER[i - 1], year) if i > 0 else ("X", year - 1)
    return pd.Timestamp(py, MONTH[pl], 1), pd.Timestamp(year, MONTH[letter], 1)


def read_columns(path: str) -> dict[tuple[str, int], pd.Series]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        rows = list(wb["Canola Prices"].iter_rows(values_only=True))
    finally:
        wb.close()
    hdr, data = rows[0], rows[2:]
    dates = pd.to_datetime([r[0] for r in data], errors="coerce")
    out: dict[tuple[str, int], pd.Series] = {}
    for j, name in enumerate(hdr[1:], start=1):
        if not name or not str(name).startswith("RS") or str(name)[2] not in MONTH:
            continue
        s = pd.Series([r[j] for r in data], index=dates, dtype=float).dropna()
        s = s[s.index.notna()]
        if s.empty:
            continue
        letter, last = str(name)[2], s.index.max()
        yr = last.year if last <= pd.Timestamp(last.year, MONTH[letter], 28) else last.year + 1
        key = (letter, yr)
        if key in out:                                   # duplicate column: must agree where both have data
            both = out[key].index.intersection(s.index)
            bad = (out[key].loc[both] - s.loc[both]).abs() > 1e-9
            if bad.any():
                raise SystemExit(f"{letter}{yr % 100}: duplicate columns disagree on {int(bad.sum())} days")
            s = out[key].combine_first(s)
        out[key] = s
    return out


def stitch(cols: dict[tuple[str, int], pd.Series]) -> pd.Series:
    parts = []
    for (letter, yr), s in sorted(cols.items(), key=lambda kv: (kv[0][1], MONTH[kv[0][0]])):
        lo, hi = front_window(letter, yr)
        parts.append(s[(s.index >= lo) & (s.index < hi)])
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated(keep="last")]


def main(src: str) -> None:
    cols = read_columns(src)
    print(f"{len(cols)} contracts: " + ", ".join(f"{l}{y % 100:02d}" for (l, y) in sorted(cols, key=lambda k: (k[1], MONTH[k[0]]))))
    out = stitch(cols)
    target = Path(__file__).resolve().parent.parent / "canola_seed_history.csv"
    old = None
    if target.exists():
        old = pd.read_csv(target, parse_dates=["date"]).set_index("date")["close"]
    out.rename("close").rename_axis("date").to_csv(target)
    gaps = out.index.to_series().diff().dt.days
    print(f"front-month series: {len(out)} days, {out.index.min().date()} to {out.index.max().date()}; "
          f"longest gap {int(gaps.max())} days; largest day-to-day move {out.diff().abs().max():.1f}")
    if old is not None:
        both = old.index.intersection(out.index)
        d = (old.loc[both] - out.loc[both]).abs()
        print(f"vs previous file: {len(both)} shared days, {int((d > 1e-9).sum())} differ (max {d.max():.2f}); "
              f"added {len(out.index.difference(old.index))} days, dropped {len(old.index.difference(out.index))}")
    print(out.tail(3))


if __name__ == "__main__":
    main(sys.argv[1])
