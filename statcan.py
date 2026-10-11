"""Canola production / yield and supply & disposition straight from Statistics Canada.

Same two tables the Canola Council's Markets & Stats pages are built on, via StatCan's open
Web Data Service (https://www150.statcan.gc.ca/t1/wds/):
  32-10-0359-01  area, yield, production, farm price and value of principal field crops (annual)
  32-10-0013-01  supply and disposition of grains in Canada (positions at Dec 31, Mar 31, Jul 31)

load() fetches live and falls back to the canola-only snapshot in data/. Refresh the snapshot
with scripts/refresh_statcan.py.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).parent / "data"
WDS = "https://www150.statcan.gc.ca/t1/wds/rest"
PRODUCTION_MEASURES = {
    "Seeded area (acres)": "seeded_acres", "Harvested area (acres)": "harvested_acres",
    "Average yield (bushels per acre)": "yield_bu_ac", "Production (metric tonnes)": "production_t",
    "Average farm price (dollars per tonne)": "farm_price_t", "Total farm value (dollars)": "farm_value",
}
SD_ITEMS = {
    "Total beginning stocks": "beginning_stocks", "Production": "production", "Imports": "imports",
    "Total supplies": "total_supply", "Total exports": "exports",
    "Total domestic disappearance": "domestic_use", "Industrial use": "industrial_use",
    "Human food": "human_food", "Seed requirements": "seed_use", "Loss in handling": "loss",
    "Animal feed, waste and dockage": "feed_waste", "Total ending stocks": "ending_stocks",
}


def _table(pid: int) -> pd.DataFrame:
    url = requests.get(f"{WDS}/getFullTableDownloadCSV/{pid}/en", timeout=60).json()["object"]
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=300).content))
    name = next(n for n in z.namelist() if n.endswith(".csv") and "MetaData" not in n)
    return pd.read_csv(z.open(name), dtype=str, encoding="utf-8-sig")


def build_all() -> dict[str, pd.DataFrame]:
    p = _table(32100359)
    p = p[p["Type of crop"].str.contains("Canola", na=False)]
    p = p[p["Harvest disposition"].isin(PRODUCTION_MEASURES)].copy()
    p["measure"] = p["Harvest disposition"].map(PRODUCTION_MEASURES)
    p["year"] = p["REF_DATE"].astype(int)
    p["value"] = pd.to_numeric(p["VALUE"], errors="coerce")
    production = p.rename(columns={"GEO": "geo"})[["year", "geo", "measure", "value"]]

    s = _table(32100013)
    s = s[(s["Type of crop"] == "Canola") & (s["GEO"] == "Canada")
          & s["Supply and disposition of grains"].isin(SD_ITEMS)].copy()
    s["item"] = s["Supply and disposition of grains"].map(SD_ITEMS)
    s["value_kt"] = pd.to_numeric(s["VALUE"], errors="coerce")   # thousands of metric tonnes
    sd = s.rename(columns={"REF_DATE": "ref_date"})[["ref_date", "item", "value_kt"]]
    return {"production": production.reset_index(drop=True), "sd": sd.reset_index(drop=True)}


def save_snapshot(d: dict[str, pd.DataFrame]) -> None:
    DATA.mkdir(exist_ok=True)
    for k, v in d.items():
        v.to_csv(DATA / f"statcan_{k}.csv", index=False)


def load_snapshot() -> dict[str, pd.DataFrame]:
    return {k: pd.read_csv(DATA / f"statcan_{k}.csv") for k in ("production", "sd")}


def load() -> tuple[dict[str, pd.DataFrame], str]:
    try:
        d = build_all()
        if len(d["production"]) > 500 and len(d["sd"]) > 200:
            return d, "Statistics Canada (live)"
    except Exception:
        pass
    return load_snapshot(), "bundled snapshot"


# ── derived views ─────────────────────────────────────────────────────────────

def national(production: pd.DataFrame) -> pd.DataFrame:
    """One row per year for Canada: seeded/harvested acres, yield, production, farm price, value."""
    t = production[production["geo"] == "Canada"].pivot_table(
        index="year", columns="measure", values="value", aggfunc="first").reset_index()
    return t.sort_values("year")


def by_province(production: pd.DataFrame) -> pd.DataFrame:
    t = production[(production["measure"] == "production_t")
                   & production["geo"].isin(["Saskatchewan", "Alberta", "Manitoba"])]
    return t.pivot_table(index="year", columns="geo", values="value", aggfunc="first").reset_index()


def crop_years(sd: pd.DataFrame) -> pd.DataFrame:
    """Crop-year (Aug-Jul) balance sheet in thousand tonnes from the July 31 positions, plus
    stocks-to-use. The July row of year Y closes crop year (Y-1)/Y."""
    w = sd.pivot_table(index="ref_date", columns="item", values="value_kt", aggfunc="first")
    jul = w[[i.endswith("-07") for i in w.index]].copy()
    jul["crop_year"] = [f"{int(i[:4]) - 1}/{i[2:4]}" for i in jul.index]
    use = jul["exports"] + jul["domestic_use"]
    jul["stocks_to_use_pct"] = jul["ending_stocks"] / use * 100
    return jul.reset_index(drop=True)


def current_positions(sd: pd.DataFrame) -> pd.DataFrame:
    """Dec 31 / Mar 31 / Jul 31 positions for the latest crop year, in thousand tonnes."""
    w = sd.pivot_table(index="ref_date", columns="item", values="value_kt", aggfunc="first")
    latest = sorted(w.index)[-3:]
    return w.loc[latest]
