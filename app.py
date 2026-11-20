"""RightHour Lite: Streamlit UI (Section 8.3), themed with the deck's tokens (Section 5.11).

    streamlit run app.py

A plant manager's console. The sidebar runs the day (which day, weather, goal, model, live mode). The Control room tab is the
design diagram: click any box to see where its information comes from and to change its settings. Plan shows the result.
"""
from __future__ import annotations

import datetime as dt
from html import escape
import io
import json
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from righthour import architecture as ARCH
from righthour import config as CFG
from righthour import data as D
from righthour import live as LIVE
from righthour.agents import Context, lakh, parse_tariff, rs, verify_and_learn
from righthour.audit import AuditLog
from righthour.graph import resume, start
from righthour.llm import KEY_ENV, PROVIDERS, LLMClient, default_provider
from righthour.router import MODELS, PRICES, set_provider
from righthour.tariff import N_SLOTS, SLOTS_PER_HOUR, validate_tariff
from righthour.theme import (AMBER, AMBER_TEXT, CSS, FONT, GREEN, ICONS, INK, MUTED, NAVY, NAVY_10, NAVY_30, NAVY_60, RED,
                             RULE)

ROOT = Path(__file__).parent
STEEL_FILE = ROOT / "Steel_industry_data.csv"
INCOMING = ROOT / "incoming"
BAND = {"normal": NAVY_60, "solar": AMBER, "peak": RED}
WEATHER = {"Live forecast": None, "Clear sky": False, "Cloudy day": True}
NOTE_COLS = ["Name", "Kind", "Note, in plain words", "Could start as early as", "Could finish as late as", "Side cost if relaxed"]
EXAMPLE_ORDER = {"Name": "Grinding line (new order)", "Kind": "New order",
                 "Note, in plain words": "New order: grinding line, 400 kW for 4 hours. Crew starts at 9 am and the order must be finished by 5 pm.",
                 "Could start as early as": 6, "Could finish as late as": 21, "Side cost if relaxed": "early-shift allowance, or dispatch moves to the evening"}

st.set_page_config(page_title="RightHour Lite", page_icon=str(ROOT / "righthour" / "assets" / "favicon.png"),
                   layout="wide", initial_sidebar_state="expanded")
st.markdown(CSS, unsafe_allow_html=True)

# ------------------------------------------------------------------ state
ss = st.session_state
ss.setdefault("cfg", CFG.load())            # the plant's own settings: site, tariff, machines, crew rules
ss.setdefault("cfg_version", 0)
SITE, MACH = ss.cfg["site"], ss.cfg["machines"]
ss.setdefault("hist", None)
ss.setdefault("bias", 1.0)
ss.setdefault("audit", AuditLog(Path("righthour_audit.sqlite")))
ss.setdefault("tariff", ss.cfg["tariff"])
ss.setdefault("library", {})
ss.setdefault("run", None)        # dict(ctx, graph, cfg, state, pending)
ss.setdefault("approved", None)   # (ctx, state) of the last approved plan
ss.setdefault("verify", None)
ss.setdefault("draft_tariff", None)
ss.setdefault("autoran", False)
ss.setdefault("trigger_plan", False)
ss.setdefault("provider", default_provider())
ss.setdefault("notes_ver", 0)
ss.setdefault("sel_box", "orch")
ss.setdefault("scale_mode", True)
ss.setdefault("live_active", False)       # live mode: the last days of the file are held back and arrive one by one
ss.setdefault("live_future", [])
ss.setdefault("live_base", None)
ss.setdefault("live_seen", {})
ss.setdefault("live_last_release", 0.0)
ss.setdefault("live_last_auto", None)
ss.setdefault("live_log", [])
ss.setdefault("notes_rows", [{"Name": n["defaults"]["name"], "Kind": "Fixed batch", "Note, in plain words": n["text"],
                               "Could start as early as": None, "Could finish as late as": None, "Side cost if relaxed": ""}
                              for n in MACH.get("daily_notes", [])])
if ss.hist is None and STEEL_FILE.exists():          # the steel data is the default: nothing to upload to get started
    ss.hist = D.load_uci_csv(STEEL_FILE, None)


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def chip(tier: str) -> str:
    return f"<span class='rh-chip {tier.lower()}'>{tier}</span>"


def _hist_key() -> str:
    h = ss.hist
    return f"{len(h)}_{h['ts'].iloc[0]:%Y%m%d}_{h['ts'].iloc[-1]:%Y%m%d}"


def _default_day(d_last: dt.date, d_tom: dt.date) -> dt.date:
    if ss.live_active:
        return d_tom             # live mode always plans the next day
    d = d_last
    while d.weekday() >= 5:      # a working day by default
        d -= dt.timedelta(days=1)
    return d


def reset_run() -> None:
    ss.run = ss.approved = ss.verify = None


def live_note(text: str) -> None:
    ss.live_log = ([f"{dt.datetime.now():%H:%M:%S}  {text}"] + ss.live_log)[:12]


def ingest_day(day_df: pd.DataFrame, label: str) -> None:
    """New meter readings arrive from a dropped file."""
    ss.hist, n_new = LIVE.merge_readings(ss.hist, day_df)
    live_note(f"{label}: {n_new} new readings")
    check_approved_plan()
    ss.trigger_plan = True


def check_approved_plan() -> None:
    """If a plan was approved for a day whose real readings are now in the file, check the saving and learn from it."""
    if ss.approved and ss.verify is None:
        actx, ast = ss.approved
        pday = ast["forecast"]["date"]
        real = D.day_actuals(ss.hist, pday)
        if real is not None:
            try:
                ss.verify = verify_and_learn(actx, ast, dict(ast["final_schedule"]), real)
                ss.bias = ss.verify["new_bias"]
                live_note(f"checked the approved plan for {pday} against the real readings: saving {rs(ss.verify['verified_saving_rs'])}")
            except Exception as e:  # noqa: BLE001
                live_note(f"could not check {pday}: {e}")


# ------------------------------------------------------------------ run controls (shared by the sidebar and the Manager console)
# The same controls appear in one place per screen: on the Manager console they sit in its Inputs column, on every other
# screen in the sidebar. Their values live in session state, so they survive moving between screens.
VIEWS = ["Manager console", "Plan", "Control room", "Agent log", "Audit trail"]
ss.setdefault("view", "Manager console")
ss.setdefault("w_weather", "Live forecast")
ss.setdefault("w_goal", "Cut tomorrow's bill without disrupting production.")
ss.setdefault("w_live", False)
ss.setdefault("w_auto_adv", False)
ss.setdefault("w_adv_s", 20)
ss.setdefault("w_auto_plan", True)
ss.setdefault("w_plan_at", dt.time(18, 0))
ss.setdefault("w_provider", ss.provider)
ss.setdefault("pd_for", None)
for _k in ["w_weather", "w_goal", "w_live", "w_auto_adv", "w_adv_s", "w_auto_plan", "w_plan_at", "w_provider", "w_plan_day",
           "key_Groq", "key_Claude", "view_nav"]:
    if _k in ss:                       # keep values alive while their widget is drawn on another screen
        ss[_k] = ss[_k]

# the day to plan: reset to a sensible default whenever the data changes
plan_date, has_actuals, d_range, d_err = None, False, None, None
if ss.hist is not None:
    try:
        d_range = D.plan_date_range(ss.hist)
        tag = f"{_hist_key()}_{ss.live_active}"
        if ss.pd_for != tag or not (d_range[0] <= ss.get("w_plan_day", d_range[0]) <= d_range[2]):
            ss.w_plan_day, ss.pd_for = _default_day(d_range[1], d_range[2]), tag
        plan_date = ss.w_plan_day
        has_actuals = D.day_actuals(ss.hist, plan_date) is not None
    except ValueError as e:
        d_err = str(e)
weather, goal = ss.w_weather, ss.w_goal
ss.provider = ss.w_provider or ss.provider
LLM = LLMClient(ss.provider, api_key=ss.get(f"key_{ss.provider}") or None)
set_provider(ss.provider)
go_btn = bool(ss.get("go_btn")) or bool(ss.get("go_btn_c"))

# live mode switches happen here, before anything is drawn
if ss.w_live and not ss.live_active and ss.hist is not None:
    try:
        ss.live_base = ss.hist
        ss.hist, ss.live_future = LIVE.split_for_live(ss.hist, 7)
        ss.live_active, ss.live_last_release = True, time.time()
        live_note("live mode on: the last 7 days are held back")
        reset_run()
        ss.autoran = False
        st.rerun()
    except ValueError as e:
        ss.w_live = False
        live_note(f"live mode needs more data: {e}")
elif not ss.w_live and ss.live_active:
    ss.hist, _ = LIVE.merge_readings(ss.live_base, ss.hist)
    ss.live_active, ss.live_future, ss.live_base = False, [], None
    live_note("live mode off: full file restored")
    reset_run()
    ss.autoran = False
    st.rerun()
auto_adv, adv_s, plan_at, auto_plan = ss.w_auto_adv, ss.w_adv_s, ss.w_plan_at, ss.w_auto_plan


def run_controls(where: str) -> None:
    """Every control that runs the day. `where` is 'side' or 'console'; only one is drawn per screen."""
    btn_key = "go_btn" if where == "side" else "go_btn_c"
    if d_range:
        st.date_input("Plan for", min_value=d_range[0], max_value=d_range[2], key="w_plan_day",
                      help="The agent forecasts this day from the 4 same weekdays before it. Days inside the meter file can be replayed "
                           "and checked against the real readings. The day after the file ends is 'tomorrow'.")
        html(f"<div class='rh-note'>{'Replay: the meter file holds this day, so the next-day check can use its real readings.' if has_actuals else 'Tomorrow: no readings yet. They arrive later and the check runs then.'}</div>")
    elif d_err:
        st.error(d_err)
    st.radio("Solar forecast", list(WEATHER), horizontal=True, key="w_weather",
             help="Live forecast asks Open-Meteo for the day's sunshine (recorded data for past days). Falls back to a clear-sky model if offline.")
    st.text_area("Goal for the agent", height=70, key="w_goal")
    st.button("Generate plan", type="primary", width="stretch", disabled=ss.hist is None or plan_date is None, key=btn_key)


def live_controls() -> None:
    st.toggle("Live mode", key="w_live", help="The last 7 days of the file are held back, as if they had not happened yet. "
              "They arrive one at a time like a real meter feed. Each arrival checks yesterday's plan and plans the next day. "
              "Files dropped into the incoming/ folder are picked up too.")
    if ss.live_active:
        st.checkbox("Simulated meter: release a day automatically", key="w_auto_adv",
                    help="Stands in for the real meter feed. Off: use the Release button.")
        st.slider("Seconds between days", 5, 120, key="w_adv_s", disabled=not ss.w_auto_adv)
        st.checkbox("Plan automatically when new readings arrive", key="w_auto_plan")
        st.time_input("Also plan every day at", key="w_plan_at", help="Checked while the app is open. A server would run this as a scheduled job.")
        html(f"<div class='rh-pill live'><i></i>Live: {len(ss.live_future)} days still to arrive</div>")


def model_controls() -> None:
    st.segmented_control("Provider", list(PROVIDERS), selection_mode="single", label_visibility="collapsed", key="w_provider")
    if ss.provider != "Offline":
        st.text_input(f"{ss.provider} API key", type="password", key=f"key_{ss.provider}",
                      placeholder=f"or set {KEY_ENV[ss.provider]}", help="Kept in this browser session only.")
    html(f"<div class='rh-pill {'live' if LLM.live else ''}'><i></i>{LLM.label}</div>"
         f"<div class='rh-note' style='margin-top:8px'>Small steps: <span class='mono'>{MODELS[ss.provider]['T1']}</span><br>"
         f"Large steps: <span class='mono'>{MODELS[ss.provider]['T2']}</span><br>Numbers never come from the model.</div>")


def goto(view: str, box: str | None = None) -> None:
    """Navigation callback: switch screens, and on the Control room open one box of the design diagram."""
    ss.view = view
    if box:
        ss.sel_box = box
        ss.hld_room_nonce = ss.get("hld_room_nonce", 0) + 1   # a fresh diagram, so it opens on this box


