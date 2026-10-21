"""The Global crush view: US, Brazil and Argentina soybean crush, with meal and oil output, by marketing year."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import jsa_style as ui
import store

COUNTRIES = {"US": "United States", "BR": "Brazil", "AR": "Argentina", "CH": "China"}
COLORS = {"US": ui.BLUE, "BR": ui.JSA_GREEN, "AR": ui.CLAY, "CH": ui.PURPLE}
ST_TO_MT = 0.90718474            # NASS reports short tons
BU_PER_KMT = 36.7437 / 1000.0      # million bushels per 1,000 metric tons of soybeans (60-lb bushel)
SOY, MEAL, OIL = "2222000", "0813100", "4232000"
CRUSH, PROD = 125, 28


@st.cache_data(ttl=3600, show_spinner=False)
def load() -> pd.DataFrame:
    return store.fetch_psd_crush()


def wide(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per country and marketing year: crush, soybean production, meal and oil output (1,000 t)."""
    name = raw.commodity.map({SOY: "soy", MEAL: "meal", OIL: "oil"})
    r = raw.assign(key=name + "_" + raw.attribute.astype(str))
    w = r.pivot_table(index=["country", "year"], columns="key", values="value", aggfunc="last").reset_index()
    w = w.rename(columns={"soy_125": "crush", "soy_28": "soy_prod", "meal_28": "meal", "oil_28": "oil"})
    for c in ("crush", "soy_prod", "meal", "oil"):
        if c not in w:
            w[c] = float("nan")
    w["crush_share_prod"] = w.crush / w.soy_prod * 100
    return w.sort_values(["country", "year"])


def my_label(y: int) -> str:
    return f"{int(y)}/{str(int(y) + 1)[-2:]}"


def _lines(w: pd.DataFrame, col: str, scale: float, unit: str, last_year: int, height: int = 400,
           skip: tuple = ()) -> go.Figure:
    """One line per country; the last two points (the USDA forecast and the year before) are dotted."""
    fig = go.Figure()
    for code, name in COUNTRIES.items():
        if code in skip:
            continue
        d = w[(w.country == code) & w[col].notna()]
        if d.empty:
            continue
        done, proj = d[d.year < last_year], d[d.year >= last_year - 1]
        tip = f"{name}<br>%{{customdata}}<br>%{{y:,.1f}} {unit}<extra></extra>"
        fig.add_scatter(x=done.year, y=done[col] * scale, mode="lines", name=name, line=dict(color=COLORS[code], width=2.4),
                        hovertemplate=tip, customdata=[my_label(y) for y in done.year])
        fig.add_scatter(x=proj.year, y=proj[col] * scale, mode="lines+markers", showlegend=False,
                        line=dict(color=COLORS[code], width=2.4, dash="dot"), marker=dict(size=5),
                        hovertemplate=tip.replace(name, f"{name} (latest, USDA forecast)"),
                        customdata=[my_label(y) for y in proj.year])
    fig.update_xaxes(dtick=5)
    return ui.style_fig(fig, height, unit)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@st.cache_data(ttl=3600, show_spinner=False)
def load_nass() -> pd.DataFrame:
    return store.fetch_nass_crush()


