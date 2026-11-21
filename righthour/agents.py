"""RightHour Lite agents (Section 8.2): Data & Forecast, Tariff & Constraints, Orchestrator / Explainer.

Each step function takes the shared context and the run state and returns the state updates, so the same steps run
in the LangGraph graph (graph.py) and in the plain sequential runner below (tests and the CLI).
The steps follow the team's agentic table: Goal, Data, Analysis, Decision, Problem, Action, Learning.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field

import pandas as pd

from . import data as D
from .audit import AuditLog
from .llm import LLMClient, number_guard
from .optimiser import evaluate, optimise_schedule
from .router import TYPICAL_TOKENS, cost_usd, route
from .tariff import N_SLOTS, SLOTS_PER_HOUR, validate_tariff

DEFAULT_GOAL = "Cut tomorrow's bill without disrupting production."


# ------------------------------------------------------------------ formatting
def hhmm(h: float) -> str:
    return f"{int(h):02d}:00"


def rs(x: float) -> str:
    """Indian digit grouping, whole rupees."""
    neg, n = x < 0, str(int(round(abs(x))))
    if len(n) > 3:
        head, tail, parts = n[:-3], n[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        n = ",".join(parts + [tail])
    return ("-" if neg else "") + "₹" + n


def lakh(x: float) -> str:
    return f"₹{x / 1e5:.1f} lakh"


# ------------------------------------------------------------------ context
@dataclass
class Context:
    site: dict
    tariff: dict
    machines_cfg: dict
    history: pd.DataFrame
    goal: str = DEFAULT_GOAL
    cloudy: bool | None = False          # None = ask Open-Meteo
    include_extra: bool = False          # legacy demo switch: the canned grinding order
    plan_date: dt.date | None = None     # the day being planned; None = the day after the data ends
    notes: list | None = None            # the manager's notes for that day; None = the canned demo notes
    relaxation: str | None = None        # the manager's answer after an infeasible plan
    bias: float = 1.0                    # learned forecast correction
    process: dict = field(default_factory=dict)   # how each box works, set in the Control room (see PROCESS_DEFAULTS)
    llm: LLMClient = field(default_factory=LLMClient)
    audit: AuditLog = field(default_factory=AuditLog)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def log(self, steps: list, agent: str, task: str, tier=None, model=None, mode=None, reason=None,
            tokens=(0, 0), detail=None) -> dict:
        r = route(task)
        tier = tier or r.tier
        model = model if model is not None else r.model
        mode = mode or ("code" if tier == "T0" else self.llm.mode)
        row = self.audit.log(self.run_id, agent, task, tier=tier, model=model, mode=mode, reason=reason or r.reason,
                             tokens_in=tokens[0], tokens_out=tokens[1], cost_usd=cost_usd(model, *tokens), detail=detail)
        steps.append(row)
        return row


# ------------------------------------------------------------------ parsers (language steps with offline fallback)
def _clock(t: str, pattern: str) -> int | None:
    m = re.search(pattern, t)
    if not m:
        return None
    h = int(m.group(1))
    ampm = m.group(2) if m.lastindex and m.lastindex >= 2 else None
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return h


def offline_parse_note(note: str, defaults: dict) -> dict:
    """Rule-based stand-in for the small model: operator note -> constraint JSON."""
    t = note.lower()
    kw = re.search(r"(\d+(?:\.\d+)?)\s*kw", t)
    dur = re.search(r"(\d+(?:\.\d+)?)\s*hours?", t)
    out = dict(defaults)
    out["kw"] = float(kw.group(1)) if kw else None
    if dur:
        out["duration_h"] = float(dur.group(1))
    frm = _clock(t, r"from (\d{1,2})(?::00)?\s*(am|pm)?")
    to = _clock(t, r"to (\d{1,2})(?::00)?\s*(am|pm)")
    if frm is not None and to is not None:          # a fixed batch: "from 10 am to 4 pm"
        out.update(start_h=frm, end_h=to)
    st = _clock(t, r"starts? at (\d{1,2})(?::00)?\s*(am|pm)?")
    fin = _clock(t, r"finished by (\d{1,2})(?::00)?\s*(am|pm)?")
    if st is not None:
        out["earliest_h"] = st
    if fin is not None:
        out["latest_finish_h"] = fin
    out["hard"] = defaults.get("hard", "cannot stop" in t or "must" in t)
    return out


def validate_constraint(m: dict) -> list[str]:
    errs = []
    if not isinstance(m.get("kw"), (int, float)) or not 0 < m["kw"] <= 5000:
        errs.append("kw missing or out of range")
    if "start_h" in m:   # fixed batch
        if not (isinstance(m.get("end_h"), (int, float)) and 0 <= m["start_h"] < m["end_h"] <= 24):
            errs.append("batch hours invalid")
    else:
        for k in ("duration_h", "earliest_h", "latest_finish_h"):
            if not isinstance(m.get(k), (int, float)):
                errs.append(f"{k} missing")
        if not errs and not (0 <= m["earliest_h"] < m["latest_finish_h"] <= 24 and 0 < m["duration_h"] <= 24):
            errs.append("window invalid")
    return errs


_TARIFF_LINE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(?:to|-|–)\s*(\d{1,2})(?::(\d{2}))?\s*(?:hrs|hours|h)?[^\n₹]*?"
    r"(normal|solar|peak|off-peak)[^\n₹]*?(?:₹|rs\.?|inr)\s*(\d+(?:\.\d+)?)", re.I)


def offline_parse_tariff(text: str) -> dict:
    """Rule-based stand-in for the large model: tariff-order text -> slot JSON (validated afterwards)."""
    slots = []
    for m in _TARIFF_LINE.finditer(text):
        a, b = int(m.group(1)), int(m.group(3))
        b = 24 if b == 0 else b
        ty = m.group(5).lower().replace("off-peak", "normal")
        slots.append({"start_h": a, "end_h": b, "type": ty, "rate": float(m.group(6))})
    dc = re.search(r"demand charge[^\n₹]*?(?:₹|rs\.?|inr)\s*(\d+(?:\.\d+)?)", text, re.I)
    return {"id": "parsed-" + hashlib.sha1(text.encode()).hexdigest()[:8], "name": "Parsed tariff order (draft)",
            "unit": "kVAh", "slots": sorted(slots, key=lambda s: s["start_h"]),
            "demand_charge_per_kva_month": float(dc.group(1)) if dc else None, "status": "draft, awaiting sign-off"}


def parse_tariff(ctx: Context, text: str, library: dict[str, dict], steps: list) -> dict:
    """Tariff agent tool parse_tariff(): cache first; a new order goes to T2 once, then a person signs it off."""
    fp = hashlib.sha1(text.strip().encode()).hexdigest()[:12]
    if fp in library:
        ctx.log(steps, "Tariff & Constraints agent", "tariff_lookup",
                reason="order fingerprint already in the shared library, so no model call", detail={"fingerprint": fp})
        return library[fp]
    r = route("tariff_parse", novel=True)
    draft = None
    if ctx.llm.live:
        try:
            out, tin, tout = ctx.llm.complete(r.model, "Extract ToD slots as JSON: {slots:[{start_h,end_h,type,rate}], "
                                              "demand_charge_per_kva_month}. Types: normal, solar, peak. JSON only.", text, 1200)
            draft = json.loads(out[out.find("{"): out.rfind("}") + 1])
            draft.setdefault("unit", "kVAh")
            mode, tok = "live API", (tin, tout)
        except Exception:  # noqa: BLE001
            draft = None
    if draft is None:
        draft = offline_parse_tariff(text)
        mode, tok = "offline template", TYPICAL_TOKENS["tariff_parse"]
    draft["fingerprint"] = fp
    issues = validate_tariff(draft) if draft.get("demand_charge_per_kva_month") else \
        validate_tariff({**draft, "demand_charge_per_kva_month": 0})
    ctx.log(steps, "Tariff & Constraints agent", "tariff_parse", tier=r.tier, model=r.model, mode=mode, reason=r.reason,
            tokens=tok, detail={"slots": draft.get("slots"), "validation": [i.text for i in issues]})
    draft["issues"] = [i.__dict__ for i in issues]
    return draft


def parse_note(ctx: Context, note: str, defaults: dict, steps: list) -> dict:
    """Operator note -> constraint JSON on the small model. Retry once with the error, then escalate to T2."""
    last_err = ""
    for attempt in (1, 2, 3):
        r = route("constraint_parse", attempt=attempt, escalate_at=_esc(ctx))
        if ctx.llm.live:
            prompt = ("Turn this operator note into JSON. Keys: kw, and either start_h and end_h for a fixed batch, or "
                      "duration_h, earliest_h, latest_finish_h for a flexible job (24-hour numbers). JSON only.\n"
                      f"Note: {note}" + (f"\nYour previous answer failed: {last_err}" if last_err else ""))
            try:
                text, tin, tout = ctx.llm.complete(r.model, "You convert factory notes to strict JSON.", prompt, 300)
                m = {**defaults, **json.loads(text[text.find("{"): text.rfind("}") + 1])}
            except Exception as e:  # noqa: BLE001
                m, tin, tout, last_err = {}, 0, 0, str(e)
            mode = "live API"
        else:
            m, (tin, tout), mode = offline_parse_note(note, defaults), TYPICAL_TOKENS["constraint_parse"], "offline template"
        errs = validate_constraint(m) if m else [last_err or "no JSON returned"]
        ctx.log(steps, "Tariff & Constraints agent", "constraint_parse", tier=r.tier, model=r.model, mode=mode,
                reason=r.reason, tokens=(tin, tout), detail={"note": note, "attempt": attempt, "json": m,
                                                              "validation": errs or "passed"})
        if not errs:
            return m
        last_err = "; ".join(errs)
    raise ValueError(f"Could not turn the note into a valid constraint: {last_err}")


# ------------------------------------------------------------------ policy checks (T0, governance)
def policy_check(problem: dict, schedule: dict[str, int], baseline_peak: float | None = None, saving_rs: float | None = None) -> dict:
    """Every rule, checked again in plain code. The manager's guardrails (problem["guard"]) are part of it."""
    g = {"margin_pct": 0, "block_new_peak": False, "max_moves": None, "min_saving_rs": 0, **(problem.get("guard") or {})}
    ev = evaluate(problem, schedule)
    checks = []
    for m in problem["shiftable"]:
        s = schedule[m["id"]]
        checks.append({"rule": f"{m['name']} inside {hhmm(m['earliest_h'])} to {hhmm(m['latest_finish_h'])}",
                       "ok": m["earliest_h"] <= s and s + m["duration_h"] <= m["latest_finish_h"], "hard": True})
    cd = problem["contract_demand_kva"]
    lim = cd * (1 - g["margin_pct"] / 100)
    checks.append({"rule": (f"Max demand within contract demand: {ev['peak_kva']:.0f} of {cd:.0f} kVA" if not g["margin_pct"] else
                            f"Max demand keeps your {g['margin_pct']:g}% safety margin: {ev['peak_kva']:.0f} of {lim:.0f} kVA (contract {cd:.0f})"),
                   "ok": ev["peak_kva"] <= lim + 1e-6, "hard": True})
    by_id = {m["id"]: m for m in problem["shiftable"]}
    for a, b in problem.get("no_overlap", []):
        if a in by_id and b in by_id:
            sa, sb, da, db = schedule[a], schedule[b], by_id[a]["duration_h"], by_id[b]["duration_h"]
            checks.append({"rule": f"{by_id[a]['name']} and {by_id[b]['name']} not at the same time (one crew)",
                           "ok": sa + da <= sb or sb + db <= sa, "hard": True})
    for f in problem["fixed"]:
        checks.append({"rule": f"{f['name']} {hhmm(f['start_h'])} to {hhmm(f['end_h'])} untouched", "ok": True, "hard": True})
    mtd = problem["mtd_max_kva"]
    checks.append({"rule": f"Monthly max demand unchanged: {ev['peak_kva']:.0f} kVA against this month's {mtd:.0f} kVA",
                   "ok": ev["peak_kva"] <= mtd + 1e-6, "hard": bool(g["block_new_peak"])})
    if g["max_moves"] is not None:
        moved = sum(1 for m in problem["shiftable"] if m.get("baseline_start_h") is not None and schedule[m["id"]] != m["baseline_start_h"])
        checks.append({"rule": f"At most {g['max_moves']} machine(s) moved: {moved} moved", "ok": moved <= g["max_moves"], "hard": True})
    if g["min_saving_rs"] and saving_rs is not None:
        checks.append({"rule": f"Saving worth the change: {rs(saving_rs)} against your minimum {rs(g['min_saving_rs'])}",
                       "ok": saving_rs >= g["min_saving_rs"], "hard": True})
    return {"checks": checks, "passed": all(c["ok"] for c in checks if c["hard"]), "eval": ev}


