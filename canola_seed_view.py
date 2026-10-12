"""The Canola seed view: ICE canola front month by year on one calendar, and its monthly tendency."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import canola_seasonal as cs
import jsa_style as ui
import margins
import store

YEAR_COLORS = [ui.BLUE, ui.CLAY, ui.PURPLE, "#d9a441", "#4b8f8c", "#a64d79", "#7a8b99", "#8c6d1f", "#6b9080"]
def ordinal(p: float) -> str:
    n = int(round(p))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@st.cache_data(ttl=3600, show_spinner=False)
def load_seed() -> tuple[pd.Series, str]:
    """The bundled workbook history, extended by settles banked in Snowflake after its last day."""
    seed = cs.load_bundled()
    note = f"workbook history to {seed.index.max():%b %d, %Y}"
    if store.configured():
        try:
            live = margins.front_seed_series(store.fetch_ice_settles((seed.index.max() - timedelta(days=7)).date()))
            live = live[live.index > seed.index.max()]
            if len(live):
                seed = pd.concat([seed, live]).sort_index()
                note = f"workbook history plus live feed through {seed.index.max():%b %d, %Y}"
        except Exception:
            pass
    return seed, note


def _overlay(d: pd.DataFrame, grid: pd.DataFrame, seasons: list[str], current: str, show_band: bool, start_month: int,
             earlier: list[str]):
    fig = go.Figure()
    prior = earlier
    ref0 = pd.Timestamp(2000, start_month, 1)
    if show_band and len(prior) >= 3:
        band = cs.prior_band(grid, prior)
        x = [ref0 + pd.Timedelta(days=int(k)) for k in band.index]
        fig.add_scatter(x=x, y=band["p75"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip")
        fig.add_scatter(x=x, y=band["p25"], mode="lines", line=dict(width=0), fill="tonexty",
                        fillcolor="rgba(94,113,100,0.16)", name=f"25th to 75th percentile, all {len(prior)} earlier seasons", hoverinfo="skip")
        fig.add_scatter(x=x, y=band["median"], mode="lines", name="Median of earlier", line=dict(color=ui.DM_TEXT, width=2, dash="dash"),
                        hovertemplate="Median<br>%{x|%b %d}<br>%{y:,.0f} CAD/mt<extra></extra>")
    for i, s in enumerate(seasons):
        x = d[d.season == s]
        if x.empty:
            continue
        is_cur = s == current
        fig.add_scatter(x=x.plot_date, y=x.price, mode="lines", name=s,
                        line=dict(color=YEAR_COLORS[i % len(YEAR_COLORS)], width=3.2 if is_cur else 1.4),
                        hovertemplate=f"{s}<br>%{{x|%b %d}}<br>%{{y:,.0f}} CAD/mt<extra></extra>")
    last_x = ref0 + pd.Timedelta(days=364)
    fig.update_xaxes(tickformat="%b", dtick="M1", range=[ref0, last_x])
    return ui.style_fig(fig, 460, "CAD per tonne")


def render() -> None:
    seed, note = load_seed()
    ui.asof(f"ICE canola (RS) front-month settlements, CAD per tonne &middot; {note}. The front month rolls at the 1st of "
            f"January, March, May, July and November, and the price steps by the spread between contracts when it does.")
    basis_label = st.radio("Season", list(cs.BASES), horizontal=True, key="cs_basis")
    start_month = cs.BASES[basis_label]
    d = cs.prepare(seed, start_month)
    grid = cs.season_grid(d)
    seasons_all = sorted(d.season.unique())
    current = seasons_all[-1]
    earlier = [s for s in seasons_all if s != current]
    pos = cs.position_now(d, grid, current, earlier)

    hi = seed[seed.index > seed.index.max() - timedelta(days=364)]
    c = st.columns(5)
    ui.metric(c[0], "Latest front-month settle", f"{pos['price']:,.1f} CAD/mt", sub=f"{pos['date']:%b %d, %Y}")
    ui.metric(c[1], "Same date last year", f"{pos['year_ago']:,.1f} CAD/mt" if pd.notna(pos["year_ago"]) else "—",
              f"{pos['price'] - pos['year_ago']:+,.1f} CAD/mt this year vs then" if pd.notna(pos["year_ago"]) else None,
              sub=f"{pos['year_ago_date']:%b %d, %Y}" if pos["year_ago_date"] is not None else "")
    ui.metric(c[2], "Median of all earlier years, this date",
              f"{pos['median']:,.1f} CAD/mt" if pd.notna(pos["median"]) else "—",
              f"{pos['price'] - pos['median']:+,.1f} CAD/mt this year vs median" if pd.notna(pos["median"]) else None,
              sub=f"{pos['n_prior']} earlier seasons")
    ui.metric(c[3], "Percentile for this time of year",
              ordinal(pos["percentile"]) if pd.notna(pos["percentile"]) else "—",
              sub=f"higher than {pos['percentile']:.0f}% of {pos['n_prior']} earlier seasons at this date"
              if pd.notna(pos["percentile"]) else "")
    ui.metric(c[4], "52-week range", f"{hi.min():,.0f} to {hi.max():,.0f}", sub="CAD/mt, front month")

    ui.sec(f"Canola seed by year, {basis_label.split(' (')[0].lower()} view")
    c1, c2 = st.columns([3.2, 1])
    default = [s for s in earlier if d[d.season == s].day.min() <= 40][-4:] + [current]
    seasons = c1.multiselect("Seasons drawn as lines", seasons_all, default=default, key=f"cs_years_{start_month}")
    show_band = c2.checkbox("Show band of all earlier years", value=True, key="cs_band")
    if seasons:
        cur = current if current in seasons else seasons[-1]
        st.plotly_chart(_overlay(d, grid, sorted(seasons), cur, show_band, start_month, earlier), width="stretch")
        st.caption("Levels are the front-month price as traded, so each year steps at the roll dates. "
                   "The band, median and percentile use every earlier season, whichever lines are drawn.")
    else:
        st.info("Pick at least one season.")

    ui.sec("Month-by-month tendency")
    t = cs.monthly_tendency(seed)
    f = go.Figure(go.Bar(x=[MONTHS[m - 1] for m in t.month], y=t.avg_pct,
                         marker_color=[ui.COL_POS if v >= 0 else ui.COL_NEG for v in t.avg_pct],
                         customdata=t[["up", "years", "median_pct"]].values,
                         hovertemplate="%{x}<br>average %{y:+.1f}%<br>median %{customdata[2]:+.1f}%<br>"
                                       "up in %{customdata[0]:.0f}% of %{customdata[1]:.0f} years<extra></extra>"))
    f.add_hline(y=0, line_color=ui.DM_MUTED, line_width=1)
    st.plotly_chart(ui.style_fig(f, 300, "average % change in the month", legend=False, hovermode="closest"), width="stretch")
    show = t.rename(columns={"month": "Month", "avg_pct": "Average change %", "median_pct": "Median change %",
                             "up": "Years up %", "years": "Years"}).assign(Month=lambda x: [MONTHS[m - 1] for m in x.Month])
    ui.table(show.style.format({"Average change %": "{:+.1f}", "Median change %": "{:+.1f}", "Years up %": "{:.0f}", "Years": "{:.0f}"}),
             hide_index=True, width="stretch")
    st.caption(f"Each month's change is measured from the last settle of the month before, with the roll steps taken out so a "
               f"change of contract is not counted as a price move. Complete months from {seed.index.min():%b %Y}; "
               f"about {int(t.years.max())} years per month. A tendency is a record of what happened, not a forecast.")
