"""Oilseeds Crush Margins — canola (COOPA-style) vs. US soy board crush, cents/bu."""
from __future__ import annotations

import io
from pathlib import Path
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import canada
import store
from margins import (canola_margin_cad, canola_margin_cents_bu, load_history,
                     load_history_snowflake, soy_margin_cents_bu)

JPSI_DARK, JPSI_BLUE, ORANGE, RED = "#32373c", "#0693e3", "#e8833a", "#d64545"
LOGO_URL = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png"

st.set_page_config(page_title="Oilseeds Crush Margins · JPSI", layout="wide")
st.markdown(f"""<style>
header[data-testid="stHeader"]{{display:none}} footer{{visibility:hidden}}
.block-container{{padding-top:.75rem;max-width:1280px}}
.dash-header{{border-bottom:3px solid {JPSI_BLUE};padding:14px 8px;margin:-.75rem 0 14px;display:flex;align-items:center;gap:20px}}
.dash-header img{{height:48px}} .dash-header h1{{margin:0;font-size:1.6rem;color:{JPSI_DARK}}}
.dash-header .sub{{color:#6b7280;font-size:.85rem}}
.sh{{font-weight:700;color:{JPSI_DARK};border-left:4px solid {JPSI_BLUE};padding:2px 0 2px 10px;margin:18px 0 8px}}
.note{{background:rgba(232,131,58,.08);border:1px solid #f3d9c3;border-left:4px solid {ORANGE};
border-radius:8px;padding:10px 16px;font-size:.9rem;color:{JPSI_DARK}}}
</style>
<div class="dash-header"><img src="{LOGO_URL}"><div><h1>Oilseeds Crush Margins</h1>
<div class="sub">Canola crush vs. US soybean board crush · ¢ per bushel</div></div></div>""",
            unsafe_allow_html=True)

try:
    api_key = st.secrets.get("MASSIVE_API_KEY", "")
except Exception:
    api_key = ""

# ── Inputs ────────────────────────────────────────────────────────────────────
view = st.segmented_control("View", ["Crush margins", "Canadian industry"], default="Crush margins",
                            key="view", label_visibility="collapsed")
if view == "Canadian industry":
    canada.render()
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
    months = st.slider("History (months)", 3, 14, 12)
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
st.caption(f"Data as of settle **{df.index[-1]:%b %d, %Y}** · loaded {datetime.now():%b %d %H:%M} · "
           f"canola legs: {oil_note} · source: {source}")

c = st.columns(6)
c[0].metric("Soy board crush", f"{last.soy:.1f} ¢/bu")
c[1].metric("Canola crush", f"{last.canola:.1f} ¢/bu" if has_canola else "—",
            None if has_canola else "enter seed price")
c[2].metric("Canola − Soy", f"{last.spread:+.1f} ¢/bu" if has_canola else "—")
c[3].metric("ZL / ZM", f"{last.ZL:.2f}¢ / ${last.ZM:.1f}")
c[4].metric("ZS", f"{last.ZS / 100:.4f} $/bu")
c[5].metric("USD/CAD", f"{last.USDCAD:.4f}")

if legs == "Fastmarkets cash":
    st.markdown('<div class="note"><b>Cash legs:</b> canola oil is fob Los Angeles and canola meal fob Velva ND '
                '(Fastmarkets spot assessments), so this is an indicator rather than any single plant margin. '
                'Canola ¢/bu is CAD-denominated (margin ÷ 55.1155); soy is USD.</div>', unsafe_allow_html=True)
else:
  st.markdown('<div class="note"><b>Proxy caveat:</b> the canola index uses soybean oil as a proxy for '
            'canola oil and likely <b>understates</b> true canola crush returns, since canola oil trades at a '
            'premium. Enter a canola oil cash price in the oil override above to replace the soy oil leg. '
            'Canola ¢/bu is CAD-denominated (margin ÷ 55.1155) while soy is USD; both are shown as '
            'specified for direct comparison. The 0.75 protein factor is applied to the 60% meal term.</div>',
            unsafe_allow_html=True)


def style(fig, h=380):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="#fff",
                      paper_bgcolor="#fff", hovermode="x unified",
                      legend=dict(orientation="h", y=1.1, x=0))
    fig.update_yaxes(gridcolor="#f0f0f0", zeroline=True, zerolinecolor="#d1d5db")
    return fig


st.markdown('<div class="sh">Crush margins, ¢/bu</div>', unsafe_allow_html=True)
f1 = go.Figure()
f1.add_scatter(x=df.index, y=df.soy, name="US soy crush", line=dict(color=JPSI_BLUE, width=2.5))
f1.add_scatter(x=df.index, y=df.canola, name="Canola crush", line=dict(color=ORANGE, width=2.5),
               mode="lines+markers" if df.canola.notna().sum() < 3 else "lines")
st.plotly_chart(style(f1), width="stretch")

st.markdown('<div class="sh">Spread: canola − soy, ¢/bu</div>', unsafe_allow_html=True)
if df.spread.notna().any():
    f2 = go.Figure()
    f2.add_bar(x=df.index, y=df.spread,
               marker_color=[JPSI_BLUE if v >= 0 else RED for v in df.spread.fillna(0)])
    st.plotly_chart(style(f2, 300), width="stretch")
else:
    st.info("Enter a seed price (above) to see the canola − soy spread.")

st.markdown('<div class="sh">Sensitivity — effect on canola margin (latest day)</div>', unsafe_allow_html=True)
if has_canola:
    o0, m0, fx0, s0 = oil.iloc[-1], last.c_meal, last.USDCAD, last.seed
    base = canola_margin_cents_bu(o0, m0, fx0, s0)
    rows = [
        ("Oil leg +1 ¢/lb", canola_margin_cents_bu(o0 + 1, m0, fx0, s0) - base),
        ("Meal leg +$1/short ton", canola_margin_cents_bu(o0, m0 + 1, fx0, s0) - base),
        ("Canola seed +1 CAD/mt", canola_margin_cents_bu(o0, m0, fx0, s0 + 1) - base),
        ("USD/CAD +1 cent (0.01)", canola_margin_cents_bu(o0, m0, fx0 + 0.01, s0) - base),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["Move", "Δ canola margin (¢/bu)"]).style.format(
        {"Δ canola margin (¢/bu)": "{:+.3f}"}), hide_index=True, width="stretch")
    st.caption(f"Base canola margin {base:.1f} ¢/bu = {canola_margin_cad(o0, m0, fx0, s0):,.0f} CAD/mt.")
else:
    st.info("Enter a seed price to compute sensitivities.")

with st.expander("Connection check"):
    st.caption("Which Snowflake secrets this app can see (names only, never values).")
    st.json(store.diagnostics())

with st.expander("Data & export"):
    st.dataframe(df.round(4).sort_index(ascending=False), width="stretch")
    st.download_button("Download CSV", df.to_csv().encode(), "oilseed_crush_margins.csv", "text/csv")