with st.sidebar:
    html(f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:4px'><div class='rh-mark' style='width:30px;height:30px;border-radius:8px'>"
         f"{ICONS['clock']}</div><div><div style='font-size:17px;font-weight:650;color:{NAVY};letter-spacing:-.02em'>RightHour Lite</div>"
         f"<div class='rh-note'>Day-ahead ToD bill optimiser</div></div></div>")
    if ss.view == "Manager console":
        html("<div class='rh-card panel' style='margin-top:14px'><h4>All controls are on the console</h4>"
             "<p class='rh-note'>Data, the day to plan, weather, goal, live mode and the language model sit in the console's Inputs column. "
             "Every panel there links to its box on the design diagram.</p></div>")
    else:
        st.subheader("The day to plan", divider="gray")
        run_controls("side")
        st.subheader("Live mode", divider="gray")
        live_controls()
        if ss.live_log:
            html("<div class='rh-note' style='margin-top:6px'>" + "<br>".join(ss.live_log[:5]) + "</div>")
        st.subheader("Language model", divider="gray")
        model_controls()


# the live tick: while live mode is on, look for new readings every few seconds
def _live_tick() -> None:
    changed = False
    frames, ss.live_seen, errs = LIVE.scan_folder(INCOMING, ss.live_seen, ss.hist.attrs.get("scale") if ss.hist is not None else None)
    for name, df in frames:
        ingest_day(df, f"file {Path(name).name}")
        changed = True
    for e in errs:
        live_note(f"skipped a file: {e}")
    if auto_adv and ss.live_future and time.time() - ss.live_last_release >= adv_s:
        ss.hist, ss.live_future, day = LIVE.release_next_day(ss.hist, ss.live_future)
        ss.live_last_release = time.time()
        live_note(f"meter feed delivered {day}")
        if auto_plan:
            ss.trigger_plan = True
        check_approved_plan()
        changed = True
    if auto_plan and LIVE.is_plan_due(dt.datetime.now(), plan_at, ss.live_last_auto):
        ss.live_last_auto, ss.trigger_plan, changed = dt.datetime.now().date(), True, True
        live_note(f"scheduled plan time {plan_at:%H:%M} reached")
    if changed:
        st.rerun()


if ss.live_active:
    st.fragment(run_every=4)(_live_tick)()


def notes_for_run() -> list[dict]:
    out = []
    for r in ss.notes_rows:
        text = (r.get("Note, in plain words") or "").strip()
        if not text:
            continue
        out.append({"name": r.get("Name"), "kind": r.get("Kind") or "New order", "text": text,
                    "relax_earliest_h": r.get("Could start as early as"), "relax_latest_finish_h": r.get("Could finish as late as"),
                    "relax_note": r.get("Side cost if relaxed") or ""})
    return out


# the first visit runs the plan on its own, so the whole flow is visible without a single click
run_error = None
if (go_btn or ss.trigger_plan or not ss.autoran) and ss.hist is not None and plan_date is not None:
    ss.autoran, ss.trigger_plan = True, False
    with st.spinner("Agents working: forecasting, reading the tariff, solving..."):
        try:
            ctx = Context(SITE, ss.tariff, MACH, ss.hist, goal=goal or "Cut tomorrow's bill.", cloudy=WEATHER[weather], plan_date=plan_date,
                          notes=notes_for_run(), bias=ss.bias, llm=LLM, audit=ss.audit)
            graph, cfg, state, pending = start(ctx)
            ss.run = {"ctx": ctx, "graph": graph, "cfg": cfg, "state": state, "pending": pending, "cfg_version": ss.cfg_version}
            ss.verify = None if not (ss.approved and ss.verify and ss.verify.get("_run") == ctx.run_id) else ss.verify
        except Exception as e:  # noqa: BLE001
            ss.run, run_error = None, f"No plan could be made: {e}"


def do_resume(value) -> None:
    r = ss.run
    with st.spinner("Re-planning..."):
        r["state"], r["pending"] = resume(r["graph"], r["cfg"], value)
    if r["state"].get("status") == "work_order_issued":
        ss.approved = (r["ctx"], r["state"])
        ss.verify = None


# ------------------------------------------------------------------ header
html(f"""<div class='rh-top'><div class='rh-mark'>{ICONS['clock']}</div>
<div><div class='name'>RightHour Lite</div>
<div class='sub'>Tells a factory when to run its machines so the power bill goes down</div></div>
<div class='site'><b>{SITE['name']}</b><br>{ss.tariff['name']}</div></div>""")


# ------------------------------------------------------------------ the agentic pipeline
def _short(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def step_strip(state: dict | None, pending: dict | None, verified: bool) -> None:
    s = state or {}
    status = s.get("status", "")
    replanned = any(r["action"] == "relax_rule" for r in (ss.audit.rows(ss.run["ctx"].run_id) if ss.run else []))
    asking = bool(pending and pending.get("type") == "choose_relaxation")
    items = [
        ("Goal", "done" if s else "wait", _short(s.get("goal", "Set a goal")) if s else "Give the agent a goal"),
        ("Data", "done" if s.get("forecast") else "wait", "Meter, tariff, weather, production" if s.get("forecast") else "Gathers its own data"),
        ("Analysis", "done" if s.get("fixed") is not None and s.get("forecast") else "wait", "Forecasts, tariff, constraints"),
        ("Decision", "done" if s.get("plan") else "wait", "Calls the solver" if s.get("plan") else "Invokes the optimiser"),
        ("Problem", "alert" if asking else ("done" if replanned else "wait"),
         "Asks which rule can flex" if asking else ("Conflict resolved" if replanned else "Only if rules conflict")),
        ("Action", "active" if pending and pending.get("type") == "approval" else ("ok" if status == "work_order_issued" else "wait"),
         "Awaiting your approval" if pending and pending.get("type") == "approval" else
         ("Approved, work order issued" if status == "work_order_issued" else "Plan, savings, approval")),
        ("Learning", "ok" if verified else "wait", "Actual vs expected checked" if verified else "Next-day check"),
    ]
    icon = {"done": "check", "ok": "check", "alert": "alert", "active": "person", "wait": "dot"}
    cells = "".join(f"<div class='rh-node {c}'><div class='rh-dot'>{ICONS[icon[c]]}</div><div class='t'>{t}</div><div class='s'>{d}</div></div>"
                    for t, c, d in items)
    html(f"<div class='rh-pipe'>{cells}</div>")


# ------------------------------------------------------------------ charts
def hour_axis():
    return dict(tickmode="array", tickvals=list(range(0, 25, 3)), ticktext=[f"{h:02d}:00" for h in range(0, 25, 3)],
                range=[0, 24], showgrid=False, linecolor=RULE, ticks="outside", tickcolor=RULE, tickfont=dict(size=12, color=MUTED))


def bands(fig, y0=0, y1=1, opacity=0.10, yref="paper"):
    for s in ss.tariff["slots"]:
        fig.add_shape(type="rect", xref="x", yref=yref, x0=s["start_h"], x1=s["end_h"], y0=y0, y1=y1,
                      fillcolor=BAND[s["type"]], opacity=opacity, line_width=0, layer="below")


HOVER = dict(bgcolor="#fff", bordercolor=NAVY_30, font=dict(family=FONT, size=13, color=INK))


def plan_figure(state: dict) -> go.Figure:
    """Demand curve on top, machine schedule below, one shared hour axis so bars line up with the load."""
    from plotly.subplots import make_subplots
    schedule = state["plan"]["schedule"]
    prob = state["problem"]
    rows = [(f"{f['name']}, {f['kw']:.0f} kW", None, f["start_h"], f["end_h"] - f["start_h"], True) for f in state["fixed"]]
    rows += [(f"{m['name'].replace(' (new order)', ', new')}, {m['kw']:.0f} kW", m.get("baseline_start_h"), schedule[m["id"]],
              m["duration_h"], False) for m in prob["shiftable"]]
    top_h, row_h = 300, 34
    gantt_h = 40 + row_h * len(rows)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.035,
                        row_heights=[top_h / (top_h + gantt_h), gantt_h / (top_h + gantt_h)])
    # tariff bands across both panels
    for s in ss.tariff["slots"]:
        fig.add_shape(type="rect", xref="x", yref="paper", x0=s["start_h"], x1=s["end_h"], y0=0, y1=1,
                      fillcolor=BAND[s["type"]], opacity=0.085, line_width=0, layer="below")
        fig.add_annotation(x=(s["start_h"] + s["end_h"]) / 2, y=1.0, xref="x", yref="paper", yanchor="bottom", showarrow=False,
                           text=f"<b>{s['type']}</b>  ₹{s['rate']:.2f}",
                           font=dict(size=12, color=AMBER_TEXT if s["type"] == "solar" else (RED if s["type"] == "peak" else NAVY_60)))
    x = [k / SLOTS_PER_HOUR for k in range(N_SLOTS)] + [24]
    b, p = state["baseline"]["kva"], state["plan"]["kva"]
    fig.add_trace(go.Scatter(x=x, y=b + [b[-1]], name="Usual schedule", line=dict(color=MUTED, width=1.8, dash="dot", shape="hv")), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=p + [p[-1]], name="RightHour plan", line=dict(color=NAVY, width=3, shape="hv"),
                             fill="tozeroy", fillcolor="rgba(20,40,75,0.06)"), row=1, col=1)
    cd, mtd = SITE["contract_demand_kva"], SITE["month_to_date_max_kva"]
    fig.add_hline(y=cd, line=dict(color=RED, width=1.4, dash="dash"), row=1, col=1)
    fig.add_hline(y=mtd, line=dict(color=NAVY_60, width=1, dash="dot"), row=1, col=1)
    fig.add_annotation(x=0.15, y=cd, xref="x", yref="y", text=f"Contract demand {cd} kVA", showarrow=False, yanchor="bottom",
                       xanchor="left", font=dict(size=12, color=RED))
    fig.add_annotation(x=0.15, y=mtd, xref="x", yref="y", text=f"This month's max {mtd} kVA", showarrow=False, yanchor="top",
                       xanchor="left", font=dict(size=12, color=NAVY_60))
    names = [r[0] for r in rows][::-1]
    for name, bs, st_, d, fixed in rows:
        if bs is not None and bs != st_:
            fig.add_trace(go.Bar(y=[name], x=[d], base=[bs], orientation="h", marker=dict(color="rgba(0,0,0,0)", line=dict(color=MUTED, width=1.5)),
                                 width=0.5, showlegend=False, hoverinfo="skip"), row=2, col=1)
        fig.add_trace(go.Bar(y=[name], x=[d], base=[st_], orientation="h", width=0.5, showlegend=False,
                             marker=dict(color=NAVY_60 if fixed else NAVY, line=dict(width=0)),
                             hovertemplate=f"{name}<br>{st_:02d}:00 to {st_ + d:02.0f}:00<extra></extra>"), row=2, col=1)
    ax = hour_axis()
    fig.update_xaxes(**ax, row=1, col=1)
    fig.update_xaxes(**ax, row=2, col=1)
    fig.update_yaxes(title=dict(text="kVA", standoff=4), gridcolor="#EEF1F6", zeroline=False, rangemode="tozero",
                     tickfont=dict(size=12, color=MUTED), row=1, col=1)
    fig.update_yaxes(categoryorder="array", categoryarray=names, tickfont=dict(size=12.5), ticksuffix="  ", row=2, col=1)
    fig.update_layout(barmode="overlay", height=top_h + gantt_h + 70, margin=dict(l=8, r=14, t=30, b=8), plot_bgcolor="#fff",
                      paper_bgcolor="#fff", font=dict(family=FONT, color=INK, size=13), hoverlabel=HOVER,
                      legend=dict(orientation="h", y=-0.07, x=0, font=dict(size=12.5), entrywidth=150))
    return fig


