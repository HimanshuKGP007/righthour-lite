"""Tariff tools: slot rates, Rule 8A checks and the bill calculator (all T0, no language model)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SLOTS_PER_HOUR = 4          # 15-minute data
N_SLOTS = 24 * SLOTS_PER_HOUR
DT_H = 1 / SLOTS_PER_HOUR   # hours per slot

REQUIRED_SLOT_KEYS = {"start_h", "end_h", "type", "rate"}
SLOT_TYPES = {"normal", "solar", "peak"}


@dataclass
class TariffIssue:
    level: str   # "error" blocks use; "warning" is shown to the person
    text: str


def load_tariff(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


PRICE_RULES = {"peak_x": 1.2, "solar_off": 0.2, "spread_x": 1.5}   # Rule 8A floors and the deck's spread; the manager can change them


def price_gauges(t: dict, rules: dict | None = None) -> list[dict]:
    """The three price checks as numbers, for a picture: what the tariff gives against the floor it should meet."""
    r = {**PRICE_RULES, **(rules or {})}
    rate = {s["type"]: s["rate"] for s in t.get("slots") or []}
    n, pk, so = rate.get("normal"), rate.get("peak"), rate.get("solar")
    out = []
    if n and pk:
        out.append({"name": "Peak price", "value": pk / n, "floor": r["peak_x"], "unit": "x normal", "ok": pk / n >= r["peak_x"] - 1e-9, "more_is_ok": True})
    if n and so:
        out.append({"name": "Solar discount", "value": (1 - so / n) * 100, "floor": r["solar_off"] * 100, "unit": "% off normal",
                    "ok": so <= (1 - r["solar_off"]) * n + 1e-9, "more_is_ok": True})
    if pk and so:
        out.append({"name": "Peak vs solar", "value": pk / so, "floor": r["spread_x"], "unit": "x solar", "ok": pk / so >= r["spread_x"] - 1e-9, "more_is_ok": True})
    return out


def validate_tariff(t: dict, rules: dict | None = None) -> list[TariffIssue]:
    """Schema check plus the Rule 8A floors (C&I: peak >= 1.2x normal, solar <= 0.8x normal). `rules` overrides the floors."""
    pr = {**PRICE_RULES, **(rules or {})}
    issues: list[TariffIssue] = []
    slots = t.get("slots") or []
    if not slots:
        return [TariffIssue("error", "Tariff has no time slots.")]
    covered = [0] * 24
    for s in slots:
        if not REQUIRED_SLOT_KEYS <= set(s):
            issues.append(TariffIssue("error", f"Slot {s} is missing {REQUIRED_SLOT_KEYS - set(s)}."))
            continue
        if s["type"] not in SLOT_TYPES:
            issues.append(TariffIssue("error", f"Unknown slot type '{s['type']}'."))
        if not (0 <= s["start_h"] < s["end_h"] <= 24):
            issues.append(TariffIssue("error", f"Slot hours {s['start_h']} to {s['end_h']} are invalid."))
            continue
        if s["rate"] <= 0:
            issues.append(TariffIssue("error", "Rates must be positive."))
        for h in range(int(s["start_h"]), int(s["end_h"])):
            covered[h] += 1
    if any(c != 1 for c in covered):
        issues.append(TariffIssue("error", "Slots must cover every hour of the day exactly once."))
    if any(i.level == "error" for i in issues):
        return issues

    rate = {s["type"]: s["rate"] for s in slots}
    if "normal" in rate:
        n = rate["normal"]
        if "peak" in rate and rate["peak"] < pr["peak_x"] * n - 1e-9:
            issues.append(TariffIssue("warning", f"Peak {rate['peak']:.2f} is {rate['peak']/n:.3f}x normal; the floor is {pr['peak_x']:g}x."))
        if "solar" in rate and rate["solar"] > (1 - pr["solar_off"]) * n + 1e-9:
            issues.append(TariffIssue("warning", f"Solar {rate['solar']:.2f} is only {(1-rate['solar']/n)*100:.1f}% below normal; the floor is {pr['solar_off']*100:g}%."))
    if "peak" in rate and "solar" in rate and rate["peak"] / rate["solar"] < pr["spread_x"] - 1e-9:
        issues.append(TariffIssue("warning", f"Peak is {rate['peak']/rate['solar']:.2f}x solar; the floor is {pr['spread_x']:g}x."))
    if t.get("status") == "illustrative":
        issues.append(TariffIssue("warning", "Rates are illustrative, not from the final MERC order."))
    return issues


def slot_rates(t: dict) -> list[float]:
    """Rate (Rs per kVAh) for each of the 96 fifteen-minute slots."""
    out = [0.0] * N_SLOTS
    for s in t["slots"]:
        for k in range(int(s["start_h"] * SLOTS_PER_HOUR), int(s["end_h"] * SLOTS_PER_HOUR)):
            out[k] = float(s["rate"])
    return out


def slot_types(t: dict) -> list[str]:
    out = [""] * N_SLOTS
    for s in t["slots"]:
        for k in range(int(s["start_h"] * SLOTS_PER_HOUR), int(s["end_h"] * SLOTS_PER_HOUR)):
            out[k] = s["type"]
    return out


def site_kva(base_kw: list[float], pf: list[float], pv_kw: list[float], machine_kw: list[float]) -> list[float]:
    """Apparent power per slot. Base load uses the metered power factor; scheduled machines are taken at a power
    factor close to 1 (the deck's worked-example assumption). Rooftop solar offsets base load first, then machines."""
    out = []
    for k in range(N_SLOTS):
        surplus = max(0.0, pv_kw[k] - base_kw[k])
        out.append(max(0.0, base_kw[k] - pv_kw[k]) / pf[k] + max(0.0, machine_kw[k] - surplus))
    return out


def bill(kva: list[float], t: dict, mtd_max_kva: float) -> dict:
    """Day cost: energy (kVAh x slot rate) + demand charge on any rise above this month's recorded maximum kVA."""
    rates = slot_rates(t)
    energy = sum(kva[k] * DT_H * rates[k] for k in range(N_SLOTS))
    peak = max(kva)
    extra_kva = max(0.0, peak - mtd_max_kva)
    demand = extra_kva * t["demand_charge_per_kva_month"]
    by_type: dict[str, float] = {}
    for k, ty in enumerate(slot_types(t)):
        by_type[ty] = by_type.get(ty, 0.0) + kva[k] * DT_H
    return {"energy_rs": energy, "demand_rs": demand, "total_rs": energy + demand,
            "peak_kva": peak, "extra_kva": extra_kva, "kvah_by_slot": by_type, "kva": kva}
