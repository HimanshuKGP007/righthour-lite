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
from righthour import ask as ASK
from righthour import config as CFG
from righthour import data as D
from righthour import live as LIVE
from righthour.agents import PROCESS_DEFAULTS, Context, lakh, parse_tariff, rs, verify_and_learn
from righthour.audit import AuditLog
from righthour.graph import resume, start
from righthour.llm import KEY_ENV, LABEL, PROVIDERS, LLMClient, default_provider, suggest_models
from righthour.router import MODELS, PRICES, cost_usd, set_models, set_provider
from righthour.tariff import N_SLOTS, SLOTS_PER_HOUR, validate_tariff
from righthour.theme import (AMBER, AMBER_TEXT, CSS, FONT, GREEN, ICONS, INK, LOGO, MUTED, NAVY, NAVY_10, NAVY_30, NAVY_60, RED,
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
ss.setdefault("proc", dict(PROCESS_DEFAULTS))   # how each diagram box works; changed in the Control room
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
# The left menu: one screen per job. Name, Material icon, what the screen is for.
NAV = [("Today", ":material/today:", "Plan the day, see the saving, approve"),
       ("Upload data", ":material/upload_file:", "Meter readings, production notes, price orders"),
       ("Plant setup", ":material/precision_manufacturing:", "Machines, crew rules, prices, solar, AI model"),
       ("Control room", ":material/account_tree:", "The agents' diagram, box by box"),
       ("Next-day check", ":material/fact_check:", "Real saving against the plan"),
       ("History", ":material/history:", "Every agent step and decision")]
VIEWS = [n for n, _, _ in NAV]
OLD_VIEW = {"Manager console": "Today", "Plan": "Today", "Agent log": "History", "Audit trail": "History"}
ss.setdefault("view", "Today")
ss.view = OLD_VIEW.get(ss.view, ss.view) if ss.view not in VIEWS else ss.view
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
           "key_Groq", "key_Grok", "key_Claude", "view_nav"]:
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
ss.setdefault("llm_err", {"text": ""})   # this session's newest AI error
LLM = LLMClient(ss.provider, api_key=ss.get(f"key_{ss.provider}") or None, error_sink=ss.llm_err)
set_provider(ss.provider)
ss.setdefault("models", {})          # the small and large model chosen for each provider
ss.setdefault("model_lists", {})     # live model lists, asked of each provider with your key
for _p, _m in ss.models.items():
    set_models(_p, _m.get("T1", ""), _m.get("T2", ""))
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


def run_controls() -> None:
    """The plan bar on Today: which day, the solar forecast, the goal, and the button that runs the agents."""
    with st.container(border=True, key="planbar"):
        c1, c2, c3, c4 = st.columns([1, 1, 2.4, 0.95], vertical_alignment="bottom", gap="small")
        if d_range:
            c1.date_input("Plan for", min_value=d_range[0], max_value=d_range[2], key="w_plan_day",
                          help="The agent forecasts this day from the 4 same weekdays before it. Days inside the meter file can be replayed "
                               "and checked against the real readings. The day after the file ends is 'tomorrow'.")
        elif d_err:
            c1.error(d_err)
        c2.selectbox("Solar forecast", list(WEATHER), key="w_weather",
                 help="Live forecast asks Open-Meteo for the day's sunshine (recorded data for past days). Falls back to a clear-sky model if offline.")
        c3.text_input("Goal for the agent", key="w_goal")
        c4.button("Generate plan", type="primary", icon=":material/play_arrow:", width="stretch",
                  disabled=ss.hist is None or plan_date is None, key="go_btn_c")
        if d_range:
            html(f"<div class='rh-note' style='margin-top:-4px'>{'Replay day: the meter file holds this day, so the next-day check can use its real readings.' if has_actuals else 'Tomorrow: no readings yet. They arrive later and the check runs then.'}</div>")


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
        html(f"<div class='rh-pill live'>Live: {len(ss.live_future)} days still to arrive</div>")


def _pick_model(tier: str) -> None:
    ss.models.setdefault(ss.provider, {})[tier] = ss[f"m_{tier}_{ss.provider}"]
    set_models(ss.provider, ss.models[ss.provider].get("T1", ""), ss.models[ss.provider].get("T2", ""))
    ss.model_ok = None


def _load_models(force: bool = False) -> dict:
    """Ask the provider which models this key can use. Cached per provider and key; refresh on demand."""
    tag = hash(LLM.key)
    got = ss.model_lists.get(ss.provider)
    if got and got["tag"] == tag and not force:
        return got
    try:
        ids = LLM.list_models()
        got = {"tag": tag, "ids": ids, "err": ""}
        cur = ss.models.get(ss.provider, {})
        if ids and (cur.get("T1") not in ids or cur.get("T2") not in ids):   # a saved or built-in name that no longer exists
            small, large = suggest_models(ss.provider, ids)
            ss.models[ss.provider] = {"T1": small, "T2": large}
            set_models(ss.provider, small, large)
            for t in ("T1", "T2"):
                ss.pop(f"m_{t}_{ss.provider}", None)
    except Exception as e:  # noqa: BLE001
        got = {"tag": tag, "ids": [], "err": str(e)}
    ss.model_lists[ss.provider] = got
    return got


def model_controls() -> None:
    st.segmented_control("Provider", list(PROVIDERS), selection_mode="single", label_visibility="collapsed", key="w_provider",
                         format_func=lambda p: LABEL[p])
    if ss.provider == "Offline":
        html("<div class='rh-note' style='margin-top:6px'>Everything runs on this computer with fixed templates. No AI is called. "
             "Pick Groq, Grok or Claude to use a language model for the text steps. Numbers never come from a model.</div>")
        return
    env_set = bool(__import__("os").environ.get(KEY_ENV[ss.provider]))
    st.text_input(f"{LABEL[ss.provider]} API key", type="password", key=f"key_{ss.provider}",
                  placeholder=("found in the app's secrets" if env_set else f"or set {KEY_ENV[ss.provider]}"),
                  help="Kept in this browser session only. On the hosted app, add it under Settings, Secrets.")
    if not LLM.live:
        html(f"<div class='rh-note'>Add a key and the list of models you can use appears here.</div>")
        return
    got = _load_models()
    if got["err"]:
        html(f"<div class='rh-card alert' style='padding:10px 12px;margin:6px 0'><h4>{ICONS['alert']}Could not load the model list</h4>"
             f"<p style='font-size:13px'>{escape(got['err'])}</p><p class='rh-note'>Check the key. Then press Refresh models.</p></div>")
    ids = got["ids"] or sorted({MODELS[ss.provider]["T1"], MODELS[ss.provider]["T2"]})
    cur = ss.models.get(ss.provider, MODELS[ss.provider])
    for t, label, hint in (("T1", "Small model: routine steps", "Fast and cheap. Reads notes, writes the plan in words."),
                           ("T2", "Large model: hard cases", "Used when the small model fails twice, or no plan fits.")):
        opts = ids if cur.get(t) in ids else [cur.get(t)] + ids
        st.selectbox(label, opts, index=opts.index(cur.get(t)), key=f"m_{t}_{ss.provider}", on_change=_pick_model, args=(t,), help=hint)
    b1, b2 = st.columns(2)
    if b1.button("Refresh models", width="stretch", key=f"mref_{ss.provider}"):
        _load_models(force=True)
        st.rerun()
    if b2.button("Test connection", width="stretch", key=f"mtest_{ss.provider}"):
        try:
            LLM.complete(MODELS[ss.provider]["T1"], "Reply with the single word OK.", "ping", 8)
            ss.model_ok = f"Connected. {MODELS[ss.provider]['T1']} answered."
        except Exception as e:  # noqa: BLE001
            ss.model_ok = f"Failed: {e}"
    if ss.get("model_ok"):
        bad = ss.model_ok.startswith("Failed")
        html(f"<div class='rh-note' style='color:var(--{'red' if bad else 'green'});margin-top:6px'>{escape(ss.model_ok)}</div>")
    elif ss.llm_err["text"]:
        html(f"<div class='rh-note' style='color:var(--red);margin-top:6px'>Last AI call failed: {escape(ss.llm_err['text'])}</div>")
    html(f"<div class='rh-pill live'>{LLM.label}</div><div class='rh-note' style='margin-top:6px'>Numbers never come from the model.</div>")


def goto(view: str, box: str | None = None) -> None:
    """Navigation callback: switch screens, and on the Control room open one box of the design diagram."""
    ss.view = view
    if box:
        ss.sel_box = box
        ss.hld_room_nonce = ss.get("hld_room_nonce", 0) + 1   # a fresh diagram, so it opens on this box


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
                          notes=notes_for_run(), bias=ss.bias, llm=LLM, audit=ss.audit, process=dict(ss.proc))
            graph, cfg, state, pending = start(ctx)
            ss.run = {"ctx": ctx, "graph": graph, "cfg": cfg, "state": state, "pending": pending, "cfg_version": ss.cfg_version,
                      "proc": dict(ss.proc)}
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