def bill(state: dict) -> None:
    """The headline: one bar for the usual bill, with the saving cut out of it, and demand against the contract."""
    base, plan = state["baseline"], state["plan"]
    sav = state.get("saving_rs")
    cd, mtd, pk = SITE["contract_demand_kva"], SITE["month_to_date_max_kva"], plan["peak_kva"]
    ok = pk <= mtd
    if sav is not None and base.get("feasible") and base["total_rs"] > 0:
        total = base["total_rs"]
        a, b_ = max(plan["total_rs"] / total, 0) * 100, max(sav / total, 0) * 100
        left = (f"<div class='cap'>Projected saving tomorrow</div>"
                f"<div class='rh-big'>{rs(sav)}<small>a day, about {lakh(sav * SITE['working_days_per_year'])} a year</small></div>"
                f"<div class='rh-bar' role='img' aria-label='Plan cost and saving as a share of the usual bill'>"
                f"<div class='a' style='width:{a:.2f}%'></div><div class='b' style='width:{b_:.2f}%'></div></div>"
                f"<div class='rh-legend'><span>RightHour plan <b>{rs(plan['total_rs'])}</b></span>"
                f"<span>Saved <b style='color:{GREEN}'>{rs(sav)}</b></span><span>Usual schedule <b>{rs(total)}</b></span></div>")
    else:
        left = (f"<div class='cap'>Plan cost for the day</div><div class='rh-big' style='color:{NAVY}'>{rs(plan['total_rs'])}</div>"
                f"<div class='rh-note'>No like-for-like baseline for this order, so no saving is claimed.</div>")
    pct = lambda v: min(max(v / cd * 100, 0), 100)  # noqa: E731
    right = (f"<div class='cap'>Max demand against the contract</div>"
             f"<div class='rh-big' style='color:{NAVY if ok else RED}'>{pk:.0f}<small>kVA, {'within' if ok else 'above'} this month's max</small></div>"
             f"<div class='rh-meter'><div class='fill' style='width:{pct(pk):.1f}%'></div>"
             f"<div class='tick top' style='left:{pct(mtd):.1f}%'><span style='transform:translateX(-50%)'>month max {mtd}</span></div>"
             f"<div class='tick cd bot' style='left:99.6%'><span style='transform:translateX(-100%)'>contract {cd}</span></div></div>")
    html(f"<div class='rh-bill'><div class='rh-panel'>{left}</div><div class='rh-panel'>{right}</div></div>")


# ------------------------------------------------------------------ the Control room: one pane per box of the design diagram
run = ss.run
state = run["state"] if run else {}
pending = run["pending"] if run else None
ctx = run["ctx"] if run else None


def save_cfg(**updates) -> None:
    """Write plant settings (site, tariff, machines, rules) to plant_config.json and mark any current plan as out of date."""
    cfg = ss.cfg
    new = {"site": updates.get("site", cfg["site"]), "tariff": updates.get("tariff", cfg["tariff"]),
           "machines": {**cfg["machines"], **{k: updates[k] for k in ("shiftable", "rules") if k in updates}}}
    ss.cfg = new
    CFG.save(new)
    ss.tariff, ss.cfg_version = new["tariff"], ss.cfg_version + 1
    ss.audit.log("setup", "Plant manager", "setup_saved", tier="human", mode="human", reason="plant settings changed",
                 detail={k: "changed" for k in updates})
    st.toast("Saved. Press Generate plan to plan with the new setup.")
    st.rerun()


def provenance(eid: str, rows: list[dict]) -> None:
    """The top of every pane: what the box does, where its information comes from, and what it did in this run."""
    info = ARCH.info(eid)
    got = ARCH.BY_ID[eid][4] if eid in ARCH.BY_ID else set()
    mine = [r for r in rows if r["action"] in got]
    src = "".join(f"<li>{x}</li>" for x in info.get("comes_from", []))
    last = (f"{len(mine)} step{'s' if len(mine) != 1 else ''} in this run, last at {mine[-1]['ts'][11:]} ({mine[-1]['mode']})" if mine else "Not run yet in this run.")
    html(f"<div class='rh-card panel'><h4>{ICONS['chip']}{info.get('title', eid)}</h4><p>{info.get('what', '')}</p>"
         f"<p class='rh-note' style='margin:8px 0 2px'><b>Where its information comes from</b></p><ul class='rh-src'>{src}</ul>"
         f"<p class='rh-note'>Code: <span class='mono'>{info.get('code', '')}</span> &nbsp;·&nbsp; Gives: {info.get('produces', '')}<br>{last}</p></div>")


def step_rows(actions: set[str], rows: list[dict]) -> None:
    mine = [r for r in rows if r["action"] in actions]
    if not mine:
        return
    body = "".join(f"<tr><td class='m'>{r['ts'][11:]}</td><td>{chip(r['tier'])}</td><td><b>{r['action']}</b></td><td>{r['reason']}</td><td>{r['mode']}</td></tr>" for r in mine)
    html(f"<table class='rh-log'><thead><tr><th>Time</th><th>Tier</th><th>Step</th><th>Why</th><th>Mode</th></tr></thead><tbody>{body}</tbody></table>")


def need_run(what: str) -> bool:
    if run:
        return False
    html(f"<div class='rh-note' style='margin:8px 0'>{what} Generate a plan first.</div>")
    return True


# ---- meters: the data source and the live feed
def pane_meters(rows) -> None:
    h = ss.hist
    if h is not None:
        f = LIVE.describe_feed(h)
        html(f"<div class='rh-stats'><div class='rh-stat'><div class='v'>{f['n_rows']:,}</div><div class='l'>15-minute readings loaded</div></div>"
             f"<div class='rh-stat'><div class='v'>{f['n_full_days']}</div><div class='l'>complete days</div></div>"
             f"<div class='rh-stat'><div class='v' style='font-size:20px'>{str(f['last_ts'])[:16]}</div><div class='l'>latest reading</div></div></div>")
        html(f"<div class='rh-note'>{h.attrs.get('source', '')}</div>")
        for i in D.validate_load(h):
            html(f"<div class='rh-note'><span class='rh-chip {'ok' if i['level'] == 'ok' else 'warn'}'>{i['level']}</span>{i['text']}</div>")
    st.markdown("**Choose the data**")
    auto = st.toggle("Auto-scale the load to the demo site", value=ss.scale_mode, key="scale_toggle",
                     help="The UCI plant is smaller than the demo site. Scaling keeps its real shape at the site's size.")
    ss.scale_mode = auto
    scale = None if auto else st.number_input("Scale factor (x)", 0.5, 10.0, 3.0, 0.5, key="scale_x")
    c1, c2 = st.columns(2)
    if STEEL_FILE.exists() and c1.button("Use the steel industry data", width="stretch", help="Steel_industry_data.csv (UCI, DAEWOO Steel). The default."):
        ss.hist = D.load_uci_csv(STEEL_FILE, scale)
        ss.live_active = False
        reset_run()
        ss.autoran = False
        st.rerun()
    if c2.button("Use the synthetic sample", width="stretch"):
        ss.hist = D.synthetic_history()
        ss.live_active = False
        reset_run()
        ss.autoran = False
        st.rerun()
    up = st.file_uploader("Or upload your own meter CSV", type=["csv"], help="Same columns as the UCI steel dataset: date, Usage_kWh, Lagging_Current_Power_Factor.")
    if up is not None and st.button("Load my CSV", width="stretch"):
        try:
            ss.hist = D.load_uci_csv(up, scale)
            ss.live_active = False
            reset_run()
            ss.autoran = False
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read that file: {e}")
    st.markdown("**Live feed**")
    if ss.live_active:
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        if c1.button("Release the next day now", disabled=not ss.live_future, type="primary", width="stretch"):
            ss.hist, ss.live_future, day = LIVE.release_next_day(ss.hist, ss.live_future)
            live_note(f"meter feed delivered {day}")
            check_approved_plan()
            ss.trigger_plan = True
            st.rerun()
        c2.markdown(f"{len(ss.live_future)} held-back days still to arrive. Or drop a CSV in <span class='mono'>{INCOMING.name}/</span>; it is picked up within seconds.",
                    unsafe_allow_html=True)
        for line in ss.live_log:
            html(f"<div class='rh-note mono'>{line}</div>")
    else:
        html("<div class='rh-note'>Turn on <b>Live mode</b> in the sidebar. The last 7 days of the file are held back and arrive one by one, like a real meter feed.</div>")


# ---- factory systems: the day's notes, the machines, the crew rules
def pane_factory(rows) -> None:
    html(f"<p style='max-width:74ch;margin:6px 0 4px'><b>Notes for {plan_date:%A %d %B %Y}.</b> Write what the supervisor would say. "
         f"A <b>Fixed batch</b> must run at its stated hours. A <b>New order</b> is placed by the agent inside the hours you state. "
         f"{'Groq and Claude read free wording.' if LLM.live else 'Offline mode reads simple notes: kW, hours, from 10 am to 4 pm, starts at 9 am, finished by 5 pm. Pick Groq or Claude in the sidebar for free wording.'}</p>"
         if plan_date else "")
    df = st.data_editor(pd.DataFrame(ss.notes_rows, columns=NOTE_COLS), num_rows="dynamic", hide_index=True, width="stretch", key=f"notes{ss.notes_ver}",
                        column_config={"Kind": st.column_config.SelectboxColumn(options=["Fixed batch", "New order"], required=True, default="New order"),
                                       "Note, in plain words": st.column_config.TextColumn(width="large"),
                                       "Could start as early as": st.column_config.NumberColumn(min_value=0, max_value=23, step=1,
                                                                                               help="Optional. If there is no valid plan, the manager could start this order this early."),
                                       "Could finish as late as": st.column_config.NumberColumn(min_value=1, max_value=24, step=1)})
    ss.notes_rows = df.to_dict("records")
    if st.button("Add the example grinding order", help="A 400 kW order that does not fit the usual schedule. Shows what happens when rules conflict."):
        ss.notes_rows = ss.notes_rows + [dict(EXAMPLE_ORDER)]
        ss.notes_ver += 1
        st.rerun()
    cfg = ss.cfg
    st.markdown("**Machines that can move**")
    html("<div class='rh-note' style='margin-bottom:6px'>Each machine runs once a day for its hours, anywhere inside its window. <b>Usual start</b> is when it runs today; "
         "the saving is measured against that. The last two columns are looser windows you would allow if the agent asks.</div>")
    v = ss.cfg_version
    mach = st.data_editor(pd.DataFrame(CFG.machines_to_rows(cfg["machines"]["shiftable"]), columns=CFG.MACHINE_COLS), num_rows="dynamic", hide_index=True,
                          width="stretch", key=f"mach{v}",
                          column_config={"kW": st.column_config.NumberColumn(min_value=1, max_value=5000),
                                         "Hours": st.column_config.NumberColumn(min_value=0.5, max_value=24, step=0.5),
                                         "Earliest start": st.column_config.NumberColumn(min_value=0, max_value=23, step=1, help="Hour of day, 0 to 23"),
                                         "Latest finish": st.column_config.NumberColumn(min_value=1, max_value=24, step=1),
                                         "Usual start": st.column_config.NumberColumn(min_value=0, max_value=23, step=1),
                                         "Could start as early as": st.column_config.NumberColumn(min_value=0, max_value=23, step=1),
                                         "Could finish as late as": st.column_config.NumberColumn(min_value=1, max_value=24, step=1)})
    machines, m_errs = CFG.rows_to_machines(mach.to_dict("records"))
    st.markdown("**Crew rules: machines that cannot run together**")
    names = [m["name"] for m in machines]
    rdf = st.data_editor(pd.DataFrame(CFG.rules_to_rows(cfg["machines"]["rules"], cfg["machines"]["shiftable"]), columns=CFG.RULE_COLS), num_rows="dynamic",
                         hide_index=True, width="stretch", key=f"rules{v}",
                         column_config={"Machine A": st.column_config.SelectboxColumn(options=names), "Machine B": st.column_config.SelectboxColumn(options=names),
                                        "Can be relaxed?": st.column_config.CheckboxColumn(help="If ticked, the agent may offer to break this rule when there is no valid plan. The contract-demand rule can never be relaxed.")})
    rules, r_errs = CFG.rows_to_rules(rdf.to_dict("records"), machines)
    for e_ in m_errs + r_errs:
        html(f"<div class='rh-note' style='color:var(--red)'>{ICONS['alert']} {e_}</div>")
    if st.button("Save machines and rules", type="primary", disabled=bool(m_errs + r_errs)):
        save_cfg(shiftable=machines, rules=rules)


