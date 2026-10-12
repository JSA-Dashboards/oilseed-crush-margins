"""Build data/cash_crush_history.csv: JSA's Central Illinois cash crush, weekly, 2009 to date.

Sources (the Research Analyst's "Central IL Cash Crush" folder):
  * Archived/2024/Central Illinois Cash Crush.xlsx, sheet "basis data": every weekly point from
    2009-10-07 to 2024-07-10 with basis, cash crush and breakeven; the three inputs (cash #1 yellow
    beans, Hi-Pro meal rail cash, median bean oil) are filled only through 2014-05-14.
  * Archived/<year>/Central Illinois Cash Crush MM-DD-YYYY.pdf: the weekly report. The page-2 title
    line prints that week's "Basis $x  Breakeven $y  Cash Crush $z", which carries the series on
    to the latest report. The PDFs hold no beans/meal/oil inputs.

The crush formula (from the workbook): cash crush $/bu = meal $/short ton * 0.022 + oil c/lb * 0.11
- cash #1 yellow beans $/bu, for Decatur / Central Illinois. Where inputs exist the formula is
re-checked against the stored crush. Workbook rows win over PDF rows for the same week; the
overlap (2019-2024) is compared and reported.

    python scripts/build_cash_crush_history.py "C:\\...\\Central IL Cash Crush"
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cash_crush_history.csv"
MEAL_TONS_PER_BU = 0.022
OIL_LBS_FACTOR = 0.11            # c/lb * 0.11 = $/bu from 11 lb of oil per bushel
LOCATION = "Decatur-Central Illinois, IL"


def read_workbook(path: Path) -> pd.DataFrame:
    import openpyxl

    ws = openpyxl.load_workbook(path, data_only=True)["basis data"]
    num = lambda v: float(v) if isinstance(v, (int, float)) else None
    rows = []
    for r in ws.iter_rows(min_row=3, values_only=True):
        if isinstance(r[0], dt.datetime) and r[2] is not None:
            rows.append(dict(date=r[0].date(), basis=num(r[1]), cash_crush=num(r[2]), breakeven=num(r[3]),
                             cash_ysb=num(r[4]), meal_hipro=num(r[6]), oil_bo=num(r[8]), source="workbook"))
    df = pd.DataFrame(rows)
    both = df.dropna(subset=["cash_ysb", "meal_hipro", "oil_bo"])
    err = (both.meal_hipro * MEAL_TONS_PER_BU + both.oil_bo * OIL_LBS_FACTOR - both.cash_ysb - both.cash_crush).abs()
    print(f"workbook: {len(df)} weeks {df.date.min()}..{df.date.max()}; inputs on {len(both)}; "
          f"formula vs stored max error {err.max():.5f}")
    return df


def _signed(text: str, label: str) -> float | None:
    m = re.search(label + r"[ \t]+(-?)\$(-?)(\d*\.\d+|\d+)", text)      # same line: axis titles sit above prices
    if not m:
        return None
    v = float(m.group(3))
    return -v if (m.group(1) or m.group(2)) else v


def read_pdfs(folder: Path) -> pd.DataFrame:
    rows = []
    for f in sorted(folder.glob("20*/Central Illinois Cash Crush *.pdf")):
        m = re.search(r"(\d\d)-(\d\d)-(\d{4})\.pdf$", f.name)
        if not m:
            continue
        text = subprocess.run(["pdftotext", "-layout", str(f), "-"], capture_output=True, text=True).stdout
        rows.append(dict(date=dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))),
                         basis=_signed(text, "Basis"), cash_crush=_signed(text, "Cash Crush"),
                         breakeven=_signed(text, "Breakeven"), source="pdf"))
    df = pd.DataFrame(rows)
    print(f"pdfs: {len(df)} reports {df.date.min()}..{df.date.max()}; unparsed crush: {df.cash_crush.isna().sum()}")
    return df


def main(folder: Path) -> None:
    wb = read_workbook(folder / "Archived" / "2024" / "Central Illinois Cash Crush.xlsx")
    pdf = read_pdfs(folder / "Archived")
    m = pdf.merge(wb, on="date", suffixes=("_pdf", "_wb"))
    for c in ("basis", "cash_crush", "breakeven"):
        d = (m[c + "_pdf"] - m[c + "_wb"]).abs()
        print(f"overlap {len(m)} weeks, {c}: max diff {d.max():.4f}, weeks off by >0.01: {int((d > 0.01).sum())}")
    new = pdf[~pdf.date.isin(wb.date)]
    out = pd.concat([wb, new], ignore_index=True).sort_values("date")
    out["location"] = LOCATION
    OUT.parent.mkdir(exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(out)} rows, {out.date.min()}..{out.date.max()})")


DDL = """
CREATE TABLE IF NOT EXISTS COST_OF_CARRY.CASH_CRUSH_HISTORY (
    DATE       DATE NOT NULL,
    LOCATION   VARCHAR(64) NOT NULL,
    CASH_CRUSH FLOAT,      -- $/bu
    BASIS      FLOAT,      -- $/bu
    BREAKEVEN  FLOAT,      -- $/bu
    CASH_YSB   FLOAT,      -- cash #1 yellow beans, $/bu (2009-2014 only)
    MEAL_HIPRO FLOAT,      -- Hi-Pro meal rail cash, $/short ton (2009-2014 only)
    OIL_BO     FLOAT,      -- median bean oil, c/lb (2009-2014 only)
    SOURCE     VARCHAR(16),
    LOADED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
)
"""


def push() -> int:
    sys.path.insert(0, str(ROOT / "collector"))
    import qst_canola as q

    q._load_env()
    df = pd.read_csv(OUT, parse_dates=["date"])
    cols = ["date", "location", "cash_crush", "basis", "breakeven", "cash_ysb", "meal_hipro", "oil_bo", "source"]
    rows = [tuple(None if pd.isna(v) else (v.date().isoformat() if c == "date" else (float(v) if isinstance(v, float) else v))
                  for c, v in zip(cols, r)) for r in df[cols].itertuples(index=False)]
    conn = q._connect()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute("CREATE TEMPORARY TABLE CC_STAGE LIKE COST_OF_CARRY.CASH_CRUSH_HISTORY")
        cur.executemany("INSERT INTO CC_STAGE (DATE, LOCATION, CASH_CRUSH, BASIS, BREAKEVEN, CASH_YSB, MEAL_HIPRO, OIL_BO, SOURCE) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        cur.execute("""
            MERGE INTO COST_OF_CARRY.CASH_CRUSH_HISTORY t USING CC_STAGE s ON t.DATE = s.DATE AND t.LOCATION = s.LOCATION
            WHEN MATCHED THEN UPDATE SET CASH_CRUSH = s.CASH_CRUSH, BASIS = s.BASIS, BREAKEVEN = s.BREAKEVEN,
                 CASH_YSB = s.CASH_YSB, MEAL_HIPRO = s.MEAL_HIPRO, OIL_BO = s.OIL_BO, SOURCE = s.SOURCE,
                 LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (DATE, LOCATION, CASH_CRUSH, BASIS, BREAKEVEN, CASH_YSB, MEAL_HIPRO, OIL_BO, SOURCE)
                 VALUES (s.DATE, s.LOCATION, s.CASH_CRUSH, s.BASIS, s.BREAKEVEN, s.CASH_YSB, s.MEAL_HIPRO, s.OIL_BO, s.SOURCE)
        """)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
    if "--push" in sys.argv:
        print(f"merged {push()} rows into COST_OF_CARRY.CASH_CRUSH_HISTORY")