# ------------------------------------------------------------------ the steps
def _extras(st: dict) -> list[dict]:
    e = st.get("extra")
    return [] if not e else (e if isinstance(e, list) else [e])


def build_problem(ctx: Context, st: dict, relax: dict | None = None, drop_rule: str | None = None) -> dict:
    from .config import runs_today
    day, hol = st["forecast"]["date"], ctx.site.get("holidays", [])
    running = [m for m in ctx.machines_cfg["shiftable"] if runs_today(m, day, hol)]   # off today: not planned, nothing to save
    idle = [m["name"] for m in ctx.machines_cfg["shiftable"] if m not in running]
    shiftable = copy.deepcopy(running) + copy.deepcopy(_extras(st))
    if relax and relax.get("field"):
        target = relax.get("machine") or (_extras(st)[0]["id"] if _extras(st) else None)
        for m in shiftable:
            if m["id"] == target:
                m[relax["field"]] = relax["value"]
    apply_learned_rules(ctx.machines_cfg, shiftable)   # every "no" that became a rule
    apply_learned_rules({"learned_rules": (ctx.process or {}).get("today_rules") or []}, shiftable)   # rules for this day only
    for m in shiftable:                         # machines the manager pinned: they run at exactly that hour
        h = ((ctx.process or {}).get("pins") or {}).get(m["id"])
        if h is not None:
            m["earliest_h"], m["latest_finish_h"] = int(h), int(h) + m["duration_h"]
    no_overlap = [(r["a"], r["b"]) for r in ctx.machines_cfg["rules"] if r["type"] == "no_overlap" and r["id"] != drop_rule]
    return {"idle": idle, "holiday": str(day) in set(map(str, hol)),
            "base_kw": st["forecast"]["kw"], "pf": st["forecast"]["pf"], "pv_kw": st["pv"]["kw"], "fixed": st["fixed"],
            "shiftable": shiftable, "no_overlap": no_overlap, "tariff": ctx.tariff,
            "contract_demand_kva": ctx.site["contract_demand_kva"], "mtd_max_kva": ctx.site["month_to_date_max_kva"],
            "guard": {k: {**PROCESS_DEFAULTS, **(ctx.process or {})}[k] for k in GUARD_KEYS}}


