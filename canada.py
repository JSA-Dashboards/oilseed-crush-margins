"""Canadian industry view: crush plants, crush / oil / meal output, seed and product exports."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import copa
import statcan

JPSI_DARK, JPSI_BLUE, ORANGE, GREEN, RED = "#32373c", "#0693e3", "#e8833a", "#5aa469", "#d64545"
COMPANY_COLORS = {"ADM": "#d64545", "Bunge": "#e0b800", "Cargill": "#5a8a3c",
                  "LDC": "#7a4fa3", "Richardson": "#0693e3"}
COPA_MAP = "https://copacanada.com/map-of-plant-locations/"
MT = 1_000_000


@st.cache_data(ttl=12 * 3600, show_spinner="Loading COPA tables…")
def _tables():
    return copa.load()


@st.cache_data(ttl=12 * 3600, show_spinner="Loading Statistics Canada tables…")
def _statcan():
    return statcan.load()


def _style(fig, h=360):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="#fff",
                      paper_bgcolor="#fff", hovermode="x unified",
                      legend=dict(orientation="h", y=1.12, x=0))
    fig.update_yaxes(gridcolor="#f0f0f0", zeroline=True, zerolinecolor="#d1d5db")
    return fig


def _sh(text: str):
    st.markdown(f'<div class="sh">{text}</div>', unsafe_allow_html=True)


def _plants():
    st.caption("Source: Canadian Oilseed Processors Association map of oilseed processing facilities "
               f"(July 29, 2026 edition). Town-level locations are approximate. [COPA map]({COPA_MAP})")
    df = pd.read_csv(Path(__file__).parent / "data" / "canola_plants.csv")
    counts = df.groupby("company").size().sort_values(ascending=False)
    cols = st.columns(len(counts) + 1)
    cols[0].metric("Facilities", len(df))
    for c, (name, n) in zip(cols[1:], counts.items()):
        c.metric(name, int(n))

    fig = go.Figure()
    for company, g in df.groupby("company"):
        fig.add_trace(go.Scattergeo(
            lon=g["lon"], lat=g["lat"], mode="markers", name=company,
            marker=dict(size=13, color=COMPANY_COLORS.get(company, "#888"), line=dict(width=1, color="#fff")),
            text=g["town"] + ", " + g["province"], hovertemplate="%{text}<extra>" + company + "</extra>"))
    fig.update_geos(scope="north america", resolution=50, showland=True, landcolor="#eceff1",
                    showcountries=True, countrycolor="#c9ced3", showsubunits=True, subunitcolor="#d9dde1",
                    lonaxis_range=[-122, -66], lataxis_range=[41, 56], projection_type="mercator",
                    showlakes=True, lakecolor="#ffffff", showocean=True, oceancolor="#ffffff")
    fig.update_layout(height=470, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor="#fff",
                      legend=dict(orientation="h", y=0.02, x=0.01))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(df.rename(columns={"company": "Company", "town": "Town", "province": "Prov"})
                 [["Company", "Town", "Prov"]], hide_index=True, width="stretch")
    st.caption("Facilities are oilseed crushing plants: the western plants crush canola; COPA notes the "
               "three eastern plants crush canola and soybeans.")


def _production():
    d, source = _statcan()
    n = statcan.national(d["production"]).dropna(subset=["production_t"])
    prov = statcan.by_province(d["production"]).dropna()
    st.caption("Statistics Canada table 32-10-0359-01 (area, yield and production of principal field crops) · "
               f"loaded from {source}. The latest year is Statistics Canada's current estimate, not a final.")
    last, prev = n.iloc[-1], n.iloc[-2]
    c = st.columns(4)
    c[0].metric(f"Production {int(last.year)} (est.)", f"{last.production_t / MT:.2f} Mt",
                f"{(last.production_t / prev.production_t - 1) * 100:+.1f}% vs {int(prev.year)}")
    c[1].metric("Seeded area", f"{last.seeded_acres / MT:.1f} M ac",
                f"{(last.seeded_acres / prev.seeded_acres - 1) * 100:+.1f}%")
    c[2].metric("Harvested area", f"{last.harvested_acres / MT:.1f} M ac")
    c[3].metric("Yield", f"{last.yield_bu_ac:.1f} bu/ac", f"{last.yield_bu_ac - prev.yield_bu_ac:+.1f}")
    yrs = st.slider("Years shown", 10, int(len(n)), min(30, int(len(n))), key="pr_years")
    n = n.tail(yrs)
    _sh("Production (Mt) and yield (bu/ac)")
    f = go.Figure()
    f.add_bar(x=n["year"], y=n["production_t"] / MT, name="Production (Mt)", marker_color=JPSI_BLUE)
    f.add_scatter(x=n["year"], y=n["yield_bu_ac"], name="Yield (bu/ac)", yaxis="y2",
                  mode="lines+markers", line=dict(color=ORANGE, width=2.5))
    f.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="bu/ac"))
    st.plotly_chart(_style(f, 400), width="stretch")
    _sh("Seeded vs. harvested area (million acres)")
    a = go.Figure()
    a.add_scatter(x=n["year"], y=n["seeded_acres"] / MT, name="Seeded", line=dict(color=GREEN, width=2.5))
    a.add_scatter(x=n["year"], y=n["harvested_acres"] / MT, name="Harvested", line=dict(color=ORANGE, width=2.5))
    st.plotly_chart(_style(a, 300), width="stretch")
    _sh("Production by province (Mt)")
    pv = go.Figure()
    for name, col in (("Saskatchewan", JPSI_BLUE), ("Alberta", ORANGE), ("Manitoba", GREEN)):
        pv.add_bar(x=prov["year"].tail(yrs), y=prov[name].tail(yrs) / MT, name=name, marker_color=col)
    pv.update_layout(barmode="stack")
    st.plotly_chart(_style(pv, 320), width="stretch")
    with st.expander("Annual table"):
        show = n.rename(columns={"year": "Year", "seeded_acres": "Seeded ac", "harvested_acres": "Harvested ac",
                                 "yield_bu_ac": "Yield bu/ac", "production_t": "Production t",
                                 "farm_price_t": "Farm price $/t", "farm_value": "Farm value $"}).iloc[::-1]
        st.dataframe(show, hide_index=True, width="stretch")
        st.caption("Farm price and value are blank where Statistics Canada has not published them yet.")


def _balance():
    d, source = _statcan()
    cy_all = statcan.crop_years(d["sd"])
    pos = statcan.current_positions(d["sd"])
    st.caption("Statistics Canada table 32-10-0013-01 (supply and disposition of grains), canola seed, "
               f"crop year Aug–Jul, thousand tonnes · loaded from {source}.")
    last, prev = cy_all.iloc[-1], cy_all.iloc[-2]
    c = st.columns(4)
    c[0].metric(f"Ending stocks {last.crop_year}", f"{last.ending_stocks:,.0f} kt",
                f"{last.ending_stocks - prev.ending_stocks:+,.0f} vs {prev.crop_year}")
    c[1].metric("Stocks / use", f"{last.stocks_to_use_pct:.1f}%",
                f"{last.stocks_to_use_pct - prev.stocks_to_use_pct:+.1f} pts")
    c[2].metric("Exports", f"{last.exports:,.0f} kt", f"{(last.exports / prev.exports - 1) * 100:+.1f}%")
    c[3].metric("Industrial use (crush)", f"{last.industrial_use:,.0f} kt",
                f"{(last.industrial_use / prev.industrial_use - 1) * 100:+.1f}%")
    n = st.slider("Crop years shown", 5, int(len(cy_all)), min(15, int(len(cy_all))), key="bal_years")
    cy = cy_all.tail(n)
    _sh("Where the seed goes: exports and domestic use (kt)")
    f = go.Figure()
    f.add_bar(x=cy["crop_year"], y=cy["exports"], name="Exports", marker_color=JPSI_BLUE)
    f.add_bar(x=cy["crop_year"], y=cy["industrial_use"], name="Industrial use (crush)", marker_color=ORANGE)
    f.add_bar(x=cy["crop_year"], y=cy["domestic_use"] - cy["industrial_use"], name="Other domestic",
              marker_color="#9aa5b1")
    f.update_layout(barmode="stack")
    st.plotly_chart(_style(f, 380), width="stretch")
    _sh("Ending stocks (kt) and stocks-to-use (%)")
    s = go.Figure()
    s.add_bar(x=cy["crop_year"], y=cy["ending_stocks"], name="Ending stocks (kt)", marker_color=GREEN)
    s.add_scatter(x=cy["crop_year"], y=cy["stocks_to_use_pct"], name="Stocks / use (%)", yaxis="y2",
                  mode="lines+markers", line=dict(color=RED, width=2.5))
    s.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="%"))
    st.plotly_chart(_style(s, 320), width="stretch")
    _sh("Latest crop-year positions (kt)")
    show = pos[["beginning_stocks", "production", "imports", "total_supply", "exports", "domestic_use",
                "ending_stocks"]].copy()
    names = {"12": "Dec 31", "03": "Mar 31", "07": "Jul 31"}
    show.index = [names[i[5:]] + " " + i[:4] for i in show.index]
    show.columns = ["Beginning stocks", "Production", "Imports", "Total supply", "Exports (to date)",
                    "Domestic use (to date)", "Ending stocks"]
    st.dataframe(show.style.format("{:,.1f}"), width="stretch")
    with st.expander("Balance sheet table"):
        tbl = cy[["crop_year", "beginning_stocks", "production", "imports", "total_supply", "exports",
                  "domestic_use", "industrial_use", "ending_stocks", "stocks_to_use_pct"]].iloc[::-1]
        st.dataframe(tbl.set_index("crop_year").style.format("{:,.1f}"), width="stretch")
        st.caption("Total use = exports + domestic disappearance. Stocks-to-use = ending stocks / total use.")


def _crush(d):
    annual, crop, monthly = d["annual"].copy(), d["crop_annual"].copy(), d["monthly"].copy()
    # soybean output is published with a lag; the latest rows are partial-year, so drop them
    annual.loc[annual["year"] >= 2025, ["soy_crush", "soy_oil", "soy_meal"]] = float("nan")
    crop.loc[crop["crop_year"] >= "2024/25", ["soy_crush", "soy_oil", "soy_meal"]] = float("nan")

    basis = st.radio("Basis", ["Calendar year", "Crop year (Aug–Jul)"], horizontal=True, key="cr_basis")
    t = annual.rename(columns={"year": "p"}) if basis == "Calendar year" else crop.rename(columns={"crop_year": "p"})
    t["p"] = t["p"].astype(str)
    last = t.dropna(subset=["canola_crush"]).iloc[-1]
    prev = t.dropna(subset=["canola_crush"]).iloc[-2]
    c = st.columns(4)
    c[0].metric(f"Canola crush {last.p}", f"{last.canola_crush / MT:.2f} Mt",
                f"{(last.canola_crush / prev.canola_crush - 1) * 100:+.1f}% vs {prev.p}")
    c[1].metric("Canola oil produced", f"{last.canola_oil / MT:.2f} Mt",
                f"{(last.canola_oil / prev.canola_oil - 1) * 100:+.1f}%")
    c[2].metric("Canola meal produced", f"{last.canola_meal / MT:.2f} Mt",
                f"{(last.canola_meal / prev.canola_meal - 1) * 100:+.1f}%")
    c[3].metric("Oil / meal yield", f"{last.canola_oil / last.canola_crush * 100:.1f}% / "
                                    f"{last.canola_meal / last.canola_crush * 100:.1f}%")

    _sh("Canola crush, oil and meal production (million tonnes)")
    f = go.Figure()
    f.add_bar(x=t["p"], y=t["canola_crush"] / MT, name="Canola crush", marker_color=JPSI_BLUE)
    f.add_scatter(x=t["p"], y=t["canola_oil"] / MT, name="Oil", mode="lines+markers", line=dict(color=ORANGE, width=2.5))
    f.add_scatter(x=t["p"], y=t["canola_meal"] / MT, name="Meal", mode="lines+markers", line=dict(color=GREEN, width=2.5))
    st.plotly_chart(_style(f, 400), width="stretch")

    _sh("Extraction yields, % of seed crushed")
    y = go.Figure()
    y.add_scatter(x=t["p"], y=t["canola_oil"] / t["canola_crush"] * 100, name="Oil %", line=dict(color=ORANGE, width=2.5))
    y.add_scatter(x=t["p"], y=t["canola_meal"] / t["canola_crush"] * 100, name="Meal %", line=dict(color=GREEN, width=2.5))
    st.plotly_chart(_style(y, 280), width="stretch")

    _sh("Monthly canola crush and output (tonnes)")
    m = monthly.dropna(subset=["canola_crush"])
    mf = go.Figure()
    mf.add_bar(x=m["month"], y=m["canola_crush"], name="Crush", marker_color=JPSI_BLUE)
    mf.add_scatter(x=m["month"], y=m["canola_oil"], name="Oil", mode="lines+markers", line=dict(color=ORANGE, width=2.5))
    mf.add_scatter(x=m["month"], y=m["canola_meal"], name="Meal", mode="lines+markers", line=dict(color=GREEN, width=2.5))
    mf.update_xaxes(dtick="M2", tickformat="%b '%y")
    st.plotly_chart(_style(mf), width="stretch")

    with st.expander("Canola vs. soybean crush table"):
        show = t.copy()
        show.insert(0, "Period", show.pop("p"))
        st.dataframe(show.rename(columns={"canola_crush": "Canola crush", "canola_oil": "Canola oil",
                                          "canola_meal": "Canola meal", "soy_crush": "Soy crush",
                                          "soy_oil": "Soy oil", "soy_meal": "Soy meal"}).iloc[::-1],
                     hide_index=True, width="stretch")
        st.caption("Metric tonnes. Soybean figures are shown only where the year is complete.")


def _exports(d):
    e = d["exports"]
    c1, c2 = st.columns(2)
    product = c1.radio("Product", ["Canola seed", "Canola oil", "Canola meal"], horizontal=True, key="ex_prod")
    basis = c2.radio("Basis", ["calendar", "crop"], horizontal=True, key="ex_basis",
                     format_func=lambda v: "Calendar year" if v == "calendar" else "Crop year (Aug–Jul)")
    x = e[(e["product"] == product) & (e["basis"] == basis)]
    periods = list(dict.fromkeys(x["period"]))
    tot = x[x["destination"] == "All markets"].set_index("period")["tonnes"]

    cols = st.columns(min(len(periods), 4))
    for col, p in zip(cols, periods[-4:]):
        label = f"{p}" + (" (partial)" if bool(x[x["period"] == p]["partial"].iloc[0]) else "")
        col.metric(label, f"{tot.get(p, float('nan')) / 1e3:,.0f} kt")

    _sh(f"{product} exports by destination (tonnes)")
    dests = x[x["destination"] != "All markets"]
    order = (dests[~dests["period"].str.contains("[A-Za-z]")].groupby("destination")["tonnes"].sum()
             .sort_values(ascending=False).index.tolist()) or dests["destination"].unique().tolist()
    order = [o for o in order if o != "Others"] + (["Others"] if "Others" in set(dests["destination"]) else [])
    palette = ["#0693e3", "#e8833a", "#5aa469", "#b05fb0", "#c0392b", "#8c6d1f",
               "#2a9d8f", "#9aa5b1", "#264653", "#f4a261", "#6c757d", "#adb5bd"]
    fig = go.Figure()
    for i, dest in enumerate(order):
        g = dests[dests["destination"] == dest].set_index("period")["tonnes"].reindex(periods)
        fig.add_bar(x=periods, y=g.values, name=dest, marker_color=palette[i % len(palette)])
    fig.update_layout(barmode="stack")
    st.plotly_chart(_style(fig, 420), width="stretch")
    st.caption("Periods labelled with months are partial-year; compare them to the same months of prior years, "
               "not to full years.")

    _sh(f"Implied average export value, $/tonne ({product.lower()})")
    pv = x[(x["destination"] == "All markets") & x["value_k"].notna()].copy()
    pv["price"] = pv["value_k"] * 1000 / pv["tonnes"]
    pf = go.Figure()
    pf.add_bar(x=pv["period"], y=pv["price"], marker_color=[ORANGE if p else JPSI_BLUE for p in pv["partial"]],
               text=[f"${v:,.0f}" for v in pv["price"]], textposition="outside")
    st.plotly_chart(_style(pf, 280), width="stretch")
    st.caption("Export value / export volume (CAD, Statistics Canada via COPA). Orange = partial year. "
               "A trade-value average, not a price series.")

    _sh("Destination table (tonnes)")
    tbl = dests.pivot_table(index="destination", columns="period", values="tonnes", aggfunc="sum")[periods]
    tbl = tbl.loc[[o for o in order if o in tbl.index]]
    st.dataframe(tbl.style.format("{:,.0f}"), width="stretch")


def render():
    d, source = _tables()
    st.caption(f"Data: Statistics Canada, republished by the Canadian Oilseed Processors Association "
               f"([copacanada.com](https://copacanada.com/crush-oil-meal-production/)) · loaded from {source}.")
    tab = st.segmented_control("Section", ["Crush & production", "Production & yield", "Supply & demand",
                                           "Exports", "Plant locations"],
                               default="Crush & production", key="can_tab", label_visibility="collapsed")
    if tab == "Exports":
        _exports(d)
    elif tab == "Production & yield":
        _production()
    elif tab == "Supply & demand":
        _balance()
    elif tab == "Plant locations":
        _plants()
    else:
        _crush(d)