# ---- power bill: price bands and the site's contract
def pane_bill(rows) -> None:
    cfg, v = ss.cfg, ss.cfg_version
    site = dict(cfg["site"])
    a, b, c = st.columns(3)
    site["contract_demand_kva"] = a.number_input("Contract demand (kVA)", 10, 100000, int(site["contract_demand_kva"]), 10, key=f"cd{v}",
                                                 help="The most the plant may draw under its supply contract. A plan can never go above it.")
    site["month_to_date_max_kva"] = b.number_input("This month's max demand so far (kVA)", 0, 100000, int(site["month_to_date_max_kva"]), 10, key=f"mtd{v}",
                                                   help="From the latest bill or the utility portal. A plan that goes higher pays the demand charge on the extra kVA.")
    site["working_days_per_year"] = c.number_input("Working days a year", 1, 366, int(site["working_days_per_year"]), 1, key=f"wd{v}",
                                                   help="Used only to turn a daily saving into a yearly figure.")
    site["name"] = st.text_input("Plant name", site["name"], key=f"nm{v}")
    st.markdown("**Price bands**")
    slots = st.data_editor(pd.DataFrame(CFG.tariff_to_rows(cfg["tariff"]), columns=CFG.SLOT_COLS), num_rows="dynamic", hide_index=True, width="stretch", key=f"slots{v}",
                           column_config={"From hour": st.column_config.NumberColumn(min_value=0, max_value=23, step=1),
                                          "To hour": st.column_config.NumberColumn(min_value=1, max_value=24, step=1),
                                          "Band": st.column_config.SelectboxColumn(options=["normal", "solar", "peak"], required=True),
                                          "Rate (₹ per kVAh)": st.column_config.NumberColumn(min_value=0.01, format="%.2f")})
    dc = st.number_input("Demand charge (₹ per kVA per month)", 0.0, 100000.0, float(cfg["tariff"]["demand_charge_per_kva_month"]), 10.0, key=f"dc{v}")
    tariff, errs = CFG.rows_to_tariff(cfg["tariff"], slots.to_dict("records"), dc)
    for e_ in errs:
        html(f"<div class='rh-note' style='color:var(--red)'>{ICONS['alert']} {e_}</div>")
    for i in validate_tariff(tariff):
        if i.level != "error":
            html(f"<div class='rh-note'><span class='rh-chip warn'>check</span>{i.text}</div>")
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("Save bill settings", type="primary", disabled=bool(errs), width="stretch"):
        save_cfg(site=site, tariff=tariff)
    if c2.button("Back to demo defaults", width="stretch"):
        ss.cfg = CFG.reset()
        ss.tariff, ss.cfg_version = ss.cfg["tariff"], ss.cfg_version + 1
        st.rerun()


# ---- price orders: read a new tariff order
def pane_orders(rows) -> None:
    st.markdown("The Tariff agent drafts the price bands once on the large model, checks them, and waits for a person to sign off before any plan uses them.")
    sample = ("MERC order (sample text). 00:00 to 09:00 normal hours: Rs 8.79 per kVAh. 09:00 to 17:00 solar hours: "
              "Rs 7.03 per kVAh. 17:00 to 24:00 peak hours: Rs 10.55 per kVAh. Demand charge Rs 600 per kVA per month.")
    txt = st.text_area("Tariff order text", sample, height=110)
    if st.button("Read this order"):
        steps: list = []
        tctx = Context(SITE, ss.tariff, MACH, ss.hist, llm=LLM, audit=ss.audit)
        ss.draft_tariff = parse_tariff(tctx, txt, ss.library, steps)
        ss.draft_log = steps
    d_ = ss.draft_tariff
    if d_:
        tier = ss.draft_log[-1]["tier"]
        html(f"<div class='rh-card panel'><h4>{chip(tier)}Draft slots</h4>" + "".join(
            f"<p>{s['type']}: {s['start_h']:02d}:00 to {s['end_h']:02d}:00, ₹{s['rate']:.2f}</p>" for s in d_.get("slots", []))
             + "".join(f"<p class='rh-note'>{i['level']}: {i['text']}</p>" for i in d_.get("issues", [])) + "</div>")
        if not any(i["level"] == "error" for i in d_.get("issues", [])) and d_.get("demand_charge_per_kva_month"):
            if st.button("Sign off and use for the next plan", type="primary"):
                d2 = {k: x for k, x in d_.items() if k != "issues"}
                d2.update(status="signed off", source="Parsed from pasted order text and signed off in the app")
                ss.library[d_["fingerprint"]] = d2
                ss.tariff = d2
                ss.audit.log("library", "Plant manager", "tariff_sign_off", tier="human", mode="human",
                             reason="person confirmed the parsed tariff", detail={"fingerprint": d_["fingerprint"]})
                st.toast("Tariff added to the library. Generate a new plan to use it.")
        else:
            html("<div class='rh-note'>Fix the errors above before sign-off.</div>")


