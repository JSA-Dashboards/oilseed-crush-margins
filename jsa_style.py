"""JSA house style for this app: sage palette, header, tiles, watermark, disclaimer.

Mirrors the CFTC Commitments of Traders dashboard (and the Livestock Portal's COT page it follows),
so the oilseed page reads as part of the same family. The disclaimer is JSA's compliance text and is
reproduced verbatim; it is never reworded.
"""
from __future__ import annotations

import base64
import itertools
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

# -- palette (matches cftc-cot-dashboard / livestock-portal COT page) ------------
JSA_GREEN = "#5e7164"
DM_BG, DM_SURFACE, DM_BORDER = "#f6f8f7", "#ffffff", "#d7e2dc"
DM_TEXT, DM_MUTED = "#32373c", "#5f7267"
COL_POS, COL_NEG = "#16a34a", "#dc2626"
BLUE, CLAY, SAGE, PURPLE, GREY = "#6fa8c4", "#c4785a", "#5e7164", "#9b89c4", "#b8c4bc"
LOGO_URL = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png"

HERE = Path(__file__).parent
WATERMARK = HERE / "assets" / "logo-50yr.png"
WATERMARK_OPACITY = 0.10

_tbl = itertools.count()

AXIS = dict(gridcolor=DM_BORDER, linecolor=DM_BORDER, showgrid=True, tickfont=dict(color=DM_MUTED, size=11),
            title_font=dict(color=DM_MUTED, size=11), zeroline=False)

DISCLAIMER = (
    "Trading commodity futures, options on futures, cash commodities, and "
    "over-the-counter derivative products involves substantial risk of loss and "
    "may not be suitable for all investors. This communication is provided for "
    "informational purposes only and does not constitute investment advice, a "
    "recommendation, or an offer or solicitation to buy or sell any futures, "
    "options, cash commodities, or derivative products. John Stewart & "
    "Associates, Inc. does not accept orders to buy or sell any financial "
    "instruments via email. The information contained herein has been obtained "
    "from sources believed to be reliable; however, its accuracy and "
    "completeness are not guaranteed. Any opinions expressed are solely those of "
    "the author, are subject to change without notice, and should not be relied "
    "upon as a basis for investment decisions. Past performance is not "
    "indicative of future results. This message may contain confidential or "
    "proprietary information intended solely for the use of the designated "
    "recipient. © John Stewart & Associates, Inc. {year}"
)


@st.cache_data(show_spinner=False)
def watermark_uri() -> str | None:
    try:
        return "data:image/png;base64," + base64.b64encode(WATERMARK.read_bytes()).decode()
    except OSError:
        return None


def inject_css() -> None:
    global _tbl
    _tbl = itertools.count()      # fresh keys every run, so table wrappers keep stable ids
    wm = watermark_uri()
    # The dataframe grid paints on an opaque canvas, so a background image never shows through; the
    # mark is overlaid instead, pointer-events:none so sorting, scrolling and the toolbar still work.
    table_wm = (f"""
[class*="st-key-tablewrap_"] {{ position: relative; }}
[class*="st-key-tablewrap_"]::after {{
  content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 5;
  background-image: url('{wm}'); background-repeat: no-repeat; background-position: center center;
  background-size: min(34%, 260px); opacity: {WATERMARK_OPACITY};
}}""" if wm else "")
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@500;600&family=Source+Sans+3:wght@400;600;700&display=swap');
html, body, [data-testid="stAppViewContainer"] {{ background-color:{DM_BG}; color:{DM_TEXT}; }}
html, body, p, span, div, label, button, td, th {{ font-family:'Source Sans 3','Source Sans Pro',sans-serif; }}
.block-container {{ padding-top: 3.2rem; max-width: 1400px; }}
.dash-header {{ display:flex; align-items:center; gap:18px; padding-bottom:10px;
    border-bottom:1px solid {DM_BORDER}; margin-bottom:14px; }}
.dash-header img {{ height:40px; }}
.dash-header .t b {{ font-family:'EB Garamond',Georgia,serif; font-size:1.7rem; color:{DM_TEXT}; font-weight:600; }}
.dash-header .t div {{ font-size:.85rem; color:{DM_MUTED}; }}
@media (max-width: 640px) {{ .dash-header img {{ display:none; }} }}
.tile {{ background:{DM_SURFACE}; border:1px solid {DM_BORDER}; border-top:3px solid {JSA_GREEN};
    border-radius:10px; padding:16px 14px; text-align:center; height:100%; }}
