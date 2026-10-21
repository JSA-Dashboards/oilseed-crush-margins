"""Oilseeds Crush Margins — canola (COOPA-style) vs. US soy board crush, cents/bu."""
from __future__ import annotations

import io
from pathlib import Path
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import canola_seed_view
import cash_crush
import canada
import global_crush
import jsa_style as ui
import store
import margins
from margins import (canola_margin_cad, canola_margin_cents_bu, load_history,
                     load_history_snowflake, soy_margin_cents_bu)

st.set_page_config(page_title="Oilseeds Crush Margins · JPSI", page_icon=":material/bar_chart:", layout="wide")
ui.inject_css()
ui.header("Oilseeds Crush Margins",
          "Canola and soybean crush margins, canola seed seasonals, Central Illinois cash crush, Canadian industry &middot; "
          "John Stewart &amp; Associates")

try:
    api_key = st.secrets.get("MASSIVE_API_KEY", "")
except Exception:
    api_key = ""

# ── Inputs ────────────────────────────────────────────────────────────────────
view = st.segmented_control("View", ["Crush margins", "Canola seed", "Cash crush", "Global crush", "Canadian industry"],
                            default="Crush margins", key="view", label_visibility="collapsed")
if view == "Canola seed":
    canola_seed_view.render()
    ui.disclaimer()
    st.stop()
if view == "Cash crush":
    cash_crush.render()
    ui.disclaimer()
    st.stop()
if view == "Global crush":
    global_crush.render()
    ui.disclaimer()
    st.stop()
if view == "Canadian industry":
    canada.render()
    ui.disclaimer()
    st.stop()

c1, c2, c3, c4 = st.columns([1.3, 1.3, 1.3, 1])
with c1:
    legs = st.radio("Canola oil & meal legs", ["Soy futures proxy", "Fastmarkets cash"],
                    help="Cash = canola oil fob Los Angeles (¢/lb) and canola meal fob Velva ND ($/short ton).")
with c2:
    seed_now = st.number_input("Seed, CAD/mt (latest settle)", min_value=0.0, value=0.0, step=1.0,
                               help="ICE canola isn't on Massive. History is bundled from Canola Prices.xlsx "
                                    "(through Oct 9); paste a newer settle here.")
    oil_override = st.number_input("Canola oil override, ¢/lb (0 = off)", min_value=0.0,
                                   value=0.0, step=0.25,
                                   help="Replaces the canola oil leg on the latest day.")
with c3:
    seed_file = st.file_uploader("Seed history CSV (replaces bundled)", type="csv",
                                 help="Columns: date, close (CAD/mt).")
with c4:
    months = st.slider("History (months)", 3, 130, 12,
                       help="Back to Dec 2015 where the longer history is available (soybean, meal, oil and CAD from the "
                            "analyst files); the Massive-fed archive alone starts Oct 2021 (CAD Oct 2022).")
    if not api_key and not store.configured():
        api_key = st.text_input("Massive API Key", type="password")
st.markdown("---")

use_sf = store.configured()
if not use_sf and not api_key:
    st.info("Add the Snowflake secrets (preferred) or enter a Massive API key above.")
    st.stop()


@st.cache_data(ttl=900, show_spinner="Loading ZS / ZM / ZL / CAD history…")
def _hist(key: str, n_months: int, as_of: str, snowflake: bool) -> pd.DataFrame:
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=int(n_months * 30.5))
    if snowflake:
        try:                                  # older ZS/ZM/ZL and CAD from the analyst files, if the role can read them
            return load_history_snowflake(start, end, store.fetch_settlements, store.fetch_legacy_futures)
        except Exception as exc:
            if "OILSEED_LEGACY_FUTURES" not in str(exc):
                raise
        return load_history_snowflake(start, end, store.fetch_settlements)
    return load_history(key, start, end)


source = "Snowflake settlement archive" if use_sf else "Massive API (direct)"
try:
    df = _hist(api_key, months, date.today().isoformat(), use_sf)
    if df.empty and use_sf:        # archive unreachable/empty -> fall back to Massive if we can
        raise RuntimeError("settlement archive returned no rows")
except Exception as exc:
    if use_sf and api_key:
        st.warning(f"Snowflake read failed ({exc}); using Massive directly.")
        source = "Massive API (direct, Snowflake fallback)"
        df = _hist(api_key, months, date.today().isoformat(), False)
    else:
        st.error(f"Data error: {exc}")
        st.stop()
