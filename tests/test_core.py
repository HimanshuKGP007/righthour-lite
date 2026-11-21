import itertools
import json

import pytest

from righthour import DATA_DIR
from righthour import data as D
from righthour.agents import Context, Orchestrator, policy_check, verify_and_learn
from righthour.llm import number_guard
from righthour.optimiser import allowed_starts, evaluate, optimise_schedule
from righthour.router import annual_cost_model, route
from righthour.tariff import N_SLOTS, load_tariff, validate_tariff

TARIFF = load_tariff(DATA_DIR / "tariff_msedcl_illustrative.json")
SITE = json.loads((DATA_DIR / "site.json").read_text())
MACH = json.loads((DATA_DIR / "machines.json").read_text())


def flat_problem(shiftable, fixed=(), cd=10_000, mtd=10_000):
    return {"base_kw": [0.0] * N_SLOTS, "pf": [1.0] * N_SLOTS, "pv_kw": [0.0] * N_SLOTS, "fixed": list(fixed),
            "shiftable": shiftable, "no_overlap": [], "tariff": TARIFF, "contract_demand_kva": cd, "mtd_max_kva": mtd}


def test_worked_example_2704():
    """Deck Section 2: 200 kW for 4 h, 17:00-21:00 at 10.55 vs 11:00-15:00 at 7.17 = Rs 2,704 a day."""
    load = [{"id": "cp", "name": "Compressor + press line", "kw": 200, "duration_h": 4, "earliest_h": 0,
             "latest_finish_h": 24, "baseline_start_h": 17}]
    p = flat_problem(load)
    evening = evaluate(p, {"cp": 17})["energy_rs"]
    solar = evaluate(p, {"cp": 11})["energy_rs"]
    assert evening == pytest.approx(8440)
    assert solar == pytest.approx(5736)
    assert evening - solar == pytest.approx(2704)


def test_rule_8a_flags_deck_rates():
    texts = " ".join(i.text for i in validate_tariff(TARIFF))
    assert "1.47x solar" in texts          # 10.55 / 7.17 < 1.5
    assert "20%" in texts                   # solar discount below the floor at normal 8.79


def test_routing_cost_model_matches_deck():
    m = annual_cost_model()
    assert m["All Opus 5.5"]["per_site_year"] == pytest.approx(64.8)
    assert m["All Sonnet 5.5"]["fleet_year"] == pytest.approx(16_200)
    assert m["Routed 90% Haiku / 10% Opus"]["fleet_year"] == pytest.approx(3969, rel=1e-3)
    saving = 1 - m["Routed 90% Haiku / 10% Opus"]["fleet_year"] / m["All Opus 5.5"]["fleet_year"]
    assert saving == pytest.approx(0.8775, abs=1e-3)


def test_router_escalation_rules():
    assert route("forecast_load").tier == "T0"
    assert route("constraint_parse").tier == "T1"
    assert route("constraint_parse", attempt=3).tier == "T2"
    assert route("replan", infeasible=True).tier == "T2"
    assert route("explain", input_tokens=200_000).tier == "T2"


def test_milp_matches_brute_force():
    machines = [dict(m) for m in MACH["shiftable"][:3]]
    hist = D.synthetic_history()
    fc = D.forecast_load(hist)
    furnace = {"id": "furnace", "name": "Furnace", "kw": 300, "start_h": 10, "end_h": 16}
    machines[0] = dict(machines[0], earliest_h=6)
    p = {**flat_problem(machines, [furnace], cd=SITE["contract_demand_kva"], mtd=SITE["month_to_date_max_kva"]),
         "base_kw": fc["kw"], "pf": fc["pf"], "pv_kw": D.clear_sky_pv(150)}
    best = min(evaluate(p, dict(zip([m["id"] for m in machines], c)))["total_rs"]
               for c in itertools.product(*[allowed_starts(m) for m in machines]))
    sol = optimise_schedule(p)
    assert sol["total_rs"] == pytest.approx(best, abs=0.5)


def _ctx(**kw):
    return Context(SITE, TARIFF, MACH, D.synthetic_history(), **kw)