# ------------------------------------------------------------------ the left menu
def sidebar() -> None:
    asking = bool(ss.run and ss.run.get("pending"))
    with st.sidebar:
        html(f"<div class='rh-brand'>{LOGO}<div><div class='n'>RightHour</div><div class='s'>Power bill planner</div></div></div>")
        with st.container(key="nav"):
            for name, icon, hint in NAV:
                on = ss.view == name
                label = f"{name}  ·  your turn" if name == "Today" and asking else name
                st.button(label, icon=icon, key=f"{'navon' if on else 'nav'}_{name}", width="stretch", on_click=goto, args=(name,))
        h = ss.hist
        days = LIVE.describe_feed(h)["n_full_days"] if h is not None else 0
        live = f"<span class='rh-tag ok'>on, {len(ss.live_future)} days to come</span>" if ss.live_active else "<span class='rh-tag'>off</span>"
        rows = [("Meter data", f"{days} days" if h is not None else "none yet"),
                ("Plan for", f"{plan_date:%a %d %b %Y}" if plan_date else "not set"),
                ("AI", escape(LLM.label)), ("Live feed", live)]
        html(f"<div class='rh-side'><div class='h'>Plant</div><div class='v'>{escape(str(SITE['name']))}</div><dl>"
             + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl></div>")
        st.button("Generate plan", type="primary", icon=":material/play_arrow:", width="stretch", key="go_btn",
                  disabled=ss.hist is None or plan_date is None, help="Runs the agents for the day set on Today.")


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
    word = {"done": "Done", "ok": "Done", "alert": "Your turn", "active": "Your turn", "wait": "Waiting"}
    cells = "".join(f"<li class='{c}'><div class='top'><span class='no'>{i}</span><span class='sw'>{word[c]}</span></div>"
                    f"<div class='t'>{t}</div><div class='s'>{d}</div></li>" for i, (t, c, d) in enumerate(items, 1))
    html(f"<ol class='rh-steps' aria-label='Steps for the day'>{cells}</ol>")


def next_step(state: dict | None, pending: dict | None, verified: bool) -> None:
    """One sentence that says what to do now. The class sees the loop; the manager sees the next action."""
    status = (state or {}).get("status", "")
    if ss.hist is None:
        cls, lbl, msg = "", "Start here.", "Load the meter data in <b>Inputs</b>, then press <b>Generate plan</b>."
    elif not state:
        cls, lbl, msg = "", "Next.", "Choose the day and the goal, then press <b>Generate plan</b>. The agent does the rest."
    elif pending and pending.get("type") == "choose_relaxation":
        cls, lbl, msg = "you", "Your turn.", "No plan fits every rule. Pick which flexible rule the agent may relax."
    elif pending and pending.get("type") == "approval":
        cls, lbl, msg = "you", "Your turn.", "Check the saving and the schedule, then <b>approve</b> or <b>reject</b> the plan."
    elif verified:
        cls, lbl, msg = "done", "Done.", "The meters confirmed the saving. The agent kept what it learned for the next plan."
    elif status == "work_order_issued":
        cls, lbl, msg = "", "Plan approved.", "Machines run on the work order. Next day, check the saving against the real readings."
    elif status == "rejected":
        cls, lbl, msg = "", "Plan rejected.", "Your reason is saved. Change the goal or the notes and generate again."
    else:
        cls, lbl, msg = "", "Next.", "Generate the plan again with the new settings."
    html(f"<div class='rh-next {cls}'><span class='lbl'>{lbl}</span><span>{msg}</span></div>")


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


def step_rows(actions: set[str], rows: list[dict]) -> None:
    mine = [r for r in rows if r["action"] in actions]
    if not mine:
        return
    body = "".join(f"<tr><td class='m'>{r['ts'][11:19]}</td><td>{TIER_WORDS.get(r['tier'], r['tier'])}</td><td><b>{escape(words(r['action']))}</b></td><td>{escape(str(r['reason']))}</td></tr>" for r in mine)
    html(f"<table class='rh-log'><thead><tr><th>Time</th><th>Done by</th><th>What happened</th><th>Why</th></tr></thead><tbody>{body}</tbody></table>")


def need_run(what: str) -> bool:
    if run:
        return False
    html(f"<div class='rh-note' style='margin:8px 0'>{what} Generate a plan first.</div>")
    return True


def meter_controls(p: str) -> None:
    """Load meter data: upload a CSV, or use the steel plant data or the sample. Used on Upload data and in the Control room."""
    up = st.file_uploader("Upload a meter CSV", type=["csv"], key=f"{p}_up",
                          help="Same columns as the UCI steel dataset: date, Usage_kWh, Lagging_Current_Power_Factor.")
    auto = st.toggle("Scale the load to this plant's size", value=ss.scale_mode, key=f"{p}_scale",
                     help="The UCI plant is smaller than the demo site. Scaling keeps its real shape at the site's size.")
    b1, b2, b3 = st.columns(3)
    choice = None
    if b1.button("Load my CSV", type="primary", width="stretch", disabled=up is None, key=f"{p}_load"):
        choice = "upload"
    if b2.button("Steel plant data", width="stretch", disabled=not STEEL_FILE.exists(), key=f"{p}_steel",
                 help="Steel_industry_data.csv (UCI, DAEWOO Steel). The default."):
        choice = "steel"
    if b3.button("Sample data", width="stretch", key=f"{p}_sample", help="A labelled synthetic profile."):
        choice = "sample"
    if choice:
        try:
            ss.scale_mode = auto
            scale = None if auto else 3.0
            ss.hist = {"upload": lambda: D.load_uci_csv(up, scale), "steel": lambda: D.load_uci_csv(STEEL_FILE, scale),
                       "sample": D.synthetic_history}[choice]()
            ss.w_live, ss.live_active, ss.live_future, ss.live_base = False, False, [], None
            reset_run()
            ss.autoran = False
            ss.audit.log("setup", "Plant manager", "data_loaded", tier="human", mode="human",
                         reason=f"meter data source set to: {choice}", detail={"rows": len(ss.hist)})
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read that file: {e}. Check it has the columns date, Usage_kWh and Lagging_Current_Power_Factor.")
    if p != "d":
        st.markdown("**Live feed**")
        live_controls()


# ---- factory systems: the day's notes, the machines, the crew rules
def notes_editor() -> None:
    html(f"<p class='rh-lead'><b>Notes for {plan_date:%A %d %B %Y}.</b> Write what the supervisor would say. "
         f"A <b>Fixed batch</b> must run at its stated hours. A <b>New order</b> is placed by the agent inside the hours you state. "
         f"{'The AI model reads free wording.' if LLM.live else 'Offline mode reads simple notes: kW, hours, from 10 am to 4 pm, starts at 9 am, finished by 5 pm. Pick an AI model in Plant setup for free wording.'}</p>"
         if plan_date else "")
    df = st.data_editor(pd.DataFrame(ss.notes_rows, columns=NOTE_COLS), num_rows="dynamic", hide_index=True, width="stretch", key=f"notes{ss.notes_ver}",
                        column_config={"Kind": st.column_config.SelectboxColumn(options=["Fixed batch", "New order"], required=True, default="New order"),
                                       "Note, in plain words": st.column_config.TextColumn(width="large"),
                                       "Could start as early as": st.column_config.NumberColumn(min_value=0, max_value=23, step=1,
                                                                                               help="Optional. If there is no valid plan, the manager could start this order this early."),
                                       "Could finish as late as": st.column_config.NumberColumn(min_value=1, max_value=24, step=1)})
    ss.notes_rows = df.to_dict("records")
    if st.button("Add the example grinding order", icon=":material/add:", help="A 400 kW order that does not fit the usual schedule. Shows what happens when rules conflict."):
        ss.notes_rows = ss.notes_rows + [dict(EXAMPLE_ORDER)]
        ss.notes_ver += 1
        st.rerun()


def pane_machines() -> None:
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
    up = st.file_uploader("Upload the order as a text file", type=["txt", "md"], key="tariff_file",
                          help="Plain text copied from the regulator's order. Or paste it in the box below.")
    if up is not None:
        sample = up.getvalue().decode("utf-8", errors="replace")
    txt = st.text_area("Tariff order text", sample, height=110, key=f"tariff_txt_{up.file_id if up else 'sample'}")
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
    if st.button("Save solar settings", type="primary", key=f"pv_save{v}"):
        save_cfg(site=site)


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


# ---- the router and the factory memory
def pane_router(rows) -> None:
    html("<table class='rh-log'><thead><tr><th>Step</th><th>Goes to</th></tr></thead><tbody>"
         "<tr><td>Forecast, solar, tariff lookup, optimiser, rule checks, next-day check</td><td><span class='rh-chip t0'>T0</span>plain code. Never a model.</td></tr>"
         "<tr><td>Reading notes, writing the explanation, choosing the next step</td><td><span class='rh-chip t1'>T1</span>small model</td></tr>"
         "<tr><td>A new tariff order; a small-model answer that fails its check twice; no plan fits the rules</td><td><span class='rh-chip t2'>T2</span>large model</td></tr>"
         "</tbody></table>")
    st.markdown("**Which models**")
    model_controls()


def pane_memory(rows) -> None:
    rej = [r for r in ss.audit.rows() if r["action"] == "reject"]
    saves = [r for r in ss.audit.rows() if r["action"] == "setup_saved"]
    html(f"<div class='rh-kv three'><div><b>x{ss.bias:.3f}</b>learned forecast correction</div><div><b>{len(rej)}</b>rejected plans on record</div>"
         f"<div><b>{len(saves)}</b>setup changes saved</div></div>")
    if ss.bias != 1.0 and st.button("Forget the learned correction", icon=":material/restart_alt:", key="mem_reset"):
        ss.bias = 1.0
        st.rerun()


# ---- how each box works: its steps, filled in from this run
def _flow(eid: str) -> list[tuple[str, str]]:
    s, p, site = state or {}, ss.proc, ss.cfg["site"]
    h = ss.hist
    fc, pv, plan = s.get("forecast"), s.get("pv"), s.get("plan") or {}
    feed = LIVE.describe_feed(h) if h is not None else None
    notes = notes_for_run()
    off = lambda key, what: f"<b class='off'>Switched off.</b> {what}" if not p[key] else None  # noqa: E731
    if eid == "meters":
        return [("Reads the meter file", escape(str(h.attrs.get("source", ""))) if h is not None else "No file loaded yet."),
                ("Puts readings on a 15-minute grid", "Missing slots are filled from the readings on each side."),
                ("Scales the load to this plant", f"x{h.attrs.get('scale', 1):.2f}" if h is not None else "Waiting for data."),
                ("Hands the readings to 1 Collect", f"{feed['n_rows']:,} readings, {feed['n_full_days']} full days, last at {str(feed['last_ts'])[:16]}." if feed else "Nothing yet.")]
    if eid == "check":
        issues = s.get("data_issues") or (D.validate_load(h, p["spike_x"], p["pf_min"]) if h is not None else [])
        return [("Looks for gaps", "Counts missing 15-minute readings between the first and last reading."),
                ("Looks for negative readings", "A negative load means a meter wired the wrong way round."),
                ("Looks for spikes", f"Flags readings above <b>{p['spike_x']:g}x</b> the plant's 99th-percentile load."),
                ("Checks power factor", f"Flags readings with power factor outside <b>{p['pf_min']:g} to 1.0</b>."),
                ("Reports", "<br>".join(f"<span class='rh-chip {'ok' if i['level'] == 'ok' else 'warn'}'>{i['level']}</span>{i['text']}" for i in issues) or "Nothing checked yet.")]
    if eid == "collect":
        return [("Takes the meter history", f"Every reading before {plan_date:%d %b %Y}." if plan_date else "Waiting for data."),
                ("Takes the price bands", escape(str(ss.tariff["name"]))),
                ("Takes the day's production notes", f"{len(notes)} note(s)" + (" (reader switched off)" if not p["notes_on"] else "")),
                ("Takes the weather setting", f"{ss.w_weather}" + ("" if p["solar_on"] else " (solar switched off)")),
                ("Hands one bundle to the agents", "Starts when you press Generate plan.")]
    if eid == "store":
        return [("Writes every step", "Who did it, which tier, which model, why, what it cost."),
                ("Keeps it in one file per plant", f"<span class='mono'>righthour_audit.sqlite</span>, {len(ss.audit.rows())} rows so far."),
                ("Lets anyone replay a run", "Every row has the run id and the step's full details.")]
    if eid == "orch":
        return [("Reads your goal", f"“{escape(str(s.get('goal') or ss.w_goal))}”"),
                ("Plans the steps", "Gather data → forecast → read prices → read notes → optimise → check rules → explain → ask you."),
                ("Calls each tool in turn", f"{len(_run_rows())} steps logged in this run." if run else "Not run yet."),
                ("Plans again if a step fails", "No plan fits? It tests each flexible rule and asks you which one to relax."),
                ("Asks you to approve", f"Status now: <b>{escape(str(s.get('status', 'no plan yet')))}</b>.")]
    if eid in ("price", "bill"):
        bands = ", ".join(f"{b['type']} {b['start_h']:02d}–{b['end_h']:02d} h ₹{b['rate']:.2f}" for b in ss.tariff["slots"])
        warns = [i.text for i in validate_tariff(ss.tariff) if i.level == "warning"]
        return [("Looks up the plant's tariff", f"{escape(str(ss.tariff['name']))}. From the library, so no model call."),
                ("Reads the price bands", bands),
                ("Reads the demand charge", f"₹{ss.tariff['demand_charge_per_kva_month']:,.0f} per kVA per month; contract {site['contract_demand_kva']} kVA."),
                ("Checks them against the regulator's rules", f"{len(warns)} warning(s)." + (f" First: {warns[0]}" if warns else ""))]
    if eid == "orders":
        return [("Reads the order text", "Pasted or uploaded in Upload data."),
                ("Drafts the price bands", "Large model, once per new order. Offline mode uses a fixed reader."),
                ("Checks the draft", "Bands must cover 24 hours with no overlap; rates must be positive."),
                ("Waits for a person", "Nothing uses the new prices until someone signs them off."),
                ("Adds it to the library", f"{len(ss.library)} signed-off order(s) in the library.")]
    if eid == "forecast":
        meth = "the last 7 days" if p["method"] == "recent" else f"the same weekday over the last {p['weeks']} weeks"
        return [("Picks past days", f"Takes {meth} before the plan day."),
                ("Averages them slot by slot", "One value for each 15-minute slot of the day."),
                ("Applies the learned correction", off("use_learning", "The correction is not used (x1.000).") or f"x{ss.bias:.3f}, learned from next-day checks."),
                ("Result", f"{fc['weekday']} {fc['date']}: peak {max(fc['kw']):,.0f} kW, {sum(fc['kw']) / SLOTS_PER_HOUR:,.0f} kWh in the day ({fc['method']})." if fc else "Appears after a run.")]
    if eid == "weather":
        if not p["solar_on"]:
            return [("Solar", off("solar_on", "The plan counts no rooftop power. Turn it on to use the weather forecast."))]
        return [("Asks for the day's sunshine", f"Open-Meteo at {site['latitude']}, {site['longitude']}. Setting: <b>{ss.w_weather}</b>."),
                ("Falls back if offline", "A clear-sky model for the site, so a plan is always possible."),
                ("Turns sunshine into kW", f"For {site['rooftop_pv_kwp']} kWp of rooftop panels."),
                ("Result", f"Peak {max(pv['kw']):.0f} kW, {sum(pv['kw']) / SLOTS_PER_HOUR:,.0f} kWh in the day. Source: {escape(str(pv['source']))}." if pv else "Appears after a run.")]
    if eid in ("rules", "factory"):
        if not p["notes_on"]:
            return [("Production notes", off("notes_on", "Notes are ignored. Fixed batches and new orders are not planned."))]
        nf, ne = len(s.get("fixed") or []), len(s.get("extra") or [])
        return [("Takes the day's notes", f"{len(notes)} note(s) from Upload data."),
                ("Reads each note", f"{escape(LLM.label)}. It finds kW, hours and the allowed window."),
                ("Checks what it read", "Hours must be 0 to 24 and the window long enough. A failed read is tried again, then sent to the large model."),
                ("Result", f"{nf} fixed batch(es), {ne} new order(s)." if run else "Appears after a run.")]
    if eid == "opt":
        prob = s.get("problem") or {}
        moved = sum(1 for m in prob.get("shiftable", []) if m.get("baseline_start_h") is not None and plan.get("schedule", {}).get(m["id"]) not in (None, m["baseline_start_h"]))
        return [("Takes the inputs", f"{len(prob.get('shiftable', MACH['shiftable']))} machines that can move, their windows and crew rules, the forecast, solar and prices."),
                ("Sets the goal", "The lowest bill for the day: energy cost in each slot plus any new peak demand."),
                ("Keeps every hard rule and your guardrails", f"Contract demand {site['contract_demand_kva']} kVA" + (f" less your {p['margin_pct']}% margin" if p['margin_pct'] else "") + ", machine windows, crew rules, fixed batches" + (", no new monthly peak" if p['block_new_peak'] else "") + (f", at most {p['max_moves']} machine(s) moved" if p['max_moves'] is not None else "") + "."),
                ("Solves", f"An exact solver, with up to {p['time_limit_s']} s to think. Starts on the hour. No AI model touches the numbers."),
                ("Result", f"{plan.get('status', '')}: {moved} machine(s) moved, cost {rs(plan['total_rs'])}, peak {plan['peak_kva']:.0f} kVA." if plan.get("total_rs") is not None else "Appears after a run.")]
    if eid == "guard":
        checks = (s.get("policy") or {}).get("checks", [])
        return [("Takes the plan", "The schedule from the optimiser, or your edited one."),
                ("Checks every rule again, in plain code", "Windows, crew rules, fixed batches, contract demand, this month's max."),
                ("Adds your guardrails", f"Safety margin {p['margin_pct']}%; new monthly peak {'blocked' if p['block_new_peak'] else 'allowed if still cheaper'}; "
                 f"{'at most ' + str(p['max_moves']) + ' machine(s) moved' if p['max_moves'] is not None else 'no limit on machines moved'}; "
                 f"smallest saving {rs(p['min_saving_rs']) if p['min_saving_rs'] else 'any'}."),
                ("Blocks a plan that breaks a hard rule", "The usual schedule is kept instead. An edit that breaks one cannot be approved."),
                ("Result", f"{sum(c['ok'] for c in checks)} of {len(checks)} passed." if checks else "Appears after a run.")]
    if eid == "router":
        return [("Looks at each step", "Is it maths, or words?"),
                ("Maths and checks go to code", "Tier T0. A model never makes a number."),
                ("Routine words go to the small model", f"{escape(LLM.label)}."),
                ("Hard cases go to the large model", "A failed check twice, a new tariff order, or no plan fits."),
                ("Logs the choice and the cost", f"{sum(1 for r in _run_rows() if r['tier'] in ('T1', 'T2'))} model step(s) in this run.")]
    if eid == "memory":
        return [("Keeps the machine profiles and rules", "From Plant setup."),
                ("Keeps the learned correction", f"x{ss.bias:.3f}" + ("" if p["use_learning"] else " (not used: switched off in Forecast)")),
                ("Keeps every “no” with its reason", f"{sum(r['action'] == 'reject' for r in ss.audit.rows())} on record.")]
    if eid == "manager":
        return [("Gets the plan, the saving and the reasons", "On the Today screen."),
                ("Decides", "Approve, edit the start times, reject with a reason, or pick which rule to relax."),
                ("Every choice is logged", "Who, when and why, in the audit store."),
                ("Now", "Waiting for you." if pending else f"Status: {escape(str(s.get('status', 'no plan yet')))}.")]
    if eid == "runs":
        return [("Turns the approved plan into a work order", "Start and end for each machine."),
                ("People run the machines", "RightHour never switches equipment."),
                ("Now", "Work order issued." if ss.approved else "No approved plan yet.")]
    if eid == "loop_day":
        return [("Waits for the day's real readings", "From the meter file, an upload, or the live feed."),
                ("Asks what actually ran", "Any machine that started at a different hour."),
                ("Measures the real saving", "Meter readings against the usual schedule, adjusted for what ran."),
                ("Learns", "Moves the forecast correction toward what really happened."),
                ("Now", f"Verified {rs(ss.verify['verified_saving_rs'])}." if ss.verify else ("Ready to check." if ss.approved else "Approve a plan first."))]
    i = ARCH.info(eid)
    return [("What it does", i.get("what", "")), ("Gives", i.get("produces", ""))]


# ---- plain words for the activity log: the manager never needs a function name
ACTION_WORDS = {"validate_data": "Checked the meter readings", "forecast_load": "Forecast the day's load", "solar_forecast": "Forecast rooftop solar",
                "tariff_lookup": "Looked up the price bands", "tariff_parse": "Read a new price order", "tariff_sign_off": "Price order signed off",
                "constraint_parse": "Read a production note", "optimise": "Found the cheapest schedule", "policy_check": "Checked every rule",
                "orchestrate": "Planned the next step", "replan": "Tested which rule could flex", "explain": "Wrote the explanation",
                "relax_rule": "Manager chose a rule to relax", "work_order": "Issued the work order", "reject": "Manager rejected the plan",
                "verify_savings": "Checked the real saving", "learn": "Learned from the day", "setup_saved": "Settings saved",
                "data_loaded": "Meter data loaded", "safe_fallback": "Kept the usual schedule", "answer_question": "Answered a question"}
TIER_WORDS = {"T0": "Plain code", "T1": "Small AI model", "T2": "Large AI model", "human": "A person"}


def words(action: str) -> str:
    return ACTION_WORDS.get(action, action.replace("_", " ").capitalize())


def kpis(items: list[tuple[str, str, str]]) -> None:
    """A row of numbers the manager watches: (value, label, colour class)."""
    if items:
        html("<div class='rh-kpis'>" + "".join(f"<div><b class='{c}'>{v}</b><span>{l}</span></div>" for v, l, c in items) + "</div>")


def _line_chart(series: list[tuple[str, list[float], str, bool]], ytitle: str, height: int = 230) -> None:
    fig = _mini(height)
    x = [k / SLOTS_PER_HOUR for k in range(N_SLOTS)]
    for name, y, color, fill in series:
        fig.add_trace(go.Scatter(x=x, y=y, name=name, line=dict(color=color, width=2.2), fill="tozeroy" if fill else None,
                                 fillcolor="rgba(217,162,27,.14)" if fill else None))
    fig.update_yaxes(title=dict(text=ytitle, standoff=4))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _mini(height: int) -> go.Figure:
    fig = go.Figure()
    for s_ in ss.tariff["slots"]:
        fig.add_shape(type="rect", xref="x", yref="paper", x0=s_["start_h"], x1=s_["end_h"], y0=0, y1=1,
                      fillcolor=BAND[s_["type"]], opacity=0.07, line_width=0, layer="below")
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="#fff", plot_bgcolor="#fff",
                      font=dict(family=FONT, size=12, color=INK), hoverlabel=HOVER, legend=dict(orientation="h", y=-0.2, x=0),
                      xaxis=dict(range=[0, 24], tickvals=list(range(0, 25, 3)), ticktext=[f"{h:02d}:00" for h in range(0, 25, 3)],
                                 showgrid=False, tickfont=dict(color=MUTED)),
                      yaxis=dict(gridcolor="#EEF1F6", zeroline=False, rangemode="tozero", tickfont=dict(color=MUTED)))
    return fig


