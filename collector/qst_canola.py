"""Collect ICE canola (RS) settlements from a running QST desktop into Snowflake.

    python qst_canola.py probe            # try symbol spellings, print raw replies (run once, QST open)
    python qst_canola.py run              # snapshot the front RS contracts, store settled prices
    python qst_canola.py run --dry-run    # same, print instead of writing
    python qst_canola.py run --date 2026-10-12 --contracts RSX26,RSF27

Rows go to JSA.COST_OF_CARRY.ICE_CANOLA_SETTLES (ticker, date, settle), merged on (ticker, date).
A contract is only stored once QST reports a settlement for it (ST present and INF not "T").
Every raw reply is appended to qst_canola_log.jsonl beside this script for auditing.

Snowflake credentials: the usual SNOWFLAKE_* variables (key-pair), from the environment, ./.env,
or ~/.snowflake/collector.env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from qst_client import QstClient, QstError  # noqa: E402

HERE = Path(__file__).parent
LOG = HERE / "qst_canola_log.jsonl"
RS_MONTHS = {"F": 1, "H": 3, "K": 5, "N": 7, "X": 11}
CENTRAL = ZoneInfo("America/Chicago")
TABLE = "ICE_CANOLA_SETTLES"
SCHEMA = "COST_OF_CARRY"
# how many RS contracts to bank each run (the front chain is all the dashboard needs)
N_CONTRACTS = 4
SYMBOL_FORMAT = os.environ.get("QST_SYMBOL_FORMAT", "RS{m}{yy}")   # e.g. RSX26 — confirm with `probe`

DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABLE} (
    TICKER    VARCHAR(16) NOT NULL,   -- ICE canola contract as the app spells it, e.g. RSX26
    DATE      DATE        NOT NULL,   -- trading date
    SETTLE    FLOAT       NOT NULL,   -- CAD per tonne
    SOURCE    VARCHAR(16),
    LOADED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
)
"""


def front_contracts(today: date, n: int = N_CONTRACTS) -> list[tuple[str, str, int]]:
    """[(letter, ticker, year)] for the next n RS contracts that have not yet reached their delivery month."""
    out = []
    for year in (today.year, today.year + 1, today.year + 2):
        for letter, m in sorted(RS_MONTHS.items(), key=lambda kv: kv[1]):
            if date(year, m, 1) > today:
                out.append((letter, f"RS{letter}{year % 100:02d}", year))
    return out[:n]


def qst_symbol(letter: str, year: int) -> str:
    return SYMBOL_FORMAT.format(m=letter, yy=f"{year % 100:02d}", y=year % 10, yyyy=year, root="RS")


def _num(v) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
        return x if x else None
    except (TypeError, ValueError):
        return None


def parse_settlement(snap: dict) -> float | None:
    """The settlement from a snapshot reply if QST says the contract has settled, else None."""
    if snap.get("RES") != "OK":
        return None
    settle = _num(snap.get("ST"))
    if settle is None or snap.get("INF") == "T":
        return None
    return settle


def probe(client: QstClient, today: date) -> None:
    letter, ticker, year = front_contracts(today)[0]
    yy = f"{year % 100:02d}"
    candidates = [f"RS{letter}{yy}", f"RS{letter}{year % 10}", f"RS {letter}{yy}", f"/RS{letter}{yy}",
                  f"RS{letter}{year}", f"RS.{letter}{yy}", f"ICE.RS{letter}{yy}", f"CAN{letter}{yy}"]
    for c in candidates:
        snap = client.snapshot(c)
        keep = {k: snap.get(k) for k in ("RES", "MSG", "INS", "TR", "ST", "PS", "CL", "INF", "TT", "VL")}
        print(f"{c:12} -> {keep}")
    print("\nSet QST_SYMBOL_FORMAT to the spelling that returned RES=OK, e.g. RS{m}{yy} for RSX26.")


def collect(client: QstClient, today: date, trade_date: date, contracts: list[str] | None) -> list[dict]:
    if contracts:
        wanted = [(c, c) for c in contracts]
    else:
        wanted = [(qst_symbol(l, y), t) for l, t, y in front_contracts(today)]
    rows = []
    with LOG.open("a", encoding="utf-8") as log:
        for symbol, ticker in wanted:
            snap = client.snapshot(symbol)
            log.write(json.dumps({"at": datetime.now(CENTRAL).isoformat(), "symbol": symbol, "reply": snap}) + "\n")
            settle = parse_settlement(snap)
            if settle is None:
                print(f"{ticker}: no settlement yet (RES={snap.get('RES')}, INF={snap.get('INF')}, ST={snap.get('ST')!r})")
                continue
            rows.append({"ticker": ticker, "date": trade_date, "settle": settle})
            print(f"{ticker}: settle {settle}")
    return rows


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (HERE / ".env", Path.home() / ".snowflake" / "collector.env", Path(os.environ.get("COLLECTOR_ENV", ""))):
        if p and p.is_file():
            load_dotenv(p)


def _connect():
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization

    pem = open(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], "rb").read()
    pwd = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(pem, password=pwd.encode() if pwd else None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
        role=os.environ.get("SNOWFLAKE_ROLE") or "ACCOUNTADMIN",
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE") or "COMPUTE_WH",
        database=os.environ.get("SNOWFLAKE_DATABASE") or "JSA", schema=SCHEMA, private_key=der)


def merge(rows: list[dict]) -> int:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute(f"CREATE TEMPORARY TABLE ICE_STAGE LIKE {SCHEMA}.{TABLE}")
        cur.executemany("INSERT INTO ICE_STAGE (TICKER, DATE, SETTLE, SOURCE) VALUES (%s,%s,%s,'QST')",
                        [(r["ticker"], r["date"].isoformat(), r["settle"]) for r in rows])
        cur.execute(f"""
            MERGE INTO {SCHEMA}.{TABLE} t USING ICE_STAGE s ON t.TICKER = s.TICKER AND t.DATE = s.DATE
            WHEN MATCHED THEN UPDATE SET SETTLE = s.SETTLE, SOURCE = s.SOURCE, LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (TICKER, DATE, SETTLE, SOURCE) VALUES (s.TICKER, s.DATE, s.SETTLE, s.SOURCE)
        """)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["probe", "run"])
    ap.add_argument("--dry-run", action="store_true", help="print rows, do not write to Snowflake")
    ap.add_argument("--date", help="trade date YYYY-MM-DD (default: today, US Central)")
    ap.add_argument("--contracts", help="comma list of QST contract symbols to snapshot")
    ap.add_argument("--ws-url", help="override the QST websocket URL (testing)")
    ap.add_argument("--validate-url", help="override the QST validate URL (testing)")
    args = ap.parse_args(argv)

    today = datetime.now(CENTRAL).date()
    trade_date = date.fromisoformat(args.date) if args.date else today
    if args.mode == "run" and trade_date.weekday() >= 5:
        print(f"{trade_date} is a weekend; nothing to collect.")
        return 0
    kw = {k: v for k, v in (("ws_url", args.ws_url), ("validate_url", args.validate_url)) if v}
    try:
        with QstClient(**kw) as client:
            if args.mode == "probe":
                probe(client, today)
                return 0
            rows = collect(client, today, trade_date, args.contracts.split(",") if args.contracts else None)
    except QstError as exc:
        print(f"QST error: {exc}")
        return 2
    if not rows:
        print("Nothing settled yet; run again after the ICE canola settlement (about 1:15 pm Central).")
        return 1
    if args.dry_run:
        print(f"[dry run] would merge {len(rows)} rows")
        return 0
    _load_env()
    print(f"merged {merge(rows)} rows into {SCHEMA}.{TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