def baseline_schedule(problem: dict, placed: dict | None = None) -> tuple[dict, bool]:
    """Counterfactual: recurring machines at their usual times. A new order has no usual time, so the solver places
    it with everything else held at the usual times. If even that breaks a hard rule there is no like-for-like
    baseline, and no saving is claimed."""
    usual = {m["id"]: m["baseline_start_h"] for m in problem["shiftable"] if m.get("baseline_start_h") is not None}
    new = [m for m in problem["shiftable"] if m.get("baseline_start_h") is None]
    if not new:
        return usual, True
    pinned = copy.deepcopy(problem)
    for m in pinned["shiftable"]:
        if m["id"] in usual:
            m["earliest_h"], m["latest_finish_h"] = usual[m["id"]], usual[m["id"]] + m["duration_h"]
    sol = optimise_schedule(pinned)
    if sol["status"] == "optimal":
        return sol["schedule"], True
    return {**usual, **{m["id"]: (placed or {}).get(m["id"], m["earliest_h"]) for m in new}}, False


def relaxation_options(ctx: Context, st: dict | None = None) -> list[dict]:
    """The Production agent's list of flexible (soft) rules. Hard rules are never offered.

    Three sources: a machine or order that has a looser time window it could use (set in Setup or in the day's notes),
    a crew rule marked as relaxable, and, for the canned demo only, the grinding order's two relaxations."""
    st = st or {}
    opts: list[dict] = []
    if ctx.notes is None:
        opts += [dict(r, kind="window") for r in ctx.machines_cfg.get("extra_machine", {}).get("relaxations", [])]
    for m in list(ctx.machines_cfg["shiftable"]) + _extras(st):
        e, l = m.get("relax_earliest_h"), m.get("relax_latest_finish_h")
        note = m.get("relax_note", "")
        if isinstance(e, (int, float)) and e < m["earliest_h"]:
            opts.append({"id": f"{m['id']}:early", "kind": "window", "machine": m["id"], "field": "earliest_h", "value": e,
                         "text": f"Start {m['name']} as early as {hhmm(e)}", "side_cost_note": note})
        if isinstance(l, (int, float)) and l > m["latest_finish_h"]:
            opts.append({"id": f"{m['id']}:late", "kind": "window", "machine": m["id"], "field": "latest_finish_h", "value": l,
                         "text": f"Let {m['name']} finish as late as {hhmm(l)}", "side_cost_note": note})
    for r in ctx.machines_cfg["rules"]:
        if not r["hard"]:
            opts.append({"id": r["id"], "text": r.get("relax_text") or r["text"], "kind": "rule",
                         "side_cost_note": r.get("side_cost_note", "")})
    return opts


