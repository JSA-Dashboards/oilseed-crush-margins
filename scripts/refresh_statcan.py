"""Refresh the bundled Statistics Canada snapshot (data/statcan_*.csv).

    python scripts/refresh_statcan.py
The app fetches live (cached 12 h) and only falls back to this snapshot.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import statcan

d = statcan.build_all()
statcan.save_snapshot(d)
print({k: v.shape for k, v in d.items()})
