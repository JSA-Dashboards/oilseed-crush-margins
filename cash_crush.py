"""Central Illinois soybean cash crush: JSA's weekly series since 2009, rebuilt from the analyst's archive.

Cash crush ($/bu) = Hi-Pro meal rail cash ($/short ton) x 0.022 + median bean oil (c/lb) x 0.11
                    - cash #1 yellow beans ($/bu), Decatur / Central Illinois.
Data: data/cash_crush_history.csv (scripts/build_cash_crush_history.py).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ams_model as am
import jsa_style as ui
import store

DATA = Path(__file__).parent / "data" / "cash_crush_history.csv"
YEAR_COLORS = [ui.BLUE, ui.CLAY, ui.SAGE, ui.PURPLE, "#d9a441", "#4b8f8c", "#a64d79", "#7a8b99"]
MONTH_TICKS = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]


def _raw() -> pd.DataFrame | None:
    """Snowflake first (the series is JSA research, so it is not shipped in this public repo); a local
    CSV from scripts/build_cash_crush_history.py works for development."""
    if store.configured():
        try:
            d = store.fetch_cash_crush()
            if len(d):
                d["date"] = pd.to_datetime(d["date"])
                return d
        except Exception:
            pass
    return pd.read_csv(DATA, parse_dates=["date"]) if DATA.exists() else None


@st.cache_data(ttl=3600, show_spinner=False)
def load() -> pd.DataFrame | None:
    d = _raw()
    if d is None:
        return None
    return with_crop_year(d.sort_values("date").reset_index(drop=True))


def with_crop_year(d: pd.DataFrame) -> pd.DataFrame:
    """Add crop_year (Oct-Sep), day_of_cy and plot_date (every year drawn on one Oct-Sep calendar)."""
    d = d.copy()
    start_year = np.where(d["date"].dt.month >= 10, d["date"].dt.year, d["date"].dt.year - 1)
    d["crop_year"] = [f"{y}/{(y + 1) % 100:02d}" for y in start_year]
    d["crop_start"] = pd.to_datetime([f"{y}-10-01" for y in start_year])
    d["day_of_cy"] = (d["date"] - d["crop_start"]).dt.days
    d["plot_date"] = [pd.Timestamp("2000-10-01") + pd.Timedelta(days=int(k)) for k in d["day_of_cy"]]
    return d


def _seasonal(d: pd.DataFrame, years: list[str], current: str, show_avg: bool) -> go.Figure:
    fig = go.Figure()
    prior = [y for y in years if y != current]
    for i, cy in enumerate(years):
        s = d[d.crop_year == cy].dropna(subset=["cash_crush"])
        if s.empty:
            continue
        is_cur = cy == current
        fig.add_scatter(x=s.plot_date, y=s.cash_crush, name=cy, mode="lines",
                        line=dict(color=YEAR_COLORS[i % len(YEAR_COLORS)], width=3 if is_cur else 1.6),
                        hovertemplate=f"{cy}<br>%{{x|%b %d}}<br>$%{{y:.2f}}/bu<extra></extra>")
    if show_avg and len(prior) >= 2:
        sub = d[d.crop_year.isin(prior)].dropna(subset=["cash_crush"]).copy()
        sub["wk"] = sub.day_of_cy // 7
        avg = sub.groupby("wk").agg(v=("cash_crush", "mean"), n=("cash_crush", "size"))
        avg = avg[avg.n >= max(2, len(prior) // 2)]
        x = [pd.Timestamp("2000-10-01") + pd.Timedelta(days=int(w) * 7 + 3) for w in avg.index]
        fig.add_scatter(x=x, y=avg.v, name=f"Avg of {len(prior)} prior yrs", mode="lines",
                        line=dict(color=ui.DM_TEXT, width=2, dash="dash"),
                        hovertemplate="Average<br>%{x|%b %d}<br>$%{y:.2f}/bu<extra></extra>")
    fig.update_xaxes(tickformat="%b", dtick="M1", range=["2000-10-01", "2001-09-30"])
    return ui.style_fig(fig, 420, "Cash crush, $/bu")


def _crush_vs_basis(d: pd.DataFrame, since: pd.Timestamp) -> go.Figure:
    s = d[d.date >= since]
    fig = go.Figure()
    fig.add_scatter(x=s.date, y=s.cash_crush, name="Cash crush ($/bu)", line=dict(color=ui.SAGE, width=2.2))
    fig.add_scatter(x=s.date, y=s.basis, name="Soybean basis ($/bu, right axis)", yaxis="y2",
                    line=dict(color=ui.CLAY, width=1.8))
    be = s.dropna(subset=["breakeven"])
    fig.add_scatter(x=be.date, y=be.breakeven, name="Breakeven ($/bu)", line=dict(color=ui.GREY, width=1.6, dash="dot"))
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="Basis, $/bu",
                                  tickfont=dict(color=ui.DM_MUTED, size=11), title_font=dict(color=ui.DM_MUTED, size=11)))
    return ui.style_fig(fig, 400, "Cash crush, $/bu")


def render_history() -> None:
    d = load()
    if d is None:
        st.error("The Central Illinois cash crush history is not available. The app reads "
                 "JSA.COST_OF_CARRY.CASH_CRUSH_HISTORY; the Snowflake role needs SELECT on it.")
        return
    last = d.dropna(subset=["cash_crush"]).iloc[-1]
    prev = d.dropna(subset=["cash_crush"]).iloc[-2]
    yago = d.dropna(subset=["cash_crush"])
    yago = yago[(yago.date <= last.date - pd.Timedelta(days=364))].iloc[-1]
    same_wk = d[(d.day_of_cy // 7 == last.day_of_cy // 7) & (d.crop_year != last.crop_year)].dropna(subset=["cash_crush"])
    avg5 = same_wk[same_wk.crop_year.isin(sorted(same_wk.crop_year.unique())[-5:])].cash_crush.mean()

    ui.asof(f"Central Illinois (Decatur) weekly report of <b>{last.date:%A, %b %d, %Y}</b>. "
            f"The analyst's weekly series, rebuilt from the Research Analyst archive: the workbook from Oct 2009 and "
            f"the weekly PDFs from Jul 2024.")
    c = st.columns(5)
    ui.metric(c[0], "Cash crush, latest week", f"${last.cash_crush:.2f}/bu",
              f"{last.cash_crush - prev.cash_crush:+.2f} vs prior week", sub=f"week of {last.date:%b %d}")
    ui.metric(c[1], "Soybean basis", f"${last.basis:.2f}/bu" if pd.notna(last.basis) else "—",
              f"{last.basis - prev.basis:+.2f} vs prior week" if pd.notna(last.basis) and pd.notna(prev.basis) else None)
    ui.metric(c[2], "Breakeven", f"${last.breakeven:.2f}/bu" if pd.notna(last.breakeven) else "—",
              sub="margin above breakeven: " + (f"${last.cash_crush - last.breakeven:+.2f}" if pd.notna(last.breakeven) else "—"))
    ui.metric(c[3], "Same week last year", f"${yago.cash_crush:.2f}/bu",
              f"{last.cash_crush - yago.cash_crush:+.2f} this year vs then", sub=f"{yago.date:%b %d, %Y}")
    ui.metric(c[4], "5-year average, same week", f"${avg5:.2f}/bu" if pd.notna(avg5) else "—",
              f"{last.cash_crush - avg5:+.2f} this year vs average" if pd.notna(avg5) else None,
              sub="prior five crop years")

    ui.sec("Cash crush by crop year (October to September)")
    all_years = sorted(d.crop_year.unique())
    cur = last.crop_year
    c1, c2 = st.columns([3, 1])
    default = all_years[-5:]
    years = c1.multiselect("Crop years", all_years, default=default, key="cc_years")
    show_avg = c2.checkbox("Average of prior years shown", value=True, key="cc_avg")
    if years:
        st.plotly_chart(_seasonal(d, sorted(years), cur if cur in years else years[-1], show_avg), width="stretch")
    else:
        st.info("Pick at least one crop year.")

    ui.sec("Central Illinois cash crush and soybean basis")
    since = st.select_slider("Since", options=[2009, 2012, 2015, 2018, 2020, 2022, 2024],
                             value=2018, key="cc_since")
    st.plotly_chart(_crush_vs_basis(d, pd.Timestamp(f"{since}-01-01")), width="stretch")
    st.caption("The breakeven line is the analyst's fixed $/bu operating-cost line carried in the report "
               "($0.40 through mid-2024; $0.70 in the 2026 reports).")

    ui.sec("Inputs, where the archive holds them")
    inp = d.dropna(subset=["cash_ysb", "meal_hipro", "oil_bo"])
    st.caption(f"Cash #1 yellow beans, Hi-Pro meal rail cash and median bean oil are stored for {len(inp)} weeks "
               f"({inp.date.min():%b %Y} to {inp.date.max():%b %Y}). Later weeks carry the computed crush, basis and "
               f"breakeven only; the regional AMS model rebuilds the inputs from USDA reports.")
    f = go.Figure()
    f.add_scatter(x=inp.date, y=inp.cash_ysb, name="Cash #1 yellow beans ($/bu)", line=dict(color=ui.CLAY, width=2))
    f.add_scatter(x=inp.date, y=inp.meal_hipro / 20, name="Hi-Pro meal rail ($/ton ÷ 20)", line=dict(color=ui.BLUE, width=2))
    f.add_scatter(x=inp.date, y=inp.oil_bo / 3, name="Median bean oil (¢/lb ÷ 3)", line=dict(color=ui.SAGE, width=2))
    st.plotly_chart(ui.style_fig(f, 300, "scaled to share an axis"), width="stretch")

    with st.expander("Data table and download"):
        show = d[["date", "crop_year", "cash_crush", "basis", "breakeven", "cash_ysb", "meal_hipro", "oil_bo", "source"]]
        ui.table(show.sort_values("date", ascending=False), "ccdata", hide_index=True, width="stretch")
        st.download_button("Download CSV", show.to_csv(index=False).encode(), "central_il_cash_crush.csv", "text/csv")
        st.caption("Where the workbook and a later PDF disagree for the same week (about 1 week in 10, usually by a few "
                   "cents and at most $0.42), the workbook value is used. The weekly PDF is what was published at the "
                   "time and the workbook looks to have been revised since.")


@st.cache_data(ttl=3600, show_spinner="Loading USDA AMS prices…")
def _ams():
    if not store.configured():
        return None, None
    try:
        return store.fetch_ams_feed(), store.fetch_ams_bean_weeks()
    except Exception as exc:
        return str(exc), None


REGION_COLORS = {"Illinois": ui.SAGE, "Iowa": ui.CLAY, "Indiana": ui.BLUE, "Ohio": ui.PURPLE, "Minnesota": "#d9a441"}


def render_regional() -> None:
    feed, beans = _ams()
    if feed is None or beans is None or isinstance(feed, str):
        st.error("The USDA AMS tables are not available to this app yet"
                 + (f" ({feed})" if isinstance(feed, str) else "")
                 + ". It reads JSA.COST_OF_CARRY.AMS_FEEDSTUFF and AMS_BEAN_BIDS; the Snowflake role needs SELECT on both.")
        return
    c1, c2, c3 = st.columns([2.2, 1.4, 1.4])
    names = c1.multiselect("Regions", list(am.REGIONS), default=list(am.REGIONS), key="rg_regions")
    basis_label = c2.radio("AMS price used", list(am.PRICE_BASES), key="rg_basis",
                           help="Low end tracks JSA's Decatur series best (see the check below).")
    mode = c3.radio("Meal delivery (IL, IN, OH)", ["Truck", "Rail"], horizontal=True, key="rg_mode")
    basis = am.PRICE_BASES[basis_label]
    if not names:
        st.info("Pick at least one region.")
        return
    models = {n: am.region_crush(feed, beans, am.REGIONS[n], mode if n in ("Illinois", "Indiana", "Ohio") else None, basis)
              for n in names}
    models = {n: m for n, m in models.items() if len(m)}
    if not models:
        st.warning("No overlapping AMS weeks for the chosen regions.")
        return
    latest = max(m.index.max() for m in models.values())
    ui.asof(f"USDA AMS report week of <b>{latest:%A, %b %d, %Y}</b>. Regional cash crush = meal × 0.022 + oil × 0.11 "
            f"− the nearby soybean bid, the same arithmetic as JSA's Central Illinois report, with AMS inputs "
            f"({basis_label.lower()}).")
    tiles = st.columns(len(models))
    for col, (n, m) in zip(tiles, models.items()):
        last, prev = m.iloc[-1], (m.iloc[-2] if len(m) > 1 else None)
        ui.metric(col, f"{n} cash crush", f"${last.crush:.2f}/bu",
                  f"{last.crush - prev.crush:+.2f} vs prior week" if prev is not None else None,
                  sub=f"meal ${last.meal:,.0f} · oil {last.oil:.1f}¢ · beans ${last.bean:.2f}", color=REGION_COLORS.get(n))

    ui.sec("Weekly regional cash crush")
    since = st.select_slider("Since", options=[2022, 2023, 2024, 2025, 2026], value=2022, key="rg_since")
    fig = go.Figure()
    for n, m in models.items():
        s = m[m.index >= pd.Timestamp(f"{since}-01-01")]
        fig.add_scatter(x=s.index, y=s.crush, name=n, line=dict(color=REGION_COLORS.get(n), width=2),
                        hovertemplate=n + "<br>%{x|%b %d, %Y}<br>$%{y:.2f}/bu<extra></extra>")
    st.plotly_chart(ui.style_fig(fig, 400, "Cash crush, $/bu"), width="stretch")

    ui.sec("Latest week by region")
    rows = []
    for n, m in models.items():
        r = m.iloc[-1]
        rows.append({"Region": n, "Week": m.index[-1].date(), "Meal $/ton": r.meal, "Oil ¢/lb": r.oil, "Beans $/bu": r.bean,
                     "Meal value $/bu": r.meal_value, "Oil value $/bu": r.oil_value, "Cash crush $/bu": r.crush,
                     "Meal source": m.attrs.get("meal_mode")})
    tbl = pd.DataFrame(rows).sort_values("Cash crush $/bu", ascending=False)
    ui.table(tbl.style.format({"Meal $/ton": "{:,.1f}", "Oil ¢/lb": "{:.2f}", "Beans $/bu": "{:.2f}",
                               "Meal value $/bu": "{:.2f}", "Oil value $/bu": "{:.2f}", "Cash crush $/bu": "{:.2f}"}),
             hide_index=True, width="stretch")
    notes = [f"{am.REGIONS[n].name}: {am.REGIONS[n].note}" for n in models if am.REGIONS[n].note]
    if notes:
        st.caption(" · ".join(notes) + ".")

    if len(models) > 1 and "Illinois" in models:
        ui.sec("Spread to Illinois")
        f2 = go.Figure()
        for n, m in models.items():
            if n == "Illinois":
                continue
            j = m.crush.to_frame("x").join(models["Illinois"].crush.rename("il"), how="inner")
            s = (j.x - j.il)[j.index >= pd.Timestamp(f"{since}-01-01")]
            f2.add_scatter(x=s.index, y=s, name=f"{n} − Illinois", line=dict(color=REGION_COLORS.get(n), width=1.8))
        f2.add_hline(y=0, line_color=ui.DM_MUTED, line_width=1)
        st.plotly_chart(ui.style_fig(f2, 300, "$/bu"), width="stretch")

    ui.sec("Seasonal pattern by crop year")
    pick = st.selectbox("Region", list(models), key="rg_season")
    sm = models[pick].reset_index().rename(columns={"week": "date", "crush": "cash_crush"})
    sm = with_crop_year(sm)
    ys = sorted(sm.crop_year.unique())
    sel = st.multiselect("Crop years", ys, default=ys[-5:], key="rg_years")
    if sel:
        st.plotly_chart(_seasonal(sm, sorted(sel), sorted(sel)[-1], True), width="stretch")

    ui.sec("Check against JSA's Central Illinois series")
    jsa = load()
    if jsa is not None and "Illinois" in models:
        matched, stats = am.validate(models["Illinois"], jsa)
        if stats:
            c = st.columns(4)
            ui.metric(c[0], "Weeks compared", f"{stats['n']}", sub=f"{stats['first']:%b %Y} to {stats['last']:%b %Y}")
            ui.metric(c[1], "Correlation", f"{stats['corr']:.2f}")
            ui.metric(c[2], "AMS model vs JSA, average", f"{stats['mean_diff']:+.2f} $/bu")
            ui.metric(c[3], "Typical weekly miss", f"${stats['mae']:.2f}/bu", sub="mean absolute difference")
            f3 = go.Figure()
            f3.add_scatter(x=matched.date, y=matched.cash_crush, name="JSA Central Illinois (Decatur)",
                           line=dict(color=ui.SAGE, width=2))
            f3.add_scatter(x=matched.date, y=matched.crush, name="AMS Illinois model", line=dict(color=ui.CLAY, width=2))
            st.plotly_chart(ui.style_fig(f3, 340, "Cash crush, $/bu"), width="stretch")
            st.caption("JSA's series uses Decatur cash beans, Hi-Pro meal rail cash and a median bean oil price; the AMS "
                       "model uses AMS's published weekly ranges and the Central Illinois processor bid. They agree on "
                       "direction and most of the level; compare regions with each other, and use JSA's own series for "
                       "the Decatur level.")

    ui.sec("How this is built")
    st.markdown(
        "- **Meal and oil:** USDA AMS National Grain and Oilseed Processor Feedstuff Report (weekly), asking-price "
        "ranges by region. *Low end* takes the bottom of each range.\n"
        "- **Soybeans:** the nearby bid at mills and processors from each state's AMS grain-bid report, averaged over "
        "the week (Minnesota: average country-elevator bid, since no processor bid is reported).\n"
        "- **History:** AMS provides meal and oil from February 2022 and processor bids from July 2020 in this feed; "
        "the series cannot be extended back from here.\n"
        "- **Refresh:** a droplet job banks the AMS reports into Snowflake daily; USDA's feed is weekly for meal and "
        "oil, so the newest week appears once AMS publishes it.")


def render() -> None:
    view = st.segmented_control("Cash crush view", ["Central Illinois (JSA history)", "Regional model (USDA AMS)"],
                                default="Central Illinois (JSA history)", key="cc_view", label_visibility="collapsed")
    if view == "Regional model (USDA AMS)":
        render_regional()
    else:
        render_history()
