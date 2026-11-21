"""The plant's own settings: site, tariff, machines and crew rules. Edited on Plant setup. A signed-in plant keeps them in its own memory; the shared demo plant uses plant_config.json.

Without a saved file the app starts from the demo defaults in data/*.json.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .tariff import validate_tariff

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "plant_config.json"

RUN_DAYS = {"Every day": (0, 1, 2, 3, 4, 5, 6), "Mon–Sat": (0, 1, 2, 3, 4, 5), "Mon–Fri": (0, 1, 2, 3, 4), "Weekends": (5, 6)}
MACHINE_COLS = ["Machine", "Runs on", "kW", "Power factor", "Hours", "Earliest start", "Latest finish", "Usual start",
                "Could start as early as", "Could finish as late as", "Side cost if relaxed", "Why this window"]
RULE_COLS = ["Machine A", "Machine B", "Why they cannot run together", "Can be relaxed?", "Wording when relaxed", "Side cost if relaxed"]
SLOT_COLS = ["From hour", "To hour", "Band", "Rate (₹ per kVAh)"]


def defaults() -> dict:
    site = json.loads((DATA_DIR / "site.json").read_text())
    tariff = json.loads((DATA_DIR / "tariff_msedcl_illustrative.json").read_text())
    machines = json.loads((DATA_DIR / "machines.json").read_text())
    return {"site": site, "tariff": tariff, "machines": machines}


def _normalise(cfg: dict) -> dict:
    """Machine ids are always the slug of the machine's name, so the Plant setup tables and the rules stay in step."""
    mapping, taken = {}, set()
    for m in cfg["machines"]["shiftable"]:
        new = slug(m["name"], taken)
        taken.add(new)
        mapping[m["id"]] = new
        m["id"] = new
    for r in cfg["machines"]["rules"]:
        if r["type"] == "no_overlap":
            r["a"], r["b"] = mapping.get(r["a"], r["a"]), mapping.get(r["b"], r["b"])
            r["id"] = f"crew_{r['a']}_{r['b']}"
    return cfg


def load() -> dict:
    cfg = defaults()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            for k in ("site", "tariff"):
                cfg[k] = saved.get(k, cfg[k])
            cfg["machines"] = {**cfg["machines"], "shiftable": saved.get("shiftable", cfg["machines"]["shiftable"]),
                               "rules": saved.get("rules", cfg["machines"]["rules"]),
                               "learned_rules": saved.get("learned_rules", [])}
        except (OSError, ValueError):
            pass
    return _normalise(cfg)


def save(cfg: dict) -> None:
    out = {"site": cfg["site"], "tariff": cfg["tariff"], "shiftable": cfg["machines"]["shiftable"], "rules": cfg["machines"]["rules"],
           "learned_rules": cfg["machines"].get("learned_rules", [])}
    CONFIG_PATH.write_text(json.dumps(out, indent=1, ensure_ascii=False))


def reset() -> dict:
    CONFIG_PATH.unlink(missing_ok=True)
    return load()


def slug(text: str, taken: set[str] | None = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:24] or "machine"
    out, i, taken = base, 2, taken or set()
    while out in taken:
        out, i = f"{base}_{i}", i + 1
    return out


def runs_today(m: dict, day, holidays=()) -> bool:
    """Does this machine run on this day? A plant holiday counts as a Sunday."""
    wd = 6 if str(day) in set(map(str, holidays or ())) else day.weekday()
    return wd in RUN_DAYS.get(m.get("runs_on", "Every day"), RUN_DAYS["Every day"])


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or v == "" else v


# ---------------------------------------------------------------- machines
def machines_to_rows(machines: list[dict]) -> list[dict]:
    return [{"Machine": m["name"], "Runs on": m.get("runs_on", "Every day"), "kW": m["kw"], "Power factor": m.get("pf", 0.85), "Hours": m["duration_h"], "Earliest start": m["earliest_h"],
             "Latest finish": m["latest_finish_h"], "Usual start": m.get("baseline_start_h"),
             "Could start as early as": m.get("relax_earliest_h"), "Could finish as late as": m.get("relax_latest_finish_h"),
             "Side cost if relaxed": m.get("relax_note", ""), "Why this window": m.get("why_window", "")} for m in machines]


