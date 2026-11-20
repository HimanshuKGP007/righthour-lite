"""Live feed helpers: the seam where a real meter feed plugs in.

Without hardware, the last days of the meter file are held back and released one day at a time (a replayed feed), or CSV
files dropped into incoming/ are merged in. A real feed would only need to produce the same ts / kw / pf frames.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import data as D

FOLDER_DEFAULT = Path(__file__).resolve().parent.parent / "incoming"


def _like(df: pd.DataFrame, src: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    df.attrs = dict(src.attrs)
    return df


def split_for_live(hist: pd.DataFrame, hold_days: int = 7) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    """Hold back the last `hold_days` full days as the 'future'. Returns (visible history, one frame per day, oldest first)."""
    days = D.full_days(hist)
    if len(days) < hold_days + 15:
        raise ValueError(f"Need at least {hold_days + 15} full days to replay live ({hold_days} held back, 15 to forecast from); found {len(days)}.")
    held = days[-hold_days:]
    first = pd.Timestamp(held[0])
    visible = _like(hist[hist["ts"] < first], hist)
    future = [_like(hist[hist["ts"].dt.date == d], hist) for d in held]
    return visible, future


def release_next_day(hist: pd.DataFrame, future_days: list[pd.DataFrame]) -> tuple[pd.DataFrame, list[pd.DataFrame], dt.date]:
    """Append the next held-back day. Raises IndexError when none is left."""
    if not future_days:
        raise IndexError("No more days to release.")
    nxt, rest = future_days[0], future_days[1:]
    merged, _ = merge_readings(hist, nxt)
    return merged, rest, nxt["ts"].iloc[0].date()


def merge_readings(hist: pd.DataFrame, new_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Add readings to the history; a repeated timestamp keeps the new value. Returns (merged, number of new timestamps)."""
    before = set(hist["ts"])
    merged = pd.concat([hist, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset="ts", keep="last").sort_values("ts")
    n_new = len(set(merged["ts"]) - before)
    return _like(merged, hist), n_new


def scan_folder(folder: Path, seen: dict[str, float], scale: float | None = None
                ) -> tuple[list[tuple[str, pd.DataFrame]], dict[str, float], list[str]]:
    """Load every new or changed *.csv in the folder. A bad file is skipped and reported, never raised."""
    frames: list[tuple[str, pd.DataFrame]] = []
    seen2, errors = dict(seen), []
    folder = Path(folder)
    if not folder.is_dir():
        return frames, seen2, errors
    for p in sorted(folder.glob("*.csv")):
        m = p.stat().st_mtime
        if seen.get(p.name) == m:
            continue
        seen2[p.name] = m
        try:
            frames.append((p.name, D.load_uci_csv(p, scale)))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p.name}: {e}")
    return frames, seen2, errors


def is_plan_due(now: dt.datetime, plan_time: dt.time, last_auto_date: dt.date | None) -> bool:
    """True once the clock passes the daily plan time, if no automatic plan has run today."""
    return now.time() >= plan_time and last_auto_date != now.date()


def describe_feed(hist: pd.DataFrame, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    last = hist["ts"].max()
    return {"first_ts": hist["ts"].min().isoformat(), "last_ts": last.isoformat(), "n_rows": int(len(hist)),
            "n_full_days": len(D.full_days(hist)), "hours_behind": round((pd.Timestamp(now) - last).total_seconds() / 3600, 1)}