def _metrics(eid: str, rows: list[dict]) -> list[tuple[str, str, str]]:
    s, p, site = state or {}, ss.proc, ss.cfg["site"]
    plan, h = s.get("plan") or {}, ss.hist
    feed = LIVE.describe_feed(h) if h is not None else None
    allr = ss.audit.rows()
    n = lambda a: sum(r["action"] == a for r in allr)  # noqa: E731
    if eid == "meters":
        return [(f"{feed['n_rows']:,}", "readings", ""), (f"{feed['n_full_days']}", "full days", ""),
                (str(feed["last_ts"])[:10], "latest day", "s"), (f"x{h.attrs.get('scale', 1):.2f}", "scaled to the plant", "")] if feed else []
    if eid in ("factory", "rules"):
        return [(str(len(notes_for_run())), "notes for the day", ""), (str(len(s.get("fixed") or [])), "fixed batches read", ""),
                (str(len(s.get("extra") or [])), "new orders read", ""), (str(len(MACH["shiftable"])), "machines that can move", "")]
    if eid in ("bill", "price"):
        r_ = {b["type"]: b["rate"] for b in ss.tariff["slots"]}
        return [(f"₹{r_.get('peak', 0):.2f}", "peak rate per kVAh", "r"), (f"₹{r_.get('solar', 0):.2f}", "solar-hours rate", "g"),
                (f"{site['contract_demand_kva']:,}", "kVA contract demand", ""),
                (str(sum(i.level == "warning" for i in validate_tariff(ss.tariff))), "tariff warnings", "")]
    if eid == "orders":
        return [(str(len(ss.library)), "signed-off orders", ""), (str(n("tariff_parse")), "orders read so far", "")]
    if eid == "weather":
        pv = s.get("pv")
        return [(f"{site['rooftop_pv_kwp']:,}", "kWp on the roof", ""), (f"{max(pv['kw']):.0f}" if pv else "–", "peak solar kW", "g"),
                (f"{sum(pv['kw']) / SLOTS_PER_HOUR:,.0f}" if pv else "–", "solar kWh in the day", ""), ("On" if p["solar_on"] else "Off", "solar in the plan", "")]
    if eid == "collect":
        return [(f"{feed['n_full_days']}" if feed else "0", "days of meter history", ""), (str(len(notes_for_run())), "production notes", ""),
                (ss.w_weather, "weather setting", "s"), (f"{plan_date:%d %b}" if plan_date else "–", "day planned", "s")]
    if eid == "check":
        iss = s.get("data_issues") or []
        return [(str(sum(i["level"] != "ok" for i in iss)), "warnings", "r" if any(i["level"] == "error" for i in iss) else ""),
                (f"{p['spike_x']:g}x", "spike limit", ""), (f"{p['pf_min']:g}", "lowest normal power factor", "")]
    if eid == "store":
        return [(f"{len(allr):,}", "steps on record", ""), (str(len({r['run_id'] for r in allr})), "runs on record", ""), (str(len(rows)), "steps this run", "")]
    if eid == "orch":
        return [(str(len(rows)), "steps this run", ""), (str(sum(r["tier"] in ("T1", "T2") for r in rows)), "AI calls", ""),
                (f"${sum(r['cost_usd'] for r in rows):.4f}", "AI spend this run", ""), (str(s.get("status", "no plan")).replace("_", " "), "status", "s")]
    if eid == "forecast":
        fc = s.get("forecast")
        return [(f"{max(fc['kw']):,.0f}" if fc else "–", "peak base load kW", ""), (f"{sum(fc['kw']) / SLOTS_PER_HOUR:,.0f}" if fc else "–", "kWh in the day", ""),
                (f"x{ss.bias:.3f}" if p["use_learning"] else "off", "learned correction", ""), ("7 days" if p["method"] == "recent" else f"{p['weeks']} weeks", "history used", "")]
    if eid == "opt":
        prob = s.get("problem") or {}
        moved = sum(1 for m in prob.get("shiftable", []) if m.get("baseline_start_h") is not None and plan.get("schedule", {}).get(m["id"]) not in (None, m["baseline_start_h"]))
        return [(rs(s["saving_rs"]) if s.get("saving_rs") is not None else "–", "saving a day", "g"), (rs(plan["total_rs"]) if plan.get("total_rs") else "–", "plan cost", ""),
                (f"{moved}", "machines moved", ""), (f"{plan['peak_kva']:.0f}" if plan.get("peak_kva") else "–", "peak kVA", "")]
    if eid == "guard":
        ch = (s.get("policy") or {}).get("checks", [])
        pk = plan.get("peak_kva")
        return [(f"{sum(c['ok'] for c in ch)}/{len(ch)}" if ch else "–", "rules passed", "g"), (str(sum(not c["ok"] and c["hard"] for c in ch)), "hard rules broken", "r"),
                (f"{pk / site['contract_demand_kva'] * 100:.0f}%" if pk else "–", "of contract demand used", ""), (f"{p['margin_pct']}%", "your safety margin", "")]
    if eid == "router":
        return [(str(sum(r["tier"] == "T0" for r in rows)), "steps in plain code", ""), (str(sum(r["tier"] == "T1" for r in rows)), "small AI calls", ""),
                (str(sum(r["tier"] == "T2" for r in rows)), "large AI calls", ""), (f"${sum(r['cost_usd'] for r in rows):.4f}", "AI spend this run", "")]
    if eid == "memory":
        return [(f"x{ss.bias:.3f}", "learned correction", ""), (str(n("reject")), "rejected plans", ""), (str(n("setup_saved")), "setup changes", "")]
    if eid == "manager":
        return [(str(n("work_order")), "plans approved", "g"), (str(n("reject")), "plans rejected", ""), (str(n("relax_rule")), "rules relaxed", ""),
                ("Yes" if pending else "No", "waiting for you", "r" if pending else "")]
    if eid == "runs":
        return [(str(n("work_order")), "work orders issued", ""), ("Yes" if ss.approved else "No", "approved plan today", "")]
    if eid == "loop_no":
        return [(str(n("reject")), "rejections recorded", "")]
    if eid == "loop_day":
        ver = [json.loads(r["detail"]) for r in allr if r["action"] == "verify_savings"]
        tot = sum(v.get("verified_saving_rs") or 0 for v in ver)
        return [(str(len(ver)), "days checked", ""), (rs(tot) if ver else "–", "verified saving in all", "g"),
                (f"{ss.verify['realisation'] * 100:.0f}%" if ss.verify and ss.verify.get("realisation") else "–", "of expected saving realised", "")]
    return []