def _esc(ctx) -> int:
    """The attempt that goes to the large model, for this run only (never a shared global)."""
    return int(((ctx.process or {}).get("small_tries") or 2)) + 1


# How each box of the diagram works. The Control room changes these per session; each run records the values it used.
PROCESS_DEFAULTS = {"weeks": 4, "method": "weekday", "use_learning": True, "solar_on": True, "spike_x": 3.0, "pf_min": 0.6, "max_gap_h": 2.0, "stuck_h": 3.0, "shutdown_pct": 0.2, "stale_days": 7,
                    "time_limit_s": 20, "notes_on": True,
                    # guardrails the manager sets: the optimiser obeys them and the rule check enforces them
                    "margin_pct": 0, "block_new_peak": False, "max_moves": None, "min_saving_rs": 0,
                    # learning from the next-day check, the data needed to plan, AI retries, the live feed
                    "learn_rate": 0.5, "learn_step_max": 0.10, "learn_outlier": 0.5, "min_history_days": 14, "small_tries": 2,
                    "live_hold_days": 7, "redact_names": True, "rule_review_days": 90}
GUARD_KEYS = ("margin_pct", "block_new_peak", "max_moves", "min_saving_rs")


def step_goal(ctx: Context, st: dict) -> dict:
    steps = st.setdefault("steps", [])
    ctx.log(steps, "Orchestrator", "orchestrate", reason="routine daily run: the small model picks the next tool",
            tokens=TYPICAL_TOKENS["orchestrate"], detail={"goal": ctx.goal, "plan": [
                "gather meter, tariff, weather and production data", "forecast load and solar", "read tariff",
                "parse today's constraints", "optimise", "policy check", "explain", "ask for approval"]})
    return {"goal": ctx.goal}