def test_day_ahead_plan_reproduces_the_deck_demo():
    """Section 8.4 step 3-4: compressors and press line to 11:00-15:00, 800 kVAh, furnace untouched, Rs 2,704."""
    r = Orchestrator().run(_ctx())
    assert r["status"] == "awaiting_approval"
    assert r["plan"]["schedule"]["compressors"] == 11 and r["plan"]["schedule"]["press"] == 11
    assert {k: v for k, v in r["plan"]["schedule"].items() if k not in ("compressors", "press")} == \
        {"shotblast": 21, "pumps": 2}                          # the demo day is a Saturday: the oven runs Mon-Fri only
    assert r["saving_rs"] == pytest.approx(2704, abs=0.5)
    assert "800 kVAh" in r["explanation"] and "untouched" in r["explanation"]
    tiers = [(s["action"], s["tier"]) for s in r["steps"]]
    assert ("tariff_lookup", "T0") in tiers and ("constraint_parse", "T1") in tiers and ("explain", "T1") in tiers
    assert r["policy"]["passed"]
    assert r["plan"]["peak_kva"] <= SITE["contract_demand_kva"]
    assert "Projected saving" in r["explanation"]


def test_infeasible_order_triggers_replan_and_options():
    r = Orchestrator().run(_ctx(include_extra=True))
    assert r["status"] == "needs_manager_choice"
    feasible = [o for o in r["options"] if o["feasible"]]
    assert {o["id"] for o in feasible} >= {"early_crew"}
    assert r["recommended"] == "early_crew"
    tiers = [s["tier"] for s in r["steps"] if s["action"] == "replan"]
    assert tiers == ["T2"]


def test_manager_choice_resolves_plan():
    r = Orchestrator().run(_ctx(include_extra=True, relaxation="early_crew"))
    assert r["status"] == "awaiting_approval"
    assert r["plan"]["schedule"]["grinding"] >= 6


def test_edited_plan_breaking_contract_demand_is_blocked():
    r = Orchestrator().run(_ctx(include_extra=True, relaxation="early_crew"))
    edited = dict(r["plan"]["schedule"], grinding=10)
    pol = policy_check(r["problem"], edited)
    assert not pol["passed"]
    assert any("contract demand" in c["rule"] and not c["ok"] for c in pol["checks"])


def test_verify_and_learn_when_actuals_match_forecast():
    ctx = _ctx()
    r = Orchestrator().run(ctx)
    import datetime as dt
    import pandas as pd
    day = pd.Timestamp(r["forecast"]["date"])
    actual = pd.DataFrame({"ts": [day + pd.Timedelta(minutes=15 * k) for k in range(N_SLOTS)],
                           "kw": r["forecast"]["kw"], "pf": r["forecast"]["pf"]})
    v = verify_and_learn(ctx, r, r["plan"]["schedule"], actual)
    assert v["verified_saving_rs"] == pytest.approx(r["saving_rs"], rel=1e-6)
    assert v["new_bias"] == pytest.approx(1.0)


def test_number_guard_catches_invented_numbers():
    assert number_guard("Saving ₹4,498 a day", {"4,498"}) == []
    assert number_guard("Saving ₹9,999 a day", {"4,498"}) == ["9999"]


def test_cloudy_forecast_triggers_a_different_plan():
    clear = Orchestrator().run(_ctx(cloudy=False))["plan"]["schedule"]
    cloudy = Orchestrator().run(_ctx(cloudy=True))
    assert cloudy["status"] == "awaiting_approval"
    assert cloudy["plan"]["schedule"] != clear
    assert cloudy["plan"]["peak_kva"] <= SITE["month_to_date_max_kva"]


def test_tariff_text_parser_and_cache():
    from righthour.agents import parse_tariff
    text = ("MERC order (sample text). 00:00 to 09:00 normal hours: Rs 8.79 per kVAh. "
            "09:00 to 17:00 solar hours: Rs 7.03 per kVAh. 17:00 to 24:00 peak hours: Rs 10.55 per kVAh. "
            "Demand charge Rs 600 per kVA per month.")
    ctx, steps, lib = _ctx(), [], {}
    t = parse_tariff(ctx, text, lib, steps)
    assert [s["type"] for s in t["slots"]] == ["normal", "solar", "peak"]
    assert t["demand_charge_per_kva_month"] == 600
    assert steps[-1]["tier"] == "T2"
    lib[t["fingerprint"]] = t
    parse_tariff(ctx, text, lib, steps)
    assert steps[-1]["action"] == "tariff_lookup" and steps[-1]["tier"] == "T0"


def test_langgraph_full_demo_flow():
    """Section 8.4 end to end through the LangGraph interrupts: infeasible -> manager picks a rule -> blocked edit
    -> approval -> work order."""
    from righthour.graph import resume, start
    ctx = _ctx(include_extra=True)
    graph, cfg, state, pending = start(ctx)
    assert pending["type"] == "choose_relaxation"
    assert pending["recommended"] == "early_crew"
    state, pending = resume(graph, cfg, "early_crew")
    assert pending["type"] == "approval"
    assert state["saving_rs"] == pytest.approx(2704, abs=0.5)
    bad = dict(state["plan"]["schedule"], grinding=10)
    state, pending = resume(graph, cfg, {"action": "edit", "schedule": bad})
    assert state["status"] == "edit_blocked" and pending["type"] == "approval"
    assert any("contract demand" in f for f in state["edit_result"]["failed"])
    state, pending = resume(graph, cfg, {"action": "approve"})
    assert pending is None and state["status"] == "work_order_issued"
    actions = [r["action"] for r in ctx.audit.rows(ctx.run_id)]
    assert actions.count("optimise") == 2 and "relax_rule" in actions and "work_order" in actions