def box_results(eid: str, rows: list[dict]) -> None:
    """The numbers, charts and steps a manager watches for one box."""
    kpis(_metrics(eid, rows))
    s = state or {}
    if eid == "forecast" and s.get("forecast"):
        _line_chart([("Base load kW", s["forecast"]["kw"], NAVY, False), ("Solar kW", s["pv"]["kw"], AMBER_TEXT, True)], "kW")
    elif eid == "weather" and s.get("pv"):
        _line_chart([("Solar kW", s["pv"]["kw"], AMBER_TEXT, True)], "kW", 200)
    elif eid == "meters" and ss.hist is not None:
        d = ss.hist.set_index("ts")["kw"].resample("D").mean().tail(28) * 24
        fig = go.Figure(go.Bar(x=d.index, y=d.values, marker_color=NAVY_60, hovertemplate="%{x|%a %d %b}: %{y:,.0f} kWh<extra></extra>"))
        fig.update_layout(height=200, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="#fff", plot_bgcolor="#fff", font=dict(family=FONT, size=12),
                          yaxis=dict(title="kWh a day", gridcolor="#EEF1F6"), xaxis=dict(showgrid=False))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    elif eid in ("guard",) and s.get("policy"):
        html("".join(f"<p class='rh-p'><span class='rh-chip {'ok' if c['ok'] else ('bad' if c['hard'] else 'warn')}'>"
                     f"{'pass' if c['ok'] else ('fail' if c['hard'] else 'note')}</span>{c['rule']}</p>" for c in s["policy"]["checks"]))
    elif eid == "opt" and (s.get("plan") or {}).get("schedule"):
        sched = s["plan"]["schedule"]
        body = "".join(f"<tr><td>{escape(str(m['name']))}</td><td class='r'>{m['kw']:.0f}</td><td class='m'>{'new' if m.get('baseline_start_h') is None else '%02d:00' % m['baseline_start_h']}</td>"
                       f"<td class='m'><b>{sched[m['id']]:02d}:00</b></td></tr>" for m in s["problem"]["shiftable"])
        html(f"<table class='rh-log'><thead><tr><th>Machine</th><th class='r'>kW</th><th>Usual start</th><th>Planned start</th></tr></thead><tbody>{body}</tbody></table>")
    elif eid in ("bill", "price"):
        bars = "".join(f"<span style='width:{(b['end_h'] - b['start_h']) / 24 * 100:.2f}%;background:{BAND[b['type']]}'>{b['type']} ₹{b['rate']:.2f}</span>" for b in ss.tariff["slots"])
        html(f"<div class='rh-bands' role='img' aria-label='Price bands across the day'>{bars}</div><div class='rh-note'>00:00 to 24:00</div>")
    elif eid == "check":
        for i in s.get("data_issues") or []:
            html(f"<p class='rh-p'><span class='rh-chip {'ok' if i['level'] == 'ok' else 'warn'}'>{i['level']}</span>{i['text']}</p>")
    elif eid in ("rules", "factory") and run:
        html(read_back(s))
    html("<div class='rh-sub'>How it works, step by step</div>")
    html("<ol class='rh-flow'>" + "".join(f"<li><span class='n'>{i}</span><div><div class='t'>{t}</div><div class='d'>{d}</div></div></li>"
                                          for i, (t, d) in enumerate(_flow(eid), 1)) + "</ol>")
    src = ARCH.info(eid).get("comes_from", [])
    if src:
        html("<div class='rh-src2'><div><b>Where its information comes from</b><ul>" + "".join(f"<li>{x}</li>" for x in src) + "</ul></div></div>")


