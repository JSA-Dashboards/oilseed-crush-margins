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
    d = d.sort_values("date").reset_index(drop=True)
    start_year = np.where(d["date"].dt.month >= 10, d["date"].dt.year, d["date"].dt.year - 1)
    d["crop_year"] = [f"{y}/{(y + 1) % 100:02d}" for y in start_year]
    d["crop_start"] = pd.to_datetime([f"{y}-10-01" for y in start_year])
    d["day_of_cy"] = (d["date"] - d["crop_start"]).dt.days
    # plot every crop year on one 2000/01 calendar (Oct-Dec 2000, Jan-Sep 2001); Feb 29 -> Feb 28
    ref = []
    for dt_, k in zip(d["date"], d["day_of_cy"]):
        base = pd.Timestamp("2000-10-01") + pd.Timedelta(days=int(k))
        ref.append(base)
    d["plot_date"] = ref
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


def render() -> None:
    render_history()