# ---- weather: solar
def pane_weather(rows) -> None:
    cfg, v = ss.cfg, ss.cfg_version
    site = dict(cfg["site"])
    a, b, c = st.columns(3)
    site["rooftop_pv_kwp"] = a.number_input("Rooftop solar (kWp)", 0, 100000, int(site["rooftop_pv_kwp"]), 10, key=f"pv{v}")
    site["latitude"] = b.number_input("Latitude", -90.0, 90.0, float(site["latitude"]), 0.01, key=f"lat{v}")
    site["longitude"] = c.number_input("Longitude", -180.0, 180.0, float(site["longitude"]), 0.01, key=f"lon{v}")
    if st.button("Save solar settings", type="primary"):
        save_cfg(site=site)
    html("<div class='rh-note'>Choose live forecast, clear sky or a cloudy day in the sidebar.</div>")
    if state.get("pv"):
        fig = go.Figure(go.Scatter(x=[k / SLOTS_PER_HOUR for k in range(N_SLOTS)], y=state["pv"]["kw"], fill="tozeroy", line=dict(color=AMBER, width=2), name="Solar kW"))
        fig.update_layout(height=200, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="#fff", plot_bgcolor="#fff", font=dict(family=FONT, size=12),
                          xaxis=dict(title="hour", range=[0, 24]), yaxis=dict(title="kW"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        html(f"<div class='rh-note'>Source: {state['pv']['source']}</div>")


# ---- data pipeline boxes
def pane_collect(rows) -> None:
    if need_run("The forecast comes from the run."):
        return
    fc = state["forecast"]
    html(f"<div class='rh-note'>Base load for <b>{fc['weekday']} {fc['date']}</b>: {fc['method']}. Learned correction x{fc['bias']:.3f}.</div>")
    if st.button("Reset learned correction"):
        ss.bias = 1.0
        st.rerun()
    step_rows({"forecast_load", "solar_forecast"}, rows)


def pane_check(rows) -> None:
    if need_run("The quality checks come from the run."):
        return
    for i in state.get("data_issues", []):
        html(f"<div class='rh-note'><span class='rh-chip {'ok' if i['level'] == 'ok' else 'warn'}'>{i['level']}</span>{i['text']}</div>")
    step_rows({"validate_data"}, rows)


def pane_store(rows) -> None:
    df = pd.DataFrame(ss.audit.rows())
    html(f"<div class='rh-note'>Audit store: <span class='mono'>righthour_audit.sqlite</span>, {len(df)} rows across all runs. Every step of every run is kept here.</div>")
    if len(df):
        st.download_button("Download the audit trail (CSV)", df.to_csv(index=False).encode(), "righthour_audit.csv", "text/csv")


# ---- agent team
def pane_orch(rows) -> None:
    if need_run("The orchestrator's steps come from the run."):
        return
    html(f"<div class='rh-note'>Goal: <b>{state.get('goal', '')}</b>. Day: <b>{state['forecast']['date']}</b>. Model: <b>{LLM.label}</b>. Change these in the sidebar.</div>")
    step_rows({"orchestrate", "replan", "explain"}, rows)


def pane_price(rows) -> None:
    t = ss.tariff
    html("<table class='rh-log'><thead><tr><th>Band</th><th>Hours</th><th class='r'>₹ per kVAh</th></tr></thead><tbody>" + "".join(
        f"<tr><td>{s['type']}</td><td>{s['start_h']:02d}:00 to {s['end_h']:02d}:00</td><td class='r'>{s['rate']:.2f}</td></tr>" for s in t["slots"])
         + f"</tbody></table><div class='rh-note' style='margin-top:6px'>Demand charge ₹{t['demand_charge_per_kva_month']} per kVA per month. Edit in the <b>Power bill</b> box.</div>")
    step_rows({"tariff_lookup", "tariff_parse"}, rows)


def pane_forecast(rows) -> None:
    if need_run("The forecast comes from the run."):
        return
    fc, pv = state["forecast"], state["pv"]
    x = [k / SLOTS_PER_HOUR for k in range(N_SLOTS)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=fc["kw"], name="Base load kW", line=dict(color=NAVY, width=2.4)))
    fig.add_trace(go.Scatter(x=x, y=pv["kw"], name="Solar kW", line=dict(color=AMBER, width=2), fill="tozeroy", fillcolor="rgba(217,162,27,.12)"))
    fig.update_layout(height=260, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="#fff", plot_bgcolor="#fff", font=dict(family=FONT, size=12),
                      xaxis=dict(title="hour", range=[0, 24]), yaxis=dict(title="kW"), legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    html(f"<div class='rh-note'>Base load: {fc['method']}. Solar: {pv['source']}. Learned correction x{ss.bias:.3f}.</div>")


def pane_rules(rows) -> None:
    if need_run("The rules read from your notes come from the run."):
        return
    html(read_back(state))
    step_rows({"constraint_parse"}, rows)
    html("<div class='rh-note'>Edit the notes in the <b>Factory systems</b> box.</div>")


# ---- optimiser, guardrails
def pane_opt(rows) -> None:
    if need_run("The schedule comes from the run."):
        return
    plan, prob = state["plan"], state["problem"]
    if plan.get("status") != "optimal":
        html(f"<div class='rh-card alert'><h4>{ICONS['alert']}No valid schedule</h4><p>{plan.get('reason', '')}</p></div>")
    else:
        sched = plan["schedule"]
        def usual(m):
            b = m.get("baseline_start_h")
            return "" if b is None else "%02d:00" % int(b)
        body = "".join(f"<tr><td>{m['name']}</td><td class='r'>{m['kw']:.0f}</td><td>{m['earliest_h']:02d}:00 to {int(m['latest_finish_h']):02d}:00</td>"
                       f"<td>{usual(m)}</td><td><b>{sched[m['id']]:02d}:00</b></td></tr>" for m in prob["shiftable"])
        html(f"<table class='rh-log'><thead><tr><th>Machine</th><th class='r'>kW</th><th>Allowed window</th><th>Usual start</th><th>Planned start</th></tr></thead><tbody>{body}</tbody></table>"
             f"<div class='rh-note' style='margin-top:6px'>{plan.get('solver', '')}. Cost for the day {rs(plan['total_rs'])}, peak {plan['peak_kva']:.0f} kVA. "
             "The solver decides the schedule; no language model is involved.</div>")
    step_rows({"optimise"}, rows)


def pane_guard(rows) -> None:
    if need_run("The rule checks come from the run."):
        return
    checks = state.get("policy", {}).get("checks", [])
    html("".join(f"<p style='margin:4px 0'><span class='rh-chip {'ok' if c['ok'] else ('bad' if c['hard'] else 'warn')}'>{'pass' if c['ok'] else ('fail' if c['hard'] else 'note')}</span>{c['rule']}</p>" for c in checks)
         or "<div class='rh-note'>No plan to check yet.</div>")
    html(f"<div class='rh-note' style='margin-top:8px'>Rules that can never be relaxed: " + "; ".join(r["text"] for r in MACH["rules"] if r["hard"]) + ".</div>")
    step_rows({"policy_check"}, rows)


# ---- the manager
def pane_manager(rows) -> None:
    if need_run("Approval needs a plan."):
        return
    if pending and pending["type"] == "choose_relaxation":
        problem_panel("pane")
    elif pending and pending["type"] == "approval":
        approval_controls("pane")
    else:
        html(f"<div class='rh-note'>Status: <b>{state.get('status', '')}</b>. Nothing is waiting for you.</div>")
    step_rows({"relax_rule", "work_order", "reject"}, rows)


def pane_runs(rows) -> None:
    if not ss.approved:
        html("<div class='rh-note'>No approved plan yet. Approve one in the <b>Manager decides</b> box.</div>")
        return
    ast = ss.approved[1]
    html(f"<div class='rh-note'>Work order for <b>{ast['forecast']['date']}</b>. RightHour never switches equipment; people do.</div>")
    st.dataframe(pd.read_csv(io.BytesIO(work_order_csv(ast))), hide_index=True, width="stretch")
    st.download_button("Download the work order (CSV)", work_order_csv(ast), f"work_order_{ast['forecast']['date']}.csv", "text/csv", key="wo_pane")


def pane_loop_no(rows) -> None:
    rej = [r for r in ss.audit.rows() if r["action"] == "reject"]
    html(f"<div class='rh-note'>{len(rej)} plan(s) rejected so far. A rejection is recorded with its reason. <b>Turning a rejection into a new rule is not built yet</b>: today a person adds the rule in the Factory systems box.</div>")
    if rej:
        html("<table class='rh-log'><thead><tr><th>Time</th><th>Reason</th></tr></thead><tbody>" + "".join(f"<tr><td class='m'>{r['ts'][:16]}</td><td>{r['reason']}</td></tr>" for r in rej) + "</tbody></table>")


def pane_loop_day(rows) -> None:
    if not ss.approved:
        html(f"<div class='rh-card'><h4>{ICONS['clock']}Nothing to verify yet</h4><p>Approve a plan first. After the day, RightHour compares the meter data with the plan and reports the real saving.</p></div>")
        return
    actx, ast = ss.approved
    pdate = ast["forecast"]["date"]
    real_day = D.day_actuals(ss.hist, pdate) if ss.hist is not None else None
    html(f"<p>Approved plan for {ast['forecast']['weekday']} {pdate}. Projected saving <b>{rs(ast.get('saving_rs') or 0)}</b>.</p>")
    c1, c2 = st.columns([2, 3])
    with c1:
        sources = (["Real readings from the loaded meter file"] if real_day is not None else []) + ["Upload next-day meter CSV", "Simulated day (demo only)"]
        src = st.radio("Where the day's meter readings come from", sources, key="vsrc")
        vfile = st.file_uploader("Next-day meter CSV (same format)", type=["csv"], key="vfile") if src.startswith("Upload") else None
        st.markdown("**What actually ran on the floor**")
        executed = {}
        for m in ast["problem"]["shiftable"]:
            opts = list(range(0, 24 - int(m["duration_h"]) + 1))
            planned = ast["final_schedule"][m["id"]]
            executed[m["id"]] = st.selectbox(f"{m['name']} (planned {planned:02d}:00)", opts, index=opts.index(planned),
                                             format_func=lambda h: f"{h:02d}:00", key=f"ex_{actx.run_id}_{m['id']}")
        if st.button("Run next-day check", type="primary"):
            try:
                if src.startswith("Real"):
                    actual = real_day
                elif src.startswith("Upload"):
                    if vfile is None:
                        raise ValueError("Choose a CSV file first.")
                    actual = D.load_uci_csv(vfile, ss.hist.attrs.get("scale") if ss.hist is not None else None)
                    actual = D.day_actuals(actual, pdate) or actual.tail(N_SLOTS)
                else:
                    actual = D.synthetic_actual_day(pdate)
                ss.verify = verify_and_learn(actx, ast, executed, actual)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not run the check: {e}")
    with c2:
        v = ss.verify
        if v:
            real = v["realisation"]
            html("<div class='rh-stats'>"
                 f"<div class='rh-stat'><div class='v'>{rs(v['projected_saving_rs'])}</div><div class='l'>Expected saving</div></div>"
                 f"<div class='rh-stat'><div class='v green'>{rs(v['verified_saving_rs'])}</div><div class='l'>Verified saving, meter vs adjusted baseline</div></div>"
                 f"<div class='rh-stat'><div class='v'>{(real or 0) * 100:.0f}%</div><div class='l'>Of the expected saving realised</div></div></div>")
            name = {m["id"]: m["name"] for m in ast["problem"]["shiftable"]}
            dev = "; ".join(f"{name[k]} planned {a:02d}:00, ran {b:02d}:00" for k, (a, b) in v["deviations"].items()) or "none"
            html(f"<div class='rh-card panel'><h4>{ICONS['chip']}What the agent learned</h4>"
                 f"<p>Base load came in {v['forecast_error_pct']:+.1f}% against the forecast, so the forecast correction moves from x{v['old_bias']:.3f} to x{v['new_bias']:.3f}.</p>"
                 f"<p>Deviations from the plan: {dev}. Saving realised: {(real or 0) * 100:.0f}%, which feeds the next projection and the shared-savings report.</p></div>")
            if st.button("Use this learning for the next plan"):
                ss.bias = v["new_bias"]
                st.toast(f"Forecast correction set to x{ss.bias:.3f}")


PANES = {"meters": pane_meters, "factory": pane_factory, "bill": pane_bill, "orders": pane_orders, "weather": pane_weather,
         "collect": pane_collect, "check": pane_check, "store": pane_store, "orch": pane_orch, "price": pane_price,
         "forecast": pane_forecast, "rules": pane_rules, "opt": pane_opt, "guard": pane_guard, "manager": pane_manager,
         "runs": pane_runs, "loop_no": pane_loop_no, "loop_day": pane_loop_day}


def control_room() -> None:
    rows = ss.audit.rows(ctx.run_id) if ctx else []
    astates = ARCH.element_states(rows, pending, ss.verify is not None, ss.hist is not None)
    st.button("← Back to the Manager console", type="tertiary", key="hld_back", on_click=goto, args=("Manager console",))
    html("<p style='max-width:72ch;margin:12px 0 8px'><b>Click any box.</b> A pane opens below the diagram: where that box gets its information, "
         "what it did in this run, and the controls that change it. Green dot: ran this run. Red dot: waiting for you.</p>")
    picked = ARCH.component(astates, ss.sel_box, key=f"hld_{ss.get('hld_room_nonce', 0)}")
    if picked and picked != ss.sel_box:
        ss.sel_box = picked
        st.rerun()
    eid = ss.sel_box if ss.sel_box in PANES else "orch"
    provenance(eid, rows)
    PANES[eid](rows)



# ------------------------------------------------------------------ plan helpers
def read_back(state: dict) -> str:
    """Show how the agent understood each note, so a wrong reading is caught before approval."""
    rows = []
    for f in state.get("fixed", []):
        rows.append(f"<tr><td>{f['name']}</td><td>Fixed batch</td><td>{f['start_h']:02d}:00 to {f['end_h']:02d}:00</td><td class='r'>{f['kw']:.0f} kW</td></tr>")
    ex = state.get("extra") or []
    for e in (ex if isinstance(ex, list) else [ex]):
        rows.append(f"<tr><td>{e['name']}</td><td>New order</td><td>{e['duration_h']:g} h inside {int(e['earliest_h']):02d}:00 to {int(e['latest_finish_h']):02d}:00</td><td class='r'>{e['kw']:.0f} kW</td></tr>")
    if not rows:
        return ""
    return (f"<div class='rh-card'><h4>{ICONS['person']}How the agent read your notes</h4><table class='rh-log'><thead><tr><th>Name</th><th>Kind</th><th>Hours</th>"
            f"<th class='r'>Load</th></tr></thead><tbody>{''.join(rows)}</tbody></table><p class='rh-note' style='margin-top:6px'>Wrong? Fix the note in the Control room (Factory systems) and generate again.</p></div>")


def work_order_csv(st_: dict) -> bytes:
    sched = st_["final_schedule"]
    rows = [{"machine": f["name"], "kw": f["kw"], "start": f"{f['start_h']:02d}:00", "end": f"{f['end_h']:02d}:00", "note": "fixed batch, untouched"}
            for f in st_["fixed"]]
    for m in st_["problem"]["shiftable"]:
        s = sched[m["id"]]
        b = m.get("baseline_start_h")
        rows.append({"machine": m["name"], "kw": m["kw"], "start": f"{s:02d}:00", "end": f"{int(s + m['duration_h']):02d}:00",
                     "note": "new order" if b is None else ("as usual" if b == s else "moved")})
    return pd.DataFrame(rows).to_csv(index=False).encode()


def approval_controls(scope: str) -> None:
    """Approve, reject or edit the plan. Rendered in the Plan tab and in the Manager pane, so each has its own widget keys."""
    reason = st.text_input("Reason, if you reject", placeholder="Recorded in the audit trail", key=f"reason_{scope}")
    b1, b2 = st.columns(2)
    if b1.button("Approve plan", type="primary", width="stretch", key=f"approve_{scope}"):
        do_resume({"action": "approve"})
        st.rerun()
    if b2.button("Reject", width="stretch", key=f"reject_{scope}"):
        do_resume({"action": "reject", "reason": reason or "no reason given"})
        st.rerun()
    with st.expander("Edit the plan before approving"):
        html("<div class='rh-note'>Change any start time. The governance layer checks the edit before it can be approved.</div>")
        edited = {}
        for m in state["problem"]["shiftable"]:
            opts = list(range(0, 24 - int(m["duration_h"]) + 1))
            edited[m["id"]] = st.selectbox(f"{m['name']} ({m['kw']:.0f} kW, {m['duration_h']:g} h)", opts,
                                           index=opts.index(state["plan"]["schedule"][m["id"]]),
                                           format_func=lambda h: f"{h:02d}:00", key=f"edit_{scope}_{ctx.run_id}_{m['id']}")
        if st.button("Check and approve edited plan", width="stretch", key=f"editgo_{scope}"):
            do_resume({"action": "edit", "schedule": edited})
            st.rerun()


def problem_panel(scope: str) -> None:
    opts = pending["options"]
    hard = [r["text"] for r in MACH["rules"] if r["hard"]] + [f"{f['name']} cannot stop mid-batch" for f in state["fixed"]]
    ex = state.get("extra") or []
    ex = ex if isinstance(ex, list) else [ex]
    news = ", ".join(f"{e['name']} ({e['kw']:.0f} kW for {e['duration_h']:g} h inside {int(e['earliest_h']):02d}:00 to {int(e['latest_finish_h']):02d}:00)" for e in ex)
    html(f"""<div class='rh-card alert'><h4>{ICONS['alert']}No valid schedule for {state['forecast']['date']}</h4>
<p>{pending['diagnosis']}{(' New work this day: ' + news + '.') if news else ''}</p>
<p><b>Rules the agent will not relax:</b> {'; '.join(hard)}.</p>
<p>It tested every flexible rule. Choose one to relax and it will re-plan.</p></div>""")
    rec_chip = " <span class='rh-chip ok'>recommended</span>"
    ok_chip, bad_chip = "<span class='rh-chip ok'>valid plan</span>", "<span class='rh-chip bad'>still no plan</span>"
    rows = ""
    for o in opts:
        cost = rs(o["cost_rs"]) if o["feasible"] else ""
        sav = (rs(o["saving_rs"]) if o.get("saving_rs") is not None else "n/a") if o["feasible"] else ""
        rows += (f"<tr><td>{o['text']}{rec_chip if o['id'] == pending['recommended'] else ''}</td>"
                 f"<td>{ok_chip if o['feasible'] else bad_chip}</td><td class='r'>{cost}</td>"
                 f"<td class='r'>{sav}</td><td>{o['side_cost_note']}</td></tr>")
    html(f"<table class='rh-log'><tr><th>Flexible rule</th><th>Result</th><th>Cost for the day</th>"
         f"<th>Saving vs usual</th><th>Side cost</th></tr>{rows}</table>")
    feas = [o for o in opts if o["feasible"]]
    if not feas:
        html("<div class='rh-note'>No flexible rule makes a valid plan. Add a looser window in the Control room, or change the order.</div>")
        return
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    choice = c1.radio("Relax which rule?", [o["id"] for o in feas], index=[o["id"] for o in feas].index(pending["recommended"]),
                      format_func=lambda i: next(o["text"] for o in feas if o["id"] == i), horizontal=True, key=f"relax_{scope}")
    if c2.button("Relax this rule and re-plan", type="primary", width="stretch", key=f"relaxgo_{scope}"):
        do_resume(choice)
        st.rerun()


def plan_tab_body() -> None:
    bill(state)
    left, right = st.columns([3, 2], gap="large")
    with left:
        html(f"<div class='rh-key'><span><i style='background:{NAVY_60}'></i>normal</span>"
             f"<span><i style='background:{AMBER}'></i>solar hours</span><span><i style='background:{RED}'></i>peak</span>"
             f"<span>Outlined bars below show the usual time.</span></div>")
        st.plotly_chart(plan_figure(state), width="stretch", config={"displayModeBar": False})
    with right:
        status = state.get("status")
        if status == "edit_blocked":
            er = state["edit_result"]
            html(f"<div class='rh-card alert'><h4>{ICONS['alert']}Edited plan blocked by the policy check</h4>"
                 + "".join(f"<p>Fails: {f}</p>" for f in er["failed"]) + "<p>The original plan is still waiting for approval.</p></div>")
        if status == "work_order_issued":
            html(f"<div class='rh-card approve'><h4>{ICONS['check']}Approved. Work order issued.</h4><p>Operators run the machines at the "
                 f"approved times; RightHour never switches equipment. After the day, use the <b>Next day</b> loop in the Control room.</p></div>")
            st.download_button("Download the work order (CSV)", work_order_csv(state), f"work_order_{state['forecast']['date']}.csv", "text/csv", width="stretch", key="wo_plan")
        elif status == "rejected":
            html(f"<div class='rh-card'><h4>Plan rejected</h4><p>Current schedule kept. Reason on record: {state.get('approval', {}).get('reason') or 'none given'}.</p></div>")
        html(f"<div class='rh-card panel'><h4>{ICONS['chip']}Why this plan</h4><p>{state.get('explanation', '')}</p></div>")
        checks = state.get("policy", {}).get("checks", [])
        html(f"<div class='rh-card'><h4>{ICONS['shield']}Rules checked</h4>" + "".join(
            f"<p><span class='rh-chip {'ok' if c['ok'] else ('bad' if c['hard'] else 'warn')}'>"
            f"{'pass' if c['ok'] else ('fail' if c['hard'] else 'note')}</span>{c['rule']}</p>" for c in checks) + "</div>")
        if pending and pending["type"] == "approval":
            approval_controls("plan")
    html(read_back(state))


def plan_tab() -> None:
    if run and run.get("cfg_version") != ss.cfg_version:
        html(f"<div class='rh-card alert'><h4>{ICONS['alert']}Settings changed since this plan</h4><p>Press <b>Generate plan</b> to plan with the new settings.</p></div>")
    if run_error:
        html(f"<div class='rh-card alert'><h4>{ICONS['alert']}{run_error}</h4><p>Check the notes (Control room, Factory systems), the day to plan and the settings, then try again.</p></div>")
    if ss.hist is None:
        html(f"""<div class='rh-empty'><h2>Load a plant's meter data to start</h2>
<p>Open the <b>Control room</b> tab, click the <b>Smart meters</b> box, and choose the steel data, the sample, or your own CSV.</p></div>""")
        return
    if not run:
        if not run_error:
            html(f"<div class='rh-card panel'><h4>{ICONS['check']}Data loaded</h4><p>Choose <b>Generate plan</b> in the sidebar.</p></div>")
        return
    warns = [w["text"] for w in state.get("tariff_issues", []) if w["level"] == "warning"]
    if warns:
        html(f"<div class='rh-note' style='margin-bottom:8px'><span class='rh-chip warn'>tariff check</span>{len(warns)} warnings on the active tariff. First: {warns[0]}</div>")
    html(f"<div class='rh-note' style='margin:2px 0 10px'>Plan for <b>{state['forecast']['weekday']} {state['forecast']['date']}</b>. "
         f"Base load: {state['forecast']['method']}. Solar: {state['pv']['source']}.</div>")
    if pending and pending["type"] == "choose_relaxation":
        problem_panel("plan")
    elif state.get("status") == "fallback":
        html(f"<div class='rh-card alert'><h4>{ICONS['alert']}Current schedule kept</h4><p>{state.get('note', '')}</p></div>")
        html(read_back(state))
    else:
        plan_tab_body()


def log_tab() -> None:
    if not ctx:
        html("<div class='rh-note' style='margin:14px 0'>Generate a plan first. This tab fills in from the run.</div>")
        return
    rows = ss.audit.rows(ctx.run_id)
    n = {t: sum(1 for r in rows if r["tier"] == t) for t in ("T0", "T1", "T2", "human")}
    total = max(len(rows), 1)
    spend = sum(r["cost_usd"] for r in rows)
    mix = "".join(f"<span class='{c}' style='width:{n[t] / total * 100:.1f}%'></span>"
                  for t, c in (("T0", "m0"), ("T1", "m1"), ("T2", "m2"), ("human", "mh")))
    html(f"<div class='rh-panel' style='margin:14px 0'>"
         f"<div class='cap'>{len(rows)} steps in this run</div>"
         f"<div class='rh-mix' role='img' aria-label='Steps by tier'>{mix}</div>"
         f"<div class='rh-key' style='margin:6px 0 10px'>"
         f"<span><span class='rh-chip t0'>T0</span>{n['T0']} code, no model</span>"
         f"<span><span class='rh-chip t1'>T1</span>{n['T1']} small model</span>"
         f"<span><span class='rh-chip t2'>T2</span>{n['T2']} large model</span>"
         f"<span><span class='rh-chip human'>human</span>{n['human']} a person decided</span></div>"
         f"<div class='rh-note'>Model spend for this run: <b class='num' style='color:{INK}'>${spend:.4f}</b> "
         f"({'measured' if LLM.live else 'estimated from typical token use'}; "
         + ", ".join(f"<span class='mono'>{m}</span> ${PRICES[m][0]} / ${PRICES[m][1]}" for m in MODELS[LLM.provider].values())
         + " per million tokens)</div></div>")
    body = "".join(
        f"<tr><td class='m'>{r['ts'][11:]}</td><td>{chip(r['tier'])}</td><td>{r['agent']}</td><td><b>{r['action']}</b></td>"
        f"<td>{r['reason']}</td><td>{r['mode']}</td><td class='r'>{'$%.4f' % r['cost_usd'] if r['cost_usd'] else ''}</td></tr>"
        for r in rows)
    html(f"<table class='rh-log'><thead><tr><th>Time</th><th>Tier</th><th>Agent</th><th>Step</th><th>Why this tier</th>"
         f"<th>Mode</th><th class='r'>Cost</th></tr></thead><tbody>{body}</tbody></table>")
    with st.expander("Step details (JSON)"):
        for r in rows:
            st.markdown(f"**{r['agent']}: {r['action']}**")
            st.code(json.dumps(json.loads(r["detail"]), indent=1, default=str)[:3000], language="json")



def audit_tab() -> None:
    rows = ss.audit.rows()
    df = pd.DataFrame(rows)
    st.dataframe(df[["ts", "run_id", "agent", "action", "tier", "model", "mode", "reason", "cost_usd"]] if len(df) else df,
                 width='stretch', hide_index=True, height=420)
    if len(df):
        st.download_button("Download audit trail (CSV)", df.to_csv(index=False).encode(), "righthour_audit.csv", "text/csv")


# ------------------------------------------------------------------ the Manager console: every input, all telemetry, every output
# One screen in three columns that follow the design diagram from left to right: what goes in, what the agents are doing,
# what comes out. Every panel carries a link to its box on the clickable diagram (Control room), and the small diagram at
# the top opens any box directly.
STATE_WORD = {"ran": "ran this run", "wait": "not run yet", "ask": "needs you", "part": "logged only"}


def _box_state(eid: str, astates: dict) -> str:
    return astates.get(eid, "wait")


SHORT = {"meters": "Smart meters", "factory": "Factory systems", "bill": "Power bill", "orders": "Price orders", "weather": "Weather",
         "collect": "1 Collect", "check": "2 Check", "store": "3 Store", "orch": "Orchestrator", "price": "Price", "forecast": "Forecast",
         "rules": "Rules", "opt": "Optimiser", "guard": "Guardrails", "manager": "Manager decides", "runs": "Plan runs",
         "loop_no": "Every no", "loop_day": "Next-day loop"}


def card_head(title: str, box: str, astates: dict, sub: str = "", key: str | None = None) -> None:
    """Panel header: status dot from the run log, title, and a link to the same box on the design diagram."""
    s = _box_state(box, astates)
    c1, c2 = st.columns([1.45, 1], vertical_alignment="center")
    c1.markdown(f"<div class='rh-ch'><span class='rh-sd {s}' title='{STATE_WORD[s]}'></span>{title}</div>", unsafe_allow_html=True)
    c2.button(f"{SHORT[box]} ↗", key=key or f"lk_{box}_{title}", type="tertiary", on_click=goto, width="stretch",
              args=("Control room", box), help=f"Open “{ARCH.info(box)['title']}” on the design diagram: where its data comes from, "
                                               f"what it did in this run ({STATE_WORD[s]}), and its full settings.")


def section(n: str, title: str, desc: str) -> None:
    html(f"<div class='rh-sec'><span class='n'>{n}</span><span class='t'>{title}</span><span class='d'>{desc}</span></div>")


def _mini_fig(height: int = 190) -> go.Figure:
    fig = go.Figure()
    for s in ss.tariff["slots"]:
        fig.add_shape(type="rect", xref="x", yref="paper", x0=s["start_h"], x1=s["end_h"], y0=0, y1=1,
                      fillcolor=BAND[s["type"]], opacity=0.08, line_width=0, layer="below")
    fig.update_layout(height=height, margin=dict(l=6, r=6, t=6, b=6), paper_bgcolor="#fff", plot_bgcolor="#fff",
                      font=dict(family=FONT, size=11, color=INK), hoverlabel=HOVER, showlegend=True,
                      legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=11)),
                      xaxis=dict(range=[0, 24], tickvals=list(range(0, 25, 6)), ticktext=[f"{h:02d}:00" for h in range(0, 25, 6)],
                                 showgrid=False, tickfont=dict(size=11, color=MUTED)),
                      yaxis=dict(gridcolor="#EEF1F6", zeroline=False, rangemode="tozero", tickfont=dict(size=11, color=MUTED)))
    return fig


