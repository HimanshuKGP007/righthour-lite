"""optimise_schedule(): the solver that decides the schedule (T0). A MILP solved with OR-Tools (SCIP, CBC fallback).

Decision: one start hour per shiftable machine. Objective: energy cost (slot rate x kVAh) plus the demand charge on
any rise above this month's recorded maximum kVA. Hard constraints: time windows, continuous runs, contract demand
and every active no-overlap rule.

Tie-break among equal-cost plans (a few paise, never changes the rupee result):
1. leave a machine at its usual time unless moving it saves money;
2. centre a moved run on 13:00, the middle of the solar window, so a late start does not spill into peak hours.
"""
from __future__ import annotations

from ortools.linear_solver import pywraplp

from .tariff import DT_H, N_SLOTS, SLOTS_PER_HOUR, bill, site_kva, slot_rates

MOVE_PENALTY_RS = 0.02
CENTRE_PENALTY_RS_PER_H = 0.001
SOLAR_CENTRE_H = 13.0


def allowed_starts(m: dict) -> list[int]:
    return [s for s in range(int(m["earliest_h"]), int(m["latest_finish_h"] - m["duration_h"]) + 1) if 0 <= s <= 23]


def machine_kw(fixed: list[dict], shiftable: list[dict], schedule: dict[str, int]) -> list[float]:
    total = [0.0] * N_SLOTS
    for f in fixed:
        for k in range(int(f["start_h"] * SLOTS_PER_HOUR), int(f["end_h"] * SLOTS_PER_HOUR)):
            total[k] += f["kw"]
    for m in shiftable:
        s = schedule[m["id"]]
        for k in range(int(s * SLOTS_PER_HOUR), int((s + m["duration_h"]) * SLOTS_PER_HOUR)):
            total[k % N_SLOTS] += m["kw"]
    return total


def evaluate(problem: dict, schedule: dict[str, int]) -> dict:
    mk = machine_kw(problem["fixed"], problem["shiftable"], schedule)
    kva = site_kva(problem["base_kw"], problem["pf"], problem["pv_kw"], mk)
    b = bill(kva, problem["tariff"], problem["mtd_max_kva"])
    b["machine_kw"] = mk
    b["load_kw"] = [problem["base_kw"][k] + mk[k] for k in range(N_SLOTS)]
    return b


def _tie(m: dict, s: int) -> float:
    b = m.get("baseline_start_h")
    pen = 0.0 if (b is not None and s == b) else MOVE_PENALTY_RS
    return pen + CENTRE_PENALTY_RS_PER_H * abs(s + m["duration_h"] / 2 - SOLAR_CENTRE_H)


def optimise_schedule(problem: dict, time_limit_s: int = 20) -> dict:
    """problem keys: base_kw, pf, pv_kw, fixed, shiftable, no_overlap [(a, b)], tariff, contract_demand_kva, mtd_max_kva."""
    base, pf, pv = problem["base_kw"], problem["pf"], problem["pv_kw"]
    tariff = problem["tariff"]
    rates = slot_rates(tariff)
    cd, mtd = problem["contract_demand_kva"], problem["mtd_max_kva"]
    dc = tariff["demand_charge_per_kva_month"]
    machines = problem["shiftable"]
    fixed_kw = machine_kw(problem["fixed"], [], {})
    base_kva = [max(0.0, base[k] - pv[k]) / pf[k] for k in range(N_SLOTS)]
    surplus = [max(0.0, pv[k] - base[k]) for k in range(N_SLOTS)]

    solver = pywraplp.Solver.CreateSolver("SCIP") or pywraplp.Solver.CreateSolver("CBC")
    solver.SetTimeLimit(time_limit_s * 1000)
    x = {}
    for m in machines:
        starts = allowed_starts(m)
        if not starts:
            return {"status": "infeasible", "reason": f"{m['name']} cannot fit its own time window.", "culprit": m["id"]}
        for s in starts:
            x[m["id"], s] = solver.BoolVar(f"x_{m['id']}_{s}")
        solver.Add(sum(x[m["id"], s] for s in starts) == 1)

    def cover(m: dict, k: int):
        return sum(x[m["id"], s] for s in allowed_starts(m)
                   if s * SLOTS_PER_HOUR <= k < (s + m["duration_h"]) * SLOTS_PER_HOUR)

    inf = solver.infinity()
    mimp = [solver.NumVar(0, inf, f"m_{k}") for k in range(N_SLOTS)]
    peak = solver.NumVar(0, inf, "peak_kva")
    extra = solver.NumVar(0, inf, "extra_kva")
    for k in range(N_SLOTS):
        solver.Add(mimp[k] >= fixed_kw[k] + sum(m["kw"] * cover(m, k) for m in machines) - surplus[k])
        solver.Add(base_kva[k] + mimp[k] <= cd)
        solver.Add(peak >= base_kva[k] + mimp[k])
    solver.Add(extra >= peak - mtd)

    by_id = {m["id"]: m for m in machines}
    for a, b in problem.get("no_overlap", []):
        if a in by_id and b in by_id:
            for k in range(N_SLOTS):
                solver.Add(cover(by_id[a], k) + cover(by_id[b], k) <= 1)

    energy = sum(rates[k] * DT_H * mimp[k] for k in range(N_SLOTS))
    tie = sum(_tie(m, s) * x[m["id"], s] for m in machines for s in allowed_starts(m))
    solver.Minimize(energy + dc * extra + tie)

    status = solver.Solve()
    if status != pywraplp.Solver.OPTIMAL:
        return {"status": "infeasible", "solver_status": int(status),
                "reason": "No schedule meets every hard rule (contract demand, time windows, fixed batches, crew rules)."}
    schedule = {m["id"]: next(s for s in allowed_starts(m) if x[m["id"], s].solution_value() > 0.5) for m in machines}
    result = evaluate(problem, schedule)
    result.update({"status": "optimal", "schedule": schedule, "solver": "OR-Tools MILP (SCIP)"})
    return result
