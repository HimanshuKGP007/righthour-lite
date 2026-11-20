"""The architecture diagram (deck, 'Agents plan the day and a person approves every plan') drawn from the real run.

Every box on the diagram is tied to the file and function that implements it, and lit up from the audit log of the
current run, so you can see which parts have actually run.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

import streamlit.components.v1 as components

# id, title, sub-lines, code location, audit actions that prove it ran, drawing kind, x, y, w, h
E = [
    ("meters", "Smart meters", ["power use every", "15 min"], "data.load_uci_csv", {"validate_data"}, "in", 22, 56, 186, 92),
    ("factory", "Factory systems", ["the production plan"], "data/machines.json", {"constraint_parse"}, "in", 22, 160, 186, 92),
    ("bill", "Power bill", ["day one starts here"], "data/tariff_*.json", {"tariff_lookup"}, "in", 22, 264, 186, 92),
    ("orders", "Price orders", ["the state's hourly prices"], "agents.parse_tariff", {"tariff_parse", "tariff_sign_off"}, "in", 22, 368, 186, 92),
    ("weather", "Weather", ["sun and heat forecast"], "data.solar_forecast", {"solar_forecast"}, "in", 22, 472, 186, 92),
    ("collect", "1 Collect", ["pulls all inputs"], "agents.step_data", {"forecast_load"}, "pipe", 270, 100, 158, 120),
    ("check", "2 Check", ["flags bad readings"], "data.validate_load", {"validate_data"}, "pipe", 270, 236, 158, 120),
    ("store", "3 Store", ["one vault per", "factory"], "audit.py (SQLite)", {"validate_data", "forecast_load"}, "pipe", 270, 372, 158, 120),
    ("orch", "Orchestrator agent", ["Plans the steps, calls the tools,", "re-plans if a step fails"], "graph.py, agents.step_*", {"orchestrate"}, "dark", 476, 92, 438, 84),
    ("price", "Price", ["reads price", "orders"], "step_analysis", {"tariff_lookup", "tariff_parse"}, "tool", 476, 196, 138, 96),
    ("forecast", "Forecast", ["tomorrow's use", "and sun"], "forecast_load", {"forecast_load", "solar_forecast"}, "tool", 626, 196, 138, 96),
    ("rules", "Machines", ["turns notes", "into rules"], "parse_note", {"constraint_parse"}, "tool", 776, 196, 138, 96),
    ("opt", "Optimiser (OR-Tools)", ["Finds the cheapest plan that keeps", "every machine rule and deadline."], "optimiser.optimise_schedule", {"optimise"}, "dark", 460, 352, 300, 116),
    ("guard", "Guardrails", ["Rule check. Every", "step logged."], "agents.policy_check", {"policy_check"}, "green", 780, 352, 160, 116),
    ("router", "Router", ["Picks the model for each", "AI step. Maths never uses AI."], "router.py, route", set(), "tool", 460, 486, 236, 86),
    ("memory", "Factory memory", ["Machine profiles, rules", "and past “no” answers"], "audit.py, config.py", {"reject", "learn", "verify_savings", "setup_saved", "learn_rule"}, "tool", 712, 486, 228, 86),
    ("manager", "Manager decides", ["Approve, edit or reject", "the plan"], "app.py, graph.py", {"work_order", "reject", "relax_rule"}, "human", 1002, 340, 216, 112),
    ("runs", "Plan runs", ["People switch the machines"], "agents.step_action", {"work_order"}, "light", 1002, 486, 216, 84),
]
BY_ID = {e[0]: e for e in E}


def element_states(rows: list[dict], pending: dict | None, verified: bool, loaded: bool) -> dict[str, str]:
    """ran | wait | ask | part"""
    seen = {r["action"] for r in rows}
    st = {}
    for eid, _t, _s, _c, actions, *_ in E:
        st[eid] = "ran" if seen & actions else "wait"
    if loaded:
        st["meters"] = "ran"
    st["router"] = "ran" if any(r.get("tier") in ("T1", "T2") for r in rows) else "wait"
    if pending and pending.get("type") == "approval":
        st["manager"] = "ask"
    elif pending and pending.get("type") == "choose_relaxation":
        st["manager"] = "ask"
    if "work_order" not in seen:
        st["runs"] = "wait"
    st["loop_no"] = "ran" if "learn_rule" in seen else ("part" if "reject" in seen else "wait")
    st["loop_day"] = "ran" if verified or seen & {"verify_savings", "learn"} else "wait"
    return st


STATE_TEXT = {"ran": "done this run", "wait": "not run yet", "ask": "your turn", "part": "logged only"}
BADGE = {"ran": "Done", "ask": "Your turn", "part": "Logged"}


def _lines(x: float, y: float, lines: list[str], cls: str, dy: float = 15) -> str:
    return "".join(f"<text class='{cls}' x='{x}' y='{y + i * dy}'>{escape(t)}</text>" for i, t in enumerate(lines))


def _badge(x: float, y: float, w: float, state: str) -> str:
    """A word, not a dot: the state is written on the box."""
    word = BADGE.get(state)
    if not word:
        return ""
    bw = 8 + 7.2 * len(word)
    return (f"<g class='bd'><rect x='{x + w - bw - 10}' y='{y - 11}' width='{bw}' height='22' rx='6'/>"
            f"<text x='{x + w - bw / 2 - 10}' y='{y + 4}' text-anchor='middle'>{word}</text></g>")


def svg(states: dict[str, str], selected: str | None = None) -> str:
    out = ["<svg class='dg' viewBox='0 0 1240 650' role='img' aria-label='Architecture: inputs, data pipeline, agent team, optimiser, guardrails, manager'>",
           "<defs><marker id='ah' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
           "<path d='M0 0L10 5L0 10z' fill='context-stroke'/></marker></defs>"]
    # panels
    for x, y, w, h, label, cls in [(10, 20, 212, 560, "Inputs (read-only)", "p"), (250, 20, 712, 560, "RightHour cloud (each customer kept separate)", "p"),
                                   (460, 56, 484, 250, "Agent team", "p2"), (990, 300, 240, 290, "Factory team", "p")]:
        out.append(f"<rect class='dg-panel {cls}' x='{x}' y='{y}' width='{w}' height='{h}' rx='10'/>")
        out.append(f"<text class='dg-pl' x='{x + 14}' y='{y + 24 if cls != 'p2' else y + 22}'>{label}</text>")
    out.append("<rect class='dg-pipe' x='262' y='56' width='174' height='500' rx='10'/>")
    out.append("<text class='dg-pt' x='276' y='84'>Data pipeline</text>")
    # connectors
    ln = lambda d, cls="dg-ln", m="ah": f"<path class='{cls}' d='{d}' fill='none' marker-end='url(#{m})'/>"  # noqa: E731
    for y in (102, 206, 310, 414, 518):
        out.append(f"<path class='dg-ln' d='M208 {y}H236' fill='none'/>")
    out.append("<path class='dg-ln' d='M236 102V518' fill='none'/>")
    out.append(ln("M236 310H262"))
    out.append(ln("M436 128H476"))
    for cx in (545, 695, 845):
        out.append(f"<path class='dg-ln' d='M{cx} 176V196' fill='none' marker-end='url(#ah)' marker-start='url(#ah)'/>")
    out.append(ln("M695 306V352"))
    out.append(ln("M760 410H780"))
    out.append(ln("M940 410H1002"))
    out.append(ln("M1110 452V486"))
    loop_no, loop_day = states["loop_no"], states["loop_day"]
    out.append(f"<g class='dg-hit{' sel' if selected == 'loop_no' else ''}' data-id='loop_no'><title>Every “no” becomes a new rule: {STATE_TEXT[loop_no]}</title>"
               f"<path class='hit' d='M1110 340V242H918'/>"
               f"<path class='dg-dash gold {'on' if loop_no != 'wait' else ''}' d='M1110 340V242H918' fill='none' marker-end='url(#ah)'/>"
               "<text class='dg-loop gold' x='974' y='224'>Every “no” becomes a new rule</text></g>")
    out.append(f"<g class='dg-hit{' sel' if selected == 'loop_day' else ''}' data-id='loop_day'><title>Next day: the meters check the saving: {STATE_TEXT[loop_day]}</title>"
               f"<path class='hit' d='M1110 570V620H115V586'/>"
               f"<path class='dg-dash {'on' if loop_day == 'ran' else ''}' d='M1110 570V620H115V586' fill='none' marker-end='url(#ah)'/>"
               "<text class='dg-loop' x='560' y='612' text-anchor='middle'>Next day: the meters check the saving</text></g>")
    out.append("<text class='dg-s' x='974' y='262'>reasons are saved;</text><text class='dg-s' x='974' y='277'>auto-rules come later</text>")
    # boxes
    for eid, title, sub, code, _a, kind, x, y, w, h in E:
        s = states[eid]
        out.append(f"<g class='dg-box {kind} s-{s}{' sel' if selected == eid else ''}' data-id='{eid}'><title>{escape(title)}: {STATE_TEXT[s]}. Click to open.</title>"
                   f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='8'/>"
                   f"{_badge(x, y, w, s)}"
                   f"<text class='dg-t' x='{x + 14}' y='{y + 26}'>{escape(title)}</text>"
                   f"{_lines(x + 14, y + 46, sub, 'dg-s')}</g>")
    out.append("</svg>")
    return "".join(out)


def mapping_rows(rows: list[dict], states: dict[str, str]) -> list[dict]:
    """One line per diagram element: where it lives in the code, and what the run log shows for it."""
    out = []
    for eid, title, _sub, code, actions, *_ in E:
        hits = [r for r in rows if r["action"] in actions or (eid == "router" and r["tier"] in ("T1", "T2"))]
        tiers = sorted({r["tier"] for r in hits})
        modes = sorted({r["mode"] for r in hits})
        out.append({"Diagram box": title, "Where in the prototype": code, "Status": STATE_TEXT[states[eid]],
                    "Steps logged": len(hits), "Tier": " ".join(tiers), "Mode": ", ".join(modes)})
    out.append({"Diagram box": "Every “no” becomes a new rule", "Where in the prototype": "graph.py reject → audit.py",
                "Status": STATE_TEXT[states["loop_no"]], "Steps logged": sum(r["action"] == "reject" for r in rows), "Tier": "human", "Mode": "human"})
    out.append({"Diagram box": "Next day: the meters check the saving", "Where in the prototype": "agents.verify_and_learn",
                "Status": STATE_TEXT[states["loop_day"]], "Steps logged": sum(r["action"] in ("verify_savings", "learn") for r in rows),
                "Tier": "T0", "Mode": "code"})
    return out


# ---------------------------------------------------------------- the clickable component
_hld = components.declare_component("rh_hld", path=str(Path(__file__).parent / "hld_component"))


def component(states: dict[str, str], selected: str | None = None, key: str = "hld", css: str = "") -> str | None:
    """Draw the diagram. Returns the id of the box the user clicked (or the previous selection if none)."""
    val = _hld(svg=svg(states, selected), css=css, selected=selected, key=key, default=selected)
    return val or selected


# ---------------------------------------------------------------- what each box is, and where its information comes from
BOX_INFO: dict[str, dict] = {
    "meters": {"title": "Smart meters", "what": "The plant's power use, read every 15 minutes.",
               "comes_from": ["Today: Steel_industry_data.csv, your own upload, or a CSV dropped in the incoming/ folder.",
                              "Live: the utility meter (AMI) or a gateway at the plant, sent over MQTT or Modbus into the data pipeline."],
               "code": "righthour/data.py · load_uci_csv", "produces": "A table of time, kW and power factor."},
    "factory": {"title": "Factory systems", "what": "What the plant plans to make, and when each machine must run.",
                "comes_from": ["Today: the Machines table in Setup, plus the notes you type for the day.",
                               "Live: the plant's ERP or production planning tool, through an API or a daily export."],
                "code": "righthour/config.py · machines · agents.parse_note", "produces": "Machines with hours, windows and fixed batches."},
    "bill": {"title": "Power bill", "what": "The price bands and the demand charge the plant pays.",
             "comes_from": ["Today: the Price bands table in Setup (starts from data/tariff_msedcl_illustrative.json).",
                            "Live: the plant's electricity bill, read once on day one and kept in the tariff library."],
             "code": "righthour/tariff.py · load_tariff, validate_tariff", "produces": "Rate per kVAh for each 15-minute slot."},
    "orders": {"title": "Price orders", "what": "New hourly prices published by the state regulator.",
               "comes_from": ["Today: paste the order text in Setup. The Tariff agent drafts the bands and a person signs off.",
                              "Live: the regulator's website, checked on a schedule. A new order is parsed once, then shared with every plant."],
               "code": "righthour/agents.py · parse_tariff", "produces": "A signed-off tariff for the library."},
    "weather": {"title": "Weather", "what": "Tomorrow's sun, which sets how much solar power the roof makes.",
                "comes_from": ["Today: Open-Meteo (forecast, or recorded data for a past day). Clear sky if offline. You can force clear or cloudy.",
                               "Live: the same weather service, fetched each evening for the plant's latitude and longitude."],
                "code": "righthour/data.py · solar_forecast", "produces": "Solar kW for each 15-minute slot."},
    "collect": {"title": "1 Collect", "what": "Pulls every input into one place before the agents start.",
                "comes_from": ["Today: runs when you press Generate plan, using the loaded file and Setup.",
                               "Live: a scheduled job each evening that pulls meters, plan, prices and weather."],
                "code": "righthour/agents.py · step_data", "produces": "One bundle of inputs for the day."},
    "check": {"title": "2 Check", "what": "Flags bad readings: gaps, negative values, odd spikes.",
              "comes_from": ["Today: the readings in the loaded meter file.",
                             "Live: the same checks on every new batch of meter readings, with an alert to a person."],
              "code": "righthour/data.py · validate_load", "produces": "A list of data warnings, or 'no gaps or spikes'."},
    "store": {"title": "3 Store", "what": "Keeps the data and every decision, one vault per factory.",
              "comes_from": ["Today: the audit log in righthour_audit.sqlite, and the loaded data in memory.",
                             "Live: a separate database or storage area per customer, so plants never mix."],
              "code": "righthour/audit.py · AuditLog", "produces": "A record of every step that can be replayed."},
    "orch": {"title": "Orchestrator agent", "what": "Plans the steps, calls the tools, and plans again if a step fails.",
             "comes_from": ["Today: your goal text, plus the results of the other boxes.",
                            "Live: the same, started by a scheduler each evening instead of a button."],
             "code": "righthour/graph.py · build_graph; agents.step_goal", "produces": "The order of steps, and a request for approval."},
    "price": {"title": "Price", "what": "Reads the price bands and checks them against the regulator's rules.",
              "comes_from": ["Today: the tariff in Setup, or one you signed off in the tariff library.",
                             "Live: the tariff library, kept up to date from price orders."],
              "code": "righthour/agents.py · step_analysis", "produces": "Rates per slot and any tariff warnings."},
    "forecast": {"title": "Forecast", "what": "Predicts tomorrow's base load and solar power.",
                 "comes_from": ["Today: the average of the same weekday over the 4 weeks before the plan day, times a learned correction. Solar from Weather.",
                                "Live: the same method on the meter store, with the correction learned from each next-day check."],
                 "code": "righthour/data.py · forecast_load, solar_forecast", "produces": "kW and power factor for each 15-minute slot."},
    "router": {"title": "Router", "what": "Picks the model for every AI step. Maths and checks always go to code, never to a model.",
               "comes_from": ["Today: fixed rules in code. Routine text goes to a small model. A step that fails its check twice, a plan that does not fit, or a free question goes to a large model.",
                              "Live: the same rules, plus a privacy setting that keeps routine steps on a model running at the plant."],
               "code": "righthour/router.py · route", "produces": "One model choice per step, with the reason and the cost logged."},
    "memory": {"title": "Factory memory", "what": "Keeps what the agent has learned about this factory: machine profiles, rules and past “no” answers.",
               "comes_from": ["Today: the Setup tables and the audit log. A rejected plan is logged with its reason.",
                              "Live: a store per factory. A manager confirms each rule the agent learned before it is used."],
               "code": "righthour/audit.py · config.py", "produces": "Rules and corrections that make each day's plan fit better."},
    "rules": {"title": "Machines", "what": "Turns the day's notes into rules the solver can use.",
              "comes_from": ["Today: the notes you type in the Plan tab. A small model (Groq or Claude) reads them, or simple offline reading.",
                             "Live: notes from supervisors, or fields from the factory system, read the same way."],
              "code": "righthour/agents.py · parse_note", "produces": "Fixed batches and new orders with hours and kW."},
    "opt": {"title": "Optimiser (OR-Tools)", "what": "Finds the cheapest plan that keeps every machine rule and deadline.",
            "comes_from": ["Today: the forecast, tariff, machines, rules and notes from the boxes above.",
                           "Live: the same inputs. The solver never calls a language model, so the maths is always exact."],
            "code": "righthour/optimiser.py · optimise_schedule", "produces": "A start hour for every machine, and its cost."},
    "guard": {"title": "Guardrails", "what": "Checks the plan against hard rules and logs every step.",
              "comes_from": ["Today: the plan, plus contract demand and rules from Setup.",
                             "Live: the same checks, plus limits set by the customer's own policy."],
              "code": "righthour/agents.py · policy_check", "produces": "Pass or fail for each rule, with reasons."},
    "manager": {"title": "Manager decides", "what": "A person approves, edits or rejects the plan, or picks which rule to relax.",
                "comes_from": ["Today: buttons in the Plan tab. Every choice goes to the audit log.",
                               "Live: the same screen, or a message to the manager's phone at 6:30 am with the same buttons."],
                "code": "righthour/graph.py · approval, ask_manager", "produces": "A decision, with a reason if rejected."},
    "runs": {"title": "Plan runs", "what": "The approved plan becomes a work order. People switch the machines.",
             "comes_from": ["Today: the approved schedule. Download the work order as a CSV.",
                            "Live: sent to supervisors, or to the factory system. RightHour never switches equipment."],
             "code": "righthour/graph.py · work_order", "produces": "A work order with start and end for each machine."},
    "loop_no": {"title": "Every “no” becomes a new rule", "what": "When a manager rejects a plan, the reason should teach the agent a rule.",
                "comes_from": ["Today: when you reject a plan, the agent asks whether to remember it. Say yes and it becomes a rule in Factory memory.",
                               "Live: the same, from the manager's phone."],
                "code": "righthour/graph.py · approval (reject)", "produces": "A rule every later plan obeys."},
    "loop_day": {"title": "Next day: the meters check the saving", "what": "After the day, real meter readings show what was really saved.",
                 "comes_from": ["Today: real readings from the loaded file for a replayed day, or an uploaded CSV. You say what actually ran.",
                                "Live: yesterday's readings arrive from the meter store with no upload."],
                 "code": "righthour/agents.py · verify_and_learn", "produces": "Verified saving, and a better forecast correction."},
}


def info(eid: str) -> dict:
    return BOX_INFO[eid]


# which box each logged step belongs to, for telemetry rows that link back to the diagram
ACTION_BOX: dict[str, str] = {}
for _e in E:
    for _a in sorted(_e[4]):
        ACTION_BOX.setdefault(_a, _e[0])
ACTION_BOX.update({"validate_data": "check", "forecast_load": "forecast", "solar_forecast": "weather", "tariff_lookup": "price",
                   "tariff_parse": "orders", "tariff_sign_off": "orders", "constraint_parse": "rules", "optimise": "opt",
                   "policy_check": "guard", "orchestrate": "orch", "replan": "orch", "explain": "orch", "relax_rule": "manager",
                   "reject": "loop_no", "work_order": "runs", "verify_savings": "loop_day", "learn": "loop_day",
                   "setup_saved": "factory", "safe_fallback": "guard"})