def box_activity(eid: str, rows: list[dict]) -> None:
    acts = ARCH.BY_ID[eid][4] if eid in ARCH.BY_ID else ({"reject"} if eid == "loop_no" else {"verify_savings", "learn"})
    src = ss.audit.rows() if eid in ("loop_no", "loop_day", "memory", "orders") else rows
    mine = [r for r in src if r["action"] in acts or (eid == "router" and r["tier"] in ("T1", "T2"))]
    if not mine:
        html("<div class='rh-note'>Nothing from this box yet.</div>")
        return
    body = "".join(f"<tr><td class='m'>{r['ts'][11:19]}</td><td>{TIER_WORDS.get(r['tier'], r['tier'])}</td><td><b>{escape(words(r['action']))}</b></td>"
                   f"<td>{escape(str(r['reason']))}</td><td class='r'>{'$%.4f' % r['cost_usd'] if r['cost_usd'] else ''}</td></tr>" for r in mine[-30:])
    html(f"<table class='rh-log'><thead><tr><th>Time</th><th>Done by</th><th>What happened</th><th>Why</th><th class='r'>AI cost</th></tr></thead><tbody>{body}</tbody></table>")


# ---- the controls of each box: every setting of the system lives in one of these
def ctl_day() -> None:
    c1, c2 = st.columns([1, 2])
    if d_range:
        c1.date_input("Day to plan", min_value=d_range[0], max_value=d_range[2], key="w_plan_day")
    c2.text_input("Goal for the agent", key="w_goal")
    st.selectbox("Solar forecast", list(WEATHER), key="w_weather")


def ctl_check() -> None:
    p = ss.proc
    c1, c2 = st.columns(2)
    p["spike_x"] = c1.slider("Flag a reading as a spike above (times the usual top load)", 1.5, 6.0, float(p["spike_x"]), 0.5, key="pk_spike")
    p["pf_min"] = c2.slider("Lowest normal power factor", 0.3, 0.95, float(p["pf_min"]), 0.05, key="pk_pf")


def ctl_forecast() -> None:
    p = ss.proc
    c1, c2 = st.columns(2)
    meth = c1.radio("Which past days to average", ["Same weekday", "Last 7 days"], index=0 if p["method"] == "weekday" else 1, horizontal=True, key="pk_method")
    p["method"] = "weekday" if meth == "Same weekday" else "recent"
    p["weeks"] = c2.slider("Weeks of the same weekday", 1, 8, int(p["weeks"]), key="pk_weeks", disabled=p["method"] == "recent")
    if ss.bias != 1.0 and st.button("Forget the learned correction", icon=":material/restart_alt:", key="pk_bias"):
        ss.bias = 1.0
        st.rerun()


def ctl_opt() -> None:
    p = ss.proc
    p["time_limit_s"] = st.slider("Time the solver may take (seconds)", 5, 120, int(p["time_limit_s"]), 5, key="pk_time",
                                  help="Longer can find a cheaper plan on a big problem. This plant solves in under a second.")
    html("<div class='rh-note'>Limits on what the optimiser may do (safety margin, monthly peak, how many machines move, smallest saving) "
         "are set in <b>Guardrails</b>.</div>")


