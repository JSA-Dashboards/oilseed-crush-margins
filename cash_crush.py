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
    d = d.sort_values("date").reset_index(drop=True)
    d["source"] = d["source"].fillna("pdf") if "source" in d.columns else "pdf"
    try:                                  # weeks after JSA's last report: the AMS Illinois model, flagged as an estimate
        feed, beans = _ams()
        if feed is not None and beans is not None and not isinstance(feed, str):
            model = am.region_crush(feed, beans, am.REGIONS["Illinois"], "Truck", "min")
            tail = am.estimate_tail(model, d)
            if len(tail):
                d = pd.concat([d, tail], ignore_index=True)
    except Exception:
        pass
    return with_crop_year(d)


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
        color = YEAR_COLORS[i % len(YEAR_COLORS)]
        est = s[s.source == "ams_estimate"] if "source" in s.columns else s.iloc[0:0]
        real = s[s.source != "ams_estimate"] if "source" in s.columns else s
        fig.add_scatter(x=real.plot_date, y=real.cash_crush, name=cy, mode="lines", legendgroup=cy,
                        line=dict(color=color, width=3 if is_cur else 1.6),
                        hovertemplate=f"{cy}<br>%{{x|%b %d}}<br>$%{{y:.2f}}/bu<extra></extra>")
        if len(est):                      # dotted continuation from the last real point
            join = pd.concat([real.tail(1), est])
            fig.add_scatter(x=join.plot_date, y=join.cash_crush, name=f"{cy} (AMS estimate)", mode="lines+markers",
                            legendgroup=cy, showlegend=False, line=dict(color=color, width=3 if is_cur else 1.6, dash="dot"),
                            marker=dict(size=5), hovertemplate=f"{cy} AMS estimate<br>%{{x|%b %d}}<br>$%{{y:.2f}}/bu<extra></extra>")
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
    real = s[s.source != "ams_estimate"]
    est = s[s.source == "ams_estimate"]
    fig.add_scatter(x=real.date, y=real.cash_crush, name="Cash crush ($/bu)", line=dict(color=ui.SAGE, width=2.2))
    if len(est):
        join = pd.concat([real.tail(1), est])
        fig.add_scatter(x=join.date, y=join.cash_crush, name="Cash crush, AMS estimate", mode="lines+markers",
                        line=dict(color=ui.SAGE, width=2.2, dash="dot"), marker=dict(size=5))
    fig.add_scatter(x=real.date, y=real.basis, name="Soybean basis ($/bu, right axis)", yaxis="y2",
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
    real = d[d.source != "ams_estimate"].dropna(subset=["cash_crush"])
    last_real = real.iloc[-1]
    n_est = int((d.source == "ams_estimate").sum())
    is_est = last.source == "ams_estimate"
    yago = d.dropna(subset=["cash_crush"])
    yago = yago[(yago.date <= last.date - pd.Timedelta(days=364))].iloc[-1]
    same_wk = d[(d.day_of_cy // 7 == last.day_of_cy // 7) & (d.crop_year != last.crop_year)].dropna(subset=["cash_crush"])
    avg5 = same_wk[same_wk.crop_year.isin(sorted(same_wk.crop_year.unique())[-5:])].cash_crush.mean()

    ui.asof(f"Central Illinois (Decatur) weekly series. Latest JSA report: <b>{last_real.date:%A, %b %d, %Y}</b>, "
            f"rebuilt from the Research Analyst archive (workbook from Oct 2009, weekly PDFs from Jul 2024)."
            + (f" <b>The {n_est} week{'s' if n_est != 1 else ''} since, through {last.date:%b %d}, are USDA AMS model "
               f"estimates</b> (dotted on the charts): the Illinois AMS crush tracked JSA's own series with a typical "
               f"miss of about &#36;0.24/bu. They are replaced by JSA's numbers when the weekly reports are loaded."
               if n_est else ""))
    c = st.columns(5)
    ui.metric(c[0], "Cash crush, latest week" + (" (AMS estimate)" if is_est else ""), f"${last.cash_crush:.2f}/bu",
              f"{last.cash_crush - prev.cash_crush:+.2f} vs prior week",
              sub=f"week of {last.date:%b %d}" + (" · estimate, not a JSA report" if is_est else ""),
              color=ui.CLAY if is_est else None)
    prev_real = real.iloc[-2]
    ui.metric(c[1], "Soybean basis", f"${last_real.basis:.2f}/bu" if pd.notna(last_real.basis) else "—",
              f"{last_real.basis - prev_real.basis:+.2f} vs prior week"
              if pd.notna(last_real.basis) and pd.notna(prev_real.basis) else None,
              sub=f"JSA report of {last_real.date:%b %d}" if is_est else "")
    ui.metric(c[2], "Breakeven", f"${last.breakeven:.2f}/bu" if pd.notna(last.breakeven) else "—",
              sub="margin above breakeven: " + (f"{'+' if last.cash_crush >= last.breakeven else '-'}${abs(last.cash_crush - last.breakeven):.2f}" if pd.notna(last.breakeven) else "—"))
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
    st.caption("The breakeven line is the analyst's fixed operating-cost line carried in the report "
               "(\\$0.40/bu through mid-2024; \\$0.70/bu in the 2026 reports).")

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


REGION_COLORS = {"Illinois": ui.SAGE, "Iowa": ui.CLAY, "Indiana": ui.BLUE, "Ohio": ui.PURPLE, "Minnesota": "#d9a441", "Nebraska": "#4b8f8c"}


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
            f"(AMS price used: {basis_label}).")
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
    if jsa is not None:
        jsa = jsa[jsa.source != "ams_estimate"]
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

    ui.sec("Backtest: how the AMS Illinois model performed against JSA's reports")
    if jsa is not None:
        bt = am.matched_frame(am.region_crush(feed, beans, am.REGIONS["Illinois"], "Truck", "min"), jsa)
        if len(bt) >= 20:
            s, dr = am.error_stats(bt), am.direction_stats(bt)
            c = st.columns(5)
            ui.metric(c[0], "Weeks tested", f"{s['n']}", sub=f"{bt.date.min():%b %Y} to {bt.date.max():%b %Y}")
            ui.metric(c[1], "Typical miss", f"${s['mae']:.2f}/bu", sub=f"average bias {s['bias']:+.2f}")
            ui.metric(c[2], "Within $0.25 of JSA", f"{s['within_25'] * 100:.0f}% of weeks",
                      sub=f"within $0.50: {s['within_50'] * 100:.0f}%")
            ui.metric(c[3], "Level correlation", f"{s['corr']:.2f}")
            ui.metric(c[4], "Moves the same way", f"{dr['same_sign'] * 100:.0f}% of weeks" if dr else "—",
                      sub=(f"{dr['same_sign_big'] * 100:.0f}% when JSA moved $0.10 or more" if dr else ""))

            by_year = am.stats_by(bt.assign(year=bt.date.dt.year), "year")
            by_month = am.stats_by(bt.assign(month=bt.date.dt.month), "month")
            c1, c2 = st.columns(2)
            yt = by_year.rename(columns={"group": "Year", "n": "Weeks", "corr": "Correlation", "bias": "Bias $/bu",
                                         "mae": "Typical miss $/bu", "within_25": "Within $0.25"})
            yt["Within $0.25"] = yt["Within $0.25"] * 100
            c1.markdown("**By year**")
            with c1:
                ui.table(yt[["Year", "Weeks", "Correlation", "Bias $/bu", "Typical miss $/bu", "Within $0.25"]].style.format(
                    {"Correlation": "{:.2f}", "Bias $/bu": "{:+.2f}", "Typical miss $/bu": "{:.2f}", "Within $0.25": "{:.0f}%",
                     "Year": "{:d}"}), hide_index=True, width="stretch")
            c2.markdown("**Typical miss by month**")
            mf = go.Figure(go.Bar(x=[pd.Timestamp(2000, int(m), 1).strftime("%b") for m in by_month["group"]],
                                  y=by_month["mae"], marker_color=[ui.CLAY if m in (7, 8, 9, 11) else ui.SAGE for m in by_month["group"]],
                                  hovertemplate="%{x}<br>$%{y:.2f}/bu<extra></extra>"))
            with c2:
                st.plotly_chart(ui.style_fig(mf, 230, "$/bu", legend=False, hovermode="closest"), width="stretch")

            ui.sec("Weekly error: AMS minus JSA")
            ef = go.Figure()
            ef.add_hrect(y0=-0.25, y1=0.25, fillcolor=ui.GREY, opacity=0.25, line_width=0)
            ef.add_scatter(x=bt.date, y=bt.gap, mode="lines+markers", name="AMS minus JSA",
                           line=dict(color=ui.SAGE, width=1.6), marker=dict(size=4),
                           hovertemplate="%{x|%b %d, %Y}<br>%{y:+.2f} $/bu<extra></extra>")
            ef.add_hline(y=0, line_color=ui.DM_MUTED, line_width=1)
            st.plotly_chart(ui.style_fig(ef, 280, "$/bu (shaded = within $0.25)", legend=False), width="stretch")

            ui.sec("Which AMS price to use: chosen on 2022-23, tested on 2024 onward")
            vt = am.variant_table(feed, beans, jsa)
            vt = vt.rename(columns={"variant": "AMS price and meal", "cal_n": "Weeks 2022-23", "cal_mae": "Miss 2022-23",
                                    "test_n": "Weeks 2024 on", "test_mae": "Miss 2024 on", "test_bias": "Bias 2024 on",
                                    "test_corr": "Correlation 2024 on"})
            ui.table(vt.style.format({"Miss 2022-23": "{:.2f}", "Miss 2024 on": "{:.2f}", "Bias 2024 on": "{:+.2f}",
                                      "Correlation 2024 on": "{:.2f}"}), hide_index=True, width="stretch")
            st.caption("The low end of AMS's range with truck meal was best on the 2022-23 weeks it would have been "
                       "picked from, and held up on the later weeks it had not seen, so the choice is not a fit to the "
                       "test data. The midpoint runs about $0.45/bu above JSA.")

            ui.sec("How far past JSA's last report can the estimate be trusted?")
            ht = am.horizon_table(bt)
            ht = ht.rename(columns={"weeks": "Weeks after last JSA report", "n": "Cases", "raw": "Typical miss, as published",
                                    "adjusted": "Typical miss if corrected for recent bias"})
            ui.table(ht.style.format({"Typical miss, as published": "{:.2f}", "Typical miss if corrected for recent bias": "{:.2f}"}),
                     hide_index=True, width="stretch")
            st.caption("The estimate uses only AMS data, so its error does not grow the longer JSA's report is missing: "
                       "about the same one week out or eight. Correcting it for the recent gap makes it worse, so the "
                       "estimates shown are the model as is.")
            st.markdown(
                "**Where it is weaker:** July to September, when processors roll from old-crop to new-crop bids "
                "(correlation 0.6 to 0.7 in those months against 0.93 to 0.99 from January to June and in October), and "
                "in November and December, when it has run below JSA. Single weeks can miss by $0.70 or more, and "
                "errors persist for a week or two (week-to-week correlation of the error 0.5).")
        else:
            st.info("Not enough overlapping weeks to backtest.")

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
