"""Data tools (T0): load the meter CSV, check its quality, forecast tomorrow's base load, forecast rooftop solar."""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import urllib.request
from pathlib import Path

import pandas as pd

from .tariff import N_SLOTS, SLOTS_PER_HOUR

# Hourly shape of the non-shiftable plant load (kW) on a working day. Team assumption for the synthetic profile.
_WEEKDAY_SHAPE_KW = [260, 260, 255, 255, 260, 290, 350, 370, 400, 430, 430, 430,
                     425, 410, 420, 425, 420, 400, 370, 360, 350, 330, 300, 280]
_WEEKDAY_PF = [0.97, 0.97, 0.97, 0.97, 0.97, 0.96, 0.96, 0.96, 0.95, 0.95, 0.95, 0.95,
               0.95, 0.95, 0.95, 0.95, 0.95, 0.96, 0.96, 0.96, 0.96, 0.96, 0.97, 0.97]


def synthetic_history(days: int = 28, end: dt.date | None = None, seed: int = 7) -> pd.DataFrame:
    """A labelled synthetic 15-minute load history with the shape of a small steel or forging plant.

    Used only when the real UCI file is not present. Sunday runs at 55% of the weekday shape.
    """
    rng = random.Random(seed)
    end = end or dt.date(2026, 11, 20)
    start = end - dt.timedelta(days=days - 1)
    rows = []
    for d in range(days):
        day = start + dt.timedelta(days=d)
        day_factor = 0.55 if day.weekday() == 6 else 1.0
        day_noise = rng.gauss(1.0, 0.02)
        for k in range(N_SLOTS):
            h = k // SLOTS_PER_HOUR
            kw = _WEEKDAY_SHAPE_KW[h] * day_factor * day_noise * rng.gauss(1.0, 0.03)
            ts = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=15 * k)
            rows.append({"ts": ts, "kw": max(kw, 0.0), "pf": _WEEKDAY_PF[h]})
    df = pd.DataFrame(rows)
    df.attrs["source"] = "Synthetic profile (team assumption, shaped like a small steel plant). Load the UCI CSV for real data."
    return df


def synthetic_actual_day(day: dt.date, seed: int = 99) -> pd.DataFrame:
    """Next-day 'actual' base load for the savings check when no real meter file is uploaded."""
    df = synthetic_history(days=1, end=day, seed=seed)
    df["kw"] = df["kw"] * 1.03  # the plant ran a little heavier than forecast
    df.attrs["source"] = "Simulated next-day meter data (demo)"
    return df


AUTO_SCALE_TARGET_PEAK_KW = 430.0   # the demo site's typical daytime base load (team assumption)


def load_uci_csv(path, scale: float | None = None) -> pd.DataFrame:
    """Load the UCI 'Steel Industry Energy Consumption' file (DAEWOO Steel, Gwangyang, South Korea).

    Usage_kWh is energy per 15 minutes, so kW = Usage_kWh x 4. Power factor comes from the lagging PF column (%).
    `scale` sizes the real shape to the demo site. None = automatic: the average working-day profile is scaled so
    its peak is 430 kW, the demo site's base load. The scale used is recorded and shown in the app.
    """
    raw = pd.read_csv(path)
    ts = pd.to_datetime(raw["date"], dayfirst=True, errors="coerce")
    pf = (raw.get("Lagging_Current_Power_Factor", pd.Series([95.0] * len(raw))) / 100.0).clip(lower=0.5, upper=1.0)
    df = pd.DataFrame({"ts": ts, "kw": raw["Usage_kWh"].astype(float) * 4, "pf": pf.fillna(0.95)})
    # UCI stamps the end of each 15-minute interval; shift to interval start
    df["ts"] = df["ts"] - pd.Timedelta(minutes=15)
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    if scale is None:
        wk = df[df["ts"].dt.dayofweek < 5]
        prof = wk.groupby(wk["ts"].dt.hour)["kw"].mean()
        scale = AUTO_SCALE_TARGET_PEAK_KW / max(float(prof.max()), 1.0)
    df["kw"] = df["kw"] * scale
    df.attrs["scale"] = scale
    df.attrs["source"] = (f"Uploaded meter CSV in the UCI steel-dataset format, load scaled x{scale:.2f} to the demo site. "
                          f"The UCI file is DAEWOO Steel, Gwangyang, South Korea, not a Maharashtra plant.")
    return df