def _run_rows() -> list[dict]:
    return ss.audit.rows(ctx.run_id) if ctx else []


# ---- column 1: inputs
def c_meters(astates) -> None:
    with st.container(border=True):
        card_head("Meter data", "meters", astates, "what goes in")
        h = ss.hist
        if h is not None:
            f = LIVE.describe_feed(h)
            html(f"<div class='rh-kv three'><div><b>{f['n_rows']:,}</b>readings</div><div><b>{f['n_full_days']}</b>full days</div>"
                 f"<div><b class='s'>x{h.attrs.get('scale', 1):.2f}</b>scale to site</div></div>"
                 f"<div class='rh-note'>{str(f['first_ts'])[:10]} to {str(f['last_ts'])[:16]}. {h.attrs.get('source', '')}</div>")
        src = st.radio("Source", ["Steel industry data (UCI)", "Synthetic sample", "Upload a CSV"], key="c_src", horizontal=True,
                       label_visibility="collapsed")
        auto = st.toggle("Auto-scale to the demo site", value=ss.scale_mode, key="c_scale",
                         help="The UCI plant is smaller than the demo site. Scaling keeps its real shape at the site's size.")
        up = st.file_uploader("Meter CSV in the UCI format", type=["csv"], key="c_up") if src.startswith("Upload") else None
        if st.button("Load this data", width="stretch", key="c_load",
                     disabled=(src.startswith("Steel") and not STEEL_FILE.exists()) or (src.startswith("Upload") and up is None)):
            try:
                ss.scale_mode = auto
                scale = None if auto else 3.0
                ss.hist = (D.load_uci_csv(STEEL_FILE, scale) if src.startswith("Steel") else
                           D.synthetic_history() if src.startswith("Synthetic") else D.load_uci_csv(up, scale))
                ss.w_live, ss.live_active, ss.live_future, ss.live_base = False, False, [], None
                reset_run()
                ss.autoran = False
                ss.audit.log("setup", "Plant manager", "data_loaded", tier="human", mode="human",
                             reason=f"meter data source set to: {src}", detail={"rows": len(ss.hist)})
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read that file: {e}")
        st.markdown("**Live feed**")
        live_controls()
        if ss.live_active:
            if st.button("Release the next day now", disabled=not ss.live_future, width="stretch", key="c_release"):
                ss.hist, ss.live_future, day = LIVE.release_next_day(ss.hist, ss.live_future)
                live_note(f"meter feed delivered {day}")
                check_approved_plan()
                ss.trigger_plan = True
                st.rerun()
            html(f"<div class='rh-note'>Or drop a CSV in <span class='mono'>{INCOMING.name}/</span>; it is picked up within seconds.</div>")


