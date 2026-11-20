"""Run the 90-second demo script (Section 8.4) in a terminal through the LangGraph graph, with no UI.

    python cli_demo.py                                   # synthetic profile
    python cli_demo.py data/Steel_industry_data.csv      # real UCI data (auto-scaled)
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

from righthour import DATA_DIR
from righthour import data as D
from righthour.agents import Context, rs, verify_and_learn
from righthour.graph import resume, start
from righthour.tariff import load_tariff


def show(ctx: Context, seen: set) -> None:
    for s in ctx.audit.rows(ctx.run_id):
        if s["id"] not in seen:
            seen.add(s["id"])
            print(f"  [{s['tier']:<5}] {s['agent']:<27} {s['action']:<17} {s['reason']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--scale", type=float, default=None, help="default: auto-scale to the demo site")
    a = ap.parse_args()
    site = json.loads((DATA_DIR / "site.json").read_text())
    mach = json.loads((DATA_DIR / "machines.json").read_text())
    tariff = load_tariff(DATA_DIR / "tariff_msedcl_illustrative.json")
    hist = D.load_uci_csv(a.csv, a.scale) if a.csv else D.synthetic_history()
    print("Data:", hist.attrs.get("source"))

    print("\nSteps 1-4: goal, data, analysis, decision")
    ctx = Context(site, tariff, mach, hist)
    seen: set = set()
    graph, cfg, st, pending = start(ctx)
    show(ctx, seen)
    print("\n  Explanation:", st["explanation"])
    st, pending = resume(graph, cfg, {"action": "approve"})
    show(ctx, seen)
    first = st

    print("\nStep 5: one more machine, so the plan cannot fit the solar window")
    ctx2 = Context(site, tariff, mach, hist, include_extra=True, audit=ctx.audit)
    graph, cfg, st, pending = start(ctx2)
    show(ctx2, seen)
    if pending["type"] == "choose_relaxation":
        for o in pending["options"]:
            print("   option:", o["text"], "->", f"feasible, {rs(o['cost_rs'])} for the day" if o["feasible"] else "still infeasible")
        print("   manager relaxes:", pending["recommended"])
        st, pending = resume(graph, cfg, pending["recommended"])
    else:
        print("   on this data the order fits without relaxing any rule")
    show(ctx2, seen)
    print("  Explanation:", st["explanation"])

    print("\nStep 6: an edited plan that breaks contract demand")
    st, pending = resume(graph, cfg, {"action": "edit", "schedule": dict(st["plan"]["schedule"], grinding=10)})
    show(ctx2, seen)
    print("   BLOCKED:", "; ".join(st["edit_result"]["failed"]))

    print("\nStep 7: approve, then next-day actuals")
    st, pending = resume(graph, cfg, {"action": "approve"})
    show(ctx2, seen)
    actual = D.synthetic_actual_day(pd.Timestamp(first["forecast"]["date"]).date())
    executed = dict(first["final_schedule"], press=14)   # the press line started three hours late, one hour into peak
    v = verify_and_learn(ctx, first, executed, actual)
    show(ctx, seen)
    print(f"   projected {rs(v['projected_saving_rs'])}, verified {rs(v['verified_saving_rs'])} "
          f"(press line started late, one hour ran in peak); forecast error {v['forecast_error_pct']:+.1f}%, "
          f"bias {v['old_bias']:.3f} -> {v['new_bias']:.3f}")


if __name__ == "__main__":
    main()