def validate_load(df: pd.DataFrame, spike_x: float = 3.0, pf_min: float = 0.6, max_gap_h: float = 2.0, stuck_h: float = 3.0,
                  shutdown_pct: float = 0.2, stale_days: int = 7, contract_kva: float | None = None,
                  plan_date: dt.date | None = None) -> list[dict]:
    """Data-quality checks before anything is forecast. Each check says what it found and what the agent does about it.
    Every limit can be changed in the Control room (Check box) or on the Guardrails screen."""
    issues = []
    if df.empty:
        return [{"level": "error", "text": "No meter rows found."}]
    df = df.sort_values("ts")
    # 1. timestamps: duplicates, off-grid times, gaps
    dup = int(df["ts"].duplicated().sum())
    if dup:
        issues.append({"level": "warning", "text": f"{dup} repeated timestamps (the same 15 minutes twice); the average is used."})
    off = int(((df["ts"].dt.minute % 15) != 0).sum() + (df["ts"].dt.second != 0).sum())
    if off:
        issues.append({"level": "warning", "text": f"{off} readings not on a 15-minute mark; they are moved to the nearest mark."})
    expected = pd.date_range(df["ts"].min().floor("15min"), df["ts"].max().floor("15min"), freq="15min")
    missing = len(expected) - df["ts"].dt.floor("15min").nunique()
    if missing > 0:
        issues.append({"level": "warning", "text": f"{missing} fifteen-minute readings missing; short gaps are filled by interpolation."})
    gaps = df["ts"].diff().dt.total_seconds().div(3600)
    if len(gaps) and gaps.max() > max_gap_h:
        at = df["ts"].iloc[int(gaps.values.argmax())]
        issues.append({"level": "warning", "text": f"Longest gap {gaps.max():.1f} h, ending {at:%d %b %H:%M} (limit {max_gap_h:g} h). "
                                                   "Filling it is a guess: check the meter link for that day."})
    # 2. values: negatives, spikes, stuck meter
    if (df["kw"] < 0).any():
        issues.append({"level": "error", "text": f"{int((df['kw'] < 0).sum())} negative load readings (check CT polarity)."})
    # A plant swings from light night load to full day load, so "5x the median" flags normal shifts. A CT-ratio error
    # shows up as readings far above anything the plant normally draws: above 3x its 99th percentile.
    p99 = float(df["kw"].quantile(0.99))
    spikes = int((df["kw"] > spike_x * max(p99, 1)).sum())
    if spikes:
        issues.append({"level": "warning", "text": f"{spikes} readings above {spike_x:g}x the plant's 99th-percentile load (possible CT-ratio error)."})
    run_len = int(stuck_h * 4)
    same = df["kw"].round(3).ne(df["kw"].round(3).shift()).cumsum()
    runs = df.groupby(same)["kw"].agg(["size", "first"])
    stuck = runs[(runs["size"] >= run_len) & (runs["first"] > 0)]
    if len(stuck):
        issues.append({"level": "warning", "text": f"Meter looks stuck {len(stuck)} time(s): the same non-zero value for {stuck_h:g} h or more. "
                                                   "Those hours may be copied, not measured."})
    # 3. units: does the size of the load fit the plant's contract?
    if contract_kva:
        if p99 > 1.5 * contract_kva:
            issues.append({"level": "error", "text": f"Top load {p99:,.0f} kW is far above the {contract_kva:,.0f} kVA contract: the file may be in W or kWh per hour, "
                                                     "or for a bigger site. Check the units."})
        elif p99 < 0.02 * contract_kva:
            issues.append({"level": "warning", "text": f"Top load {p99:,.1f} kW is tiny next to the {contract_kva:,.0f} kVA contract: the file may be in MW. Check the units."})
    # 4. days: shutdowns and holidays, part days
    day = df.set_index("ts")["kw"].resample("D")
    energy, n = day.mean(), day.size()
    full = energy[n >= 90]
    if len(full) >= 3:
        wd_med = full.groupby(full.index.dayofweek).transform("median")      # a light Sunday is normal; a light Tuesday is not
        low = full[full < shutdown_pct * wd_med]
        if len(low):
            issues.append({"level": "warning", "text": f"{len(low)} day(s) with under {shutdown_pct:.0%} of a usual day for that weekday (holiday or shutdown?): "
                                                       + ", ".join(f"{d:%d %b}" for d in low.index[:5]) + (" …" if len(low) > 5 else "")
                                                       + ". They pull the same-weekday forecast down."})
    part = int((n[(n > 0) & (n < 90)]).size)
    if part:
        issues.append({"level": "warning", "text": f"{part} part day(s) with fewer than 90 of 96 readings; they are not used as a whole day."})
    # 5. power factor
    bad_pf = (df["pf"] < pf_min) | (df["pf"] > 1.0)
    low_pf = int(bad_pf.sum())
    if low_pf:
        light = float((df.loc[bad_pf, "kw"] < df["kw"].median()).mean())
        where = ", mostly at light load" if light >= 0.7 else ""
        issues.append({"level": "warning", "text": f"{low_pf:,} readings ({low_pf / len(df):.0%}) with power factor outside {pf_min:g} to 1.0"
                                                   f"{where}; kVAh there is less certain."})
    # 6. freshness: is the newest reading close enough to the day being planned?
    if plan_date is not None:
        last = df["ts"].max().date()
        old = (plan_date - last).days - 1
        if old > stale_days:
            issues.append({"level": "warning", "text": f"Newest reading is {old} days before the plan day (limit {stale_days}). "
                                                       "Recent changes at the plant are not in the forecast."})
    if not issues:
        issues.append({"level": "ok", "text": f"{len(df):,} readings: no gaps, spikes, stuck meter, odd days or unit problems."})
    return issues