def c_day(astates) -> None:
    with st.container(border=True):
        card_head("The day, the goal, the weather", "orch", astates, "run the agents", key="lk_orch_day")
        run_controls("console")


def c_notes(astates) -> None:
    with st.container(border=True):
        card_head("Production: today's notes", "factory", astates)
        cols = ["Name", "Kind", "Note, in plain words"]
        df = st.data_editor(pd.DataFrame(ss.notes_rows, columns=NOTE_COLS)[cols], num_rows="dynamic", hide_index=True, width="stretch",
                            key=f"c_notes{ss.notes_ver}",
                            column_config={"Kind": st.column_config.SelectboxColumn(options=["Fixed batch", "New order"], required=True, default="New order"),
                                           "Note, in plain words": st.column_config.TextColumn(width="large")})
        old = {r.get("Name"): r for r in ss.notes_rows}
        ss.notes_rows = [{**{k: old.get(r.get("Name"), {}).get(k) for k in NOTE_COLS}, **r} for r in df.to_dict("records")]
        b1, b2 = st.columns(2)
        if b1.button("Add example order", width="stretch", key="c_example",
                     help="A 400 kW order that does not fit the usual schedule. Shows what happens when rules conflict."):
            ss.notes_rows = ss.notes_rows + [dict(EXAMPLE_ORDER)]
            ss.notes_ver += 1
            st.rerun()
        b2.button("Machines & rules ↗", width="stretch", key="c_mach", on_click=goto, args=("Control room", "factory"))
        m, r = MACH["shiftable"], [x for x in MACH["rules"] if x["type"] == "no_overlap"]
        html(f"<div class='rh-note'>{len(m)} machines can move ({sum(x['kw'] for x in m):,.0f} kW in all); {len(r)} crew rule(s). "
             f"The notes are read by the Rules tool ({LLM.label}).</div>")


def c_bill(astates) -> None:
    with st.container(border=True):
        card_head("Price bands and contract", "bill", astates)
        t = ss.tariff
        total = 24
        bars = "".join(f"<span style='width:{(s['end_h'] - s['start_h']) / total * 100:.2f}%;background:{BAND[s['type']]}' "
                       f"title='{s['type']} {s['start_h']:02d}:00 to {s['end_h']:02d}:00'>{s['type']} ₹{s['rate']:.2f}</span>" for s in t["slots"])
        html(f"<div class='rh-bands' role='img' aria-label='Price bands across the day'>{bars}</div>"
             f"<div class='rh-note'>00:00 to 24:00. Demand charge ₹{t['demand_charge_per_kva_month']:,.0f} per kVA per month. {t.get('status', '')}</div>")
        warns = [i.text for i in validate_tariff(t) if i.level == "warning"]
        if warns:
            with st.expander(f"{len(warns)} tariff check warning(s)"):
                for w in warns:
                    html(f"<div class='rh-note'><span class='rh-chip warn'>check</span>{w}</div>")
        site, v = dict(ss.cfg["site"]), ss.cfg_version
        a, b = st.columns(2)
        site["contract_demand_kva"] = a.number_input("Contract demand (kVA)", 10, 100000, int(site["contract_demand_kva"]), 10, key=f"c_cd{v}")
        site["month_to_date_max_kva"] = b.number_input("This month's max so far (kVA)", 0, 100000, int(site["month_to_date_max_kva"]), 10, key=f"c_mtd{v}")
        b1, b2 = st.columns(2)
        changed = (site["contract_demand_kva"], site["month_to_date_max_kva"]) != (ss.cfg["site"]["contract_demand_kva"], ss.cfg["site"]["month_to_date_max_kva"])
        if b1.button("Save contract", type="primary" if changed else "secondary", disabled=not changed, width="stretch", key="c_savecd"):
            save_cfg(site=site)
        b2.button("Bands & orders ↗", width="stretch", key="c_orders", on_click=goto, args=("Control room", "orders"))
        html(f"<div class='rh-note'>Tariff library: {len(ss.library)} signed-off order(s).</div>")


def c_solar(astates) -> None:
    with st.container(border=True):
        card_head("Weather and rooftop solar", "weather", astates)
        site = ss.cfg["site"]
        pv = state.get("pv") if run else None
        if pv:
            kwh = sum(pv["kw"]) / SLOTS_PER_HOUR
            html(f"<div class='rh-kv three'><div><b>{site['rooftop_pv_kwp']:,}</b>kWp on the roof</div><div><b>{max(pv['kw']):.0f}</b>peak kW tomorrow</div>"
                 f"<div><b>{kwh:,.0f}</b>kWh in the day</div></div><div class='rh-note'>Source: {pv['source']}. Change the forecast in "
                 f"<b>The day, the goal, the weather</b>.</div>")
        else:
            html(f"<div class='rh-note'>{site['rooftop_pv_kwp']} kWp at {site['latitude']}, {site['longitude']}. The forecast appears after a run.</div>")


def c_model(astates) -> None:
    with st.container(border=True):
        card_head("Language model", "rules", astates, "small and large steps", key="lk_rules_model")
        model_controls()


# ---- column 2: telemetry
def c_run(astates) -> None:
    rows = _run_rows()
    with st.container(border=True):
        card_head("This run", "store", astates, ctx.run_id if ctx else "", key="lk_store_run")
        if not rows:
            html("<div class='rh-note'>No run yet. Press <b>Generate plan</b>.</div>")
            return
        n = {t: sum(1 for r in rows if r["tier"] == t) for t in ("T0", "T1", "T2", "human")}
        spend = sum(r["cost_usd"] for r in rows)
        t0, t1 = pd.Timestamp(rows[0]["ts"]), pd.Timestamp(rows[-1]["ts"])
        total = max(len(rows), 1)
        mix = "".join(f"<span class='{c}' style='width:{n[t] / total * 100:.1f}%'></span>"
                      for t, c in (("T0", "m0"), ("T1", "m1"), ("T2", "m2"), ("human", "mh")))
        html(f"<div class='rh-kv three'><div><b>{len(rows)}</b>steps logged</div><div><b>{n['T1'] + n['T2']}</b>model calls, {n['T2']} large</div>"
             f"<div><b>${spend:.4f}</b>{'measured' if LLM.live else 'estimated'} spend</div></div>"
             f"<div class='rh-mix' role='img' aria-label='Steps by tier'>{mix}</div>"
             f"<div class='rh-key' style='margin:0 0 6px'><span><span class='rh-chip t0'>T0</span>{n['T0']}</span><span><span class='rh-chip t1'>T1</span>{n['T1']}</span>"
             f"<span><span class='rh-chip t2'>T2</span>{n['T2']}</span><span><span class='rh-chip human'>human</span>{n['human']}</span></div>"
             f"<div class='rh-note'>Started {t0:%H:%M:%S}, last step {t1:%H:%M:%S} ({(t1 - t0).total_seconds():.0f} s). Mode: {LLM.label}. "
             f"Audit store holds {len(ss.audit.rows())} rows across all runs.</div>")
        esc = [r for r in rows if r["tier"] == "T2"]
        if esc:
            html("<div class='rh-note' style='margin-top:4px'><b>Why the large model was used:</b> " + "; ".join(sorted({r["reason"] for r in esc})) + "</div>")


def c_health(astates) -> None:
    with st.container(border=True):
        card_head("Data health", "check", astates)
        issues = state.get("data_issues") if run else (D.validate_load(ss.hist) if ss.hist is not None else [])
        for i in issues or []:
            html(f"<div class='rh-note' style='margin:2px 0'><span class='rh-chip {'ok' if i['level'] == 'ok' else ('bad' if i['level'] == 'error' else 'warn')}'>{i['level']}</span>{i['text']}</div>")