def ctl_guard() -> None:
    p, cd = ss.proc, ss.cfg["site"]["contract_demand_kva"]
    c1, c2 = st.columns(2, gap="large")
    with c1:
        p["margin_pct"] = st.slider("Safety margin below the contract demand (%)", 0, 20, int(p["margin_pct"]), key="g_margin",
                                    help="The plan must stay this far under the contract demand.")
        html(f"<div class='rh-note' style='margin:-6px 0 12px'>The plan may draw at most <b>{cd * (1 - p['margin_pct'] / 100):,.0f} kVA</b> (contract {cd:,} kVA).</div>")
        p["block_new_peak"] = st.toggle("Never set a new monthly peak", value=bool(p["block_new_peak"]), key="g_peak",
                                        help="On: the plan never goes above this month's highest demand. Off: it may, if the plan is still cheaper with the extra demand charge.")
    with c2:
        lim = st.toggle("Limit how many machines move in a day", value=p["max_moves"] is not None, key="g_lim",
                        help="Fewer moves means less disruption on the floor, and maybe a smaller saving.")
        nm = len(MACH["shiftable"])
        p["max_moves"] = st.slider("Most machines moved", 0, nm, min(int(p["max_moves"] if p["max_moves"] is not None else 2), nm), key="g_moves") if lim else None
        p["min_saving_rs"] = st.number_input("Smallest saving worth changing the schedule (₹ a day)", 0, 1_000_000, int(p["min_saving_rs"]), 250, key="g_min",
                                             help="Below this, the agent keeps the usual schedule.")
    html("<div class='rh-locked'><b>Always on. These cannot be switched off:</b><ul>"
         "<li>The plan never goes above the contract demand.</li><li>Fixed batches never move or stop midway.</li>"
         "<li>Every machine runs inside its allowed hours.</li><li>Crew rules hold (edit them in <b>Factory systems</b>).</li>"
         "<li>A person approves every plan before anything runs.</li></ul></div>")


def ctl_store() -> None:
    df = pd.DataFrame(ss.audit.rows())
    html(f"<div class='rh-note'>Every step of every run is kept: {len(df):,} rows.</div>")
    if len(df):
        st.download_button("Download the full record (CSV)", df.to_csv(index=False).encode(), "righthour_audit.csv", "text/csv", icon=":material/download:")


def ctl_manager() -> None:
    if not run:
        html("<div class='rh-note'>Generate a plan first.</div>")
    elif pending and pending["type"] == "choose_relaxation":
        problem_panel("pane")
    elif pending and pending["type"] == "approval":
        approval_controls("pane")
    else:
        html(f"<div class='rh-note'>Nothing is waiting for you. Status: <b>{str(state.get('status', '')).replace('_', ' ')}</b>.</div>")


def ctl_runs() -> None:
    if not ss.approved:
        html("<div class='rh-note'>No approved plan yet. Approve one in <b>Manager decides</b>.</div>")
        return
    ast = ss.approved[1]
    st.dataframe(pd.read_csv(io.BytesIO(work_order_csv(ast))), hide_index=True, width="stretch")
    st.download_button("Download the work order (CSV)", work_order_csv(ast), f"work_order_{ast['forecast']['date']}.csv", "text/csv",
                       key="wo_pane", icon=":material/download:")


def ctl_none(text: str):
    return lambda: html(f"<div class='rh-note'>{text}</div>")


CONTROLS = {"meters": lambda: meter_controls("cr"), "factory": lambda: (notes_editor(), pane_machines()), "rules": notes_editor,
            "bill": lambda: pane_bill([]), "price": lambda: pane_bill([]), "orders": lambda: pane_orders([]),
            "weather": lambda: (st.selectbox("Solar forecast", list(WEATHER), key="w_weather", disabled=not ss.proc["solar_on"]), pane_weather([])),
            "collect": ctl_day, "orch": ctl_day, "check": ctl_check, "store": ctl_store, "forecast": ctl_forecast, "opt": ctl_opt,
            "guard": ctl_guard, "router": lambda: pane_router([]), "memory": lambda: pane_memory([]), "manager": ctl_manager, "runs": ctl_runs,
            "loop_no": ctl_none("No settings yet. Each rejection is saved with its reason. Turning a reason into a new rule is not built yet: "
                                "for now, add the rule in <b>Factory systems</b>."),
            "loop_day": lambda: pane_loop_day([])}
PANES = CONTROLS


# the on/off switch on boxes that can be left out of a run
SWITCH = {"weather": ("solar_on", "Use rooftop solar", "Off: the plan counts no solar power."),
          "forecast": ("use_learning", "Use the learned correction", "Off: the forecast is the plain average of past days."),
          "rules": ("notes_on", "Read the day's production notes", "Off: notes are ignored, so batches and orders are not planned."),
          "factory": ("notes_on", "Read the day's production notes", "Off: notes are ignored, so batches and orders are not planned.")}


def box_panel(eid: str, rows: list[dict], astates: dict) -> None:
    info = ARCH.info(eid)
    stt = astates.get(eid, "wait")
    tag = {"ran": "<span class='rh-tag ok'>Done this run</span>", "ask": "<span class='rh-tag ask'>Your turn</span>",
           "part": "<span class='rh-tag part'>Logged only</span>"}.get(stt, "<span class='rh-tag'>Not run yet</span>")
    with st.container(border=True, key="boxpanel"):
        c1, c2 = st.columns([3, 1.3], vertical_alignment="top")
        c1.markdown(f"<div class='rh-ct'><span class='i'>{ICONS['chip']}</span><div><div class='t'>{info['title']} {tag}</div>"
                    f"<div class='s'>{info['what']}</div></div></div>", unsafe_allow_html=True)
        if eid in SWITCH:
            k, label, hint = SWITCH[eid]
            ss.proc[k] = c2.toggle(label, value=ss.proc[k], key=f"sw_{k}_{eid}", help=hint)
        if run and run.get("proc", ss.proc) != ss.proc:
            html(f"<div class='rh-alert'><b>{ICONS['alert']}Settings changed.</b> The next plan uses them. "
                 "Press <b>Generate plan</b> to see the effect.</div>")
        t1, t2, t3 = st.tabs([":material/monitoring: Results", ":material/tune: Controls", ":material/receipt_long: Activity"])
        with t1:
            box_results(eid, rows)
        with t2:
            _ = CONTROLS[eid]()   # assigned, so Streamlit does not print the return value
            if ss.proc != PROCESS_DEFAULTS and st.button("Back to the standard settings", icon=":material/restart_alt:", key=f"pk_reset_{eid}", type="tertiary"):
                ss.proc = dict(PROCESS_DEFAULTS)
                st.rerun()
        with t3:
            box_activity(eid, rows)


def control_room() -> None:
    rows = ss.audit.rows(ctx.run_id) if ctx else []
    astates = ARCH.element_states(rows, pending, ss.verify is not None, ss.hist is not None)
    html("<div class='rh-legend-hld'><span><span class='rh-tag ok'>Done</span> ran in this run</span>"
         "<span><span class='rh-tag ask'>Your turn</span> waiting for you</span><span><span class='rh-tag'>No tag</span> not run yet</span></div>")
    with st.container(border=True, key="diagram"):
        picked = ARCH.component(astates, ss.sel_box, key=f"hld_{ss.get('hld_room_nonce', 0)}")
    if picked and picked != ss.sel_box:
        ss.sel_box = picked
        st.rerun()
    eid = ss.sel_box if ss.sel_box in PANES else "orch"
    box_panel(eid, rows, astates)



# ------------------------------------------------------------------ plan helpers
def read_back(state: dict) -> str:
    """Show how the agent understood each note, so a wrong reading is caught before approval."""
    rows = []
    for f in state.get("fixed", []):
        rows.append(f"<tr><td>{f['name']}</td><td>Fixed batch</td><td>{f['start_h']:02d}:00 to {f['end_h']:02d}:00</td><td class='r'>{f['kw']:.0f} kW</td></tr>")
    ex = state.get("extra") or []
    for e in (ex if isinstance(ex, list) else [ex]):
        rows.append(f"<tr><td>{escape(str(e['name']))}</td><td>New order</td><td>{e['duration_h']:g} h inside {int(e['earliest_h']):02d}:00 to {int(e['latest_finish_h']):02d}:00</td><td class='r'>{e['kw']:.0f} kW</td></tr>")
    if not rows:
        return ""
    return (f"<div class='rh-card'><h4>{ICONS['person']}How the agent read your notes</h4><table class='rh-log'><thead><tr><th>Name</th><th>Kind</th><th>Hours</th>"
            f"<th class='r'>Load</th></tr></thead><tbody>{''.join(rows)}</tbody></table><p class='rh-note' style='margin-top:6px'>Wrong? Fix the note in Upload data and generate again.</p></div>")


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
        html("<div class='rh-note'>No flexible rule makes a valid plan. Add a looser window in Plant setup, or change the order.</div>")
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
        with st.container(border=True, key="decide"):
            card_title("person", "Your decision", "A person approves every plan")
            if status == "edit_blocked":
                er = state["edit_result"]
                html(f"<div class='rh-alert'><b>{ICONS['alert']}Edited plan blocked by the policy check.</b> "
                     + " ".join(f"Fails: {f}." for f in er["failed"]) + " The original plan is still waiting for approval.</div>")
            if status == "work_order_issued":
                html(f"<div class='rh-ok'><b>{ICONS['check']}Approved. Work order issued.</b> Operators run the machines at the "
                     f"approved times; RightHour never switches equipment. After the day, open <b>Next-day check</b>.</div>")
                st.download_button("Download the work order (CSV)", work_order_csv(state), f"work_order_{state['forecast']['date']}.csv", "text/csv",
                                   width="stretch", key="wo_plan", icon=":material/download:")
            elif status == "rejected":
                html(f"<div class='rh-note'>Plan rejected. Current schedule kept. Reason on record: {state.get('approval', {}).get('reason') or 'none given'}.</div>")
            if pending and pending["type"] == "approval":
                approval_controls("plan")
        with st.container(border=True):
            card_title("chip", "Why this plan")
            html(f"<p class='rh-p'>{state.get('explanation', '')}</p>")
        checks = state.get("policy", {}).get("checks", [])
        with st.container(border=True):
            bad = sum(not c["ok"] for c in checks)
            card_title("shield", "Rules checked", f"{len(checks) - bad} of {len(checks)} passed" + (f", {bad} need a look" if bad else ""))
            with st.expander("See every rule", expanded=bool(bad)):
                html("".join(f"<p class='rh-p'><span class='rh-chip {'ok' if c['ok'] else ('bad' if c['hard'] else 'warn')}'>"
                             f"{'pass' if c['ok'] else ('fail' if c['hard'] else 'note')}</span>{c['rule']}</p>" for c in checks))
    html(read_back(state))