def _day_matrix(df: pd.DataFrame, col: str) -> pd.DataFrame:
    d = df.set_index("ts")[col].resample("15min").mean().interpolate(limit_direction="both")
    frame = d.to_frame(col)
    frame["day"] = frame.index.normalize()
    frame["slot"] = frame.index.hour * SLOTS_PER_HOUR + frame.index.minute // 15
    return frame.pivot_table(index="day", columns="slot", values=col)


def full_days(df: pd.DataFrame) -> list[dt.date]:
    """Days that have a complete set of 96 fifteen-minute readings."""
    d = df.set_index("ts")["kw"].resample("15min").count()
    per_day = d.groupby(d.index.normalize()).sum()
    return [x.date() for x in per_day[per_day >= N_SLOTS].index]


def plan_date_range(df: pd.DataFrame, min_history_days: int = 14) -> tuple[dt.date, dt.date, dt.date]:
    """(earliest day we can plan, latest day with real readings, tomorrow). Days with real readings can be replayed."""
    days = full_days(df)
    if len(days) <= min_history_days:
        raise ValueError(f"Need more than {min_history_days} full days of meter data to forecast; found {len(days)}.")
    return days[min_history_days], days[-1], days[-1] + dt.timedelta(days=1)


def day_actuals(df: pd.DataFrame, day: dt.date) -> pd.DataFrame | None:
    """The real meter readings of one day, if the file holds all 96 of them."""
    sel = df[df["ts"].dt.date == day]
    return sel.reset_index(drop=True) if len(sel) >= N_SLOTS else None


def forecast_load(df: pd.DataFrame, bias: float = 1.0, target: dt.date | None = None, weeks: int = 4,
                  method: str = "weekday") -> dict:
    """Base load for the target day: mean of the same weekday over the `weeks` weeks before it (seasonal naive), times a
    learned bias. method="recent" uses the last 7 days instead. Only readings from before the target day are used.
    No target means the day after the data ends."""
    if target is not None:
        df = df[df["ts"] < pd.Timestamp(target)]
    kw = _day_matrix(df, "kw").dropna()
    pf = _day_matrix(df, "pf").dropna()
    if kw.empty:
        raise ValueError("No meter history before the chosen day.")
    last_day = kw.index.max()
    target = pd.Timestamp(target) if target is not None else last_day + pd.Timedelta(days=1)
    if method == "recent":
        same, method = kw.tail(7), "last 7 days"
    else:
        same = kw[kw.index.dayofweek == target.dayofweek].tail(weeks)
        method = f"same weekday, last {weeks} week{'s' if weeks != 1 else ''}"
        if len(same) < 2 and weeks > 1:
            same = kw.tail(7)
            method = "last 7 days"
    profile = (same.mean(axis=0) * bias).reindex(range(N_SLOTS)).interpolate().tolist()
    pf_prof = pf.tail(7).mean(axis=0).reindex(range(N_SLOTS)).fillna(0.95).tolist()
    return {"date": target.date(), "kw": profile, "pf": pf_prof, "method": method, "bias": bias,
            "weekday": target.day_name()}


def clear_sky_pv(kwp: float, cloud_factor: float = 1.0) -> list[float]:
    """Simple rooftop PV shape: sine between 06:30 and 18:00, 80% system derate. Team assumption."""
    out = []
    for k in range(N_SLOTS):
        h = (k + 0.5) / SLOTS_PER_HOUR
        v = math.sin(math.pi * (h - 6.5) / 11.5) if 6.5 < h < 18.0 else 0.0
        out.append(max(0.0, kwp * 0.8 * v * cloud_factor))
    return out


def solar_forecast(kwp: float, lat: float, lon: float, day: dt.date, cloudy_override: bool | None = None,
                   timeout: float = 4.0) -> dict:
    """Try Open-Meteo hourly shortwave radiation; fall back to the clear-sky shape if offline."""
    if cloudy_override is not None:
        cf = 0.35 if cloudy_override else 1.0
        return {"kw": clear_sky_pv(kwp, cf), "source": "Clear-sky model" + (", cloud cover forecast (x0.35)" if cloudy_override else ""),
                "cloudy": bool(cloudy_override)}
    past = day < dt.date.today()
    host = "archive-api" if past else "api"
    path = "archive" if past else "forecast"
    label = "Open-Meteo recorded radiation (past day)" if past else "Open-Meteo shortwave radiation forecast"
    url = ("https://{host}.open-meteo.com/v1/{path}?latitude={:.2f}&longitude={:.2f}&hourly=shortwave_radiation"
           "&timezone=Asia%2FKolkata&start_date={d}&end_date={d}").format(lat, lon, d=day.isoformat(), host=host, path=path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            ghi = json.load(r)["hourly"]["shortwave_radiation"]
        kw = []
        for k in range(N_SLOTS):
            kw.append(max(0.0, kwp * 0.8 * float(ghi[k // SLOTS_PER_HOUR] or 0) / 1000.0))
        return {"kw": kw, "source": label, "cloudy": max(kw) < 0.5 * kwp * 0.8}
    except Exception:
        return {"kw": clear_sky_pv(kwp), "source": "Clear-sky model (Open-Meteo unreachable)", "cloudy": False}


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
