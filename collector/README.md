# QST canola collector

Banks ICE canola (RS) settlements from a running **QST Professional** desktop into
`JSA.COST_OF_CARRY.ICE_CANOLA_SETTLES`. The dashboard reads that table and extends the bundled
seed history with it. Runs on a PC that has QST open; it is not a cloud job.

## One-time setup

1. **QST:** Ctrl+P > Desktop > tick **Enable Extensions API**, press OK, log out *with save settings*,
   log back in. (QST must have granted JSA API permission.)
2. **Python packages on that PC:** `pip install websockets snowflake-connector-python cryptography python-dotenv`
3. **Snowflake credentials:** put the usual key-pair variables in `~/.snowflake/collector.env`
   (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_PRIVATE_KEY_PATH`,
   `SNOWFLAKE_PRIVATE_KEY_PWD`, `SNOWFLAKE_WAREHOUSE`). Prefer a writer role limited to this one table.
4. **Find the contract spelling:** with QST open, run `python collector\qst_canola.py probe`.
   Use the spelling that returns `RES=OK`; if it is not `RS{m}{yy}` (e.g. `RSX26`), set
   `QST_SYMBOL_FORMAT` (placeholders `{m}` month letter, `{yy}` 2-digit year, `{y}` 1-digit, `{yyyy}`).
5. **Dry run after the ICE settlement** (about 1:15 pm Central):
   `python collector\qst_canola.py run --dry-run`. Check `qst_canola_log.jsonl` to confirm which
   field carries the settlement (`ST`) and what `INF` reads once settled.
6. **Read access for the app**, in Snowsight as ACCOUNTADMIN (after the first real run creates the table):
   `GRANT SELECT ON TABLE JSA.COST_OF_CARRY.ICE_CANOLA_SETTLES TO ROLE OILSEED_CRUSH_ROLE;`
7. **Schedule it** (weekdays 2:30 pm Central; adjust if the PC is not on Central time):
   `schtasks /Create /TN "QST Canola Settles" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 14:30 /TR "python C:\path\to\collector\qst_canola.py run"`

## Behavior

- Banks the next four RS contracts that have not reached their delivery month.
- Stores a contract only once QST reports a settlement (`ST` present and `INF` not `T`), never an
  intraday price. Re-runs are safe: rows merge on (ticker, date).
- If QST is closed the run exits with a clear message and writes nothing; the app then simply stays on
  the last banked settle.
- Tests: `python -m unittest collector/test_collector.py` (uses a mock QST server).

## Fallback: the analyst workbook (no QST needed)

`workbook_canola.py` reads the saved `Canola Prices.xlsx` (the Barchart add-in tab) and loads the last
10 days into the same table with source `WORKBOOK`. It never overwrites a QST value for the same
contract and day, and it warns when the workbook's newest date is more than 3 weekdays old.

    python collector\workbook_canola.py --dry-run     # preview
    python collector\workbook_canola.py               # load

It is only as fresh as the last time the workbook was **refreshed in Excel and saved**. To automate,
schedule it a little after whoever refreshes the workbook (weekdays 5:00 pm):

    schtasks /Create /TN "Canola Workbook Load" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:00 /TR "python C:\path	o\collector\workbook_canola.py"

**Before Nov 1:** the workbook has no RSF27 column yet. RSX26 stops being the front contract on
Nov 1, so add RSF27 (then RSH27, RSK27, ...) as new columns in the Barchart tab, next to the others,
or the live seed will stop at Oct 31. The loader picks new columns up automatically.