def step_data(ctx: Context, st: dict) -> dict:
    steps, agent = st["steps"], "Data & Forecast agent"
    p = {**PROCESS_DEFAULTS, **(ctx.process or {})}
    lim = {k: p[k] for k in ("spike_x", "pf_min", "max_gap_h", "stuck_h", "shutdown_pct", "stale_days")}
    hist = ctx.history[ctx.history["ts"].dt.date < ctx.plan_date] if ctx.plan_date else ctx.history   # only what the plan may use
    issues = D.validate_load(hist if len(hist) else ctx.history, contract_kva=ctx.site.get("contract_demand_kva"), plan_date=ctx.plan_date, **lim)
    ctx.log(steps, agent, "validate_data", detail={"source": ctx.history.attrs.get("source", ""), "issues": issues, **lim})
    bias = ctx.bias if p["use_learning"] else 1.0
    fc = D.forecast_load(ctx.history, bias=bias, target=ctx.plan_date, weeks=p["weeks"], method=p["method"])
    ctx.log(steps, agent, "forecast_load", detail={"method": fc["method"], "for": f"{fc['weekday']} {fc['date']}",
                                                     "bias": round(bias, 4), "learning_used": p["use_learning"]})
    s = ctx.site
    if p["solar_on"]:
        pv = D.solar_forecast(s["rooftop_pv_kwp"], s["latitude"], s["longitude"], fc["date"], cloudy_override=ctx.cloudy)
    else:
        pv = {"kw": [0.0] * len(fc["kw"]), "source": "Solar switched off in the Control room: no rooftop power counted"}
    ctx.log(steps, agent, "solar_forecast", detail={"source": pv["source"], "peak_kw": round(max(pv["kw"]), 1)})
    return {"forecast": fc, "pv": pv, "data_issues": issues}


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:24] or "order"
    out, i = base, 2
    while out in taken:
        out, i = f"{base}_{i}", i + 1
    return out


def step_analysis(ctx: Context, st: dict) -> dict:
    steps, agent = st["steps"], "Tariff & Constraints agent"
    issues = validate_tariff(ctx.tariff)
    ctx.log(steps, agent, "tariff_lookup", reason="tariff already in the shared library, so no model call",
            detail={"tariff": ctx.tariff["name"], "warnings": [i.text for i in issues]})
    fixed, extras = [], []
    if not {**PROCESS_DEFAULTS, **(ctx.process or {})}["notes_on"]:
        ctx.log(steps, agent, "constraint_parse", tier="T0", mode="code", reason="notes reader switched off in the Control room",
                detail={"notes": "ignored"})
        return {"tariff_issues": [i.__dict__ for i in issues], "fixed": [], "extra": []}
    if ctx.notes is None:                                   # the canned demo notes
        for n in ctx.machines_cfg.get("daily_notes", []):
            fixed.append(parse_note(ctx, n["text"], n["defaults"], steps))
        if ctx.include_extra:
            em = ctx.machines_cfg["extra_machine"]
            extras.append(parse_note(ctx, em["note"], em["machine"], steps))
    else:                                                   # the manager's own notes for the day
        taken = {m["id"] for m in ctx.machines_cfg["shiftable"]}
        for n in ctx.notes:
            text = (n.get("text") or "").strip()
            if not text:
                continue
            name = (n.get("name") or "").strip() or "Production note"
            mid = _slug(name, taken)
            taken.add(mid)
            if n.get("kind") == "Fixed batch":
                fixed.append(parse_note(ctx, text, {"id": mid, "name": name, "hard": True}, steps))
            else:
                d = {"id": mid, "name": name, "baseline_start_h": None, "continuous": True}
                for src, dst in (("relax_earliest_h", "relax_earliest_h"), ("relax_latest_finish_h", "relax_latest_finish_h"),
                                 ("relax_note", "relax_note")):
                    if n.get(src) not in (None, "") and n.get(src) == n.get(src):
                        d[dst] = n[src]
                extras.append(parse_note(ctx, text, d, steps))
    return {"tariff_issues": [i.__dict__ for i in issues], "fixed": fixed, "extra": extras}


