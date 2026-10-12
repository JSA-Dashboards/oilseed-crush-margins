"""Regional soybean cash crush from USDA AMS reports.

Same idea as JSA's Central Illinois cash crush, rebuilt for any region AMS reports:

    cash crush ($/bu) = meal ($/short ton) x 0.022 + oil (c/lb) x 0.11 - soybean bid ($/bu)

  meal, oil   AMS National Grain and Oilseed Processor Feedstuff Report (MARS 3511), weekly asking-price ranges
              by region. "Low end" uses the bottom of each range, which tracks JSA's Decatur series best
              (see validate()); "Midpoint" uses AMS's average.
  soybeans    the state's grain-bid report (IL 3192, IA 2850, IN 3463, OH 2851, S. MN 3049): the nearby
              (current) bid at mills and processors, weekly average. Minnesota reports no processor bid, so it
              uses the average country-elevator bid.

Data: JSA.COST_OF_CARRY.AMS_FEEDSTUFF and AMS_BEAN_BIDS, filled by the droplet (cost-of-carry-calculator/ams_crush.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MEAL_TONS_PER_BU = 0.022
OIL_LBS_PER_BU = 11.0


@dataclass(frozen=True)
class Region:
    name: str
    state: str
    meal_loc: str
    oil_loc: str
    bean_point: str                    # AMS delivery point the soybean bid is taken from
    meal_modes: tuple[str, ...]        # AMS transport modes carried for this region's meal, preferred first
    note: str = ""


REGIONS = {
    "Illinois": Region("Illinois", "IL", "Illinois", "Illinois", "Mills and Processors", ("Truck", "Rail")),
    "Iowa": Region("Iowa", "IA", "Iowa", "Iowa", "Mills and Processors", ("Truck/Rail",)),
    "Indiana": Region("Indiana", "IN", "Indiana-Ohio", "Indiana-Ohio", "Mills and Processors", ("Truck", "Rail"),
                      "meal and oil are the Indiana-Ohio region's"),
    "Ohio": Region("Ohio", "OH", "Indiana-Ohio", "Indiana-Ohio", "Mills and Processors", ("Truck", "Rail"),
                   "meal and oil are the Indiana-Ohio region's"),
    "Minnesota": Region("Minnesota", "MN", "Minnesota", "Minnesota", "Country Elevators", ("Truck",),
                        "no processor bid is reported; the bean price is the average country-elevator bid"),
}
PRICE_BASES = {"Low end of AMS range": "min", "Midpoint (AMS average)": "avg"}


def crush(meal, oil_c_lb, bean):
    """$/bu, the formula used by JSA's Central Illinois report."""
    return meal * MEAL_TONS_PER_BU + oil_c_lb * (OIL_LBS_PER_BU / 100.0) - bean


def weekly_feed(feed: pd.DataFrame, region: Region, mode: str | None = None, basis: str = "min") -> pd.DataFrame:
    """Weekly meal ($/ton) and oil (c/lb) for a region. feed columns: report_date, commodity, trade_loc,
    trans_mode, price_min, avg_price."""
    col = "price_min" if basis == "min" else "avg_price"
    f = feed.copy()
    f["report_date"] = pd.to_datetime(f["report_date"])
    meal = f[(f.commodity == "Soybean Meal") & (f.trade_loc == region.meal_loc)]
    modes = [mode] if mode else list(region.meal_modes)
    pick = next((m for m in modes if (meal.trans_mode == m).any()), None)
    if pick is not None:
        meal = meal[meal.trans_mode == pick]
    m = meal.groupby("report_date")[col].mean().rename("meal")
    o = f[(f.commodity == "Soybean Oil") & (f.trade_loc == region.oil_loc)].groupby("report_date")[col].mean().rename("oil")
    out = pd.concat([m, o], axis=1, sort=True).dropna()
    out.attrs["meal_mode"] = pick
    return out


def weekly_beans(bids_weekly: pd.DataFrame, region: Region) -> pd.Series:
    """Weekly average nearby bid. bids_weekly columns: state, week (Monday), delivery_point, bean."""
    b = bids_weekly[(bids_weekly.state == region.state) & (bids_weekly.delivery_point == region.bean_point)].copy()
    b["week"] = pd.to_datetime(b["week"])
    return b.groupby("week")["bean"].mean().rename("bean")


def region_crush(feed: pd.DataFrame, bids_weekly: pd.DataFrame, region: Region, mode: str | None = None,
                 basis: str = "min") -> pd.DataFrame:
    w = weekly_feed(feed, region, mode, basis)
    beans = weekly_beans(bids_weekly, region)
    df = w.join(beans, how="inner").dropna()
    df["meal_value"] = df.meal * MEAL_TONS_PER_BU
    df["oil_value"] = df.oil * (OIL_LBS_PER_BU / 100.0)
    df["crush"] = crush(df.meal, df.oil, df.bean)
    df["region"] = region.name
    df.index.name = "week"
    df.attrs["meal_mode"] = w.attrs.get("meal_mode")
    return df


def validate(model: pd.DataFrame, jsa: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Compare the model to JSA's weekly Central Illinois series. Each JSA report date (Wednesday) is matched to
    the model week (Monday) that contains it. Returns the matched frame and summary statistics."""
    j = jsa.dropna(subset=["cash_crush"]).copy()
    j["date"] = pd.to_datetime(j["date"])
    j["week"] = (j["date"] - pd.to_timedelta(j["date"].dt.weekday, unit="D")).dt.normalize()
    m = j.merge(model[["crush"]].reset_index(), on="week", how="inner")
    if m.empty:
        return m, {}
    diff = m["crush"] - m["cash_crush"]
    return m, {"n": int(len(m)), "corr": float(m["crush"].corr(m["cash_crush"])), "mean_diff": float(diff.mean()),
               "mae": float(diff.abs().mean()), "sd": float(diff.std()),
               "first": m["date"].min(), "last": m["date"].max()}