def plan_tab() -> None:
    if run and (run.get("cfg_version") != ss.cfg_version or run.get("proc", ss.proc) != ss.proc):
        html(f"<div class='rh-card alert'><h4>{ICONS['alert']}Settings changed since this plan</h4><p>Press <b>Generate plan</b> to plan with the new settings.</p></div>")
    if run_error:
        html(f"<div class='rh-card alert'><h4>{ICONS['alert']}{run_error}</h4><p>Check the production notes in Upload data, the day to plan and Plant setup, then try again.</p></div>")
    if ss.hist is None:
        html(f"""<div class='rh-empty'><h2>Load your meter data to start</h2>
<p>RightHour needs the plant's 15-minute meter readings. Upload a CSV, or use the steel plant data or the sample.</p></div>""")
        st.button("Go to Upload data", icon=":material/upload_file:", type="primary", on_click=goto, args=("Upload data",), key="empty_up")
        return
    if not run:
        if not run_error:
            html(f"<div class='rh-card panel'><h4>{ICONS['check']}Data loaded</h4><p>Press <b>Generate plan</b>.</p></div>")
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
        f"<tr><td class='m'>{r['ts'][11:19]}</td><td>{chip(r['tier'])}</td><td>{escape(str(r['agent']))}</td><td><b>{escape(words(r['action']))}</b></td>"
        f"<td>{escape(str(r['reason']))}</td><td>{escape(str(r['mode']))}</td><td class='r'>{'$%.4f' % r['cost_usd'] if r['cost_usd'] else ''}</td></tr>"
        for r in rows)
    html(f"<table class='rh-log'><thead><tr><th>Time</th><th>Tier</th><th>Agent</th><th>Step</th><th>Why this tier</th>"
         f"<th>Mode</th><th class='r'>Cost</th></tr></thead><tbody>{body}</tbody></table>")


def audit_tab() -> None:
    rows = ss.audit.rows()
    df = pd.DataFrame(rows)
    if len(df):
        view = pd.DataFrame({"Time": df["ts"].str.slice(0, 19).str.replace("T", " "), "Run": df["run_id"], "Who": df["agent"],
                             "What happened": df["action"].map(words), "Done by": df["tier"].map(lambda t: TIER_WORDS.get(t, t)),
                             "Why": df["reason"], "AI cost ($)": df["cost_usd"].round(4)})
        st.dataframe(view.iloc[::-1], width="stretch", hide_index=True, height=460)
    if len(df):
        st.download_button("Download audit trail (CSV)", df.to_csv(index=False).encode(), "righthour_audit.csv", "text/csv")


# ------------------------------------------------------------------ the Manager console: every input, all telemetry, every output
# ------------------------------------------------------------------ the screens: one job each
def page_header(title: str, sub: str) -> None:
    html(f"<header class='rh-ph'><h1>{title}</h1><p>{sub}</p></header>")


def card_title(icon: str, title: str, sub: str = "") -> None:
    html(f"<div class='rh-ct'><span class='i'>{ICONS[icon]}</span><div><div class='t'>{title}</div>"
         + (f"<div class='s'>{sub}</div>" if sub else "") + "</div></div>")


def _run_rows() -> list[dict]:
    return ss.audit.rows(ctx.run_id) if ctx else []


def work_order_card() -> None:
    if not ss.approved:
        return
    ast = ss.approved[1]
    with st.container(border=True):
        card_title("clipboard", "Work order", f"For {ast['forecast']['weekday']} {ast['forecast']['date']}. People switch the machines.")
        wo = pd.read_csv(io.BytesIO(work_order_csv(ast)))
        body = "".join(f"<tr><td>{escape(str(r.machine))}</td><td class='m'>{r.start} to {r.end}</td><td>{escape(str(r.note))}</td></tr>" for r in wo.itertuples())
        html(f"<table class='rh-log'><thead><tr><th>Machine</th><th>Runs</th><th>Note</th></tr></thead><tbody>{body}</tbody></table>")
        st.download_button("Download the work order (CSV)", work_order_csv(ast), f"work_order_{ast['forecast']['date']}.csv", "text/csv",
                           key="wo_today", icon=":material/download:")


def page_today() -> None:
    page_header("Today's plan", f"{escape(str(SITE['name']))} · {escape(str(ss.tariff['name']))}")
    next_step(state if run else None, pending, ss.verify is not None)
    run_controls()
    step_strip(state if run else None, pending, ss.verify is not None)
    plan_tab()
    work_order_card()


def page_data() -> None:
    page_header("Upload data", "Everything the agent reads before it plans: meter readings, the day's production notes and price orders.")
    left, right = st.columns([1.35, 1], gap="medium")
    with left, st.container(border=True, height="stretch"):
        card_title("upload", "Meter readings", "15-minute readings from the plant's main meter")
        h = ss.hist
        if h is not None:
            f = LIVE.describe_feed(h)
            html(f"<div class='rh-kv three'><div><b>{f['n_rows']:,}</b>readings</div><div><b>{f['n_full_days']}</b>full days</div>"
                 f"<div><b>{str(f['last_ts'])[:10]}</b>latest day</div></div>"
                 f"<div class='rh-note' style='margin-bottom:10px'>{escape(str(h.attrs.get('source', '')))}</div>")
        meter_controls("d")
    with right:
        with st.container(border=True):
            card_title("pulse", "Data health", "Checked every time data loads")
            issues = state.get("data_issues") if run else (D.validate_load(ss.hist) if ss.hist is not None else [])
            for i in issues or []:
                html(f"<div class='rh-note' style='margin:2px 0 6px'><span class='rh-chip {'ok' if i['level'] == 'ok' else ('bad' if i['level'] == 'error' else 'warn')}'>{i['level']}</span>{i['text']}</div>")
            if not issues:
                html("<div class='rh-note'>Load meter data to see its checks.</div>")
        with st.container(border=True):
            card_title("bolt", "Live feed", "New days arrive one at a time, like a real meter")
            live_controls()
            if ss.live_active:
                if st.button("Release the next day now", disabled=not ss.live_future, width="stretch", key="d_release"):
                    ss.hist, ss.live_future, day = LIVE.release_next_day(ss.hist, ss.live_future)
                    live_note(f"meter feed delivered {day}")
                    check_approved_plan()
                    ss.trigger_plan = True
                    st.rerun()
                html(f"<div class='rh-note'>Or drop a CSV in <span class='mono'>{INCOMING.name}/</span>; it is picked up within seconds.</div>")
            if ss.live_log:
                html("<div class='rh-note mono' style='margin-top:6px'>" + "<br>".join(ss.live_log[:6]) + "</div>")
    with st.container(border=True):
        card_title("notes", "Production notes for the day", "What the supervisor says: fixed batches and new orders")
        if plan_date:
            notes_editor()
        else:
            html("<div class='rh-note'>Load meter data first, so there is a day to plan.</div>")
    with st.container(border=True):
        card_title("doc", "New price order", "Read a tariff order from the regulator, check it, sign it off")
        pane_orders([])


def page_setup() -> None:
    page_header("Plant setup", "Set once, change when the plant changes. Saved settings are used by the next plan.")
    t1, t2, t3, t4 = st.tabs([":material/precision_manufacturing: Machines and crew rules", ":material/currency_rupee: Prices and contract",
                              ":material/wb_sunny: Rooftop solar", ":material/smart_toy: AI model"])
    with t1:
        pane_machines()
    with t2:
        pane_bill([])
    with t3:
        pane_weather([])
    with t4:
        html("<p class='rh-lead'>The AI model only writes and reads words: it reads notes and explains the plan. "
             "Code does every calculation. Offline mode needs no key.</p>")
        with st.container(border=True, key="modelbox"):
            model_controls()