def rows_to_machines(rows: list[dict]) -> tuple[list[dict], list[str]]:
    out, errs, taken = [], [], set()
    for i, r in enumerate(rows, 1):
        name = (r.get("Machine") or "").strip()
        if not name:
            continue
        tag = f"Machine {i} ({name})"
        try:
            kw, dur, e, l = float(r["kW"]), float(r["Hours"]), float(r["Earliest start"]), float(r["Latest finish"])
        except (TypeError, ValueError, KeyError):
            errs.append(f"{tag}: kW, hours, earliest start and latest finish must all be numbers.")
            continue
        usual = _num(r.get("Usual start"))
        if not 0 < kw <= 5000:
            errs.append(f"{tag}: kW must be between 0 and 5000.")
        if not (0 <= e < l <= 24 and 0 < dur <= l - e):
            errs.append(f"{tag}: the run of {dur:g} h must fit between {e:g}:00 and {l:g}:00.")
        if usual is None:
            errs.append(f"{tag}: give its usual start hour, so the saving has something to be measured against.")
        elif not e <= float(usual) <= l - dur:
            errs.append(f"{tag}: the usual start {usual}:00 is outside its own window.")
        mid = slug(name, taken)
        taken.add(mid)
        m = {"id": mid, "name": name, "kw": kw, "duration_h": dur, "earliest_h": int(e), "latest_finish_h": int(l),
             "baseline_start_h": int(usual) if usual is not None else None, "continuous": True,
             "why_window": (r.get("Why this window") or "").strip(),
             "runs_on": r.get("Runs on") if r.get("Runs on") in RUN_DAYS else "Every day"}
        pf_ = _num(r.get("Power factor"))
        if pf_ is not None and not 0.3 <= float(pf_) <= 1.0:
            errs.append(f"{tag}: power factor must be between 0.3 and 1.0.")
        m["pf"] = round(float(pf_), 2) if pf_ is not None else 0.85
        re_, rl = _num(r.get("Could start as early as")), _num(r.get("Could finish as late as"))
        if re_ is not None:
            if float(re_) < e:
                m["relax_earliest_h"] = int(re_)
            else:
                errs.append(f"{tag}: 'could start as early as' must be before {e:g}:00.")
        if rl is not None:
            if float(rl) > l:
                m["relax_latest_finish_h"] = int(rl)
            else:
                errs.append(f"{tag}: 'could finish as late as' must be after {l:g}:00.")
        note = (r.get("Side cost if relaxed") or "").strip()
        if note:
            m["relax_note"] = note
        out.append(m)
    if not out:
        errs.append("Add at least one machine.")
    return out, errs


# ---------------------------------------------------------------- crew rules (two machines that cannot overlap)
def rules_to_rows(rules: list[dict], machines: list[dict]) -> list[dict]:
    name = {m["id"]: m["name"] for m in machines}
    return [{"Machine A": name.get(r["a"], r["a"]), "Machine B": name.get(r["b"], r["b"]), "Why they cannot run together": r["text"],
             "Can be relaxed?": not r["hard"], "Wording when relaxed": r.get("relax_text", ""),
             "Side cost if relaxed": r.get("side_cost_note", "")} for r in rules if r["type"] == "no_overlap"]


def rows_to_rules(rows: list[dict], machines: list[dict]) -> tuple[list[dict], list[str]]:
    ids = {m["name"]: m["id"] for m in machines}
    out, errs = [{"id": "contract_demand", "text": "Site demand must stay at or below the contract demand",
                  "type": "contract_demand", "hard": True}], []
    for i, r in enumerate(rows, 1):
        a, b = r.get("Machine A"), r.get("Machine B")
        if not a and not b:
            continue
        if a not in ids or b not in ids or a == b:
            errs.append(f"Rule {i}: choose two different machines from the machine list.")
            continue
        out.append({"id": f"crew_{ids[a]}_{ids[b]}", "type": "no_overlap", "a": ids[a], "b": ids[b],
                    "text": (r.get("Why they cannot run together") or f"{a} and {b} cannot run at the same time").strip(),
                    "hard": not bool(r.get("Can be relaxed?")), "relax_text": (r.get("Wording when relaxed") or "").strip(),
                    "side_cost_note": (r.get("Side cost if relaxed") or "").strip()})
    return out, errs


# ---------------------------------------------------------------- tariff
def tariff_to_rows(t: dict) -> list[dict]:
    return [{"From hour": s["start_h"], "To hour": s["end_h"], "Band": s["type"], "Rate (₹ per kVAh)": s["rate"]} for s in t["slots"]]


def rows_to_tariff(base: dict, rows: list[dict], demand_charge: float) -> tuple[dict, list[str]]:
    slots = []
    for r in rows:
        if _num(r.get("From hour")) is None and _num(r.get("To hour")) is None:
            continue
        try:
            slots.append({"start_h": int(r["From hour"]), "end_h": int(r["To hour"]), "type": r["Band"], "rate": float(r["Rate (₹ per kVAh)"])})
        except (TypeError, ValueError, KeyError):
            return base, ["Each price band needs from-hour, to-hour, band and rate."]
    t = {**base, "slots": sorted(slots, key=lambda s: s["start_h"]), "demand_charge_per_kva_month": float(demand_charge)}
    errs = [i.text for i in validate_tariff(t) if i.level == "error"]
    return t, errs
