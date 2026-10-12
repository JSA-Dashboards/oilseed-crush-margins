"""Load soy meal, soy oil and CAD history from the analyst's Canola Prices.xlsx (plus soybeans from the legacy
archive) into JSA.COST_OF_CARRY.OILSEED_LEGACY_FUTURES, so the canola and soy crush margins reach back to Dec 2015.

Sheets used (values, not the Barchart-formula copies): "Meal" (ZM per contract), "SBO" (ZL per contract),
"CAD" (D6Y00, continuous CAD futures, USD per CAD).  Soybeans come from FUTURES_HISTORY_ARCHIVE (ZS, 2008-2021).

    python scripts/extract_legacy_futures.py "<Canola Prices.xlsx>" [--push]

The data are exchange prices, so they live in Snowflake and not in this public repo.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import openpyxl
import pandas as pd

MONTH_NUM = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}
ROOT = Path(__file__).resolve().parent.parent
DDL = """
CREATE TABLE IF NOT EXISTS COST_OF_CARRY.OILSEED_LEGACY_FUTURES (
    PRODUCT_CODE VARCHAR(8) NOT NULL,   -- ZS | ZM | ZL | 6C
    MONTH        VARCHAR(1) NOT NULL,   -- contract month letter; Y = continuous (6C)
    YEAR         NUMBER(4,0) NOT NULL,  -- contract year; 0 for the continuous series
    DATE         DATE NOT NULL,
    SETTLE       FLOAT NOT NULL,        -- ZS cents/bu, ZM $/short ton, ZL cents/lb, 6C USD per CAD
    SOURCE       VARCHAR(24),
    LOADED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
)
"""


def _sheet(path: str, name: str) -> list[tuple]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return list(wb[name].iter_rows(values_only=True))
    finally:
        wb.close()


def contracts(path: str, sheet: str, product: str) -> pd.DataFrame:
    rows = _sheet(path, sheet)
    hdr, data = rows[0], rows[2:]
    dates = pd.to_datetime([r[0] for r in data], errors="coerce")
    seen: dict[tuple[str, int], pd.Series] = {}
    for j, name in enumerate(hdr[1:], start=1):
        if not name or not str(name).startswith(product) or str(name)[2] not in MONTH_NUM:
            continue
        s = pd.Series([r[j] for r in data], index=dates, dtype=float).dropna()
        s = s[s.index.notna()]
        if s.empty:
            continue
        letter, last = str(name)[2], s.index.max()
        yr = last.year if last <= pd.Timestamp(last.year, MONTH_NUM[letter], 28) else last.year + 1
        key = (letter, yr)
        if key in seen:
            both = seen[key].index.intersection(s.index)
            if ((seen[key].loc[both] - s.loc[both]).abs() > 1e-9).any():
                raise SystemExit(f"{product}{letter}{yr}: duplicate columns disagree")
            s = seen[key].combine_first(s)
        seen[key] = s
    out = [pd.DataFrame({"product_code": product, "month": l, "year": y, "date": s.index.date, "settle": s.values})
           for (l, y), s in seen.items()]
    return pd.concat(out, ignore_index=True)


def cad(path: str) -> pd.DataFrame:
    rows = _sheet(path, "CAD")
    data = [(r[0], r[2]) for r in rows[2:] if r and hasattr(r[0], "year") and isinstance(r[2], (int, float))]
    d = pd.DataFrame(data, columns=["date", "settle"])
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d.insert(0, "product_code", "6C")
    d.insert(1, "month", "Y")
    d.insert(2, "year", 0)
    return d.drop_duplicates("date", keep="last")


def build(src: str) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        copy = str(Path(tmp) / "canola.xlsx")
        shutil.copy2(src, copy)
        parts = [contracts(copy, "Meal", "ZM"), contracts(copy, "SBO", "ZL"), cad(copy)]
    out = pd.concat(parts, ignore_index=True)
    out["source"] = "canola_prices.xlsx"
    for p, g in out.groupby("product_code"):
        print(f"{p}: {len(g)} rows, {g.date.min()}..{g.date.max()}, contracts {g.groupby(['month', 'year']).ngroups}")
    return out


def push(df: pd.DataFrame) -> None:
    sys.path.insert(0, str(ROOT / "collector"))
    import qst_canola as q

    q._load_env()
    conn = q._connect()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute("CREATE TEMPORARY TABLE LF_STAGE LIKE COST_OF_CARRY.OILSEED_LEGACY_FUTURES")
        rows = [(r.product_code, r.month, int(r.year), r.date.isoformat(), float(r.settle), r.source) for r in df.itertuples()]
        cur.executemany("INSERT INTO LF_STAGE (PRODUCT_CODE, MONTH, YEAR, DATE, SETTLE, SOURCE) VALUES (%s,%s,%s,%s,%s,%s)", rows)
        # soybeans from the legacy archive: 2016-2021 contracts, same shape
        cur.execute("""
            INSERT INTO LF_STAGE (PRODUCT_CODE, MONTH, YEAR, DATE, SETTLE, SOURCE)
            SELECT PRODUCT_CODE, MONTH, YEAR, DATE, PRICE, 'futures_history_archive'
            FROM COST_OF_CARRY.FUTURES_HISTORY_ARCHIVE WHERE PRODUCT_CODE = 'ZS' AND YEAR >= 2015""")
        cur.execute("""
            MERGE INTO COST_OF_CARRY.OILSEED_LEGACY_FUTURES t USING
              (SELECT PRODUCT_CODE, MONTH, YEAR, DATE, MAX(SETTLE) AS SETTLE, MAX(SOURCE) AS SOURCE FROM LF_STAGE
               GROUP BY 1, 2, 3, 4) s
              ON t.PRODUCT_CODE = s.PRODUCT_CODE AND t.MONTH = s.MONTH AND t.YEAR = s.YEAR AND t.DATE = s.DATE
            WHEN MATCHED THEN UPDATE SET SETTLE = s.SETTLE, SOURCE = s.SOURCE, LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (PRODUCT_CODE, MONTH, YEAR, DATE, SETTLE, SOURCE)
                 VALUES (s.PRODUCT_CODE, s.MONTH, s.YEAR, s.DATE, s.SETTLE, s.SOURCE)""")
        conn.commit()
        cur.execute("SELECT PRODUCT_CODE, COUNT(*), MIN(DATE), MAX(DATE) FROM COST_OF_CARRY.OILSEED_LEGACY_FUTURES GROUP BY 1 ORDER BY 1")
        for r in cur.fetchall():
            print("table:", r)
    finally:
        conn.close()


if __name__ == "__main__":
    frame = build(sys.argv[1])
    if "--push" in sys.argv:
        push(frame)
