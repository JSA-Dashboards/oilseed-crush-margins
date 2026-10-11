"""Refresh the bundled COPA snapshot (data/copa_*.csv) from copacanada.com.

    python scripts/refresh_copa.py
The app scrapes live (cached 12 h) and only falls back to this snapshot.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import copa

d = copa.build_all()
copa.save_snapshot(d)
print({k: v.shape for k, v in d.items()})