def step_decide(ctx: Context, st: dict) -> dict:
    relax = drop = None
    if ctx.relaxation:
        o = next((o for o in relaxation_options(ctx, st) if o["id"] == ctx.relaxation), None)
        if o and o["kind"] == "rule":
            drop = o["id"]
        else:
            relax = o
    problem = build_problem(ctx, st, relax, drop)
    plan = optimise_schedule(problem, time_limit_s=int({**PROCESS_DEFAULTS, **(ctx.process or {})}["time_limit_s"]))
    ctx.log(st["steps"], "Orchestrator", "optimise",
            reason="the solver decides the schedule; the agent only chose to call it",
            detail={"status": plan["status"], "solver": "OR-Tools MILP", "relaxed": ctx.relaxation,
                    "schedule": plan.get("schedule")})
    base_sched, base_ok = baseline_schedule(problem, plan.get("schedule"))
    base_ev = evaluate(problem, base_sched)
    return {"problem": problem, "plan": plan, "baseline": dict(base_ev, schedule=base_sched, feasible=base_ok)}


def step_problem(ctx: Context, st: dict) -> dict:
    """No valid schedule: find the flexible rules, test each, and ask the manager which one to relax."""
    r = route("replan", infeasible=True)
    options = []
    for o in relaxation_options(ctx, st):
        p2 = build_problem(ctx, st, o if o["kind"] == "window" else None, o["id"] if o["kind"] == "rule" else None)
        sol = optimise_schedule(p2)
        opt = {"id": o["id"], "text": o["text"], "side_cost_note": o.get("side_cost_note", ""),
               "feasible": sol["status"] == "optimal"}
        if opt["feasible"]:
            b_s, b_ok = baseline_schedule(p2, sol["schedule"])
            b_ev = evaluate(p2, b_s)
            opt.update(cost_rs=sol["total_rs"], saving_rs=(b_ev["total_rs"] - sol["total_rs"]) if b_ok else None,
                       schedule=sol["schedule"], peak_kva=sol["peak_kva"])
        options.append(opt)
    feas = [o for o in options if o["feasible"]]
    rec = min(feas, key=lambda o: o["cost_rs"])["id"] if feas else None
    hard = [x["text"] for x in ctx.machines_cfg["rules"] if x["hard"]] + [f"{f['name']} cannot stop mid-batch" for f in st["fixed"]]
    ctx.log(st["steps"], "Orchestrator", "replan", tier=r.tier, model=r.model, mode=ctx.llm.mode, reason=r.reason,
            tokens=TYPICAL_TOKENS["replan"], detail={"diagnosis": st["plan"]["reason"], "hard_rules_kept": hard,
                                                      "options": [{k: v for k, v in o.items() if k != "schedule"} for o in options],
                                                      "recommended": rec})
    status = "needs_manager_choice" if feas else "fallback"
    out = {"options": options, "recommended": rec, "status": status, "diagnosis": st["plan"]["reason"]}
    if status == "fallback":
        out["note"] = "No flexible rule makes a valid plan, so the current schedule is kept and the new order is escalated."
    return out


def explain(ctx: Context, st: dict) -> str:
    problem, plan, base = st["problem"], st["plan"], st["baseline"]
    tariff_rate = {s["type"]: s["rate"] for s in ctx.tariff["slots"]}
    rate_types = {}
    for s in ctx.tariff["slots"]:
        for h in range(int(s["start_h"]), int(s["end_h"])):
            rate_types[h] = s["type"]

    def slots(h0, d):
        return " and ".join(dict.fromkeys(rate_types[h % 24] for h in range(int(h0), int(h0 + d))))

    lines, moved_kvah, from_peak = [], 0.0, True
    for m in problem["shiftable"]:
        b, p, d = m.get("baseline_start_h"), plan["schedule"][m["id"]], m["duration_h"]
        if b is None:
            lines.append(f"Placed {m['name']} at {hhmm(p)}–{hhmm(p + d)} ({slots(p, d)} hours).")
        elif b != p:
            lines.append(f"Moved {m['name']} from {hhmm(b)}–{hhmm(b + d)} to {hhmm(p)}–{hhmm(p + d)}.")
            moved_kvah += m["kw"] * d
            from_peak &= slots(b, d) == "peak" and slots(p, d) == "solar"
    if moved_kvah and from_peak and "peak" in tariff_rate and "solar" in tariff_rate:
        lines.append(f"Moved {moved_kvah:,.0f} kVAh from peak (₹{tariff_rate['peak']}) to solar hours (₹{tariff_rate['solar']}).")
    for f in st["fixed"]:
        lines.append(f"{f['name']} untouched (no mid-batch stop).")
    pol = st["policy"]
    max_ok = next(c for c in pol["checks"] if c["rule"].startswith("Monthly max demand"))["ok"]
    mtd = problem["mtd_max_kva"]
    lines.append(f"Max demand unchanged for billing ({plan['peak_kva']:.0f} kVA, below this month's {mtd:.0f} kVA); "
                 f"all deadlines met." if max_ok else
                 f"Sets a new monthly max demand ({plan['peak_kva']:.0f} kVA), adding {rs(plan['demand_rs'])}, included below; all deadlines met.")
    if base["feasible"]:
        sav = base["total_rs"] - plan["total_rs"]
        lines.append(f"Projected saving {rs(sav)} a day.")
    else:
        sav = 0.0
        lines.append(f"Without RightHour this order does not fit the usual schedule, so no saving is claimed; "
                     f"projected cost {rs(plan['total_rs'])} for the day.")
    text = " ".join(lines)

    allowed = {f"{moved_kvah:,.0f}", f"{plan['peak_kva']:.0f}", rs(sav)[1:], rs(plan["total_rs"])[1:],
               rs(plan.get("demand_rs", 0))[1:], f"{problem['mtd_max_kva']:.0f}"} | {str(v) for v in tariff_rate.values()} | {f"{h:02d}" for h in range(25)} \
        | {str(h) for h in range(25)}
    r = route("explain")
    if ctx.llm.live:
        for attempt in (1, 2, 3):
            rr = route("explain", attempt=attempt, escalate_at=_esc(ctx))
            try:
                out, tin, tout = ctx.llm.complete(rr.model, "Rewrite this plan for a plant manager in two or three plain "
                                                  "sentences. Use only the numbers given; add none.", text, 300)
            except Exception:  # noqa: BLE001
                break
            bad = number_guard(out.replace(":", " "), allowed)
            if len(out.strip()) < 60 or (sav and rs(sav)[1:].replace(",", "") not in out.replace(",", "")):
                bad = bad + ["the text is cut short or leaves out the saving"]
            ctx.log(st["steps"], "Orchestrator", "explain", tier=rr.tier, model=rr.model, mode="live API",
                    reason=rr.reason, tokens=(tin, tout), detail={"number_guard": bad or "passed"})
            if not bad:
                return out
        return text
    ctx.log(st["steps"], "Orchestrator", "explain", tier=r.tier, model=r.model, mode="offline template", reason=r.reason,
            tokens=TYPICAL_TOKENS["explain"], detail={"number_guard": "passed: every number comes from the solver"})
    return text