def test_uci_loader_auto_scales_and_labels_origin():
    d = D.load_uci_csv(DATA_DIR / "synthetic_in_uci_format.csv")
    assert 2.5 < d.attrs["scale"] < 3.5
    assert "South Korea" in d.attrs["source"]
    r = Orchestrator().run(Context(SITE, TARIFF, MACH, d))
    assert r["status"] == "awaiting_approval"


def test_groq_client_offline_without_key(monkeypatch):
    from righthour.llm import LLMClient
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    c = LLMClient()
    assert not c.live and c.mode == "offline template"


def test_groq_client_parses_openai_style_reply(monkeypatch):
    import io
    import json as _json
    from righthour import llm

    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = _json.loads(req.data)
        return io.BytesIO(_json.dumps({"choices": [{"message": {"content": "hello"}}],
                                       "usage": {"prompt_tokens": 7, "completion_tokens": 2}}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    c = llm.LLMClient("Groq", api_key="gsk_test")
    assert c.live
    assert c.complete("llama-3.1-8b-instant", "sys", "hi") == ("hello", 7, 2)
    assert seen["auth"] == "Bearer gsk_test" and seen["body"]["model"] == "llama-3.1-8b-instant"


def test_claude_client_and_router_switch(monkeypatch):
    import io
    import json as _json
    from righthour import llm, router

    def fake_urlopen(req, timeout=0):
        assert req.headers["X-api-key"] == "sk-test"
        return io.BytesIO(_json.dumps({"content": [{"text": "hi"}], "usage": {"input_tokens": 3, "output_tokens": 1}}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    assert llm.LLMClient("Claude", api_key="sk-test").complete("claude-haiku-5-5", "s", "p") == ("hi", 3, 1)
    router.set_provider("Claude")
    assert router.route("explain").model == "claude-haiku-5-5"
    router.set_provider("Groq")
    assert router.route("explain").model == "openai/gpt-oss-20b"


def test_every_logged_step_links_to_a_diagram_box():
    """The Manager console links each telemetry row to its box on the clickable diagram."""
    from righthour import architecture as ARCH
    from righthour.graph import resume, start
    ctx = _ctx(include_extra=True)
    graph, cfg, state, pending = start(ctx)
    if pending["type"] == "choose_relaxation":
        state, pending = resume(graph, cfg, pending["recommended"])
    resume(graph, cfg, {"action": "approve"})
    for r in ctx.audit.rows(ctx.run_id):
        assert ARCH.ACTION_BOX.get(r["action"]) in ARCH.BOX_INFO, r["action"]


def test_learning_ignores_outlier_days_and_caps_the_step():
    import pandas as pd
    ctx = _ctx()
    r = Orchestrator().run(ctx)
    day = pd.Timestamp(r["forecast"]["date"])
    ts = [day + pd.Timedelta(minutes=15 * k) for k in range(96)]
    big = pd.DataFrame({"ts": ts, "kw": [x * 2.4 for x in r["forecast"]["kw"]], "pf": r["forecast"]["pf"]})
    v = verify_and_learn(ctx, r, r["plan"]["schedule"], big)
    assert v["new_bias"] == pytest.approx(1.0) and "outlier" in v["learn_note"]
    up = pd.DataFrame({"ts": ts, "kw": [x * 1.4 for x in r["forecast"]["kw"]], "pf": r["forecast"]["pf"]})
    assert verify_and_learn(ctx, r, r["plan"]["schedule"], up)["new_bias"] == pytest.approx(1.10)


def test_machines_run_only_on_their_days():
    import datetime as dt
    from righthour.config import runs_today
    m = {"runs_on": "Mon–Sat"}
    assert runs_today(m, dt.date(2018, 12, 29)) and not runs_today(m, dt.date(2018, 12, 30))      # Sat yes, Sun no
    assert not runs_today(m, dt.date(2018, 12, 25), ["2018-12-25"])                                 # a holiday is a Sunday
    assert runs_today({"runs_on": "Every day"}, dt.date(2018, 12, 25), ["2018-12-25"])
    assert runs_today({}, dt.date(2018, 12, 30))                                                    # no setting: every day
