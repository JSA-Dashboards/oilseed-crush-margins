"""Stitch ICE canola (RS) per-contract closes from Canola Prices.xlsx into a front-month CSV.

Usage: python scripts/extract_canola_seed.py "<Canola Prices.xlsx>"  -> canola_seed_history.csv
Roll rule: a contract is front until the 1st of its delivery month (same as the soy/FX legs).
Contract year is read from the data itself (a column's last print falls in its delivery month),
not the header, because the sheet has a mislabeled column (RSH2).
"""
import sys
from pathlib import Path
import openpyxl
import pandas as pd

MONTH = {"F": 1, "H": 3, "K": 5, "N": 7, "X": 11}
src = sys.argv[1]
ws = openpyxl.load_workbook(src, data_only=True)["Canola Prices"]
rows = list(ws.iter_rows(values_only=True))
hdr, data = rows[0], rows[2:]
dates = pd.to_datetime([r[0] for r in data])
cols = {}
for j, name in enumerate(hdr[1:], start=1):
    if not name:
        continue
    s = pd.Series([r[j] for r in data], index=dates, dtype=float).dropna()
    if s.empty:
        continue
    letter = name[2]
    last = s.index.max()
    # delivery year = year of the first delivery-month-1st on/after the last print
    yr = last.year if last <= pd.Timestamp(last.year, MONTH[letter], 28) else last.year + 1
    cols[(letter, yr, name)] = s
chain = sorted(cols, key=lambda k: (k[1], MONTH[k[0]]))
print("contracts:", [(k[2], k[0] + str(k[1])) for k in chain])
parts, prev = [], pd.Timestamp(1900, 1, 1)
for letter, yr, name in chain:
    roll = pd.Timestamp(yr, MONTH[letter], 1)
    s = cols[(letter, yr, name)]
    parts.append(s[(s.index >= prev) & (s.index < roll)])
    prev = roll
# the live (not yet expired) contract continues past its own roll date being in the future
out = pd.concat(parts).sort_index()
out = out[~out.index.duplicated(keep="last")]
out.rename("close").rename_axis("date").to_csv(Path(__file__).parent.parent / "canola_seed_history.csv")
print(out.tail(), len(out), out.index.min())