def step_action(ctx: Context, st: dict) -> dict:
    """Policy checks, explanation, and the request for approval."""
    base0 = st["baseline"]
    sav0 = base0["total_rs"] - st["plan"]["total_rs"] if base0["feasible"] else None
    pol = policy_check(st["problem"], st["plan"]["schedule"], saving_rs=sav0)
    ctx.log(st["steps"], "Governance", "policy_check", detail={"passed": pol["passed"], "checks": pol["checks"]})
    if not pol["passed"]:
        failed = "; ".join(c["rule"] for c in pol["checks"] if c["hard"] and not c["ok"])
        return {"status": "fallback", "policy": pol, "note": f"A guardrail failed ({failed}), so the usual schedule is kept."}
    st = {**st, "policy": pol}
    text = explain(ctx, st)
    base = st["baseline"]
    saving = base["total_rs"] - st["plan"]["total_rs"] if base["feasible"] else None
    ctx.log(st["steps"], "Orchestrator", "orchestrate", tier="T0", model=None, mode="code",
            reason="plan sent to the plant manager; nothing runs without a yes", detail={"status": "awaiting approval"})
    return {"policy": pol, "explanation": text, "saving_rs": saving, "status": "awaiting_approval"}


# ------------------------------------------------------------------ plain sequential runner (tests, CLI)
class Orchestrator:
    def run(self, ctx: Context) -> dict:
        st: dict = {"run_id": ctx.run_id, "steps": []}
        for step in (step_goal, step_data, step_analysis, step_decide):
            st.update(step(ctx, st))
        if st["plan"]["status"] != "optimal":
            st.update(step_problem(ctx, st))
            return st
        st.update(step_action(ctx, st))
        return st