def page_control_room() -> None:
    page_header("Control room", "The agents' design, lit up from this run. Click a box to see its data, its steps and its settings.")
    control_room()


def page_check() -> None:
    page_header("Next-day check", "After the day runs, compare the real meter readings with the plan. The agent learns from the gap.")
    if not ss.approved:
        html(f"<div class='rh-empty'><h2>Nothing to check yet</h2><p>Approve a plan on Today first. After the day, come back here "
             "to see the real saving.</p></div>")
        st.button("Go to Today", icon=":material/today:", type="primary", on_click=goto, args=("Today",), key="chk_today")
    else:
        with st.container(border=True):
            pane_loop_day([])
    hist_rows = [r for r in ss.audit.rows() if r["action"] == "verify_savings"]
    if hist_rows:
        with st.container(border=True):
            card_title("history", "Savings history", "Every checked day")
            body = ""
            for r in hist_rows[-12:][::-1]:
                d = json.loads(r["detail"])
                body += (f"<tr><td class='m'>{r['ts'][:16].replace('T', ' ')}</td><td class='r'>{rs(d.get('projected_saving_rs') or 0)}</td>"
                         f"<td class='r'>{rs(d.get('verified_saving_rs') or 0)}</td></tr>")
            html(f"<table class='rh-log'><thead><tr><th>Checked</th><th class='r'>Expected</th><th class='r'>Verified</th></tr></thead><tbody>{body}</tbody></table>")


def page_history() -> None:
    page_header("History", "Every step the agents took, which tier did it, and what each decision cost.")
    html("<div class='rh-key'><span><span class='rh-chip t0'>T0</span>plain code, no AI</span><span><span class='rh-chip t1'>T1</span>small AI model</span>"
         "<span><span class='rh-chip t2'>T2</span>large AI model, only when needed</span><span><span class='rh-chip human'>human</span>a person</span></div>")
    t1, t2 = st.tabs([":material/list: This run", ":material/database: All runs"])
    with t1:
        log_tab()
    with t2:
        audit_tab()


# ------------------------------------------------------------------ Ask RightHour: one assistant with every fact on screen
def ask_facts() -> dict:
    s = state or {}
    site, t = ss.cfg["site"], ss.tariff
    plan, base = s.get("plan") or {}, s.get("baseline") or {}
    prob = s.get("problem") or {}
    sched = plan.get("schedule") or {}
    moves = []
    for m in prob.get("shiftable", []):
        if m["id"] in sched:
            b = m.get("baseline_start_h")
            moves.append(f"{m['name']} {('usually %02d:00, ' % b) if b is not None else 'new order, '}planned {sched[m['id']]:02d}:00")
    checks = (s.get("policy") or {}).get("checks", [])
    all_rows = ss.audit.rows()
    ver = [r for r in all_rows if r["action"] == "verify_savings"][::-1]
    vtxt, vtot = [], 0.0
    for r in ver[:15]:
        d = json.loads(r["detail"])
        v = d.get("verified_saving_rs") or 0
        vtot += v
        vtxt.append(f"- checked {r['ts'][:16].replace('T', ' ')}: expected {rs(d.get('projected_saving_rs') or 0)}, verified {rs(v)}")
    dec = [f"- {r['ts'][:16].replace('T', ' ')} {r['action']}: {r['reason']}" for r in all_rows if r["action"] in ("work_order", "reject", "relax_rule")][-10:][::-1]
    steps = [f"- {r['ts'][11:19]} {r['tier']} {r['agent']}: {r['action']} ({r['reason']})" for r in _run_rows()]
    h = ss.hist
    feed = LIVE.describe_feed(h) if h is not None else None
    waiting = ("choose which rule to relax" if pending and pending["type"] == "choose_relaxation" else
               "approve or reject the plan" if pending else "nothing")
    p = ss.proc
    fc_days = "the last 7 days" if p["method"] == "recent" else f"the same weekday over {p['weeks']} weeks"
    return {"plant": site["name"], "tariff": t["name"], "contract_kva": site["contract_demand_kva"], "month_max_kva": site["month_to_date_max_kva"],
            "pv_kwp": site["rooftop_pv_kwp"], "demand_charge": f"₹{t['demand_charge_per_kva_month']:,.0f} per kVA per month",
            "bands": "; ".join(f"{b['type']} {b['start_h']:02d}:00-{b['end_h']:02d}:00 ₹{b['rate']:.2f}/kVAh" for b in t["slots"]),
            "data": (f"{feed['n_rows']:,} readings, {feed['n_full_days']} full days, {str(feed['first_ts'])[:10]} to {str(feed['last_ts'])[:16]}. "
                     f"{h.attrs.get('source', '')}. Live feed {'on' if ss.live_active else 'off'}.") if feed else "No meter data loaded.",
            "plan_for": f"{s['forecast']['weekday']} {s['forecast']['date']}" if s.get("forecast") else (f"{plan_date}" if plan_date else "not set"),
            "status": str(s.get("status") or "").replace("_", " ") or ("no plan generated yet" if not run else "plan ready"), "waiting": waiting,
            "saving": f"{rs(s['saving_rs'])} a day (about {lakh(s['saving_rs'] * site['working_days_per_year'])} a year)" if s.get("saving_rs") is not None else "none claimed",
            "plan_cost": rs(plan["total_rs"]) if plan.get("total_rs") is not None else "n/a",
            "usual_cost": rs(base["total_rs"]) if base.get("total_rs") is not None else "n/a",
            "peak": f"{plan['peak_kva']:.0f} kVA" if plan.get("peak_kva") is not None else "n/a",
            "schedule": "; ".join(moves) or "no schedule", "explanation": s.get("explanation") or "no plan yet",
            "checks": "; ".join(f"{'pass' if c['ok'] else 'FAIL'}: {c['rule']}" for c in checks) or "none yet",
            "process": (f"forecast from {fc_days}, "
                        f"learned correction {'x%.3f' % ss.bias if p['use_learning'] else 'off'}, solar {'on' if p['solar_on'] else 'off'}, "
                        f"notes reader {'on' if p['notes_on'] else 'off'}"),
            "ai": LLM.label, "verified": "\n".join(vtxt), "verified_total": rs(vtot) if ver else "", "verified_n": len(ver[:15]),
            "decisions": "\n".join(dec), "steps": "\n".join(steps)}


def ask_answer(q: str) -> str:
    f = ask_facts()
    if not LLM.live:
        return ASK.offline_answer(q, f)
    model = MODELS[ss.provider]["T2"]
    history = "".join(f"{m['role']}: {m['text']}\n" for m in ss.chat[-6:])
    try:
        text, tin, tout = LLM.complete(model, ASK.SYSTEM + "\n\n" + ASK.context_text(f), f"{history}manager: {q}", 700)
    except Exception as e:  # noqa: BLE001
        return f"The AI model did not answer ({e}). Here is what plain code can tell you:\n\n" + ASK.offline_answer(q, f)
    ss.audit.log(ctx.run_id if ctx else "ask", "Ask RightHour", "answer_question", tier="T2", model=model, mode=LLM.mode,
                 reason="free-form question: large model", tokens_in=tin, tokens_out=tout,
                 cost_usd=cost_usd(model, tin, tout), detail={"question": q})
    return text


ss.setdefault("chat", [])


@st.dialog("Ask RightHour", width="large")
def ask_dialog() -> None:
    html(f"<div class='rh-note' style='margin-top:-6px'>Knows the plan, the prices, every decision and every checked saving. "
         f"{'Answers with the large model, ' + escape(MODELS[ss.provider]['T2']) + '.' if LLM.live else 'Offline: plain code answers status, savings and history. Add an AI key in Plant setup for any question.'}</div>")
    q = None
    if not ss.chat:
        cols = st.columns(2)
        for i, sug in enumerate(ASK.SUGGESTIONS):
            if cols[i % 2].button(sug, key=f"sug_{i}", width="stretch"):
                q = sug
    log = st.container(height=380, border=False, key="chatlog")
    with log:
        for m in ss.chat:
            with st.chat_message("user" if m["role"] == "manager" else "assistant", avatar=":material/person:" if m["role"] == "manager" else ":material/bolt:"):
                st.markdown(m["text"])
    q = st.chat_input("Ask about the plan, savings, history...", key="ask_in") or q
    if q:
        with log:
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(q)
            with st.chat_message("assistant", avatar=":material/bolt:"), st.spinner("Thinking..."):
                a = ask_answer(q)
                st.markdown(a)
        ss.chat += [{"role": "manager", "text": q}, {"role": "assistant", "text": a}]
    if ss.chat and st.button("Clear the conversation", type="tertiary", icon=":material/delete:", key="ask_clear"):
        ss.chat = []
        st.rerun(scope="fragment")


# ------------------------------------------------------------------ draw
sidebar()
with st.container(key="askfab"):
    if st.button("Ask RightHour", icon=":material/forum:", type="primary", key="ask_open"):
        ask_dialog()
{"Today": page_today, "Upload data": page_data, "Plant setup": page_setup, "Control room": page_control_room,
 "Next-day check": page_check, "History": page_history}[ss.view]()
