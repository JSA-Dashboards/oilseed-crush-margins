"""Canadian oilseed crush, oil/meal production and export tables from COPA.

copacanada.com republishes Statistics Canada data as plain HTML tables:
  /crush-oil-meal-production/  crush + oil + meal output (annual, crop-year, monthly)
  /exports-imports/            canola seed / oil / meal exports by destination (tonnes)

build_all() scrapes them live; load() falls back to the CSV snapshot in data/ when the
site can't be reached. Refresh the snapshot with scripts/refresh_copa.py.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).parent / "data"
UA = {"User-Agent": "Mozilla/5.0"}
BASE = "https://copacanada.com"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
CRUSH_COLS = {"CANOLA CRUSHINGS": "canola_crush", "CANOLA OIL PRODUCED": "canola_oil",
              "CANOLA MEAL PRODUCED": "canola_meal", "SOYBEANS CURSHINGS": "soy_crush",
              "SOYBEANS CRUSHINGS": "soy_crush", "SOYBEANS OIL PRODUCED": "soy_oil",
              "SOYBEANS MEAL PRODUCED": "soy_meal"}
NUM = ["canola_crush", "canola_oil", "canola_meal", "soy_crush", "soy_oil", "soy_meal"]


def _tables(path: str) -> list[pd.DataFrame]:
    html = requests.get(f"{BASE}/{path}/", headers=UA, timeout=60).text
    return pd.read_html(io.StringIO(html))


def _clean_crush(t: pd.DataFrame) -> pd.DataFrame:
    t = t.rename(columns=CRUSH_COLS)
    for c in NUM:
        if c not in t:
            t[c] = float("nan")
    t["label"] = t["YEAR"].map(lambda v: "" if pd.isna(v) else str(v).strip())
    return t[["label"] + NUM]


def parse_crush(cal: pd.DataFrame, crop: pd.DataFrame):
    """-> (annual calendar, annual crop year, monthly). Partial current-year totals are dropped."""
    cal, crop = _clean_crush(cal), _clean_crush(crop)
    ann, monthly = [], []

    # calendar: plain-year rows, then per-year month blocks closed by a "TOTAL yyyy" row
    year = None
    for r in cal.itertuples(index=False):
        lab = r.label
        has = pd.notna(r.canola_crush)
        if re.fullmatch(r"\d{4}", lab):
            if has:
                ann.append((int(lab), *[getattr(r, c) for c in NUM]))
            else:
                year = int(lab)
        elif lab in MONTHS and has and year:
            monthly.append((pd.Timestamp(year, MONTHS.index(lab) + 1, 1), *[getattr(r, c) for c in NUM]))
        elif lab.startswith("TOTAL") and has:
            y = int(lab.split()[-1])
            months_done = sum(1 for m in monthly if m[0].year == y)
            if months_done == 12:
                ann.append((y, *[getattr(r, c) for c in NUM]))
    annual = pd.DataFrame(ann, columns=["year"] + NUM).sort_values("year").reset_index(drop=True)

    # crop year (Aug-Jul): plain "yyyy/yy" rows; month blocks add the months the calendar table lacks
    crop_ann, cy = [], None
    for r in crop.itertuples(index=False):
        lab, has = r.label, pd.notna(r.canola_crush)
        if re.fullmatch(r"\d{4}/\d{2}", lab):
            if has:
                crop_ann.append((lab, *[getattr(r, c) for c in NUM]))
            else:
                cy = int(lab[:4])
        elif lab in MONTHS and has and cy:
            m = MONTHS.index(lab) + 1
            ts = pd.Timestamp(cy if m >= 8 else cy + 1, m, 1)
            if not any(x[0] == ts for x in monthly):
                monthly.append((ts, *[getattr(r, c) for c in NUM]))
    crop_annual = pd.DataFrame(crop_ann, columns=["crop_year"] + NUM)
    mon = pd.DataFrame(monthly, columns=["month"] + NUM).sort_values("month").reset_index(drop=True)
    return annual, crop_annual, mon


def parse_exports() -> pd.DataFrame:
    """Calendar- and crop-year canola seed / oil / meal exports by destination: tonnes and $000."""
    tabs = _tables("exports-imports")
    # (tonnes table, $000 table, basis, product)
    spec = [(0, 6, "calendar", "Canola seed"), (1, 7, "calendar", "Canola oil"), (2, 8, "calendar", "Canola meal"),
            (12, 18, "crop", "Canola seed"), (13, 19, "crop", "Canola oil"), (14, 20, "crop", "Canola meal")]

    def tidy(i: int, basis: str, product: str, value_name: str) -> pd.DataFrame:
        t = tabs[i].drop(columns="wdt_ID")
        dest_col = t.columns[0]
        rows = []
        for col in t.columns[1:]:
            partial = bool(re.search(r"[A-Za-z]", str(col)))
            for dest, v in zip(t[dest_col], t[col]):
                v = pd.to_numeric(re.sub(r"[$,]", "", str(v)), errors="coerce")
                if pd.isna(v):
                    continue
                dest = re.sub(r"[^ -~]", "-", str(dest)).strip()
                if dest.upper().startswith("TOTAL"):
                    dest = "All markets"
                rows.append((basis, product, str(col).strip(), partial, dest, float(v)))
        return pd.DataFrame(rows, columns=["basis", "product", "period", "partial", "destination", value_name])

    out = []
    for ti, vi, basis, product in spec:
        a = tidy(ti, basis, product, "tonnes")
        b = tidy(vi, basis, product, "value_k")[["period", "destination", "value_k"]]
        out.append(a.merge(b, on=["period", "destination"], how="left"))
    return pd.concat(out, ignore_index=True)


def build_all() -> dict[str, pd.DataFrame]:
    crush = _tables("crush-oil-meal-production")
    annual, crop_annual, monthly = parse_crush(crush[0], crush[1])
    return {"annual": annual, "crop_annual": crop_annual, "monthly": monthly, "exports": parse_exports()}


def save_snapshot(d: dict[str, pd.DataFrame]) -> None:
    DATA.mkdir(exist_ok=True)
    for k, v in d.items():
        v.to_csv(DATA / f"copa_{k}.csv", index=False)


def load_snapshot() -> dict[str, pd.DataFrame]:
    d = {k: pd.read_csv(DATA / f"copa_{k}.csv") for k in ("annual", "crop_annual", "monthly", "exports")}
    d["monthly"]["month"] = pd.to_datetime(d["monthly"]["month"])
    return d


def load() -> tuple[dict[str, pd.DataFrame], str]:
    """(tables, source label). Live scrape first, bundled snapshot as the fallback."""
    try:
        d = build_all()
        if len(d["annual"]) > 20 and len(d["exports"]) > 50:
            return d, "COPA site (live)"
    except Exception:
        pass
    return load_snapshot(), "bundled snapshot"