# ------------------------------------------------------------------ next day: verify and learn
def verify_and_learn(ctx: Context, run: dict, executed: dict[str, int], actual: pd.DataFrame) -> dict:
    """IPMVP-style check: adjusted baseline = actual base load with the usual machine times; actual = as executed."""
    s = actual.set_index("ts").resample("15min").mean().interpolate()
    a, apf = s["kw"].tolist()[:N_SLOTS], s["pf"].tolist()[:N_SLOTS]
    if len(a) < N_SLOTS:
        raise ValueError("Actuals must cover a full day of 15-minute readings.")
    act_problem = dict(run["problem"], base_kw=a, pf=apf)
    adj_base = evaluate(act_problem, run["baseline"]["schedule"])
    act = evaluate(act_problem, executed)
    verified = adj_base["total_rs"] - act["total_rs"]
    projected = run.get("saving_rs") or 0.0
    err = (sum(a) / N_SLOTS) / (sum(run["forecast"]["kw"]) / N_SLOTS) - 1
    # Learn halfway towards the observed level, but never more than 10% in one day, and not at all when the day is so far
    # off (over 50%) that it is more likely a holiday, a shutdown or the wrong file than a forecast that needs correcting.
    lp = {**PROCESS_DEFAULTS, **(ctx.process or {})}
    if abs(err) > lp["learn_outlier"]:
        new_bias, learn_note = ctx.bias, f"day was {err:+.0%} off the forecast: treated as an outlier, correction unchanged"
    else:
        step = max(-lp["learn_step_max"], min(lp["learn_step_max"], lp["learn_rate"] * err))
        new_bias = ctx.bias * (1 + step)
        learn_note = "forecast correction moves halfway towards the observed load level, at most 10% a day"
    res = {"adjusted_baseline_rs": adj_base["total_rs"], "actual_rs": act["total_rs"], "verified_saving_rs": verified,
           "projected_saving_rs": projected, "realisation": verified / projected if projected else None,
           "forecast_error_pct": err * 100, "old_bias": ctx.bias, "new_bias": new_bias, "learn_note": learn_note, "actual_eval": act,
           "followed_plan": executed == run["plan"]["schedule"],
           "deviations": {k: (run["plan"]["schedule"][k], v) for k, v in executed.items() if run["plan"]["schedule"][k] != v}}
    ctx.audit.log(ctx.run_id, "Data & Forecast agent", "verify_savings", reason="next-day meter data vs adjusted baseline",
                  detail={k: (round(v, 2) if isinstance(v, float) else v) for k, v in res.items() if k != "actual_eval"})
    ctx.audit.log(ctx.run_id, "Data & Forecast agent", "learn",
                  reason=learn_note,
                  detail={"old_bias": round(ctx.bias, 4), "new_bias": round(new_bias, 4),
                          "realisation": round(res["realisation"], 3) if res["realisation"] else None})
    return res


# ------------------------------------------------------------------ the four moments with the manager (deck, slide 7)
def machine_savings(problem: dict, baseline: dict, plan: dict) -> dict[str, float]:
    """What each moved machine saves on its own: the usual day with only that machine moved, against the usual day."""
    base_ev = evaluate(problem, baseline)["total_rs"]
    out = {}
    for m in problem["shiftable"]:
        b = m.get("baseline_start_h")
        if b is None or plan.get(m["id"]) in (None, b):
            continue
        out[m["id"]] = base_ev - evaluate(problem, {**baseline, m["id"]: plan[m["id"]]})["total_rs"]
    return out


def why_moved(problem: dict, m: dict, start: int) -> str:
    """Plain-code answer to "why move this machine there?". Every number comes from the tariff and the machine's window."""
    from .tariff import slot_rates
    r = slot_rates(problem["tariff"])
    avg = lambda h: sum(r[int(k)] for k in range(int(h * SLOTS_PER_HOUR), int((h + m["duration_h"]) * SLOTS_PER_HOUR))) / (m["duration_h"] * SLOTS_PER_HOUR)  # noqa: E731
    b = m.get("baseline_start_h")
    if b is None:
        return f"{m['name']} is new today; {hhmm(start)} is the cheapest time inside its window ({hhmm(m['earliest_h'])} to {hhmm(m['latest_finish_h'])})."
    old, new = avg(b), avg(start)
    if new >= old:
        return f"{m['name']} stays close to its usual time: no cheaper hour fits its rules."
    cut = (1 - new / old) * 100
    frac = {33: "a third", 25: "a quarter", 50: "half"}.get(int(round(cut / 1)) if cut in (25, 33, 50) else 0, f"{cut:.0f}%")
    if 30 <= cut <= 36:
        frac = "a third"
    ok = start + m["duration_h"] <= m["latest_finish_h"]
    return (f"Power costs {frac} less then: ₹{new:.2f} against ₹{old:.2f} per unit at {hhmm(b)}. "
            + ("No deadline moves." if ok else "It still finishes inside its window."))


def apply_learned_rules(machines_cfg: dict, shiftable: list[dict]) -> None:
    """Every "no" the manager turned into a rule narrows that machine's window from then on."""
    for lr in machines_cfg.get("learned_rules", []):
        for m in shiftable:
            if m["id"] != lr["machine"]:
                continue
            if lr["kind"] == "usual" and m.get("baseline_start_h") is not None:
                m["earliest_h"], m["latest_finish_h"] = m["baseline_start_h"], m["baseline_start_h"] + m["duration_h"]
            elif lr["kind"] == "not_before":
                m["earliest_h"] = max(m["earliest_h"], int(lr["hour"]))
            elif lr["kind"] == "finish_by":
                m["latest_finish_h"] = min(m["latest_finish_h"], int(lr["hour"]))


def learned_rule_text(lr: dict, names: dict) -> str:
    n = names.get(lr["machine"], lr["machine"])
    h = lr.get("hour")
    if lr["kind"] == "not_before" and h is not None:
        return f"{n} never starts before {hhmm(h)}"
    if lr["kind"] == "finish_by" and h is not None:
        return f"{n} always finishes by {hhmm(h)}"
    return f"{n} stays at its usual time"