def _us_monthly(unit: str, last: int) -> None:
    """US monthly crush from NASS Fats and Oils: latest month, year-to-date pace, and a line per year."""
    try:
        m = load_nass()
    except Exception as exc:
        st.info("US monthly crush is not readable by this app yet: its Snowflake role needs read access to the "
                f"NASS cache ({type(exc).__name__}).")
        return
    if m.empty:
        return
    mt = unit.startswith("Million metric")
    f = (ST_TO_MT if mt else 2000.0 / 60.0) / 1e6          # short tons -> million t, or million 60-lb bushels
    ulab = "million t" if mt else "million bu"
    m = m.assign(v=m.tons * f, year=m.date.dt.year, month=m.date.dt.month)
    now = m.iloc[-1]
    ago = m[(m.year == now.year - 1) & (m.month == now.month)]
    ytd = m[(m.year == now.year) & (m.month <= now.month)].v.sum()
    ytd_ago = m[(m.year == now.year - 1) & (m.month <= now.month)].v.sum()
    t12 = m.tail(12).v.sum()
    t12_ago = m.iloc[-24:-12].v.sum() if len(m) >= 24 else float("nan")

    ui.sec(f"US monthly soybean crush, {ulab}")
    ui.asof(f"USDA NASS Fats and Oils, national, through {now.date:%B %Y} &middot; published about three months after the "
            f"month; refreshed daily by the NASS cache job.")
    c = st.columns(4)
    ui.metric(c[0], f"{now.date:%B %Y} crush", f"{now.v:,.2f} {ulab}",
              f"{(now.v / ago.v.iloc[0] - 1) * 100:+.1f}% vs {now.date:%b} {now.year - 1}" if len(ago) else None,
              sub="latest NASS month")
    ui.metric(c[1], f"{now.year} year to date", f"{ytd:,.1f} {ulab}",
              f"{(ytd / ytd_ago - 1) * 100:+.1f}% vs same months of {now.year - 1}" if ytd_ago else None,
              sub=f"Jan to {now.date:%b}")
    ui.metric(c[2], "Trailing 12 months", f"{t12:,.1f} {ulab}",
              f"{(t12 / t12_ago - 1) * 100:+.1f}% vs the 12 months before" if t12_ago == t12_ago else None, sub="rolling total")
    ui.metric(c[3], "Daily rate, latest month", f"{now.v / now.date.days_in_month * 1000:,.1f} thousand {ulab.split()[1]}/day",
              sub="monthly total over days in the month")

    years = sorted(m.year.unique())[-6:]
    palette = [ui.DM_MUTED, ui.CLAY, ui.PURPLE, "#d9a441", ui.JSA_GREEN, ui.BLUE]
    fig = go.Figure()
    for i, y in enumerate(years):
        d = m[m.year == y]
        is_cur = y == years[-1]
        fig.add_scatter(x=[MONTHS[k - 1] for k in d.month], y=d.v, mode="lines+markers" if is_cur else "lines", name=str(y),
                        line=dict(color=palette[(i + len(palette) - len(years)) % len(palette)], width=3.2 if is_cur else 1.5),
                        hovertemplate=f"{y}<br>%{{x}}<br>%{{y:,.2f}} {ulab}<extra></extra>")
    st.plotly_chart(ui.style_fig(fig, 340, ulab), width="stretch")
    st.caption("Monthly totals as reported, so shorter months run lower; the daily rate above removes that. NASS counts "
               "short tons of soybeans crushed; they are converted to metric tons or 60-lb bushels here. NOPA's member crush, "
               "released about the 15th for the prior month, is earlier but is not used here.")


