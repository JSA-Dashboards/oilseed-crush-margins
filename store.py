"""Read futures settlements from Snowflake (JSA.COST_OF_CARRY.FUTURES_SETTLEMENTS).

The droplet's daily settlement-archive job keeps this table current, so the app needs no
Massive key and makes one small query instead of ~25 API calls. Credentials are the
fleet-standard SNOWFLAKE_* Streamlit secrets; a read-only role is all it needs.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

PRODUCTS = ("ZS", "ZM", "ZL", "6C")


def _secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default) or default)
    except Exception:
        return default


def diagnostics() -> dict:
    """Which Snowflake secret names the app can see (names only, never values), plus a live test."""
    names = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE",
             "SNOWFLAKE_PRIVATE_KEY"]
    out = {n: bool(_secret(n)) for n in names}
    try:
        out["top-level secret names seen"] = ", ".join(sorted(str(k) for k in st.secrets.keys())) or "(none)"
    except Exception as exc:
        out["top-level secret names seen"] = f"(unreadable: {type(exc).__name__})"
    pem = _secret("SNOWFLAKE_PRIVATE_KEY")
    if pem:
        out["key has BEGIN/END lines"] = "BEGIN PRIVATE KEY" in pem and "END PRIVATE KEY" in pem
        lines = [ln for ln in pem.replace("\\n", "\n").splitlines()]
        body = [ln.strip() for ln in lines if ln.strip() and "-----" not in ln]
        alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        bad = [f"line {i + 1} col {j + 1}: char code {ord(c)}"
               for i, ln in enumerate(body) for j, c in enumerate(ln) if c not in alphabet][:5]
        out["key shape"] = (f"{len(pem)} chars, {len(lines)} lines, {len(body)} body lines "
                            f"(a 2048-bit key is about 1700 chars / 28 lines); "
                            f"non-base64 characters: {bad or 'none'}")
        try:
            import base64
            import hashlib
            from cryptography.hazmat.primitives import serialization

            key = serialization.load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
            der = key.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
            out["secret key fingerprint"] = "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()
        except Exception as exc:
            out["secret key fingerprint"] = f"(could not parse key: {type(exc).__name__})"
    if configured():
        try:
            conn = _connect()
            conn.cursor().execute("SELECT 1")
            conn.close()
            out["login test"] = "OK"
        except Exception as exc:
            out["login test"] = f"FAILED: {str(exc)[:160]}"
    return out


def configured() -> bool:
    return bool(_secret("SNOWFLAKE_ACCOUNT") and _secret("SNOWFLAKE_USER")
                and _secret("SNOWFLAKE_PRIVATE_KEY"))


def _connect():
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    pem = _secret("SNOWFLAKE_PRIVATE_KEY").replace("\n", "\n").encode()
    pwd = _secret("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(pem, password=pwd.encode() if pwd else None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return snowflake.connector.connect(
        account=_secret("SNOWFLAKE_ACCOUNT"), user=_secret("SNOWFLAKE_USER"),
        role=_secret("SNOWFLAKE_ROLE", "OILSEED_CRUSH_ROLE"),
        warehouse=_secret("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database="JSA", schema="COST_OF_CARRY", private_key=der)


def fetch_settlements(since) -> pd.DataFrame:
    """ticker / date / settle for ZS, ZM, ZL, 6C from `since` forward."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT TICKER, DATE, SETTLE FROM FUTURES_SETTLEMENTS "
            f"WHERE PRODUCT_CODE IN ({','.join(['%s'] * len(PRODUCTS))}) AND DATE >= %s",
            (*PRODUCTS, since))
        return pd.DataFrame(cur.fetchall(), columns=["ticker", "date", "settle"])
    finally:
        conn.close()


def fetch_canola_cash(since) -> pd.DataFrame:
    """Daily Fastmarkets canola cash: oil (fob Los Angeles, ¢/lb) and meal (fob Velva ND, $/short ton)."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DATE, SYMBOL, MID FROM CANOLA_CASH WHERE DATE >= %s "
                    "AND SYMBOL IN ('AG-RPS-0001','AG-RPS-0003')", (since,))
        raw = pd.DataFrame(cur.fetchall(), columns=["date", "symbol", "mid"])
    finally:
        conn.close()
    if raw.empty:
        return pd.DataFrame(columns=["oil", "meal"])
    out = raw.pivot(index="date", columns="symbol", values="mid").rename(
        columns={"AG-RPS-0001": "oil", "AG-RPS-0003": "meal"})
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def fetch_ice_settles(since) -> pd.DataFrame:
    """Per-contract ICE canola settles (CAD/mt) banked daily by the QST collector."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT TICKER, DATE, SETTLE FROM ICE_CANOLA_SETTLES WHERE DATE >= %s", (since,))
        return pd.DataFrame(cur.fetchall(), columns=["ticker", "date", "settle"])
    finally:
        conn.close()
