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
    bean_locs: tuple[str, ...] | None = None   # limit the bid to these AMS locations within the state (None = all)


REGIONS = {
    "Illinois": Region("Illinois", "IL", "Illinois", "Illinois", "Mills and Processors", ("Truck", "Rail")),
    "Iowa": Region("Iowa", "IA", "Iowa", "Iowa", "Mills and Processors", ("Truck/Rail",)),
    "Indiana": Region("Indiana", "IN", "Indiana-Ohio", "Indiana-Ohio", "Mills and Processors", ("Truck", "Rail"),
                      "meal and oil are the Indiana-Ohio region's"),
    "Ohio": Region("Ohio", "OH", "Indiana-Ohio", "Indiana-Ohio", "Mills and Processors", ("Truck", "Rail"),
                   "meal and oil are the Indiana-Ohio region's"),
    "Minnesota": Region("Minnesota", "MN", "Minnesota", "Minnesota", "Country Elevators", ("Truck",),
                        "no processor bid is reported; the bean price is the average country-elevator bid"),
    "Nebraska": Region("Nebraska", "NE", "Iowa", "Iowa", "Country Elevators", ("Truck/Rail",),
                       "AMS quotes no Nebraska meal or oil, so Iowa's are used; no processor bid is reported, so the "
                       "bean price is the average of the East and Southeast country-elevator bids",
                       bean_locs=("East", "Southeast")),
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
    if region.bean_locs and "trade_loc" in b.columns:
        b = b[b.trade_loc.isin(region.bean_locs)]
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


def estimate_tail(model: pd.DataFrame, jsa: pd.DataFrame) -> pd.DataFrame:
    """AMS-model weeks that JSA's own series does not cover yet, shaped like the JSA series so they can be
    appended and drawn as a dotted estimate. Each AMS week (Monday) is dated to its Wednesday, the day JSA's
    report is dated. JSA rows from a previous estimate are ignored, so real values replace estimates as soon as
    they are loaded."""
    real = jsa[jsa.get("source", "") != "ams_estimate"] if "source" in jsa.columns else jsa
    real = real.dropna(subset=["cash_crush"])
    last_real = pd.to_datetime(real["date"]).max()
    m = model.reset_index().rename(columns={"week": "mon"})
    m["date"] = m["mon"] + pd.Timedelta(days=2)
    m = m[m["date"] > last_real]
    if m.empty:
        return m.iloc[0:0]
    be = real.dropna(subset=["breakeven"]).sort_values("date")["breakeven"]
    return pd.DataFrame({"date": m["date"], "location": "Decatur-Central Illinois, IL (AMS estimate)",
                         "cash_crush": m["crush"].values, "basis": float("nan"),
                         "breakeven": float(be.iloc[-1]) if len(be) else float("nan"), "cash_ysb": float("nan"),
                         "meal_hipro": float("nan"), "oil_bo": float("nan"), "source": "ams_estimate"})


# ── backtest: AMS Illinois model against JSA's own Central Illinois reports ──────────────────────

def matched_frame(model: pd.DataFrame, jsa: pd.DataFrame) -> pd.DataFrame:
    """JSA report weeks joined to the AMS model, with the gap (AMS minus JSA) in $/bu."""
    m, _ = validate(model, jsa)
    if m.empty:
        return m
    m = m.rename(columns={"crush": "ams"}).sort_values("date").reset_index(drop=True)
    m["gap"] = m["ams"] - m["cash_crush"]
    return m


def error_stats(x: pd.DataFrame) -> dict:
    g = x["gap"]
    return {"n": int(len(x)), "corr": float(x["ams"].corr(x["cash_crush"])) if len(x) > 2 else float("nan"),
            "bias": float(g.mean()), "mae": float(g.abs().mean()), "rmse": float((g ** 2).mean() ** 0.5),
            "within_10": float((g.abs() <= 0.10).mean()), "within_25": float((g.abs() <= 0.25).mean()),
            "within_50": float((g.abs() <= 0.50).mean())}


def stats_by(x: pd.DataFrame, key) -> pd.DataFrame:
    rows = []
    for k, g in x.groupby(key):
        rows.append({"group": k, **error_stats(g)})
    return pd.DataFrame(rows)


def direction_stats(x: pd.DataFrame, min_move: float = 0.10) -> dict:
    """Does the AMS model move the same way as JSA from one report to the next? Consecutive weeks only."""
    y = x.copy()
    y["d_ams"], y["d_jsa"] = y["ams"].diff(), y["cash_crush"].diff()
    y = y[(y["date"].diff().dt.days <= 8)].dropna(subset=["d_ams", "d_jsa"])
    if y.empty:
        return {}
    big = y[y["d_jsa"].abs() >= min_move]
    same = lambda f: float((f["d_ams"].apply(lambda v: (v > 0) - (v < 0)) == f["d_jsa"].apply(lambda v: (v > 0) - (v < 0))).mean())
    return {"n": int(len(y)), "same_sign": same(y), "change_corr": float(y["d_ams"].corr(y["d_jsa"])),
            "n_big": int(len(big)), "same_sign_big": same(big) if len(big) else float("nan")}


def horizon_table(x: pd.DataFrame, ks=(1, 2, 4, 6, 8), window: int = 8) -> pd.DataFrame:
    """Error of the estimate k weeks after JSA's last known report, raw versus corrected by the trailing average gap."""
    rows = []
    for k in ks:
        raw, adj = [], []
        for i in range(len(x)):
            j = i - k
            if j < window - 1:
                continue
            bias = x["gap"].iloc[j - window + 1:j + 1].mean()
            raw.append(abs(x["gap"].iloc[i]))
            adj.append(abs(x["gap"].iloc[i] - bias))
        if raw:
            rows.append({"weeks": k, "n": len(raw), "raw": sum(raw) / len(raw), "adjusted": sum(adj) / len(adj)})
    return pd.DataFrame(rows)


def variant_table(feed, bids_weekly, jsa, split: str = "2024-01-01") -> pd.DataFrame:
    """Each AMS price choice on the weeks before `split` (where it would have been chosen) and on the weeks after."""
    rows = []
    for basis, bl in (("min", "Low end"), ("avg", "Midpoint")):
        for mode in ("Truck", "Rail"):
            x = matched_frame(region_crush(feed, bids_weekly, REGIONS["Illinois"], mode, basis), jsa)
            if x.empty:
                continue
            cal, test = x[x["date"] < split], x[x["date"] >= split]
            rows.append({"variant": f"{bl}, {mode} meal", "cal_n": len(cal), "cal_mae": cal["gap"].abs().mean(),
                         "test_n": len(test), "test_mae": test["gap"].abs().mean() if len(test) else float("nan"),
                         "test_bias": test["gap"].mean() if len(test) else float("nan"),
                         "test_corr": float(test["ams"].corr(test["cash_crush"])) if len(test) > 2 else float("nan")})
    return pd.DataFrame(rows)