def render() -> None:
    try:
        raw = load()
    except Exception as exc:
        if "PSD_HISTORY" in str(exc) or "WASDE" in str(exc) or "does not exist" in str(exc):
            st.info("The global crush data are not readable by this app yet: the app's Snowflake role needs read access to "
                    "the WASDE schema's PSD tables.")
            return
        raise
    if raw.empty:
        st.info("No PSD crush data found.")
        return
    w = wide(raw)
    last = int(w[w.crush.notna()].year.max())
    as_of = pd.to_datetime(raw.as_of).max()
    ui.asof(f"USDA FAS Production, Supply and Distribution (PSD), the numbers behind the monthly WASDE &middot; marketing years "
            f"&middot; data as of {as_of:%b %d, %Y}. The latest year ({my_label(last)}) is a USDA forecast and is dotted; "
            f"it is revised at each WASDE.")

    c1, c2, _ = st.columns([1.4, 1.4, 1.2])
    unit = c1.radio("Crush shown in", ["Million metric tons", "Million bushels"], horizontal=True, key="gc_unit")
    first = c2.slider("From marketing year", 1977, last - 5, 1990, key="gc_from")
    scale, ulab = (1 / 1000.0, "million t") if unit.startswith("Million metric") else (BU_PER_KMT, "million bu")
    v = w[w.year >= first]

    cols = st.columns(5)
    cur = w[w.year == last].set_index("country")
    prev = w[w.year == last - 1].set_index("country")
    for col, code in zip(cols, COUNTRIES):
        now, was = cur.crush.get(code), prev.crush.get(code)
        ui.metric(col, f"{COUNTRIES[code]} crush, {my_label(last)}",
                  f"{now * scale:,.1f} {ulab}" if pd.notna(now) else "—",
                  f"{(now / was - 1) * 100:+.1f}% vs {my_label(last - 1)}" if pd.notna(now) and pd.notna(was) and was else None,
                  sub="USDA forecast")
    tot = cur.crush.reindex(list(COUNTRIES)).sum()
    tot_prev = prev.crush.reindex(list(COUNTRIES)).sum()
    ui.metric(cols[4], f"Four-country total, {my_label(last)}", f"{tot * scale:,.1f} {ulab}",
              f"{(tot / tot_prev - 1) * 100:+.1f}% vs {my_label(last - 1)}" if tot_prev else None, sub="US + Brazil + Argentina + China")

    _us_monthly(unit, last)

    ui.sec(f"Soybean crush by marketing year, {ulab}")
    st.plotly_chart(_lines(v, "crush", scale, ulab, last), width="stretch")

    ui.sec("Share of four-country crush, %")
    s = v[v.crush.notna()].pivot(index="year", columns="country", values="crush").dropna()
    s = s.div(s.sum(axis=1), axis=0) * 100
    fig = go.Figure()
    for code, name in COUNTRIES.items():
        if code in s:
            fig.add_scatter(x=s.index, y=s[code], mode="lines", name=name, stackgroup="one",
                            line=dict(color=COLORS[code], width=0.8),
                            hovertemplate=f"{name}<br>%{{x}}<br>%{{y:.1f}}%<extra></extra>")
    fig.update_xaxes(dtick=5)
    st.plotly_chart(ui.style_fig(fig, 300, "% of the four"), width="stretch")

    ui.sec("Meal and oil output")
    prod = st.radio("Product", ["Soybean meal", "Soybean oil"], horizontal=True, key="gc_prod")
    col = "meal" if prod == "Soybean meal" else "oil"
    st.plotly_chart(_lines(v, col, 1 / 1000.0, "million t", last, 340), width="stretch")
    st.caption(f"{prod} production, USDA PSD, million metric tons. Output follows each country's crush; exports and "
               f"domestic use of the products are on the WASDE dashboard.")

    ui.sec("Crush as a share of the country's own soybean crop, %")
    st.plotly_chart(_lines(v, "crush_share_prod", 1.0, "% of production", last, 320, skip=("CH",)), width="stretch")
    st.caption("Crush divided by soybean production in the same marketing year; China is left off because it crushes about six times what it grows (imports). Argentina crushes most of its crop and "
               "exports meal and oil; the US and Brazil export a large share of their beans whole. Above 100% means more "
               "beans were crushed than grown that year (imports and carry-in stocks).")

    ui.sec("Table")
    wide_crush = w.pivot(index="year", columns="country", values="crush").sort_index(ascending=False)
    wide_crush = wide_crush[wide_crush.index >= first]
    show = pd.DataFrame({"Marketing year": [my_label(y) for y in wide_crush.index]})
    fmt = {}
    for code, name in COUNTRIES.items():
        if code not in wide_crush:
            continue
        full = w[w.country == code].set_index("year").crush
        show[name] = (wide_crush[code] * scale).values
        show[f"{name} YoY %"] = (full.pct_change() * 100).reindex(wide_crush.index).values
        fmt[name], fmt[f"{name} YoY %"] = "{:,.1f}", "{:+.1f}"
    ui.table(show.style.format(fmt, na_rep="—"), hide_index=True, width="stretch")
    st.caption(f"Crush in {ulab}; bushels use 60 lb per bushel (36.7437 per tonne). Marketing years are PSD's: "
               f"{my_label(last)} means the year starting in {last}.")