def c_forecast(astates) -> None:
    with st.container(border=True):
        card_head("Forecast for the plan day", "forecast", astates)
        if not run:
            html("<div class='rh-note'>Appears after a run.</div>")
            return
        fc, pv = state["forecast"], state["pv"]
        x = [k / SLOTS_PER_HOUR for k in range(N_SLOTS)]
        fig = _mini_fig(180)
        fig.add_trace(go.Scatter(x=x, y=fc["kw"], name="Base load kW", line=dict(color=NAVY, width=2)))
        fig.add_trace(go.Scatter(x=x, y=pv["kw"], name="Solar kW", line=dict(color=AMBER_TEXT, width=1.6), fill="tozeroy", fillcolor="rgba(217,162,27,.14)"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="c_fc_chart")
        html(f"<div class='rh-note'>{fc['weekday']} {fc['date']}: {fc['method']}. Learned correction x{fc['bias']:.3f}.</div>")
        if ss.bias != 1.0 and st.button("Reset learned correction", key="c_resetbias", type="tertiary"):
            ss.bias = 1.0
            st.rerun()


def c_opt(astates) -> None:
    with st.container(border=True):
        card_head("Optimiser", "opt", astates, "OR-Tools MILP")
        if not run:
            html("<div class='rh-note'>Appears after a run.</div>")
            return
        plan, prob = state["plan"], state["problem"]
        if plan.get("status") != "optimal":
            html(f"<div class='rh-note' style='color:var(--red)'>{ICONS['alert']} No valid schedule: {plan.get('reason', '')}</div>")
            return
        sched = plan["schedule"]
        moved = [m for m in prob["shiftable"] if m.get("baseline_start_h") is not None and m["baseline_start_h"] != sched[m["id"]]]
        body = "".join(f"<tr><td>{m['name']}</td><td class='r'>{m['kw']:.0f}</td><td class='m'>"
                       f"{'new' if m.get('baseline_start_h') is None else '%02d:00' % m['baseline_start_h']}</td>"
                       f"<td class='m'><b style='color:{NAVY}'>{sched[m['id']]:02d}:00</b></td></tr>" for m in prob["shiftable"])
        html(f"<div class='rh-kv three'><div><b>{len(moved)}/{len(prob['shiftable'])}</b>machines moved</div>"
             f"<div><b>{rs(plan['total_rs'])}</b>plan cost</div><div><b>{plan['peak_kva']:.0f}</b>peak kVA</div></div>"
             f"<table class='rh-tiny'><thead><tr><th>Machine</th><th class='r'>kW</th><th>Usual</th><th>Planned</th></tr></thead><tbody>{body}</tbody></table>")


def c_guard(astates) -> None:
    with st.container(border=True):
        card_head("Guardrails", "guard", astates, "hard rules")
        checks = state.get("policy", {}).get("checks", []) if run else []
        if not checks:
            html("<div class='rh-note'>No plan to check yet.</div>")
            return
        ok = sum(c["ok"] for c in checks)
        bad = [c for c in checks if not c["ok"]]
        html(f"<div class='rh-kv'><div><b class='g'>{ok}</b>checks passed</div><div><b class='{'r' if any(c['hard'] for c in bad) else ''}'>{len(bad)}</b>failed or noted</div></div>")
        for c in bad:
            html(f"<div class='rh-note'><span class='rh-chip {'bad' if c['hard'] else 'warn'}'>{'fail' if c['hard'] else 'note'}</span>{c['rule']}</div>")
        if state.get("status") == "edit_blocked":
            html("<div class='rh-note' style='color:var(--red)'>Your edited plan was blocked: " + "; ".join(state["edit_result"]["failed"]) + "</div>")


def c_stream(astates) -> None:
    rows = _run_rows()
    with st.container(border=True):
        card_head("Agent steps, newest first", "orch", astates, key="lk_orch_stream")
        if not rows:
            html("<div class='rh-note'>Steps appear here as the agents work.</div>")
            return
        body = "".join(f"<tr title='{escape(r['reason'])}'><td class='m'>{r['ts'][11:19]}</td><td>{chip(r['tier'])}</td><td><b>{r['action']}</b></td>"
                       f"<td class='rh-note'>{SHORT.get(ARCH.ACTION_BOX.get(r['action'], 'orch'), '')}</td></tr>"
                       for r in reversed(rows[-8:]))
        html(f"<table class='rh-tiny'><thead><tr><th>Time</th><th>Tier</th><th>Step</th><th>Diagram box</th></tr></thead><tbody>{body}</tbody></table>"
             "<div class='rh-note'>Hover a row for why that tier was used.</div>")
        c1, c2 = st.columns(2)
        c1.button("Full agent log", type="tertiary", key="c_log", on_click=goto, args=("Agent log",))
        c2.button("Audit trail", type="tertiary", key="c_audit", on_click=goto, args=("Audit trail",))
    if ss.live_log:
        with st.container(border=True):
            card_head("Live feed log", "meters", astates, key="lk_meters_feed")
            html("<div class='rh-note mono'>" + "<br>".join(ss.live_log[:8]) + "</div>")


# ---- column 3: outputs
def c_decision(astates) -> None:
    with st.container(border=True):
        card_head("Your decision", "manager", astates, "a person approves every plan")
        if run_error:
            html(f"<div class='rh-note' style='color:var(--red)'>{run_error}</div>")
        if not run:
            html("<div class='rh-note'>No plan yet.</div>")
            return
        if run.get("cfg_version") != ss.cfg_version:
            html(f"<div class='rh-note' style='color:var(--red)'>{ICONS['alert']} Settings changed since this plan. Generate again.</div>")
        status = state.get("status")
        if pending and pending["type"] == "choose_relaxation":
            compact_problem()
        elif pending and pending["type"] == "approval":
            html(f"<div class='rh-note' style='margin-bottom:6px'>{state.get('explanation', '')}</div>")
            approval_controls("console")
        elif status == "work_order_issued":
            html(f"<div class='rh-card approve' style='margin:4px 0'><h4>{ICONS['check']}Approved. Work order issued.</h4>"
                 f"<p>People run the machines at these times; RightHour never switches equipment.</p></div>")
        elif status == "rejected":
            html(f"<div class='rh-note'>Rejected. Reason on record: {state.get('approval', {}).get('reason') or 'none given'}.</div>")
        elif status == "fallback":
            html(f"<div class='rh-note' style='color:var(--red)'>Current schedule kept. {state.get('note', '')}</div>")


def compact_problem() -> None:
    """The 'which rule can flex?' question, sized for a console column. The full table is on the Plan screen."""
    opts = pending["options"]
    html(f"<div class='rh-card alert' style='margin:4px 0 8px;padding:12px 14px'><h4>{ICONS['alert']}No valid schedule for {state['forecast']['date']}</h4>"
         f"<p style='font-size:13px'>{pending['diagnosis']} The agent tested each flexible rule; contract demand and fixed batches are never relaxed.</p></div>")
    body = ""
    for o in opts:
        res = (f"{rs(o['cost_rs'])}" + (f", saves {rs(o['saving_rs'])}" if o.get("saving_rs") is not None else "")) if o["feasible"] else "still no plan"
        rec = " <span class='rh-chip ok'>recommended</span>" if o["id"] == pending["recommended"] else ""
        body += f"<tr><td>{o['text']}{rec}<br><span class='rh-note'>{o['side_cost_note']}</span></td><td class='r'>{res}</td></tr>"
    html(f"<table class='rh-tiny'><thead><tr><th>Flexible rule</th><th class='r'>Day cost</th></tr></thead><tbody>{body}</tbody></table>")
    feas = [o for o in opts if o["feasible"]]
    if not feas:
        html("<div class='rh-note'>No flexible rule makes a valid plan. Loosen a window in Factory systems, or change the order.</div>")
        return
    choice = st.radio("Relax which rule?", [o["id"] for o in feas], index=[o["id"] for o in feas].index(pending["recommended"]),
                      format_func=lambda i: next(o["text"] for o in feas if o["id"] == i), key="relax_console")
    if st.button("Relax this rule and re-plan", type="primary", width="stretch", key="relaxgo_console"):
        do_resume(choice)
        st.rerun()


def c_saving(astates) -> None:
    with st.container(border=True):
        card_head("Saving and demand", "opt", astates, key="lk_opt_saving")
        if not run or state.get("plan", {}).get("status") != "optimal":
            html("<div class='rh-note'>Appears when there is a valid plan.</div>")
            return
        base, plan, sav = state["baseline"], state["plan"], state.get("saving_rs")
        cd, mtd, pk = SITE["contract_demand_kva"], SITE["month_to_date_max_kva"], plan["peak_kva"]
        yr = lakh(sav * SITE["working_days_per_year"]) if sav is not None else "n/a"
        html(f"<div class='rh-kv'><div><b class='g' style='font-size:26px'>{rs(sav) if sav is not None else 'n/a'}</b>projected saving a day</div>"
             f"<div><b style='font-size:26px'>{yr}</b>a year at {SITE['working_days_per_year']} days</div>"
             f"<div><b class='s'>{rs(base['total_rs']) if base.get('feasible') else 'n/a'}</b>usual schedule</div>"
             f"<div><b class='s'>{rs(plan['total_rs'])}</b>RightHour plan</div></div>")
        pct = lambda v: min(max(v / cd * 100, 0), 100)  # noqa: E731
        html(f"<div class='rh-note'>Max demand <b style='color:{NAVY if pk <= mtd else RED}'>{pk:.0f} kVA</b>, {'within' if pk <= mtd else 'above'} this month's {mtd}; contract {cd}.</div>"
             f"<div class='rh-meter' style='margin:26px 0 26px'><div class='fill' style='width:{pct(pk):.1f}%'></div>"
             f"<div class='tick top' style='left:{pct(mtd):.1f}%'><span style='transform:translateX(-50%)'>month max {mtd}</span></div>"
             f"<div class='tick cd bot' style='left:99.6%'><span style='transform:translateX(-100%)'>contract {cd}</span></div></div>")
        x = [k / SLOTS_PER_HOUR for k in range(N_SLOTS)] + [24]
        b, p = base["kva"], plan["kva"]
        fig = _mini_fig(200)
        fig.add_trace(go.Scatter(x=x, y=b + [b[-1]], name="Usual", line=dict(color=MUTED, width=1.5, dash="dot", shape="hv")))
        fig.add_trace(go.Scatter(x=x, y=p + [p[-1]], name="Plan", line=dict(color=NAVY, width=2.4, shape="hv")))
        fig.add_hline(y=cd, line=dict(color=RED, width=1.2, dash="dash"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="c_plan_chart")
        st.button("Open the full plan", type="tertiary", key="c_fullplan", on_click=goto, args=("Plan",))


def c_workorder(astates) -> None:
    with st.container(border=True):
        card_head("Work order", "runs", astates)
        if not ss.approved:
            html("<div class='rh-note'>Issued when you approve a plan.</div>")
            return
        ast = ss.approved[1]
        rows = pd.read_csv(io.BytesIO(work_order_csv(ast)))
        body = "".join(f"<tr><td>{r.machine}</td><td class='m'>{r.start} to {r.end}</td><td class='rh-note'>{r.note}</td></tr>" for r in rows.itertuples())
        html(f"<div class='rh-note'>For {ast['forecast']['weekday']} {ast['forecast']['date']}.</div>"
             f"<table class='rh-tiny'><tbody>{body}</tbody></table>")
        st.download_button("Download the work order (CSV)", work_order_csv(ast), f"work_order_{ast['forecast']['date']}.csv", "text/csv",
                           width="stretch", key="c_wo")


def c_check(astates) -> None:
    with st.container(border=True):
        card_head("Next-day check and learning", "loop_day", astates)
        v = ss.verify
        if v:
            real = v["realisation"]
            html(f"<div class='rh-kv three'><div><b>{rs(v['projected_saving_rs'])}</b>expected</div>"
                 f"<div><b class='g'>{rs(v['verified_saving_rs'])}</b>verified on the meter</div><div><b>{(real or 0) * 100:.0f}%</b>realised</div></div>"
                 f"<div class='rh-note'>Base load came in {v['forecast_error_pct']:+.1f}% against the forecast; correction x{v['old_bias']:.3f} to "
                 f"x{v['new_bias']:.3f}{' (in use)' if abs(ss.bias - v['new_bias']) < 1e-9 else ''}. {v.get('learn_note', '').capitalize()}.</div>")
            if abs(ss.bias - v["new_bias"]) > 1e-9 and st.button("Use this learning for the next plan", key="c_learn", width="stretch"):
                ss.bias = v["new_bias"]
                st.rerun()
        elif ss.approved:
            actx, ast = ss.approved
            real_day = D.day_actuals(ss.hist, ast["forecast"]["date"]) if ss.hist is not None else None
            if real_day is not None:
                html("<div class='rh-note'>The meter file holds this day's real readings.</div>")
                if st.button("Check against the real readings", type="primary", width="stretch", key="c_verify"):
                    try:
                        ss.verify = verify_and_learn(actx, ast, dict(ast["final_schedule"]), real_day)
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Could not run the check: {e}")
                html("<div class='rh-note'>To record what actually ran on the floor, use the diagram's Next-day box.</div>")
            else:
                html("<div class='rh-note'>Waiting for the day's readings. In live mode they arrive and the check runs by itself.</div>")
        else:
            html("<div class='rh-note'>Runs after an approved day.</div>")
        hist_rows = [r for r in ss.audit.rows() if r["action"] == "verify_savings"]
        if hist_rows:
            body = ""
            for r in hist_rows[-6:][::-1]:
                d = json.loads(r["detail"])
                body += (f"<tr><td class='m'>{r['ts'][:16].replace('T', ' ')}</td><td class='r'>{rs(d.get('projected_saving_rs') or 0)}</td>"
                         f"<td class='r'>{rs(d.get('verified_saving_rs') or 0)}</td></tr>")
            html(f"<div class='rh-note' style='margin-top:8px'><b>Savings history</b></div><table class='rh-tiny'><thead><tr><th>Checked</th>"
                 f"<th class='r'>Expected</th><th class='r'>Verified</th></tr></thead><tbody>{body}</tbody></table>")


def manager_console() -> None:
    rows = _run_rows()
    astates = ARCH.element_states(rows, pending, ss.verify is not None, ss.hist is not None)
    # the clickable diagram, small: a click opens that box in the Control room
    with st.container(border=True):
        c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
        c1.markdown("<div class='rh-ch'>Design diagram <span class='sub'>Click any box to open it with its data sources, run log and settings</span></div>",
                    unsafe_allow_html=True)
        c2.button("Open the full diagram ↗", key="c_hldfull", type="tertiary", on_click=goto, args=("Control room",))
        html("<div class='rh-legend-hld'><span><i class='ran'></i>ran this run</span><span><i class='ask'></i>needs you</span>"
             "<span><i></i>not run yet</span></div>")
        ss.setdefault("hld_nonce", 0)
        picked = ARCH.component(astates, None, key=f"hld_console_{ss.hld_nonce}",
                                css="#root{padding:6px;border:0} svg.dg{min-width:760px;max-height:430px}")
        if picked:
            ss.hld_nonce += 1
            goto("Control room", picked)
            st.rerun()
    c_in, c_tel, c_out = st.columns([1, 1, 1], gap="medium")
    with c_in:
        section("1", "Inputs", "what goes in")
        c_meters(astates)
        c_day(astates)
        c_notes(astates)
        c_bill(astates)
        c_solar(astates)
        c_model(astates)
    with c_tel:
        section("2", "Telemetry", "what the agents are doing")
        c_run(astates)
        c_health(astates)
        c_forecast(astates)
        c_opt(astates)
        c_guard(astates)
        c_stream(astates)
    with c_out:
        section("3", "Outputs", "what comes out")
        c_decision(astates)
        c_saving(astates)
        c_workorder(astates)
        c_check(astates)


# ------------------------------------------------------------------ screens
step_strip(state if run else None, pending, ss.verify is not None)
if ss.get("view_nav") != ss.view:   # a link moved us: keep the screen switch in step (before it is drawn)
    ss.view_nav = ss.view


def _nav_changed() -> None:
    ss.view = ss.view_nav or ss.view


st.segmented_control("Screen", VIEWS, key="view_nav", on_change=_nav_changed, label_visibility="collapsed")
{"Manager console": manager_console, "Plan": plan_tab, "Control room": control_room,
 "Agent log": log_tab, "Audit trail": audit_tab}[ss.view]()