.tile-label {{ color:{DM_MUTED}; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.09em; margin-bottom:6px; }}
.tile-value {{ color:{DM_TEXT}; font-size:1.45rem; font-weight:700; line-height:1.1; white-space:nowrap; }}
.tile-sub {{ color:{DM_MUTED}; font-size:0.72rem; margin-top:5px; }}
.tile-delta-neu {{ color:{DM_MUTED}; font-size:0.82rem; font-weight:600; margin-top:4px; }}
.sec-header {{ color:{DM_MUTED}; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em;
    padding:10px 0 4px; border-bottom:1px solid {DM_BORDER}; margin:18px 0 12px; }}
.asof {{ background:{DM_SURFACE}; border:1px solid {DM_BORDER}; border-radius:8px; padding:10px 16px;
    color:{DM_MUTED}; font-size:0.82rem; margin-bottom:12px; }}
.asof b {{ color:{DM_TEXT}; }}
.note {{ background:{DM_SURFACE}; border:1px solid {DM_BORDER}; border-left:4px solid {CLAY}; border-radius:8px;
    padding:10px 16px; font-size:.88rem; color:{DM_TEXT}; margin:10px 0; }}
.disclaimer {{ color:{DM_MUTED}; font-size:.72rem; line-height:1.6; text-align:center; padding:0 24px 24px; }}
hr {{ border-color:{DM_BORDER}; }}
{table_wm}
#MainMenu, footer {{ visibility:hidden; }}
.stDeployButton {{ display:none; }}
</style>""", unsafe_allow_html=True)


def header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="dash-header"><img src="{LOGO_URL}"><div class="t"><b>{title}</b>'
                f'<div>{subtitle}</div></div></div>', unsafe_allow_html=True)


def sec(text: str) -> None:
    st.markdown(f'<div class="sec-header">{text}</div>', unsafe_allow_html=True)


def asof(html: str) -> None:
    st.markdown(f'<div class="asof">{html}</div>', unsafe_allow_html=True)


def note(html: str) -> None:
    st.markdown(f'<div class="note">{html}</div>', unsafe_allow_html=True)


def tile_html(label: str, value: str, delta: str | None = None, sub: str = "", color: str | None = None) -> str:
    style = f' style="border-top-color:{color}"' if color else ""
    d = ""
    if delta:
        s = str(delta).strip()
        arrow = "" if not s else ("▼ " if s[0] in "-−" else ("▲ " if s[0] in "+" else ""))
        d = f'<div class="tile-delta-neu">{arrow}{s.lstrip("+-−")}</div>' if arrow else f'<div class="tile-delta-neu">{s}</div>'
    sub_html = f'<div class="tile-sub">{sub}</div>' if sub else ""
    return f'<div class="tile"{style}><div class="tile-label">{label}</div><div class="tile-value">{value}</div>{d}{sub_html}</div>'


def metric(col, label: str, value: str, delta: str | None = None, sub: str = "", color: str | None = None) -> None:
    """A JSA tile in a column. Deltas are neutral-coloured: a rise is not good or bad news by itself."""
    col.markdown(tile_html(label, value, delta, sub, color), unsafe_allow_html=True)


def style_fig(fig: go.Figure, height: int = 360, yaxis_title: str | None = None, legend: bool = True,
              hovermode: str = "x unified") -> go.Figure:
    wm = watermark_uri()
    if wm:
        fig.add_layout_image(dict(source=wm, xref="paper", yref="paper", x=0.5, y=0.5, sizex=0.5, sizey=0.5,
                                  xanchor="center", yanchor="middle", sizing="contain",
                                  opacity=WATERMARK_OPACITY, layer="below"))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor=DM_SURFACE,
                      plot_bgcolor=DM_SURFACE, hovermode=hovermode, showlegend=legend,
                      legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11, color=DM_MUTED)),
                      font=dict(color=DM_TEXT))
    fig.update_xaxes(**AXIS)
    fig.update_yaxes(**AXIS)
    if yaxis_title:
        fig.update_yaxes(title=yaxis_title)
    return fig


def table(df, key: str | None = None, **kwargs) -> None:
    """A dataframe inside the keyed wrapper the watermark CSS targets."""
    with st.container(key=f"tablewrap_{key or next(_tbl)}"):
        st.dataframe(df, **kwargs)


def disclaimer() -> None:
    st.markdown("<hr style='margin-top:32px;margin-bottom:16px'>", unsafe_allow_html=True)
    st.markdown(f'<div class="disclaimer">{DISCLAIMER.format(year=datetime.now().year).replace("&", "&amp;")}</div>',
                unsafe_allow_html=True)
