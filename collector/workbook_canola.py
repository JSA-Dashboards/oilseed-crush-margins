"""Load ICE canola settlements from the analyst's Canola Prices.xlsx into Snowflake.

The fallback for when QST isn't available: the workbook's Barchart tab is refreshed by the
Barchart Excel add-in; once the analyst saves it, this script reads the saved per-contract
closes and merges the recent days into JSA.COST_OF_CARRY.ICE_CANOLA_SETTLES (source WORKBOOK).
It never overwrites a value the QST collector already banked for the same contract and day.

    python workbook_canola.py                    # last 10 days from the default workbook path
    python workbook_canola.py --dry-run          # show what would be loaded
    python workbook_canola.py --days 30 --file "C:\\path\\Canola Prices.xlsx"

Reads a temporary copy, so it works while the workbook is open in Excel. It can only be as
current as the last time the workbook was refreshed *and saved*; it warns when the newest
date is more than 3 weekdays old.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import qst_canola as q  # noqa: E402  (shares the Snowflake connection and table definition)

DEFAULT_FILE = Path.home() / "John Stewart and Associates" / "JSA - Documents" / "Research Analyst" / "Misc" / "Crush" / "Canola Prices.xlsx"
SHEET = "Canola Prices"
MONTH = q.RS_MONTHS
STALE_WEEKDAYS = 3


def read_contracts(path: Path) -> pd.DataFrame:
    """Long frame [ticker, date, settle] from the workbook's per-contract close columns.

    The contract year comes from each column's own data (its last print falls in its delivery
    month), not the header: the sheet has a mislabeled column (RSH2)."""
    import openpyxl

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "canola.xlsx"
        shutil.copy2(path, copy)
        wb = openpyxl.load_workbook(copy, data_only=True, read_only=True)
        try:
            rows = list(wb[SHEET].iter_rows(values_only=True))
        finally:
            wb.close()
    hdr, data = rows[0], rows[2:]
    dates = pd.to_datetime([r[0] for r in data], errors="coerce")
    out = []
    for j, name in enumerate(hdr[1:], start=1):
        if not name or not str(name).startswith("RS"):
            continue
        s = pd.Series([r[j] for r in data], index=dates, dtype=float).dropna()
        s = s[s.index.notna()]
        if s.empty:
            continue
        letter = str(name)[2]
        if letter not in MONTH:
            continue
        last = s.index.max()
        yr = last.year if last <= pd.Timestamp(last.year, MONTH[letter], 28) else last.year + 1
        ticker = f"RS{letter}{yr % 100:02d}"
        out.append(pd.DataFrame({"ticker": ticker, "date": s.index.date, "settle": s.values}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["ticker", "date", "settle"])


def merge(df: pd.DataFrame) -> int:
    conn = q._connect()
    try:
        cur = conn.cursor()
        cur.execute(q.DDL)
        cur.execute(f"CREATE TEMPORARY TABLE ICE_STAGE LIKE {q.SCHEMA}.{q.TABLE}")
        cur.executemany("INSERT INTO ICE_STAGE (TICKER, DATE, SETTLE, SOURCE) VALUES (%s,%s,%s,'WORKBOOK')",
                        [(r.ticker, r.date.isoformat(), float(r.settle)) for r in df.itertuples()])
        cur.execute(f"""
            MERGE INTO {q.SCHEMA}.{q.TABLE} t USING ICE_STAGE s ON t.TICKER = s.TICKER AND t.DATE = s.DATE
            WHEN MATCHED AND COALESCE(t.SOURCE, '') <> 'QST'
                 THEN UPDATE SET SETTLE = s.SETTLE, SOURCE = s.SOURCE, LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (TICKER, DATE, SETTLE, SOURCE) VALUES (s.TICKER, s.DATE, s.SETTLE, s.SOURCE)
        """)
        conn.commit()
        return len(df)
    finally:
        conn.close()


def weekdays_between(a: date, b: date) -> int:
    return sum(1 for i in range(1, (b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--days", type=int, default=10, help="load the last N calendar days of the workbook")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"Workbook not found: {path}")
        return 2
    df = read_contracts(path)
    if df.empty:
        print("No contract prices found in the workbook.")
        return 2
    newest = max(df["date"])
    stale = weekdays_between(newest, date.today())
    print(f"Workbook newest date: {newest} ({stale} weekdays ago)")
    if stale > STALE_WEEKDAYS:
        print("WARNING: the workbook looks stale. Open it in Excel, let the Barchart add-in refresh, "
              "and save before this runs.")
    recent = df[df["date"] >= newest - timedelta(days=args.days)]
    print(f"{len(recent)} rows, {recent['ticker'].nunique()} contracts, {min(recent['date'])} to {newest}")
    latest = recent[recent["date"] == newest].sort_values("ticker")
    print("latest day:", ", ".join(f"{r.ticker} {r.settle}" for r in latest.itertuples()))
    if args.dry_run:
        print("[dry run] nothing written")
        return 0
    q._load_env()
    print(f"merged {merge(recent)} rows into {q.SCHEMA}.{q.TABLE} (QST values are never overwritten)")
    return 1 if stale > STALE_WEEKDAYS else 0


if __name__ == "__main__":
    raise SystemExit(main())