if df.empty:
    st.warning("No history returned.")
    st.stop()

seed = pd.Series(dtype=float)
_BUNDLED = Path(__file__).parent / "canola_seed_history.csv"
if _BUNDLED.exists():
    _b = pd.read_csv(_BUNDLED, parse_dates=["date"]).set_index("date")["close"].astype(float)
    seed = _b.sort_index()
seed_note = "bundled history"
if use_sf and seed_file is None:
    try:        # live ICE canola settles banked by the QST collector extend the bundled history
        _live = margins.front_seed_series(store.fetch_ice_settles((seed.index.max() - timedelta(days=7)).date()
                                                                  if len(seed) else date(2018, 9, 1)))
        _live = _live[_live.index > seed.index.max()] if len(seed) else _live
        if len(_live):
            seed = pd.concat([seed, _live]).sort_index()
            seed_note = f"bundled history + live feed through {_live.index.max():%b %d}"
    except Exception:
        pass                                           # table not there yet / no grant: bundled only
if seed_file is not None:
    try:
        raw = pd.read_csv(io.BytesIO(seed_file.getvalue()))
        raw.columns = [c.strip().lower() for c in raw.columns]
        raw["date"] = pd.to_datetime(raw["date"])
        seed = raw.set_index("date")["close"].astype(float).sort_index()
    except Exception as exc:
        st.error(f"Couldn't read seed CSV ({exc}). Need columns: date, close.")
if seed_now > 0:
    seed.loc[df.index[-1]] = seed_now          # a settle pasted by hand wins for the latest day
    seed = seed.sort_index()
if len(seed):
    seed = seed.reindex(df.index.union(seed.index)).ffill().reindex(df.index)
else:
    seed = pd.Series(float("nan"), index=df.index)

# ── Canola legs: soy proxy, or Fastmarkets cash ───────────────────────────────
cash_note = ""
df["c_oil"], df["c_meal"] = df["ZL"], df["ZM"]
if legs == "Fastmarkets cash":
    try:
        if not store.configured():
            raise RuntimeError("Snowflake secrets not set")
        cash = store.fetch_canola_cash(df.index[0].date() - timedelta(days=10))
        cash = cash.reindex(cash.index.union(df.index)).ffill().reindex(df.index)
        if cash.dropna().empty:
            raise RuntimeError("no rows")
        df["c_oil"], df["c_meal"] = cash["oil"], cash["meal"]
        df = df.dropna(subset=["c_oil", "c_meal"])
        cash_note = f"Fastmarkets cash (oil {df['c_oil'].iloc[-1]:.2f} ¢/lb, meal ${df['c_meal'].iloc[-1]:.1f}/st)"
    except Exception as exc:
        st.warning(f"Fastmarkets cash unavailable ({exc}); using soy proxy. The Snowflake role needs "
                   "SELECT on JSA.COST_OF_CARRY.CANOLA_CASH.")
        legs = "Soy futures proxy"
        df["c_oil"], df["c_meal"] = df["ZL"], df["ZM"]

# ── Margins ───────────────────────────────────────────────────────────────────
df["soy"] = [soy_margin_cents_bu(r.ZL, r.ZM, r.ZS) for r in df.itertuples()]
oil = df["c_oil"].copy()
if oil_override > 0:
    oil.iloc[-1] = oil_override
df["seed"] = seed
df["canola"] = [canola_margin_cents_bu(o, r.c_meal, r.USDCAD, r.seed) if pd.notna(r.seed) else float("nan")
                for o, r in zip(oil, df.itertuples())]
df["spread"] = df["canola"] - df["soy"]
last = df.iloc[-1]
has_canola = pd.notna(last["canola"])

oil_note = (f"canola oil override {oil_override:.2f} ¢/lb" if oil_override > 0
            else cash_note if legs == "Fastmarkets cash" else "soy oil (ZL) + soy meal (ZM) proxy")
ui.asof(f"Data as of settle <b>{df.index[-1]:%b %d, %Y}</b> &middot; loaded {datetime.now():%b %d %H:%M} &middot; "
        f"canola legs: {oil_note} &middot; seed: {seed_note} &middot; source: {source}")

c = st.columns(6)
ui.metric(c[0], "Soy board crush", f"{last.soy:.1f} ¢/bu")
ui.metric(c[1], "Canola crush", f"{last.canola:.1f} ¢/bu" if has_canola else "—",
            None if has_canola else "enter seed price")
ui.metric(c[2], "Canola − Soy", f"{last.spread:+.1f} ¢/bu" if has_canola else "—")
ui.metric(c[3], "ZL / ZM", f"{last.ZL:.2f}¢ / ${last.ZM:.1f}")
ui.metric(c[4], "ZS", f"{last.ZS / 100:.4f} $/bu")
ui.metric(c[5], "USD/CAD", f"{last.USDCAD:.4f}")

if legs == "Fastmarkets cash":
    ui.note("<b>Cash legs:</b> canola oil is fob Los Angeles and canola meal fob Velva ND (Fastmarkets spot "
            "assessments), so this is an indicator rather than any single plant margin. "
            "Canola ¢/bu is USD per 50-lb canola bushel (CAD margin ÷ USD/CAD ÷ 44.0924 bu/mt; 20 lb oil + 30 lb meal per bushel).")
else:
    ui.note("<b>Proxy caveat:</b> the canola index uses soybean oil as a proxy for canola oil and likely "
            "<b>understates</b> true canola crush returns, since canola oil trades at a premium. Enter a canola oil "
            "cash price in the oil override above to replace the soy oil leg. Canola ¢/bu is USD per 50-lb "
            "canola bushel (CAD margin ÷ USD/CAD ÷ 44.0924 bu/mt; 20 lb oil + 30 lb meal per bushel), while soy is per 60-lb soybean bushel. The 0.75 "
            "protein factor is applied to the 60% meal term.")


ui.sec("Crush margins, ¢/bu")
f1 = go.Figure()
f1.add_scatter(x=df.index, y=df.soy, name="US soy crush", line=dict(color=ui.SAGE, width=2.5))
f1.add_scatter(x=df.index, y=df.canola, name="Canola crush", line=dict(color=ui.CLAY, width=2.5),
               mode="lines+markers" if df.canola.notna().sum() < 3 else "lines")
st.plotly_chart(ui.style_fig(f1, 380, "¢ per bushel"), width="stretch")

ui.sec("Spread: canola − soy, ¢/bu")
if df.spread.notna().any():
    f2 = go.Figure()
    f2.add_bar(x=df.index, y=df.spread,
               marker_color=[ui.COL_POS if v >= 0 else ui.COL_NEG for v in df.spread.fillna(0)])
    st.plotly_chart(ui.style_fig(f2, 300, "¢ per bushel", legend=False), width="stretch")
else:
    st.info("Enter a seed price (above) to see the canola − soy spread.")

ui.sec("Sensitivity: effect on canola margin (latest day)")
if has_canola:
    o0, m0, fx0, s0 = oil.iloc[-1], last.c_meal, last.USDCAD, last.seed
    base = canola_margin_cents_bu(o0, m0, fx0, s0)
    rows = [
        ("Oil leg +1 ¢/lb", canola_margin_cents_bu(o0 + 1, m0, fx0, s0) - base),
        ("Meal leg +$1/short ton", canola_margin_cents_bu(o0, m0 + 1, fx0, s0) - base),
        ("Canola seed +1 CAD/mt", canola_margin_cents_bu(o0, m0, fx0, s0 + 1) - base),
        ("USD/CAD +1 cent (0.01)", canola_margin_cents_bu(o0, m0, fx0 + 0.01, s0) - base),
    ]
    ui.table(pd.DataFrame(rows, columns=["Move", "Δ canola margin (¢/bu)"]).style.format(
        {"Δ canola margin (¢/bu)": "{:+.3f}"}), "sens", hide_index=True, width="stretch")
    st.caption(f"Base canola margin {base:.1f} ¢/bu = {canola_margin_cad(o0, m0, fx0, s0):,.0f} CAD/mt.")
else:
    st.info("Enter a seed price to compute sensitivities.")

with st.expander("Connection check"):
    st.caption("Which Snowflake secrets this app can see (names only, never values).")
    st.json(store.diagnostics())

with st.expander("Data & export"):
    ui.table(df.round(4).sort_index(ascending=False), "mdata", width="stretch")
    st.download_button("Download CSV", df.to_csv().encode(), "oilseed_crush_margins.csv", "text/csv")

ui.disclaimer()
