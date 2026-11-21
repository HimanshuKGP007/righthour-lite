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
import re
import os
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from righthour import architecture as ARCH
from righthour import act as ACT
from righthour import ask as ASK
from righthour import config as CFG
from righthour import data as D
from righthour import live as LIVE
from righthour import agents as A
from righthour.agents import PROCESS_DEFAULTS, Context, lakh, parse_tariff, rs, verify_and_learn
from righthour.audit import AuditLog
from righthour.graph import resume, start
from righthour import memory as MEM
from righthour.llm import KEY_ENV, LABEL, PROVIDERS, LLMClient, default_provider, number_guard, numbers_in, suggest_models
from righthour.router import MODELS, PRICES, cost_usd, set_models, set_provider
from righthour.tariff import N_SLOTS, PRICE_RULES, SLOTS_PER_HOUR, price_gauges, validate_tariff
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
try:                                       # a key in .streamlit/secrets.toml (local) or Cloud secrets turns the AI on
    for _env in KEY_ENV.values():
        if _env and not __import__("os").environ.get(_env) and _env in st.secrets:
            __import__("os").environ[_env] = str(st.secrets[_env])
except Exception:  # noqa: BLE001  no secrets file is fine: the app runs offline
    pass

# ------------------------------------------------------------------ state
# every clock in the app shows the plant's local time, not the server's (Streamlit Cloud runs on UTC)
os.environ["TZ"] = os.environ.get("RIGHTHOUR_TZ", "Asia/Kolkata")
if hasattr(time, "tzset"):
    time.tzset()
ss = st.session_state


@st.cache_resource
def memory_store() -> MEM.MemoryStore:
    return MEM.MemoryStore()


STORE = memory_store()
if not ss.get("plant"):                        # a new tab or a refresh starts on the demo plant; sign in again to load a plant's memory
    ss.plant, ss.token = MEM.DEMO, None         # (no session token in the link: a shared link must never sign anyone in)
if ss.get("loaded_for") != ss.plant:           # load everything this plant's agent has learned
    mem = STORE.load(ss.plant)
    base = CFG.load() if ss.plant == MEM.DEMO else CFG.defaults()
    if ss.plant != MEM.DEMO:
        base = CFG._normalise(base)
    ss.cfg = mem.get("cfg") or base
    ss.cfg["machines"].setdefault("learned_rules", [])
    ss.tariff = ss.cfg["tariff"]
    ss.bias = float(mem.get("bias", 1.0))
    ss.bias_history = mem.get("bias_history", [])
    ss.library = mem.get("library", {})
    ss.proc = {**PROCESS_DEFAULTS, **{k: v for k, v in mem.get("proc", {}).items() if k in PROCESS_DEFAULTS}}
    ss.price_rules = {**PRICE_RULES, **{k: v for k, v in mem.get("price_rules", {}).items() if k in PRICE_RULES}}
    ss.plant_name = "Demo plant" if ss.plant == MEM.DEMO else STORE.plant_name(ss.plant)
    ss.loaded_for = ss.plant


def persist() -> None:
    """Saves what this plant's agent knows. Runs at the start and the end of every screen draw."""
    if ss.get("loaded_for") != ss.plant:
        return
    hist = ss.get("bias_history", [])
    if not hist or abs(hist[-1]["bias"] - ss.bias) > 1e-9:
        if hist or ss.bias != 1.0:
            ss.bias_history = hist + [{"ts": f"{dt.datetime.now():%Y-%m-%d %H:%M}", "bias": round(ss.bias, 4)}]
    try:
        STORE.save(ss.plant, {"cfg": ss.cfg, "bias": ss.bias, "bias_history": ss.get("bias_history", []), "library": ss.library,
                              "proc": ss.proc, "price_rules": ss.get("price_rules", PRICE_RULES)})
    except Exception as e:  # noqa: BLE001  memory must never stop the planner
        ss.mem_err = str(e)


def save_plant_file(cfg: dict) -> None:
    """Only the shared demo plant uses plant_config.json. A signed-in plant's settings live only in its own memory."""
    if ss.plant == MEM.DEMO:
        CFG.save(cfg)


def switch_plant(code: str, token: str | None) -> None:
    """A new plant signs in, or the manager signs out: start clean, keep only typed AI keys."""
    for k in list(ss.keys()):
        if not (str(k).startswith(("key_", "m_T")) or k in ("setup_done", "setup_checked", "w_provider", "models", "model_ok")):
            del ss[k]
    ss.plant, ss.token = code, token


ss.setdefault("cfg", CFG.load())            # the plant's own settings: site, tariff, machines, crew rules
ss.setdefault("cfg_version", 0)
SITE, MACH = ss.cfg["site"], ss.cfg["machines"]
ss.setdefault("hist", None)
ss.setdefault("bias", 1.0)
if ss.plant == MEM.DEMO:
    ss.setdefault("audit", AuditLog(Path("righthour_audit.sqlite")))
else:                                          # each plant has its own audit trail
    Path("plant_audit").mkdir(exist_ok=True)
    ss.setdefault("audit", AuditLog(Path("plant_audit") / f"{ss.plant}.sqlite"))
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
ss.setdefault("proc", dict(PROCESS_DEFAULTS))
ss.setdefault("today_rules", [])   # rules from chat that hold for this plan day only, until the manager says "from now on"
ss.setdefault("confirm_rule", None)
ss.setdefault("whatif", None)       # a change the agent tried but the manager has not yet set as the plan
ss.setdefault("pins", {})
persist()      # machines the manager pinned to an hour: {machine id: start hour}   # how each diagram box works; changed in the Control room
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


HOURS = [f"{h:02d}:00" for h in range(25)]      # every time a manager picks is a clock time


def to_t(v) -> str | None:
    try:
        return None if v is None or v == "" or v != v else f"{int(float(v)):02d}:00"
    except (TypeError, ValueError):
        return None


def from_t(v) -> int | None:
    try:
        return None if v is None or v == "" or v != v else int(str(v)[:2])
    except (TypeError, ValueError):
        return None


def time_col(label: str, help_: str | None = None, required: bool = False):
    return st.column_config.SelectboxColumn(label, options=HOURS, required=required, help=help_)


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
       ("Guardrails", ":material/shield:", "Every guardrail, where it comes from, and whether it held"),
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
           "key_Groq", "key_Grok", "key_Claude", "view_nav", "box_tab", "cr_view"]:
    if _k in ss:                       # keep values alive while their widget is drawn on another screen
        ss[_k] = ss[_k]

# the day to plan: reset to a sensible default whenever the data changes
plan_date, has_actuals, d_range, d_err = None, False, None, None
if ss.hist is not None:
    try:
        d_range = D.plan_date_range(ss.hist, int(ss.proc.get("min_history_days", 14)))
        tag = f"{_hist_key()}_{ss.live_active}"
        if ss.pd_for != tag or not (d_range[0] <= ss.get("w_plan_day", d_range[0]) <= d_range[2]):
            ss.w_plan_day, ss.pd_for = _default_day(d_range[1], d_range[2]), tag
        plan_date = ss.w_plan_day
        if ss.get("pins_for") != plan_date:     # kept machines belong to one day
            ss.pins, ss.pins_for, ss.today_rules, ss.confirm_rule = {}, plan_date, [], None
        has_actuals = D.day_actuals(ss.hist, plan_date) is not None
    except ValueError as e:
        d_err = str(e)
weather, goal = ss.w_weather, ss.w_goal
ss.provider = ss.w_provider or ss.provider
ss.setdefault("llm_err", {"text": ""})   # this session's newest AI error
LLM = LLMClient(ss.provider, api_key=ss.get(f"key_{ss.provider}") or None, error_sink=ss.llm_err)
ss.llm_sig = (ss.provider, ss.get(f"key_{ss.provider}") or None)
if not ss.get("setup_done") and not ss.get("setup_checked"):   # on open: if the model answers, skip the setup window
    ss.setup_checked = True
    if LLM.live:
        try:
            LLM.complete(MODELS[ss.provider]["T1"], "Reply with the single word OK.", "ping", 8)
            ss.setup_done, ss.model_ok = True, f"Connected. {MODELS[ss.provider]['T1']} answered."
        except Exception as e:  # noqa: BLE001  the window opens and shows what failed
            ss.model_ok = f"Failed: {e}"
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
        ss.hist, ss.live_future = LIVE.split_for_live(ss.hist, int(ss.proc.get("live_hold_days", 7)))
        ss.live_active, ss.live_last_release = True, time.time()
        live_note(f"live mode on: the last {ss.proc.get('live_hold_days', 7)} days are held back")
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


# ------------------------------------------------------------------ the conversation with the agent (deck: four moments with the manager)
ss.setdefault("feed", [])          # [{"who": "agent"|"manager", "text": ..., "ts": ...}] for the day being planned
ss.setdefault("prev_plan", None)   # the last plan, so a new one can say what changed and why


def say(who: str, text: str) -> None:
    ss.feed = ss.feed + [{"who": who, "text": text, "ts": f"{dt.datetime.now():%H:%M}"}]


def _moves(st_: dict) -> list[tuple[dict, int]]:
    sched = (st_.get("plan") or {}).get("schedule") or {}
    return [(m, sched[m["id"]]) for m in (st_.get("problem") or {}).get("shiftable", [])
            if m["id"] in sched and m.get("baseline_start_h") is not None and sched[m["id"]] != m["baseline_start_h"]]


def _ampm(h: int) -> str:
    h = int(h) % 24
    return "midnight" if h == 0 else ("noon" if h == 12 else f"{h % 12} {'am' if h < 12 else 'pm'}")


def on_new_plan(st_: dict, pend: dict | None) -> None:
    """After every plan, the agent says what it did: a first plan, a re-plan question, or what changed and why."""
    day = st_.get("forecast", {}).get("date")
    prev = ss.prev_plan if ss.prev_plan and ss.prev_plan.get("date") == day else None
    if ss.prev_plan and ss.prev_plan.get("date") != day:
        ss.feed = []
    cur = {"date": day, "weather": ss.w_weather, "pins": dict(ss.pins), "today": len(ss.today_rules), "proc": dict(ss.proc), "cfg": ss.cfg_version,
           "rules": len(ss.cfg["machines"].get("learned_rules", [])), "schedule": dict((st_.get("plan") or {}).get("schedule") or {})}
    sav = st_.get("saving_rs")
    if pend and pend.get("type") == "choose_relaxation":
        feas = [o for o in pend["options"] if o["feasible"]]
        rec = next((o for o in feas if o["id"] == pend.get("recommended")), None)
        say("agent", f"The work won't fit the cheap hours with every rule. " + (f"{rec['text']}?" if rec else "No flexible rule makes it fit."))
    elif st_.get("status") == "fallback":
        say("agent", f"I kept the usual schedule. {st_.get('note', '')}")
    else:
        mv = _moves(st_)
        moved = "; ".join(f"{m['name']} to {_ampm(h)}" for m, h in mv) or "nothing"
        save = f"saves {rs(round(sav, -2))}" if sav else "no saving to claim"
        if not prev:
            say("agent", f"Plan ready. Move {len(mv)} machine{'s' if len(mv) != 1 else ''}. About {rs(round(sav or 0, -2))} saved.")
        elif prev["schedule"] != cur["schedule"] or prev["weather"] != cur["weather"]:
            cause = ("Clouds tomorrow." if cur["weather"] == "Cloudy day" else "Clear sky tomorrow." if cur["weather"] == "Clear sky" else "New weather forecast.") \
                if prev["weather"] != cur["weather"] else ("Your change is in." if prev["pins"] != cur["pins"] else
                                                           "Your rule is in." if prev["rules"] != cur["rules"] else
                                                           "New rules and limits." if prev["proc"] != cur["proc"] else
                                                           "New plant settings." if prev["cfg"] != cur["cfg"] else "Re-planned.")
            say("agent", f"{cause} I moved {moved}. Still {save}." if mv else f"{cause} Everything stays at its usual time.")
    ss.prev_plan = cur


# the first visit runs the plan on its own, so the whole flow is visible without a single click
run_error = None
if (go_btn or ss.trigger_plan or not ss.autoran) and ss.hist is not None and plan_date is not None and ss.get("setup_done"):
    ss.autoran, ss.trigger_plan = True, False
    with st.spinner("Agents working: forecasting, reading the tariff, solving..."):
        try:
            ctx = Context(SITE, ss.tariff, MACH, ss.hist, goal=goal or "Cut tomorrow's bill.", cloudy=WEATHER[weather], plan_date=plan_date,
                          notes=notes_for_run(), bias=ss.bias, llm=LLM, audit=ss.audit, process={**ss.proc, **ASK.parse_goal(goal), "pins": dict(ss.pins), "today_rules": list(ss.today_rules)})
            graph, cfg, state, pending = start(ctx)
            ss.run = {"ctx": ctx, "graph": graph, "cfg": cfg, "state": state, "pending": pending, "cfg_version": ss.cfg_version,
                      "proc": dict(ss.proc), "price_rules": dict(ss.get("price_rules", PRICE_RULES))}
            on_new_plan(state, pending)
            ss.verify = None if not (ss.approved and ss.verify and ss.verify.get("_run") == ctx.run_id) else ss.verify
        except Exception as e:  # noqa: BLE001
            ss.run, run_error = None, f"No plan could be made: {e}"


def do_resume(value, echo: bool = True) -> None:
    """Hand the manager's answer to the agent. echo=False when the manager's own words are already in the chat."""
    r = ss.run
    opts = (r.get("pending") or {}).get("options") or []
    r["state"], r["pending"] = resume(r["graph"], r["cfg"], value)
    if isinstance(value, str):                       # the manager picked a rule to relax
        opt = next((o for o in opts if o["id"] == value), None)
        if echo:
            say("manager", f"Yes. {opt['text'] if opt else 'Relax that rule'}.")
        sav = r["state"].get("saving_rs")
        say("agent", f"Done. {('Saves about ' + rs(round(sav, -2)) + ' today.') if sav else 'The order fits now.'}")
    elif value.get("action") == "approve":
        if echo:
            say("manager", "Approve." + (f" {value.get('note')}" if value.get("note") else ""))
        say("agent", "Approved. The work order is ready for the shift lead.")
    elif value.get("action") == "reject":
        if echo:
            say("manager", f"No. {value.get('reason', '')}")
        say("agent", "Understood. The usual schedule stays." + (" Should I remember this as a rule?" if echo else ""))
        ss.ask_rule = echo
    elif value.get("action") == "edit":
        ok = r["state"].get("status") != "edit_blocked"
        say("manager", "Use my edited times.")
        say("agent", "Checked and approved." if ok else "That edit breaks a hard rule: " + "; ".join(r["state"]["edit_result"]["failed"]))
    if r["state"].get("status") == "work_order_issued":
        ss.approved = (r["ctx"], r["state"])
        ss.verify = None


# ------------------------------------------------------------------ settings search: every control, found by name
# (what it is, extra words people type, the diagram box that holds it)
SETTINGS = [("Small AI model", "model llm small t1 groq claude grok key provider", "router"),
            ("Large AI model", "model llm large t2 groq claude grok key provider", "router"),
            ("AI provider and key", "api key groq claude grok offline connect test", "router"),
            ("Guardrails", "guardrail safety rules limits policy source", "guard"),
            ("Safety margin below contract demand", "margin contract demand kva headroom guardrail", "guard"),
            ("Never set a new monthly peak", "monthly peak max demand guardrail", "guard"),
            ("Most machines moved in a day", "moves disruption limit guardrail", "guard"),
            ("Smallest saving worth a change", "minimum saving rupees threshold guardrail", "guard"),
            ("Contract demand", "contract kva demand charge bill", "bill"),
            ("Price bands and rates", "tariff price rate peak solar normal band bill", "bill"),
            ("New price order", "tariff order regulator merc upload paste", "orders"),
            ("Meter data upload", "upload csv meter data readings file steel sample scale", "meters"),
            ("Live feed", "live mode release day incoming feed", "meters"),
            ("Production notes", "notes batch order supervisor grinding", "factory"),
            ("Machines and time windows", "machines kw hours window earliest latest usual", "factory"),
            ("Crew rules", "crew rule overlap together relax", "factory"),
            ("Rooftop solar on or off", "solar pv rooftop kwp latitude longitude", "weather"),
            ("Solar forecast (clear, cloudy, live)", "weather cloudy clear forecast sun", "weather"),
            ("Forecast method", "forecast weeks weekday history base load", "forecast"),
            ("Learned correction", "bias correction learning forecast", "forecast"),
            ("Data quality limits", "spike power factor check quality", "check"),
            ("Solver time limit", "optimiser solver time limit seconds", "opt"),
            ("Goal and day to plan", "goal day date plan orchestrator", "orch"),
            ("Learning speed", "learn rate learning outlier step correction", "loop_day"),
            ("Small-model tries before the large model", "retry tries escalate large small model router", "router"),
            ("Days of history needed", "history days minimum data", "check"),
            ("Price-check limits", "price rule floor peak solar discount regulator 8a", "bill"),
            ("Approve, reject or change the plan", "approve reject edit change keep note decision", "manager"),
            ("Work order", "work order download csv send shift lead", "runs"),
            ("Remove names before AI", "privacy redact names data ai", "router"),
            ("Factory templates", "template foundry plastics textiles cold storage machines", "factory"),
            ("Rules learned from a no", "learned rule memory reject no remember", "memory"),
            ("Memory: what the agent remembers", "memory forget remember sign in login plant correction library", "memory"),
            ("Next-day check", "verify real saving next day check learn", "loop_day"),
            ("Download the audit trail", "audit record download csv history", "store")]


def _open_setting(box: str) -> None:
    goto("Control room", box)
    ss.box_tab = "Controls"
    ss.cr_view = "Diagram"
    ss.set_q = ""


def settings_search() -> None:
    q = st.text_input("Search settings", key="set_q", placeholder="Search settings: model, guardrails…", label_visibility="collapsed",
                      icon=":material/search:")
    q = (q or "").strip().lower()
    if not q:
        return
    terms = q.split()
    hits = [x for x in SETTINGS if all(t in (x[0] + " " + x[1]).lower() for t in terms)][:7]
    with st.container(key="sethits"):
        if not hits:
            html("<div class='rh-note'>No setting matches. Try a word like model, solar or margin.</div>")
        for label, _, box in hits:
            st.button(label, key=f"hit_{label}", width="stretch", on_click=_open_setting, args=(box,),
                      help=f"Opens {ARCH.info(box)['title']} in the Control room")


# ------------------------------------------------------------------ the left menu
def account_box() -> None:
    """Sign in to a plant: its memory is kept on the server under the plant code."""
    if ss.plant == MEM.DEMO:
        with st.expander("Demo plant · sign in to load your plant's memory", icon=":material/login:"):
            with st.form("signin", border=False):
                code = st.text_input("Plant code", placeholder="e.g. pune-forge")
                pin = st.text_input("Passphrase, at least 8 characters", type="password", max_chars=64)
                name = st.text_input("Plant name (first sign-in only)", placeholder="e.g. Pune Forge, Unit 2")
                if st.form_submit_button("Sign in", type="primary", icon=":material/login:", width="stretch"):
                    try:
                        token, created = STORE.sign_in(code, pin, name)
                        switch_plant(MEM.clean_code(code), token)
                        ss.signin_note = "New plant created. Its memory starts empty." if created else "Signed in. Memory loaded."
                        st.rerun()
                    except (ValueError, PermissionError) as e:
                        st.error(str(e))
            html("<div class='rh-note'>The demo plant is shared. A new plant code makes a new plant.</div>")
    else:
        c1, c2 = st.columns([3, 1.3], vertical_alignment="center")
        c1.markdown(f"<div class='rh-note'><b>{escape(ss.plant_name)}</b><br>Signed in · memory saved</div>", unsafe_allow_html=True)
        if c2.button("Sign out", key="signout", type="tertiary"):
            STORE.sign_out(ss.get("token"))
            switch_plant(MEM.DEMO, None)
            st.rerun()
    if ss.get("signin_note"):
        st.toast(ss.pop("signin_note"))


def sidebar() -> None:
    asking = bool(ss.run and ss.run.get("pending"))
    with st.sidebar:
        html(f"<div class='rh-brand'>{LOGO}<div><div class='n'>RightHour</div><div class='s'>Power bill planner</div></div></div>")
        account_box()
        settings_search()
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
    cells = "".join(f"<li class='{c}' title='{escape(d)}'><div class='top'><span class='no'>{i}</span><span class='sw'>{word[c]}</span></div>"
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
           "machines": {**cfg["machines"], **{k: updates[k] for k in ("shiftable", "rules", "learned_rules") if k in updates}}}
    ss.cfg = new
    save_plant_file(new)
    ss.tariff, ss.cfg_version = new["tariff"], ss.cfg_version + 1
    ss.audit.log("setup", "Plant manager", "setup_saved", tier="human", mode="human", reason="plant settings changed",
                 detail={k: "changed" for k in updates})
    ss.trigger_plan = True
    st.toast("Saved. Planning again with the new settings.")
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
    html(f"<div class='rh-legend-hld'><span><b>{plan_date:%a %d %b}</b></span><span><span class='rh-tag'>Fixed batch</span> runs at its stated hours</span>"
         f"<span><span class='rh-tag'>New order</span> the agent picks the time</span></div>" if plan_date else "")
    tcols = ("Could start as early as", "Could finish as late as")
    shown = pd.DataFrame([{**r, **{c: to_t(r.get(c)) for c in tcols}} for r in ss.notes_rows], columns=NOTE_COLS)
    df = st.data_editor(shown, num_rows="dynamic", hide_index=True, width="stretch", key=f"notes{ss.notes_ver}",
                        column_config={"Kind": st.column_config.SelectboxColumn(options=["Fixed batch", "New order"], required=True, default="New order"),
                                       "Note, in plain words": st.column_config.TextColumn(width="large"),
                                       "Could start as early as": time_col("Could start as early as", "Optional. If no plan fits, the agent may ask to start this order this early."),
                                       "Could finish as late as": time_col("Could finish as late as", "Optional. If no plan fits, the agent may ask to finish this order this late.")})
    ss.notes_rows = [{**r, **{c: from_t(r.get(c)) for c in tcols}} for r in df.to_dict("records")]
    if st.button("Add the example grinding order", icon=":material/add:", help="A 400 kW order that does not fit the usual schedule. Shows what happens when rules conflict."):
        ss.notes_rows = ss.notes_rows + [dict(EXAMPLE_ORDER)]
        ss.notes_ver += 1
        st.rerun()


def pane_machines() -> None:
    template_picker()
    cfg = ss.cfg
    st.markdown("**Machines that can move**")
    html("<div class='rh-note' style='margin-bottom:6px'>Each machine runs once a day, anywhere between its start and finish times.</div>")
    v = ss.cfg_version
    tcols = ("Earliest start", "Latest finish", "Usual start", "Could start as early as", "Could finish as late as")
    shown = pd.DataFrame([{**r, **{c: to_t(r.get(c)) for c in tcols}} for r in CFG.machines_to_rows(cfg["machines"]["shiftable"])], columns=CFG.MACHINE_COLS)
    mach = st.data_editor(shown, num_rows="dynamic", hide_index=True, width="stretch", key=f"mach{v}",
                          column_config={"kW": st.column_config.NumberColumn("Power (kW)", min_value=1, max_value=5000),
                                         "Hours": st.column_config.NumberColumn("Runs for (h)", min_value=0.5, max_value=24, step=0.5, format="%.1f h"),
                                         "Earliest start": time_col("Earliest start", required=True), "Latest finish": time_col("Must finish by", required=True),
                                         "Usual start": time_col("Usual start", "When it runs today. The saving is measured against this.", required=True),
                                         "Could start as early as": time_col("If needed, start from"), "Could finish as late as": time_col("If needed, finish by")})
    machines, m_errs = CFG.rows_to_machines([{**r, **{c: from_t(r.get(c)) for c in tcols}} for r in mach.to_dict("records")])
    st.markdown("**Crew rules: machines that cannot run together**")
    names = [m["name"] for m in machines]
    rdf = st.data_editor(pd.DataFrame(CFG.rules_to_rows(cfg["machines"]["rules"], cfg["machines"]["shiftable"]), columns=CFG.RULE_COLS), num_rows="dynamic",
                         hide_index=True, width="stretch", key=f"rules{v}",
                         column_config={"Machine A": st.column_config.SelectboxColumn(options=names), "Machine B": st.column_config.SelectboxColumn(options=names),
                                        "Can be relaxed?": st.column_config.CheckboxColumn(help="If ticked, the agent may offer to break this rule when there is no valid plan. The contract-demand rule can never be relaxed.")})
    rules, r_errs = CFG.rows_to_rules(rdf.to_dict("records"), machines)
    for e_ in m_errs + r_errs:
        html(f"<div class='rh-note' style='color:var(--red)'>{ICONS['alert']} {e_}</div>")
    if st.button("Save and update plan", type="primary", icon=":material/refresh:", disabled=bool(m_errs + r_errs), key=f"savemach{v}"):
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
    shown = pd.DataFrame([{**r, "From hour": to_t(r["From hour"]), "To hour": to_t(r["To hour"])} for r in CFG.tariff_to_rows(cfg["tariff"])], columns=CFG.SLOT_COLS)
    slots = st.data_editor(shown, num_rows="dynamic", hide_index=True, width="stretch", key=f"slots{v}",
                           column_config={"From hour": time_col("From", required=True), "To hour": time_col("To", required=True),
                                          "Band": st.column_config.SelectboxColumn("Band", options=["normal", "solar", "peak"], required=True),
                                          "Rate (₹ per kVAh)": st.column_config.NumberColumn("₹ per kVAh", min_value=0.01, format="₹ %.2f")})
    dc = st.number_input("Demand charge (₹ per kVA per month)", 0.0, 100000.0, float(cfg["tariff"]["demand_charge_per_kva_month"]), 10.0, key=f"dc{v}")
    tariff, errs = CFG.rows_to_tariff(cfg["tariff"], [{**r, "From hour": from_t(r["From hour"]), "To hour": from_t(r["To hour"])} for r in slots.to_dict("records")], dc)
    for e_ in errs:
        html(f"<div class='rh-note' style='color:var(--red)'>{ICONS['alert']} {e_}</div>")
    html("<div class='rh-sub'>Price check</div>")
    price_check(tariff)
    with st.expander("Change the price-check limits", icon=":material/rule:"):
        box_limits("price", "pb", 3)
    c1, c2, _ = st.columns([1.2, 1, 1.8])
    if c1.button("Save and update plan", type="primary", icon=":material/refresh:", disabled=bool(errs), width="stretch", key=f"savebill{v}"):
        save_cfg(site=site, tariff=tariff)
    if c2.button("Back to demo defaults", width="stretch", key=f"billreset{v}"):
        ss.cfg = CFG.reset() if ss.plant == MEM.DEMO else CFG._normalise(CFG.defaults())
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
    if st.button("Save and update plan", type="primary", icon=":material/refresh:", key=f"pv_save{v}"):
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
    """Memory management: everything the agent remembers for this plant, where it came from, and a way to forget it."""
    p, lrs = ss.plant, ss.cfg["machines"].get("learned_rules", [])
    upd = STORE.updated(p) if p else {}
    changed = [k for k, v in ss.proc.items() if PROCESS_DEFAULTS.get(k) != v]
    floors = [k for k, v in ss.get("price_rules", PRICE_RULES).items() if PRICE_RULES.get(k) != v]
    who = "Shared demo plant: anyone using the demo changes it." if p == MEM.DEMO else f"Saved for {escape(ss.plant_name)} (plant code {escape(p)})."
    html(f"<div class='rh-note'>{ICONS['database']} {who} It loads in a new tab, after a refresh and on the next day.</div>")
    items = [("Lasting rules", f"{len(lrs)}", "From a “no” or from chat, after you said “from now on”.", "cfg", "rules"),
             ("Rules for this day only", f"{len(ss.today_rules)}", "Gone when you plan another day. Never saved.", None, "today"),
             ("Forecast correction", f"x{ss.bias:.3f}", f"Learned from {len(ss.get('bias_history', []))} next-day check(s).", "bias", "bias"),
             ("Tariff library", f"{len(ss.library)}", "Price orders you signed off. Read once, reused.", "library", "library"),
             ("Agent settings changed", f"{len(changed)}", escape(", ".join(changed[:4])) or "All at the standard values.", "proc", "proc"),
             ("Price-check floors changed", f"{len(floors)}", escape(", ".join(floors)) or "All at the standard values.", "price_rules", "floors")]
    html("<table class='rh-log'><thead><tr><th>What it remembers</th><th>Now</th><th>Where it came from</th><th>Saved</th></tr></thead><tbody>"
         + "".join(f"<tr><td>{a}</td><td><b>{b}</b></td><td>{c}</td><td>{escape(upd.get(k, '')[:16].replace('T', ' ')) if k else '—'}</td></tr>"
                   for a, b, c, k, _ in items) + "</tbody></table>")
    bh = ss.get("bias_history", [])
    if len(bh) > 1:
        st.line_chart(pd.DataFrame(bh).set_index("ts")["bias"], height=120)
    old_rules = [lr for lr in lrs if lr.get("ts") and (dt.datetime.now() - dt.datetime.strptime(lr["ts"][:10], "%Y-%m-%d")).days > ss.proc.get("rule_review_days", 90)]
    if old_rules:
        html(f"<div class='rh-note'>{ICONS['alert']} {len(old_rules)} rule(s) are older than {ss.proc.get('rule_review_days', 90)} days. Check they still hold, below.</div>")
    if ss.today_rules:
        names = {m["id"]: m["name"] for m in MACH["shiftable"]}
        for i, lr in enumerate(ss.today_rules):
            c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
            c1.markdown(f"<span class='rh-note'>Today only: <b>{escape(A.learned_rule_text(lr, names))}</b></span>", unsafe_allow_html=True)
            if c2.button("Keep from now on", key=f"tr_keep_{i}", type="tertiary", icon=":material/bookmark_add:"):
                ss.today_rules = [x for j, x in enumerate(ss.today_rules) if j != i]
                _save_rule_quiet(lr)
                st.rerun()
    c = st.columns(4)
    if c[0].button("Forget rules", key="mf_rules", icon=":material/delete:", width="stretch", disabled=not lrs):
        save_cfg(learned_rules=[])
        ss.audit.log("memory", "Plant manager", "forget", tier="human", mode="human", reason="forget all learned rules")
        st.rerun()
    if c[1].button("Forget correction", key="mf_bias", icon=":material/restart_alt:", width="stretch", disabled=ss.bias == 1.0):
        ss.bias = 1.0
        ss.audit.log("memory", "Plant manager", "forget", tier="human", mode="human", reason="forecast correction reset to x1.000")
        st.rerun()
    if c[2].button("Forget library", key="mf_lib", icon=":material/delete:", width="stretch", disabled=not ss.library):
        ss.library = {}
        ss.audit.log("memory", "Plant manager", "forget", tier="human", mode="human", reason="tariff library cleared")
        st.rerun()
    if c[3].button("Forget everything", key="mf_all", icon=":material/delete_forever:", width="stretch"):
        STORE.forget(p)
        ss.audit.log("memory", "Plant manager", "forget", tier="human", mode="human", reason="all memory for this plant cleared")
        ss.loaded_for = None
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
        warns = [i.text for i in validate_tariff(ss.tariff, ss.get('price_rules')) if i.level == "warning"]
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


# ---- every rule and limit in the system, in one list. Each box shows its own; "All rules and limits" shows them all.
# (box, key, label, kind, options, help). kind: slider | pct | toggle | money | radio | optint | price
LIMITS = [
    ("guard", "margin_pct", "Safety margin under the contract demand (%)", "slider", (0, 20, 1, "%d"), "The plan must stay this far under the contract."),
    ("guard", "block_new_peak", "Never set a new monthly peak", "toggle", None, "On: never above this month's highest demand."),
    ("guard", "max_moves", "Most machines moved in a day", "optint", None, "Fewer moves, less disruption on the floor."),
    ("guard", "min_saving_rs", "Smallest saving worth a change (₹ a day)", "money", (0, 1_000_000, 250), "Below this, the usual schedule is kept."),
    ("price", "peak_x", "Peak price at least (× normal)", "price", (1.0, 3.0, 0.05, "%.2f"), "Regulator floor for the peak band."),
    ("price", "solar_off", "Solar discount at least (%)", "price", (0.0, 0.6, 0.01, None), "Regulator floor for the solar-hours discount."),
    ("price", "spread_x", "Peak price at least (× solar)", "price", (1.0, 4.0, 0.05, "%.2f"), "The gap that makes moving load worth it."),
    ("check", "spike_x", "Spike above (× the usual top load)", "slider", (1.5, 6.0, 0.5, "%.1f"), "Readings above this are flagged."),
    ("check", "pf_min", "Lowest normal power factor", "slider", (0.3, 0.95, 0.05, "%.2f"), "Readings below this are flagged."),
    ("check", "max_gap_h", "Longest gap allowed (hours)", "slider", (0.5, 12.0, 0.5, "%.1f"), "A longer gap is flagged: filling it is a guess."),
    ("check", "stuck_h", "Stuck meter after (hours)", "slider", (1.0, 12.0, 0.5, "%.1f"), "The same value this long is flagged as copied, not measured."),
    ("check", "shutdown_pct", "Holiday or shutdown below (% of a usual day)", "pct", (5, 60, 5), "Such days are flagged: they pull the forecast down."),
    ("check", "stale_days", "Newest reading at most (days before the plan day)", "slider", (1, 30, 1, "%d"), "Older data misses recent changes."),
    ("check", "min_history_days", "Days of meter history needed to plan", "slider", (7, 60, 1, "%d"), "Fewer days, a weaker forecast."),
    ("forecast", "method", "Which past days", "radio", (("weekday", "Same weekday"), ("recent", "Last 7 days")), None),
    ("forecast", "weeks", "Weeks of the same weekday", "slider", (1, 8, 1, "%d"), None),
    ("forecast", "use_learning", "Use the learned correction", "toggle", None, "Off: a plain average of past days."),
    ("loop_day", "learn_rate", "How fast it learns from a checked day", "slider", (0.1, 1.0, 0.1, "%.1f"), "1.0 moves all the way to what happened."),
    ("loop_day", "learn_step_max", "Biggest change in one day (%)", "pct", (1, 30, 1), "Caps how far one day can move the forecast."),
    ("memory", "rule_review_days", "Ask to check a rule after (days)", "slider", (7, 365, 1, "%d"), "Old rules are flagged so they do not go stale."),
    ("loop_day", "learn_outlier", "Ignore days off by more than (%)", "pct", (10, 100, 5), "Such a day is treated as odd and not learned from."),
    ("opt", "time_limit_s", "Time the solver may take (seconds)", "slider", (5, 120, 5, "%d"), "This plant solves in under a second."),
    ("router", "small_tries", "Small-model tries before the large model", "slider", (1, 3, 1, "%d"), "After this many failed checks, the large model takes over."),
    ("router", "redact_names", "Remove names before any AI sees the data", "toggle", None, "The plant's name is replaced before text goes to an AI model."),
    ("rules", "notes_on", "Read the day's production notes", "toggle", None, "Off: batches and orders in notes are not planned."),
    ("weather", "solar_on", "Count rooftop solar", "toggle", None, "Off: the plan counts no solar power."),
    ("meters", "live_hold_days", "Days held back in live mode", "slider", (3, 14, 1, "%d"), "They arrive one by one, like a real meter."),
]
LIMIT_GROUPS = [("guard", "shield", "Guardrails"), ("price", "rupee", "Price rules"), ("check", "pulse", "Data checks"),
                ("forecast", "chart", "Forecast"), ("loop_day", "loop", "Learning"), ("opt", "cpu", "Solver"), ("router", "route", "AI models"),
                ("rules", "notes", "Production notes"), ("weather", "sun", "Solar"), ("meters", "meter", "Live feed")]


def limit_widget(spec: tuple, prefix: str) -> None:
    box, k, label, kind, opt, hint = spec
    store = ss.setdefault("price_rules", dict(PRICE_RULES)) if kind == "price" else ss.proc
    key = f"{prefix}_{k}"
    if kind == "toggle":
        store[k] = st.toggle(label, value=bool(store[k]), key=key, help=hint)
    elif kind == "slider":
        lo, hi, step, fmt = opt
        cast = float if isinstance(step, float) else int
        store[k] = st.slider(label, cast(lo), cast(hi), cast(store[k]), cast(step), format=fmt, key=key, help=hint)
    elif kind == "pct":
        lo, hi, step = opt
        store[k] = st.slider(label, lo, hi, int(round(store[k] * 100)), step, format="%d", key=key, help=hint) / 100
    elif kind == "money":
        lo, hi, step = opt
        store[k] = st.number_input(label, lo, hi, int(store[k]), step, key=key, help=hint)
    elif kind == "radio":
        vals = [v for v, _ in opt]
        store[k] = st.radio(label, vals, index=vals.index(store[k]), format_func=dict(opt).get, horizontal=True, key=key, help=hint)
    elif kind == "optint":
        nm = len(MACH["shiftable"])
        on = st.toggle(f"Limit: {label.lower()}", value=store[k] is not None, key=f"{key}_on", help=hint)
        store[k] = st.slider(label, 0, nm, min(int(store[k] if store[k] is not None else 2), nm), key=key) if on else None
    elif kind == "price":
        lo, hi, step, fmt = opt
        if k == "solar_off":
            store[k] = st.slider(label, 0, 60, int(round(store[k] * 100)), 1, format="%d", key=key, help=hint) / 100
        else:
            store[k] = st.slider(label, lo, hi, float(store[k]), step, format=fmt, key=key, help=hint)


def box_limits(box: str, prefix: str = "bx", cols: int = 2) -> bool:
    skip = SWITCH[box][0] if box in SWITCH and prefix == "bx" else None     # that switch already sits in the box header
    specs = [x for x in LIMITS if x[0] == box and x[1] != skip]
    if not specs:
        return False
    cs = st.columns(cols, gap="large")
    for i, spec in enumerate(specs):
        with cs[i % cols]:
            limit_widget(spec, f"{prefix}_{box}")
    return True


def settings_changed() -> bool:
    return bool(run) and (run.get("proc", ss.proc) != ss.proc or run.get("price_rules", PRICE_RULES) != ss.get("price_rules", PRICE_RULES))


def update_plan_bar(key: str) -> None:
    """The one button that applies changed settings: it plans again at once."""
    changed = settings_changed()
    c1, c2 = st.columns([3, 1.2], vertical_alignment="center")
    c1.markdown(f"<div class='rh-upd {'on' if changed else ''}'>{ICONS['alert' if changed else 'check']}"
                f"{'Settings changed. Update the plan to use them.' if changed else 'The plan uses these settings.'}</div>", unsafe_allow_html=True)
    if c2.button("Update plan", type="primary" if changed else "secondary", icon=":material/refresh:", width="stretch", key=f"upd_{key}",
                 disabled=ss.hist is None):
        ss.audit.log(ctx.run_id if ctx else "setup", "Plant manager", "setup_saved", tier="human", mode="human", reason="rules and limits changed",
                     detail={"process": {k: v for k, v in ss.proc.items() if k != "pins"}, "price_rules": ss.get("price_rules")})
        ss.trigger_plan = True
        st.rerun()


def all_limits() -> None:
    """Every rule and limit of the system on one screen, grouped, with the one Update plan button."""
    update_plan_bar("all")
    for i in range(0, len(LIMIT_GROUPS), 2):
        cols = st.columns(2, gap="medium")
        for (box, icon, title), col in zip(LIMIT_GROUPS[i:i + 2], cols):
            with col, st.container(border=True, key=f"lim_{box}"):
                card_title(icon, title)
                for spec in [x for x in LIMITS if x[0] == box]:
                    limit_widget(spec, f"al_{box}")
                st.button("Open in the diagram", type="tertiary", icon=":material/arrow_outward:", key=f"lim_go_{box}",
                          on_click=_open_setting, args=("bill" if box == "price" else box,))
    with st.container(border=True, key="lim_locked"):
        card_title("lock", "Always on", "These cannot be switched off")
        html("<div class='rh-lockgrid'>" + "".join(f"<div>{ICONS['lock']}<span>{t}</span></div>" for t in (
            "Never above the contract demand", "Fixed batches never move or stop", "Every machine inside its own hours",
            "Crew rules hold", "A person approves every plan", "An AI model never writes a number")) + "</div>")
        st.button("Edit machine hours and crew rules", type="tertiary", icon=":material/arrow_outward:", key="lim_go_factory",
                  on_click=_open_setting, args=("factory",))
    if ss.proc != PROCESS_DEFAULTS or ss.get("price_rules", PRICE_RULES) != PRICE_RULES:
        if st.button("Back to the standard rules and limits", icon=":material/restart_alt:", type="tertiary", key="lim_reset"):
            ss.proc, ss.price_rules = dict(PROCESS_DEFAULTS), dict(PRICE_RULES)
            st.rerun()


def price_check(t: dict) -> None:
    """The three price checks as gauges: the tariff's value, the floor it should meet, pass or not."""
    gs = price_gauges(t, ss.get("price_rules"))
    cells = ""
    for g in gs:
        top = max(g["value"], g["floor"]) * 1.35 or 1
        cells += (f"<div class='rh-gauge {'ok' if g['ok'] else 'bad'}'><div class='h'>{ICONS['check' if g['ok'] else 'alert']}<span>{g['name']}</span></div>"
                  f"<div class='v'>{g['value']:.2f}<small>{g['unit']}</small></div>"
                  f"<div class='bar'><i style='width:{min(g['value'] / top * 100, 100):.1f}%'></i><b style='left:{g['floor'] / top * 100:.1f}%'></b></div>"
                  f"<div class='f'>needs {g['floor']:.2f} {g['unit']}</div></div>")
    illus = t.get("status") == "illustrative"
    html(f"<div class='rh-gauges'>{cells}</div>" + (f"<div class='rh-note'>{ICONS['alert']} Rates are illustrative, not from the final order.</div>" if illus else ""))


def rule_chips(checks: list[dict]) -> None:
    """Rule checks as icon chips: green tick, amber note, red cross."""
    html("<div class='rh-rchips'>" + "".join(
        f"<span class='{'ok' if c['ok'] else ('bad' if c['hard'] else 'warn')}'>{ICONS['check' if c['ok'] else 'alert']}{escape(c['rule'])}</span>" for c in checks) + "</div>")


# ---- plain words for the activity log: the manager never needs a function name
ACTION_WORDS = {"validate_data": "Checked the meter readings", "forecast_load": "Forecast the day's load", "solar_forecast": "Forecast rooftop solar",
                "tariff_lookup": "Looked up the price bands", "tariff_parse": "Read a new price order", "tariff_sign_off": "Price order signed off",
                "constraint_parse": "Read a production note", "optimise": "Found the cheapest schedule", "policy_check": "Checked every rule",
                "orchestrate": "Planned the next step", "replan": "Tested which rule could flex", "explain": "Wrote the explanation",
                "relax_rule": "Manager chose a rule to relax", "work_order": "Issued the work order", "reject": "Manager rejected the plan",
                "verify_savings": "Checked the real saving", "learn": "Learned from the day", "setup_saved": "Settings saved",
                "data_loaded": "Meter data loaded", "approve": "Manager approved the plan", "plan_update": "Manager changed the plan",
                "plan_edit": "Manager approved an edited plan", "learn_rule": "Saved a “no” as a rule", "work_order_sent": "Work order sent to the shift lead", "plan_actions": "Turned a message into actions", "safe_fallback": "Kept the usual schedule", "answer_question": "Answered a question"}
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
                (str(sum(i.level == "warning" for i in validate_tariff(ss.tariff, ss.get('price_rules')))), "tariff warnings", "")]
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
        rule_chips(s["policy"]["checks"])
    elif eid == "opt" and (s.get("plan") or {}).get("schedule"):
        sched = s["plan"]["schedule"]
        body = "".join(f"<tr><td>{escape(str(m['name']))}</td><td class='r'>{m['kw']:.0f}</td><td class='m'>{'new' if m.get('baseline_start_h') is None else '%02d:00' % m['baseline_start_h']}</td>"
                       f"<td class='m'><b>{sched[m['id']]:02d}:00</b></td></tr>" for m in s["problem"]["shiftable"])
        html(f"<table class='rh-log'><thead><tr><th>Machine</th><th class='r'>kW</th><th>Usual start</th><th>Planned start</th></tr></thead><tbody>{body}</tbody></table>")
    elif eid in ("bill", "price"):
        price_check(ss.tariff)
        bars = "".join(f"<span style='width:{(b['end_h'] - b['start_h']) / 24 * 100:.2f}%;background:{BAND[b['type']]}'>{b['type']} ₹{b['rate']:.2f}</span>" for b in ss.tariff["slots"])
        html(f"<div class='rh-bands' role='img' aria-label='Price bands across the day'>{bars}</div><div class='rh-note'>00:00 to 24:00</div>")
    elif eid == "check":
        for i in s.get("data_issues") or []:
            html(f"<p class='rh-p'><span class='rh-chip {'ok' if i['level'] == 'ok' else 'warn'}'>{i['level']}</span>{i['text']}</p>")
    elif eid in ("rules", "factory") and run:
        html(read_back(s))
    html("<div class='rh-sub'>How it works</div>")
    steps = _flow(eid)
    html("<ol class='rh-chain'>" + "".join(f"<li class='{'last' if i == len(steps) else ''}'><span class='n'>{i}</span><div class='t'>{t}</div><div class='d'>{d}</div></li>"
                                           for i, (t, d) in enumerate(steps, 1)) + "</ol>")
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


def ctl_bias() -> None:
    if ss.bias != 1.0 and st.button("Forget the learned correction", icon=":material/restart_alt:", key="pk_bias"):
        ss.bias = 1.0
        st.rerun()


def ctl_locked() -> None:
    html("<div class='rh-lockgrid'>" + "".join(f"<div>{ICONS['lock']}<span>{t}</span></div>" for t in (
        "Never above the contract demand", "Fixed batches never move", "Machines stay in their hours", "Crew rules hold",
        "A person approves every plan")) + "</div>")


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


def learned_rules_editor() -> None:
    """The rules the agent learned from the manager's "no" answers. Each one can be removed."""
    lrs = ss.cfg["machines"].get("learned_rules", [])
    names = {m["id"]: m["name"] for m in MACH["shiftable"]}
    html("<div class='rh-sub'>Rules learned from your “no”</div>")
    if not lrs:
        html("<div class='rh-note'>None yet. Reject a plan on Today, and the agent asks whether to remember it.</div>")
        return
    for i, lr in enumerate(lrs):
        c1, c2 = st.columns([5, 1], vertical_alignment="center")
        try:
            age = (dt.datetime.now() - dt.datetime.strptime(str(lr.get("ts", ""))[:10], "%Y-%m-%d")).days
        except ValueError:
            age = None
        due = age is not None and age > ss.proc.get("rule_review_days", 90)
        c1.markdown(f"<div class='rh-lockgrid' style='grid-template-columns:1fr;margin:0'><div>{ICONS['bookmark'] if 'bookmark' in ICONS else ICONS['lock']}"
                    f"<span><b>{escape(A.learned_rule_text(lr, names))}</b><br><span class='rh-note'>{escape(str(lr.get('reason') or ''))} · {escape(str(lr.get('ts', '')))}"
                    f"{' · ' + str(age) + ' days old' if age is not None else ''}{' · <b>check it still holds</b>' if due else ''}</span></span></div></div>",
                    unsafe_allow_html=True)
        if c2.button("Remove", key=f"lr_rm_{i}", type="tertiary", icon=":material/delete:"):
            save_cfg(learned_rules=[x for j, x in enumerate(lrs) if j != i])


def ctl_none(text: str):
    return lambda: html(f"<div class='rh-note'>{text}</div>")


CONTROLS = {"meters": lambda: (meter_controls("cr"), box_limits("meters", cols=1)), "factory": lambda: (notes_editor(), pane_machines()), "rules": notes_editor,
            "bill": lambda: pane_bill([]), "price": lambda: pane_bill([]), "orders": lambda: pane_orders([]),
            "weather": lambda: (st.selectbox("Solar forecast", list(WEATHER), key="w_weather", disabled=not ss.proc["solar_on"]), pane_weather([])),
            "collect": ctl_day, "orch": ctl_day, "check": lambda: box_limits("check"), "store": ctl_store,
            "forecast": lambda: (box_limits("forecast"), ctl_bias()), "opt": lambda: box_limits("opt", cols=1),
            "guard": lambda: (box_limits("guard"), ctl_locked()), "router": lambda: (box_limits("router", cols=1), pane_router([])), "manager": ctl_manager, "runs": ctl_runs,
            "loop_no": lambda: learned_rules_editor(), "memory": lambda: (pane_memory([]), learned_rules_editor(), box_limits("memory", cols=1)),
            "loop_day": lambda: (box_limits("loop_day", cols=3), pane_loop_day([]))}
PANES = CONTROLS


BOX_ICON = {"meters": "meter", "factory": "factory", "bill": "rupee", "orders": "doc", "weather": "sun", "collect": "upload", "check": "pulse",
            "store": "database", "orch": "chip", "price": "rupee", "forecast": "chart", "rules": "notes", "opt": "cpu", "guard": "shield",
            "router": "route", "memory": "database", "manager": "person", "runs": "clipboard", "loop_no": "loop", "loop_day": "loop"}


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
        c1.markdown(f"<div class='rh-ct'><span class='i'>{ICONS[BOX_ICON.get(eid, 'chip')]}</span><div><div class='t'>{info['title']} {tag}</div>"
                    f"<div class='s'>{info['what']}</div></div></div>", unsafe_allow_html=True)
        if eid in SWITCH:
            k, label, hint = SWITCH[eid]
            ss.proc[k] = c2.toggle(label, value=ss.proc[k], key=f"sw_{k}_{eid}", help=hint)
        ss.setdefault("box_tab", "Results")
        tab = st.segmented_control("View", ["Results", "Controls", "Activity"], key="box_tab", label_visibility="collapsed",
                                   format_func=lambda t: {"Results": ":material/monitoring: Results", "Controls": ":material/tune: Controls",
                                                          "Activity": ":material/receipt_long: Activity"}[t]) or "Results"
        if tab == "Results":
            box_results(eid, rows)
        elif tab == "Controls":
            _ = CONTROLS[eid]()   # assigned, so Streamlit does not print the return value
            update_plan_bar(f"box_{eid}")
        else:
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


def plan_editor(scope: str, record) -> None:
    """Set start times or keep machines; plan again around them, or approve exactly as edited."""
    rid = ctx.run_id
    html("<div class='rh-note'>Set a start time, and tick <b>Keep</b> for any machine that must not change. "
         "<b>Update plan</b> plans again around what you kept. Your note and changes are saved in the audit trail.</div>")
    sched = state["plan"]["schedule"]
    rows_ = [{"Machine": m["name"], "Usual": "new" if m.get("baseline_start_h") is None else f"{m['baseline_start_h']:02d}:00",
              "Start": f"{(ss.pins.get(m['id'], sched[m['id']])):02d}:00", "Keep": m["id"] in ss.pins} for m in state["problem"]["shiftable"]]
    hours = [f"{h:02d}:00" for h in range(24)]
    ed = st.data_editor(pd.DataFrame(rows_), hide_index=True, width="stretch", key=f"pin_{scope}_{rid}", disabled=["Machine", "Usual"],
                        column_config={"Start": st.column_config.SelectboxColumn("Start", options=hours, required=True),
                                       "Keep": st.column_config.CheckboxColumn("Keep", help="This machine must start at this time.")})
    ids = [m["id"] for m in state["problem"]["shiftable"]]
    starts = {i: int(r["Start"][:2]) for i, r in zip(ids, ed.to_dict("records"))}
    keep = {i: starts[i] for i, r in zip(ids, ed.to_dict("records")) if r["Keep"]}
    c1, c2 = st.columns(2)
    if c1.button("Update plan", type="primary", icon=":material/refresh:", width="stretch", key=f"update_{scope}",
                 help="Plan again. Machines you kept stay at their time; the agent moves the rest."):
        ss.pins = keep
        record("plan_update", {"kept": {m: f"{h:02d}:00" for m, h in keep.items()}})
        ss.trigger_plan = True
        st.rerun()
    if c2.button("Approve exactly as edited", icon=":material/task_alt:", width="stretch", key=f"editgo_{scope}",
                 help="Use these start times as they are. The rule check runs first."):
        record("plan_edit", {"schedule": {m: f"{h:02d}:00" for m, h in starts.items()}})
        do_resume({"action": "edit", "schedule": starts})
        st.rerun()


def approval_controls(scope: str) -> None:
    """Approve, reject or change the plan. Every choice, with the manager's note, goes to the audit trail."""
    rid = ctx.run_id
    note = st.text_area("Your note", placeholder="Why you approve, reject or change it. Saved in the audit trail.", height=68, key=f"note_{scope}")

    def record(action: str, detail: dict | None = None) -> None:
        ss.audit.log(rid, "Plant manager", action, tier="human", mode="human", reason=note.strip() or "no note given", detail=detail or {})

    b1, b2 = st.columns(2)
    if b1.button("Approve plan", type="primary", icon=":material/check:", width="stretch", key=f"approve_{scope}"):
        record("approve")
        do_resume({"action": "approve", "note": note})
        st.rerun()
    if b2.button("Reject", icon=":material/close:", width="stretch", key=f"reject_{scope}"):
        do_resume({"action": "reject", "reason": note.strip() or "no reason given"})
        st.rerun()
    with st.expander("Change the plan", icon=":material/edit_calendar:", expanded=False):
        plan_editor(scope, record)
    if ss.pins:
        names = {m["id"]: m["name"] for m in state["problem"]["shiftable"]}
        html("<div class='rh-note' style='margin-top:6px'><b>Kept by you:</b> " + ", ".join(f"{escape(names.get(k, k))} at {h:02d}:00" for k, h in ss.pins.items()) + "</div>")
        if st.button("Release all kept machines", type="tertiary", key=f"unpin_{scope}"):
            record("plan_update", {"kept": "released"})
            ss.pins = {}
            ss.trigger_plan = True
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
            card_title("chip", "Why this plan", "Every number comes from the tariff and the solver")
            why_plan()
        checks = state.get("policy", {}).get("checks", [])
        with st.container(border=True):
            bad = sum(not c["ok"] for c in checks)
            card_title("shield", "Rules checked", f"{len(checks) - bad} of {len(checks)} passed" + (f", {bad} need a look" if bad else ""))
            with st.expander("See every rule", expanded=bool(bad)):
                rule_chips(checks)
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
    html(f"<div class='rh-meta'><span>Plan for <b>{state['forecast']['weekday']} {state['forecast']['date']}</b></span>"
         f"<span>Load forecast <b>{state['forecast']['method']}</b></span><span>Solar <b>{escape(str(state['pv']['source']).split('(')[0].strip())}</b></span>"
         + (f"<span title='{escape(' | '.join(warns))}'><span class='rh-chip warn'>{len(warns)} tariff warnings</span></span>" if warns else "") + "</div>")
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
         + ", ".join(f"<span class='mono'>{m}</span> ${PRICES.get(m, (0, 0))[0]} / ${PRICES.get(m, (0, 0))[1]}" for m in MODELS[LLM.provider].values())
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


# ---- the deck's business pieces: a free bill check, the shared-savings split, factory templates, settings to keep
def bill_check() -> None:
    """Free bill check (deck slide 10): a savings estimate from one power bill. Rates come from the active tariff."""
    r = {b["type"]: b["rate"] for b in ss.tariff["slots"]}
    pk, so = r.get("peak", 10.55), r.get("solar", 7.17)
    c1, c2, c3 = st.columns(3)
    bill_m = c1.number_input("Monthly power bill (₹)", 50_000, 50_000_000, 833_000, 10_000, key="bc_bill")
    peak_sh = c2.slider("Share used in peak hours (%)", 5, 60, 30, key="bc_peak")
    move_sh = c3.slider("Share of that work that can move (%)", 5, 80, 30, key="bc_move")
    year = bill_m * 12 * peak_sh / 100 * move_sh / 100 * (1 - so / pk)
    pct = year / (bill_m * 12) * 100
    fee = 12_500 * 12
    keep_sh = max(year - fee, 0)
    html(f"<div class='rh-kpis'><div><b class='g'>{lakh(year)}</b><span>estimated saving a year</span></div>"
         f"<div><b>{pct:.1f}%</b><span>of the yearly bill</span></div><div><b>{rs(year / 300)}</b><span>a working day</span></div></div>"
         f"<div class='rh-split'><i style='width:{(keep_sh / year * 100) if year else 0:.1f}%'><span>Factory keeps {lakh(keep_sh)}</span></i>"
         f"<b><span>Starter plan ₹12,500 a month</span></b></div>"
         f"<div class='rh-note'>Moving work from peak (₹{pk:.2f}) to solar hours (₹{so:.2f}). Or pay 20–30% of proven savings, nothing upfront. "
         f"An estimate, not a promise: the first plans show the real number.</div>")


def savings_split() -> None:
    """Who keeps what from the checked savings (deck slide 10): 20–30% shared-savings fee."""
    ver = [json.loads(r["detail"]) for r in ss.audit.rows() if r["action"] == "verify_savings"]
    tot = sum(v.get("verified_saving_rs") or 0 for v in ver)
    if not ver or tot <= 0:
        return
    with st.container(border=True):
        card_title("rupee", "Who keeps the saving", f"{len(ver)} checked day(s), {rs(tot)} verified on the meters")
        html(f"<div class='rh-split'><i style='width:75%'><span>Factory keeps {rs(tot * .75)}</span></i><b><span>RightHour 25%: {rs(tot * .25)}</span></b></div>"
             "<div class='rh-note'>Shared-savings option: the fee is 20–30% of savings proven on the meters, against a baseline agreed before the pilot.</div>")


TEMPLATES = {
    "Foundry": [("Sand mixer", 75, 3, 6, 22, 17), ("Shot blasting", 90, 3, 6, 23, 18), ("Air compressors", 110, 4, 6, 23, 17),
                ("Fettling grinders", 45, 4, 7, 22, 16), ("Core baking oven", 60, 3, 0, 24, 19)],
    "Plastics moulding": [("Granule dryer", 40, 4, 0, 18, 14), ("Chiller", 90, 5, 6, 24, 17), ("Regrind mill", 55, 2, 8, 22, 18),
                          ("Air compressors", 75, 4, 6, 23, 17)],
    "Textiles": [("Humidification plant", 70, 5, 6, 24, 17), ("Air compressors", 90, 4, 6, 23, 18), ("Dyeing boiler feed pumps", 45, 3, 4, 20, 17)],
    "Cold storage": [("Blast freezer", 150, 4, 0, 24, 18), ("Compressor bank B", 110, 5, 0, 24, 17), ("Defrost cycle", 40, 1, 0, 24, 19)],
}


def template_picker() -> None:
    with st.expander("Start from a factory template", icon=":material/factory:"):
        html("<div class='rh-note'>Ready-made machine lists by type of factory. They replace the machines below; edit them after.</div>")
        c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
        t = c1.selectbox("Type of factory", list(TEMPLATES), key="tpl_pick")
        if c2.button("Use this template", key="tpl_go", width="stretch"):
            rows = [{"Machine": n, "kW": kw, "Hours": h, "Earliest start": e, "Latest finish": l, "Usual start": u} for n, kw, h, e, l, u in TEMPLATES[t]]
            machines, errs = CFG.rows_to_machines(rows)
            if not errs:
                save_cfg(shiftable=machines, rules=[r for r in ss.cfg["machines"]["rules"] if r["type"] == "contract_demand"], learned_rules=[])


def validate_tariff_errors(t: dict) -> list[str]:
    try:
        return [i.text for i in validate_tariff(t) if i.level == "error"]
    except Exception as e:  # noqa: BLE001  a malformed entry is an error, not a crash
        return [str(e)]


def settings_io() -> None:
    """Settings live in this browser session and in a file on the server, which a restart clears. Download them to keep them."""
    with st.container(border=True):
        card_title("database", "Keep your settings", "The hosted app forgets them when it restarts. Download them, and load them back any time.")
        blob = json.dumps({"cfg": ss.cfg, "process": {k: v for k, v in ss.proc.items()}, "price_rules": ss.get("price_rules", PRICE_RULES),
                           "notes": ss.notes_rows, "bias": ss.bias, "bias_history": ss.get("bias_history", []), "library": ss.library},
                          default=str, indent=1).encode()
        c1, c2 = st.columns(2)
        c1.download_button("Download my settings", blob, "righthour_settings.json", "application/json", icon=":material/download:", width="stretch")
        up = c2.file_uploader("Load settings", type=["json"], key="set_up", label_visibility="collapsed")
        if up is not None and st.button("Load these settings", type="primary", icon=":material/upload:", key="set_load"):
            try:
                d = json.loads(up.getvalue())
                _, errs = CFG.rows_to_machines(CFG.machines_to_rows(d["cfg"]["machines"]["shiftable"]))   # shape check
                if errs:
                    raise ValueError(errs[0])
                ids = {m["id"] for m in d["cfg"]["machines"]["shiftable"]}
                clean = []
                for lr in d["cfg"]["machines"].get("learned_rules", []):   # a learned rule must name a real machine and a known kind
                    if not (isinstance(lr, dict) and lr.get("machine") in ids and lr.get("kind") in ("usual", "not_before", "finish_by")):
                        raise ValueError("a learned rule names an unknown machine or kind")
                    h = lr.get("hour")
                    if h is not None and not (isinstance(h, (int, float)) and 0 <= h <= 24):
                        raise ValueError("a learned rule has an hour outside 0 to 24")
                    clean.append({"machine": lr["machine"], "kind": lr["kind"], "hour": int(h) if h is not None else None,
                                  "reason": str(lr.get("reason", ""))[:300], "ts": str(lr.get("ts", ""))[:16]})
                d["cfg"]["machines"]["learned_rules"] = clean
                ss.cfg, ss.tariff = d["cfg"], d["cfg"]["tariff"]
                ss.proc = {**PROCESS_DEFAULTS, **{k: v for k, v in d.get("process", {}).items() if k in PROCESS_DEFAULTS}}
                ss.price_rules = {**PRICE_RULES, **{k: v for k, v in d.get("price_rules", {}).items() if k in PRICE_RULES}}
                ss.notes_rows, ss.notes_ver = d.get("notes", ss.notes_rows), ss.notes_ver + 1
                b = d.get("bias", ss.bias)
                ss.bias = float(b) if isinstance(b, (int, float)) and 0.5 <= b <= 2.0 else ss.bias
                ss.bias_history = [h for h in d.get("bias_history", []) if isinstance(h, dict) and isinstance(h.get("bias"), (int, float))][-200:]
                ss.library = {str(k): v for k, v in d.get("library", {}).items() if isinstance(v, dict) and not validate_tariff_errors(v)}
                save_plant_file(ss.cfg)
                ss.cfg_version += 1
                ss.trigger_plan = True
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"That file is not a RightHour settings file ({e}).")


# ---- the morning brief and the conversation (deck slides 4 and 7)
def _band_strip() -> str:
    return "".join(f"<span style='width:{(b['end_h'] - b['start_h']) / 24 * 100:.2f}%;background:{BAND[b['type']]}'></span>" for b in ss.tariff["slots"])


def morning_brief() -> None:
    """What the manager sees each morning: the moves, what each saves, the checks, and four buttons."""
    if not run or not state.get("plan") or state.get("status") in ("fallback",) or (pending and pending.get("type") == "choose_relaxation"):
        return
    mv = _moves(state)
    per = A.machine_savings(state["problem"], state["baseline"]["schedule"], state["plan"]["schedule"]) if state["baseline"].get("feasible") else {}
    sav = state.get("saving_rs")
    checks = (state.get("policy") or {}).get("checks", [])
    cd_ok = all(c["ok"] for c in checks if c["rule"].startswith(("Max demand",)))
    win_ok = all(c["ok"] for c in checks if " inside " in c["rule"])
    with st.container(border=True, key="brief"):
        html(f"<div class='rh-brief-h'><span>{LOGO}RightHour · plan for {state['forecast']['weekday']}</span><span class='t'>{_ampm(ss.w_plan_at.hour) if hasattr(ss.w_plan_at, 'hour') else ''}</span></div>"
             f"<div class='rh-brief-t'>Move {len(mv)} machine{'s' if len(mv) != 1 else ''}. Save about <b>{rs(round(sav or 0, -2))}</b>.</div>")
        rows = ""
        for m, h in mv:
            b = m["baseline_start_h"]
            x0, x1 = b / 24 * 100, h / 24 * 100
            w = m["duration_h"] / 24 * 100
            rows += (f"<div class='rh-mv'><div class='n'>{escape(m['name'])}</div>"
                     f"<div class='tl'><div class='bands'>{_band_strip()}</div><i class='was' style='left:{x0:.1f}%;width:{w:.1f}%'></i>"
                     f"<i class='now' style='left:{x1:.1f}%;width:{w:.1f}%'></i></div>"
                     f"<div class='a'>{_ampm(b)} → {_ampm(h)}</div><div class='s'>{rs(per.get(m['id'], 0))}</div></div>")
        for f in state.get("fixed", []):
            rows += (f"<div class='rh-mv kept'><div class='n'>{escape(f['name'])}</div><div class='tl'><div class='bands'>{_band_strip()}</div>"
                     f"<i class='fix' style='left:{f['start_h'] / 24 * 100:.1f}%;width:{(f['end_h'] - f['start_h']) / 24 * 100:.1f}%'></i></div>"
                     f"<div class='a'>stays: mid-batch rule</div><div class='s'>kept</div></div>")
        html(f"<div class='rh-mvs'><div class='rh-axis'><span></span><span class='ticks'><em>0h</em><em>6h</em><em>12h</em><em>18h</em><em>24h</em></span></div>{rows}</div>")
        ver = [json.loads(r["detail"]) for r in ss.audit.rows() if r["action"] == "verify_savings"]
        html("<div class='rh-ticks'>"
             f"<span class='{'ok' if win_ok else 'bad'}'>{ICONS['check' if win_ok else 'alert']}All orders on time</span>"
             f"<span class='{'ok' if cd_ok else 'bad'}'>{ICONS['check' if cd_ok else 'alert']}Under contract demand</span></div>"
             + (f"<div class='rh-note'>Last checked plan: <b>{rs(ver[-1].get('verified_saving_rs') or 0)}</b> saved, checked on the meters.</div>" if ver else ""))
        if pending and pending.get("type") == "approval":
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("Approve", type="primary", icon=":material/check:", width="stretch", key="br_ok"):
                ss.audit.log(ctx.run_id, "Plant manager", "approve", tier="human", mode="human", reason="approved from the morning brief")
                do_resume({"action": "approve"})
                st.rerun()
            if b2.button("Close edit" if ss.get("open_change") else "Edit", icon=":material/edit_calendar:", width="stretch", key="br_edit",
                         help="Change start times or keep machines where they are.", type="secondary"):
                ss.open_change, ss.show_why = not ss.get("open_change"), False
                st.rerun()
            if b3.button("Reject", icon=":material/close:", width="stretch", key="br_no"):
                ss.rejecting = True
                st.rerun()
            if b4.button("Hide why" if ss.get("show_why") else "Ask why", icon=":material/help:", width="stretch", key="br_why"):
                ss.show_why, ss.open_change = not ss.get("show_why"), False
                st.rerun()
    if pending and pending.get("type") == "approval" and ss.get("show_why"):
        with st.container(border=True, key="why_box"):
            card_title("chip", "Why this plan", "One answer, from the prices and each machine's hours")
            parts = [f"<li><b>{escape(m['name'])}</b>, {_ampm(m['baseline_start_h']) + ' → ' if m.get('baseline_start_h') is not None else 'new, at '}{_ampm(h)}: {escape(A.why_moved(state['problem'], m, h))}</li>" for m, h in mv]
            kept = [m["name"] for m in state["problem"]["shiftable"] if m["id"] not in {x["id"] for x, _ in mv}]
            html("<ul class='rh-why'>" + "".join(parts) + "</ul>"
                 + (f"<div class='rh-note'>Kept at their usual time: {escape(', '.join(kept))}. No cheaper hour fits their rules.</div>" if kept else "")
                 + (f"<div class='rh-note' style='margin-top:6px'>{escape(state.get('explanation') or '')}</div>" if state.get("explanation") else ""))
    if pending and pending.get("type") == "approval" and ss.get("open_change"):
        with st.container(border=True, key="edit_box"):
            card_title("clock", "Edit the plan", "Saved in the audit trail")

            def _rec(action: str, detail: dict | None = None) -> None:
                ss.audit.log(ctx.run_id, "Plant manager", action, tier="human", mode="human", reason="edited from the plan box", detail=detail or {})
            plan_editor("brief", _rec)


# ---- the manager types anything; the agent works out what it means and acts (deck: the four moments)
def _rid() -> str:
    return ss.run["ctx"].run_id if ss.get("run") else "chat"


def read_message(text: str, ms: list[dict], waiting: str | None) -> dict:
    """Plain code reads the message. With an AI key, the small model reads free wording first; code then checks every field."""
    base = ASK.parse_message(text, ms, waiting)
    if not LLM.live:
        return base
    try:
        menu = "; ".join(f"{m['id']} = {m['name']}" for m in ms)
        raw, tin, tout = LLM.complete(MODELS[ss.provider]["T1"], ASK.LLM_PARSE + menu, text, 150)
        js = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        ids = {m["id"] for m in ms}
        h = js.get("hour")
        got = {"intent": js.get("intent") if js.get("intent") in ASK.INTENTS else base["intent"],
               "machine": js.get("machine") if js.get("machine") in ids else base["machine"],
               "hour": int(h) if isinstance(h, (int, float)) and 0 <= h <= 24 and str(int(h)) in text + str(ASK.find_hour(text)) else base["hour"],
               "kind": js.get("kind") if js.get("kind") in ("usual", "not_before", "finish_by", "Cloudy day", "Clear sky") else base["kind"],
               "text": text}
        ss.audit.log(_rid(), "Orchestrator", "constraint_parse", tier="T1", model=MODELS[ss.provider]["T1"], mode="live API",
                     reason="read the manager's message", detail={"message": text, "read_as": {k: v for k, v in got.items() if k != "text"}})
        return got
    except Exception:  # noqa: BLE001  a failed or odd answer falls back to plain code
        return base


def _save_rule_quiet(lr: dict) -> None:
    cfg = ss.cfg
    cfg["machines"] = {**cfg["machines"], "learned_rules": cfg["machines"].get("learned_rules", []) + [lr]}
    save_plant_file(cfg)
    ss.cfg_version += 1
    ss.audit.log(_rid(), "Factory memory", "learn_rule", tier="human", mode="human", reason=lr.get("reason") or "manager's rule", detail=lr)
    ss.trigger_plan = True


def action_spec() -> dict:
    """Every setting the agent may change, with its range, from the one LIMITS list. Percent kinds are given in %."""
    out = {}
    for box, k, label, kind, opt, _ in LIMITS:
        if kind == "toggle":
            out[k] = ("toggle", None, None, label)
        elif kind == "radio":
            out[k] = ("radio", [v for v, _ in opt], None, label)
        elif kind == "optint":
            out[k] = ("optint", 0, len(MACH["shiftable"]), label)
        elif kind == "money":
            out[k] = ("money", opt[0], opt[1], label)
        elif kind == "pct" or k == "solar_off":
            out[k] = ("pct", 0, 100, label + " (in %)")
        else:
            out[k] = (kind, opt[0], opt[1], label)
    return out


_ALWAYS = r"\b(always|from now on|every ?day|every time|each day|in future|future|going forward|permanent\w*|never|expect it|lasting|remember)\b"
_ONCE = r"\b(today|tonight|tomorrow|just this once|this once|this time|one[- ]?time|only once|just once|for now)\b"


def _rule_scope(text: str) -> str:
    """Does the manager mean a lasting rule ("always", "never", "from now on") or this day only? Unsure: ask."""
    t = text.lower()
    a, o = re.search(_ALWAYS, t), re.search(_ONCE, t)
    return "always" if a and not o else "once" if o and not a else "ask"


def _confirm_rule_answer(choice: str) -> None:
    """The manager's answer to "one-time or in future?". From now on: the day's rule moves into factory memory."""
    lr, ss.confirm_rule = ss.confirm_rule, None
    names = {m["id"]: m["name"] for m in MACH["shiftable"]}
    if choice == "always":
        ss.today_rules = [x for x in ss.today_rules if x is not lr and x != lr]
        _save_rule_quiet(lr)
        say("agent", f"Saved in factory memory: {A.learned_rule_text(lr, names)}. Every future plan follows it.")
    else:
        ss.audit.log(_rid(), "Plant manager", "one_time_rule", tier="human", mode="human", reason="manager: just this day", detail=lr)
        say("agent", f"OK, only for this day: {A.learned_rule_text(lr, names)}. I will not use it in future plans.")


_WHATIF_KEYS = ("proc", "price_rules", "pins", "today_rules", "w_weather", "w_goal", "notes_rows", "notes_ver", "cfg", "cfg_version", "confirm_rule")


def _apply(acts: list[dict], text: str, sim: bool = False) -> list[str]:
    """Applies checked actions to the plan inputs. sim=True: nothing is saved or logged (the what-if run)."""
    spec, done = action_spec(), []
    names = {m["id"]: m["name"] for m in ss.cfg["machines"]["shiftable"]}
    for a in acts:
        t = a["type"]
        if t == "set":
            k, v = a["key"], a["value"]
            if spec[k][0] == "pct":
                v = v / 100
            if k in PRICE_RULES:
                ss.price_rules = {**ss.get("price_rules", PRICE_RULES), k: v}
            else:
                ss.proc = {**ss.proc, k: v}
            done.append(f"{spec[k][3].lower()}: {'no limit' if v is None else v if not isinstance(v, float) or spec[k][0] != 'pct' else f'{v * 100:g}%'}")
        elif t == "pin":
            ss.pins = {**ss.pins, a["machine"]: a["hour"]}
            done.append(f"{names[a['machine']]} at {_ampm(a['hour'])}")
        elif t == "unpin":
            ss.pins = {k: v for k, v in ss.pins.items() if k != a["machine"]}
            done.append(f"{names[a['machine']]} is free to move again")
        elif t == "rule":
            lr = {"machine": a["machine"], "kind": a["kind"], "hour": a["hour"], "reason": text, "ts": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
            scope = _rule_scope(text)
            if sim:
                ss.today_rules = ss.today_rules + [lr]
                done.append(A.learned_rule_text(lr, names))
            elif scope == "always":
                _save_rule_quiet(lr)
                done.append("new rule, from now on: " + A.learned_rule_text(lr, names))
            else:
                ss.today_rules = ss.today_rules + [lr]
                ss.audit.log(_rid(), "Plant manager", "one_time_rule", tier="human", mode="human", reason=text, detail=lr)
                done.append("for this day only: " + A.learned_rule_text(lr, names))
                if scope == "ask":
                    ss.confirm_rule = lr
        elif t == "window":
            cfg = json.loads(json.dumps(ss.cfg))
            cfg["machines"]["shiftable"] = [{**m, "earliest_h": a["earliest"], "latest_finish_h": a["latest"]} if m["id"] == a["machine"] else m
                                            for m in cfg["machines"]["shiftable"]]
            ss.cfg = cfg
            if not sim:
                save_plant_file(cfg)
                ss.cfg_version += 1
            done.append(f"{names[a['machine']]} may run {a['earliest']:02d}:00 to {a['latest']:02d}:00")
        elif t == "weather":
            ss.w_weather = a["value"]
            done.append(f"weather: {a['value'].lower()}")
        elif t == "order":
            ss.notes_rows = ss.notes_rows + [{"Name": "Order from chat", "Kind": "New order", "Note, in plain words": a["text"],
                                              "Could start as early as": None, "Could finish as late as": None, "Side cost if relaxed": ""}]
            ss.notes_ver += 1
            done.append("new order added")
        elif t == "goal":
            ss.w_goal = a["text"]
            done.append(f"goal: “{a['text']}”")
    return done


def _plan_summary(st_: dict) -> dict:
    plan = st_.get("plan") or {}
    return {"status": st_.get("status"), "saving": st_.get("saving_rs"), "schedule": dict(plan.get("schedule") or {}),
            "peak": plan.get("peak_kva"), "cost": plan.get("total_rs"),
            "usual": {m["id"]: m.get("baseline_start_h") for m in (st_.get("problem") or {}).get("shiftable", [])},
            "names": {m["id"]: m["name"] for m in (st_.get("problem") or {}).get("shiftable", [])}}


def simulate_and_offer(acts: list[dict], text: str, how: str, bad: list[str]) -> None:
    """The what-if: apply the change to a copy of the inputs, run the whole agent pipeline on it, show the result, then undo.
    The manager then presses Set as plan (or says yes) to make it the plan."""
    snap = {k: json.loads(json.dumps(ss[k], default=str)) if k in ("cfg", "proc", "notes_rows") else ss.get(k) for k in _WHATIF_KEYS}
    try:
        done = _apply(acts, text, sim=True)
        wgoal = ss.get("w_goal") or goal
        ctx_ = Context(ss.cfg["site"], ss.tariff, ss.cfg["machines"], ss.hist, goal=wgoal or "Cut tomorrow's bill.",
                       cloudy=WEATHER[ss.w_weather], plan_date=plan_date, notes=notes_for_run(), bias=ss.bias,
                       llm=LLMClient("Offline"), audit=AuditLog(":memory:"),
                       process={**ss.proc, **ASK.parse_goal(wgoal), "pins": dict(ss.pins), "today_rules": list(ss.today_rules)})
        _, _, st_new, pend_new = start(ctx_)
        new = _plan_summary(st_new)
    except Exception as e:  # noqa: BLE001
        new, done = {"error": str(e)}, done if "done" in locals() else []
    finally:
        for k, v in snap.items():
            ss[k] = v
    old = _plan_summary((ss.run or {}).get("state") or {})
    ss.whatif = {"acts": acts, "text": text, "done": done, "new": new}
    ss.audit.log(_rid(), "Orchestrator", "what_if", tier="T0", mode="code", reason=text,
                 detail={"actions": acts, "by": how, "saving_now": old.get("saving"), "saving_if": new.get("saving"), "status_if": new.get("status")})
    if "error" in new:
        say("agent", f"I tried it ({'; '.join(done)}), but no plan could be made: {new['error']}")
        ss.whatif = None
        return
    lines = [f"**What if:** {'; '.join(done)}."]
    if new["status"] == "fallback":
        lines.append("With that, no plan passes every guardrail, so the usual schedule would be kept and nothing is saved.")
    else:
        lines.append(f"Saving: {rs(old['saving'] or 0)} now → **{rs(new['saving'] or 0)}** with this change.")
    moves = [f"{new['names'][k]} {_ampm(new['usual'][k])} → {_ampm(h)}" for k, h in new["schedule"].items()
             if new["usual"].get(k) is not None and h != new["usual"][k]]
    if new["status"] != "fallback":
        lines.append("Moves: " + ("; ".join(moves) if moves else "none, every machine keeps its usual time") + ".")
        if new.get("peak"):
            lines.append(f"Peak {new['peak']:.0f} kVA.")
    if bad:
        lines.append(f"I could not: {'; '.join(bad)}.")
    lines.append("Press **Set as plan** to use it, or **Discard**.")
    say("agent", "\n".join(lines))


def whatif_decide(keep: bool) -> None:
    w, ss.whatif = ss.get("whatif"), None
    if not w:
        return
    if not keep:
        ss.audit.log(_rid(), "Plant manager", "what_if_discarded", tier="human", mode="human", reason=w["text"])
        say("agent", "Discarded. The plan stays as it was.")
        return
    done = _apply(w["acts"], w["text"])
    ss.audit.log(_rid(), "Plant manager", "setup_saved", tier="human", mode="human", reason=w["text"], detail={"applied": done, "via": "what-if, set as plan"})
    ss.trigger_plan = True
    say("agent", "Set as the plan: " + "; ".join(done) + ". Planning again, then approve it as usual.")
    if ss.confirm_rule:
        names = {m["id"]: m["name"] for m in ss.cfg["machines"]["shiftable"]}
        say("agent", f"Is “{A.learned_rule_text(ss.confirm_rule, names)}” a one-time thing, or should I expect it in future? "
                     "Answer “just today” or “from now on”.")


def act_on(text: str) -> None:
    """The agentic loop for one message: propose actions (AI or plain code), check each in code, apply, re-plan, report."""
    if ss.get("whatif"):                                          # the agent showed a what-if: yes sets it, no discards it
        t0 = text.strip().lower()
        if re.match(r"^(yes|ok|okay|set|do it|apply|go ahead|use it|sure|fine)\b", t0):
            whatif_decide(True)
            return
        if re.match(r"^(no|discard|cancel|don'?t|leave it|forget it)\b", t0):
            whatif_decide(False)
            return
        # any other message keeps the what-if open; a new change replaces it
    if ss.confirm_rule:                                           # the agent asked "one-time or in future?"
        sc = _rule_scope(text)
        if sc != "ask" or re.match(r"^(just|only|once|no)\b", text.strip().lower()):
            _confirm_rule_answer("always" if sc == "always" else "once")
            return
    r = ss.run or {}
    st_, pend = r.get("state") or {}, r.get("pending")
    ms = (st_.get("problem") or {}).get("shiftable") or MACH["shiftable"]
    names = {m["id"]: m["name"] for m in ms}
    waiting = pend["type"] if pend else None
    options = [{**o, "recommended": o["id"] == pend.get("recommended")} for o in (pend or {}).get("options", [])] if waiting == "choose_relaxation" else []
    spec = action_spec()
    acts, how = None, "plain code"
    if LLM.live:
        try:
            model = MODELS[ss.provider]["T2"]
            acts, toks = ACT.propose_llm(LLM, model, text, ms, spec, waiting, options)
            how = model
            ss.audit.log(_rid(), "Orchestrator", "plan_actions", tier="T2", model=model, mode="live API", reason="free-form instruction: large model",
                         tokens_in=toks[0], tokens_out=toks[1], cost_usd=cost_usd(model, *toks), detail={"message": text, "proposed": acts})
        except Exception as e:  # noqa: BLE001
            ss.llm_err["text"] = str(e)
            acts = None
    acts = ACT.merge(acts, ACT.propose_offline(text, ms, waiting))
    ok, bad = ACT.check(acts, ms, spec, waiting, options)
    changes = [x for x in ok if x["type"] not in ("approve", "reject", "relax", "why", "answer")]
    if changes:                                   # never change the plan straight away: try it first, then the manager decides
        simulate_and_offer(changes, text, how, bad)
        return
    for a in ok:                                                  # actions on the plan that is waiting
        if a["type"] == "approve":
            ss.audit.log(_rid(), "Plant manager", "approve", tier="human", mode="human", reason=text)
            do_resume({"action": "approve"}, echo=False)
        elif a["type"] == "reject":
            do_resume({"action": "reject", "reason": a.get("reason") or text}, echo=False)
        elif a["type"] == "relax":
            do_resume(a["option"], echo=False)
        elif a["type"] == "why":
            mv = _moves(st_)
            hit = [(x, h) for x, h in mv if x["id"] == a.get("machine")] if a.get("machine") else mv
            for x, h in hit:
                say("agent", A.why_moved(st_["problem"], x, h))
            if not hit:
                say("agent", f"{names.get(a.get('machine'), 'That machine')} stays at its usual time: no cheaper hour fits its rules."
                    if a.get("machine") else (st_.get("explanation") or "Generate a plan first."))
        elif a["type"] == "answer":
            say("agent", ask_answer(text))
    if not ok:
        say("agent", "That does not change the plan" + (f": {'; '.join(bad)}." if bad else ".") + " Try, for example, “keep the oven after 8 pm” or “use a 5% margin”.")
    elif bad:
        say("agent", f"I could not: {'; '.join(bad)}.")


def handle_chat(preset: str | None = None) -> None:
    """Runs before the page is drawn, so it may change the plan inputs and ask for a new plan."""
    text = (preset or ss.get("cv_msg") or "").strip()
    if not text:
        return
    say("manager", text)
    act_on(text)


GOAL_WORDS = {"max_moves": lambda v: "no limit on machines moved" if v is None else f"move at most {v} machine(s)",
              "margin_pct": lambda v: f"keep a {v}% margin under the contract", "block_new_peak": lambda v: "never set a new monthly peak",
              "min_saving_rs": lambda v: f"only change the schedule for {rs(v)} or more"}


def chat_chips() -> list[str]:
    if ss.get("whatif"):
        return ["Why?"]
    if ss.get("confirm_rule"):
        return ["Just today", "From now on"]
    if pending and pending.get("type") == "choose_relaxation":
        return ["Yes, that is fine", "Why?"]
    if pending and pending.get("type") == "approval":
        return ["Approve", "Why these moves?", "Fewer moves, please", "Clouds tomorrow", "No. The press line needs the night crew."]
    return ["What's the status?", "How much have we saved?", "Clouds tomorrow"]


def conversation() -> None:
    """The four moments with the manager, as they happen: re-plans, explains, remembers, reacts."""
    with st.container(border=True, key="convo"):
        card_title("forum", "Talk to the agent", "Every message is saved in the audit trail")
        bubbles = "".join(f"<div class='b {m['who']}'><b>{'Agent' if m['who'] == 'agent' else 'You'}:</b> "
                          f"{re.sub(r'[*][*](.+?)[*][*]', r'<b>\1</b>', escape(m['text'])).replace(chr(10), '<br>')}<i>{m['ts']}</i></div>"
                          for m in reversed(ss.feed[-30:])) or "<div class='rh-note'>The agent's messages appear here.</div>"
        html(f"<div class='rh-chat'>{bubbles}</div>")
        # RE-PLANS: the agent asks which rule may flex
        if pending and pending.get("type") == "choose_relaxation":
            for o in [o for o in pending["options"] if o["feasible"]][:3]:
                if st.button(f"Yes: {o['text']}", key=f"cv_relax_{o['id']}", width="stretch",
                             type="primary" if o["id"] == pending.get("recommended") else "secondary"):
                    do_resume(o["id"])
                    st.rerun()
        # WHAT-IF: the change was tried; the manager decides
        if ss.get("whatif"):
            c1, c2 = st.columns(2)
            if c1.button("Set as plan", key="wi_set", type="primary", width="stretch", icon=":material/check:"):
                whatif_decide(True)
                st.rerun()
            if c2.button("Discard", key="wi_drop", width="stretch", icon=":material/close:"):
                whatif_decide(False)
                st.rerun()
        # LEARNS: one-time, or a rule for the future?
        if ss.confirm_rule:
            html(f"<div class='rh-note'><b>One-time, or in future?</b> {escape(A.learned_rule_text(ss.confirm_rule, {m['id']: m['name'] for m in MACH['shiftable']}))}</div>")
            c1, c2 = st.columns(2)
            if c1.button("Just today", key="cv_once", width="stretch", icon=":material/today:"):
                _confirm_rule_answer("once")
                st.rerun()
            if c2.button("From now on", key="cv_always", type="primary", width="stretch", icon=":material/bookmark_add:"):
                _confirm_rule_answer("always")
                st.rerun()
        # REJECT: the manager says why
        if ss.get("rejecting") and pending and pending.get("type") == "approval":
            why = st.text_input("Tell the agent why", key="cv_why", placeholder="e.g. The press line needs the night crew.")
            c1, c2 = st.columns(2)
            if c1.button("Reject the plan", type="primary", key="cv_rej", width="stretch", disabled=not why.strip()):
                ss.rejecting = False
                do_resume({"action": "reject", "reason": why.strip()})
                ss.rule_hint = why.strip()
                st.rerun()
            if c2.button("Cancel", key="cv_rej_x", width="stretch"):
                ss.rejecting = False
                st.rerun()
        # REMEMBERS: every "no" can become a rule
        if ss.get("ask_rule"):
            ms = MACH["shiftable"]
            c1, c2, c3 = st.columns([1.4, 1.4, 0.8])
            mid = c1.selectbox("Machine", [m["id"] for m in ms], format_func={m["id"]: m["name"] for m in ms}.get, key="cv_rm")
            kind = c2.selectbox("Rule", ["usual", "not_before", "finish_by"], key="cv_rk",
                                format_func={"usual": "Keep at its usual time", "not_before": "Never start before", "finish_by": "Always finish by"}.get)
            hour = c3.selectbox("Time", HOURS, index=18, key="cv_rh", disabled=kind == "usual")
            c1, c2 = st.columns(2)
            if c1.button("Save as a rule", type="primary", icon=":material/bookmark_add:", key="cv_rs", width="stretch"):
                lr = {"machine": mid, "kind": kind, "hour": from_t(hour), "reason": ss.get("rule_hint", ""), "ts": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
                names = {m["id"]: m["name"] for m in ms}
                ss.ask_rule = False
                say("agent", f"Saved as a rule. {A.learned_rule_text(lr, names)}.")
                ss.audit.log(ctx.run_id if ctx else "memory", "Factory memory", "learn_rule", tier="human", mode="human",
                             reason=ss.get("rule_hint") or "manager's no", detail=lr)
                save_cfg(learned_rules=ss.cfg["machines"].get("learned_rules", []) + [lr])
            if c2.button("No, just this once", key="cv_rs_x", width="stretch"):
                ss.ask_rule = False
                say("agent", "Fine. Only this plan was rejected.")
                st.rerun()
        # anything else: the manager types freely, or taps an example
        if not ss.get("ask_rule") and not ss.get("rejecting"):
            chips = chat_chips()
            with st.container(horizontal=True, gap="small", key="chips"):     # small boxes that wrap, never cut short
                for t in chips:
                    st.button(t, key=f"chip_{t}", on_click=handle_chat, args=(t,), width="content", type="secondary")
        st.chat_input("Ask, tell or instruct the agent…", key="cv_msg", on_submit=handle_chat)


def work_order_card() -> None:
    if not ss.approved:
        return
    ast = ss.approved[1]
    with st.container(border=True):
        card_title("clipboard", "Work order", f"For {ast['forecast']['weekday']} {ast['forecast']['date']}. People switch the machines.")
        wo = pd.read_csv(io.BytesIO(work_order_csv(ast)))
        body = "".join(f"<tr><td>{escape(str(r.machine))}</td><td class='m'>{r.start} to {r.end}</td><td>{escape(str(r.note))}</td></tr>" for r in wo.itertuples())
        html(f"<table class='rh-log'><thead><tr><th>Machine</th><th>Runs</th><th>Note</th></tr></thead><tbody>{body}</tbody></table>")
        sent = any(r["action"] == "work_order_sent" and json.loads(r["detail"]).get("day") == str(ast["forecast"]["date"]) for r in ss.audit.rows())
        c1, c2 = st.columns(2)
        if c1.button("Sent to the shift lead" if sent else "Send to the shift lead", type="secondary" if sent else "primary", icon=":material/send:",
                     width="stretch", key="wo_send", disabled=sent):
            ss.audit.log(ss.approved[0].run_id, "Orchestrator", "work_order_sent", tier="T0", mode="code",
                         reason="work order sent to the shift lead (prototype: logged, not emailed)", detail={"day": str(ast["forecast"]["date"])})
            say("agent", "Work order sent to the shift lead.")
            st.rerun()
        c2.download_button("Download (CSV)", work_order_csv(ast), f"work_order_{ast['forecast']['date']}.csv", "text/csv",
                           key="wo_today", icon=":material/download:", width="stretch")


def why_plan() -> None:
    """The reasoning, machine by machine: what moved, between which price bands, what it saves, and why the rest stayed."""
    prob, sched = state["problem"], state["plan"]["schedule"]
    per = A.machine_savings(prob, state["baseline"]["schedule"], sched) if state["baseline"].get("feasible") else {}
    types = {h: b["type"] for b in ss.tariff["slots"] for h in range(int(b["start_h"]), int(b["end_h"]))}
    rows = ""
    for m in prob["shiftable"]:
        b, h = m.get("baseline_start_h"), sched[m["id"]]
        if b is not None and h != b:
            rows += (f"<tr><td><b>{escape(m['name'])}</b></td><td>{types.get(b, '')} {b:02d}:00 → {types.get(h, '')} {h:02d}:00</td>"
                     f"<td class='r' style='color:var(--green)'><b>{rs(per.get(m['id'], 0))}</b></td></tr>"
                     f"<tr><td colspan='3' class='rh-note'>{escape(A.why_moved(prob, m, h))}</td></tr>")
        else:
            lim = "kept by you" if m["id"] in ss.pins else ("its window has no cheaper hour" if b is not None else "new order, placed at the cheapest fit")
            rows += f"<tr><td>{escape(m['name'])}</td><td>stays {h:02d}:00</td><td class='rh-note'>{lim}</td></tr>"
    for f in state.get("fixed", []):
        rows += f"<tr><td>{escape(f['name'])}</td><td>{f['start_h']:02d}:00–{f['end_h']:02d}:00</td><td class='rh-note'>fixed batch, never moved</td></tr>"
    g = {k: ss.proc.get(k) for k in ("margin_pct", "block_new_peak", "max_moves", "min_saving_rs")}
    gl = [f"{g['margin_pct']}% margin" if g["margin_pct"] else "", "no new monthly peak" if g["block_new_peak"] else "",
          f"at most {g['max_moves']} moved" if g["max_moves"] is not None else "", f"min saving {rs(g['min_saving_rs'])}" if g["min_saving_rs"] else ""]
    html(f"<table class='rh-log'><tbody>{rows}</tbody></table><div class='rh-note' style='margin-top:6px'>Peak {state['plan']['peak_kva']:.0f} kVA "
         f"of {SITE['contract_demand_kva']} contract. Guardrails used: {', '.join(x for x in gl if x) or 'the standard ones'}.</div>")


def page_today() -> None:
    page_header("Today's plan", escape(str(SITE['name'])))
    next_step(state if run else None, pending, ss.verify is not None)
    run_controls()
    step_strip(state if run else None, pending, ss.verify is not None)
    if run:
        c1, c2 = st.columns([1.5, 1], gap="medium")
        with c1:
            morning_brief()
            work_order_card()
        with c2:
            conversation()
    plan_tab()


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
        card_title("rupee", "Free bill check", "A savings estimate from one power bill")
        bill_check()
    with st.container(border=True):
        card_title("doc", "New price order", "Read a tariff order from the regulator, check it, sign it off")
        pane_orders([])


def page_setup() -> None:
    page_header("Plant setup", "Set once, change when the plant changes.")
    settings_io()
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


# (name, limit key to edit or None, where it comes from, how the rule check names it, fixed value text)
GUARDRAILS = [
    ("Never above the contract demand", None, "Your supply contract · Plant setup → Prices and contract", ("Max demand",), lambda: f"{SITE['contract_demand_kva']:,} kVA"),
    ("Safety margin under the contract", "margin_pct", "You · here, Control room or chat (“keep a 10% margin”)", ("Max demand",), None),
    ("Never set a new monthly peak", "block_new_peak", "You · this month's max from your bill", ("Monthly max",), None),
    ("Most machines moved in a day", "max_moves", "You · or chat (“fewer moves”)", ("At most",), None),
    ("Smallest saving worth a change", "min_saving_rs", "You · or chat (“save at least ₹2,000”)", ("Saving worth",), None),
    ("Every machine inside its hours", None, "Machines table · Control room → Factory systems", (" inside ",), lambda: f"{len(MACH['shiftable'])} machines"),
    ("Crew rules hold", None, "Crew rules table · Control room → Factory systems", ("not at the same time",), lambda: f"{sum(r['type'] == 'no_overlap' for r in MACH['rules'])} rule(s)"),
    ("Fixed batches never move or stop", None, "Today's production notes · Upload data", ("untouched",), lambda: f"{len(state.get('fixed') or [])} batch(es)"),
    ("Rules learned from your “no”", None, "Your rejections · Factory memory", (), lambda: f"{len(ss.cfg['machines'].get('learned_rules', []))} rule(s)"),
    ("Peak price at least × normal", "peak_x", "Rule 8A, Electricity (Rights of Consumers) Rules 2023", (), None),
    ("Solar discount at least", "solar_off", "Rule 8A, Electricity (Rights of Consumers) Rules 2023", (), None),
    ("Peak at least × solar", "spread_x", "Deck: 1.2 ÷ 0.8 = 1.5×", (), None),
    ("Meter spikes flagged", "spike_x", "Data quality · CEA metering rules", (), None),
    ("Lowest normal power factor", "pf_min", "Data quality · CEA metering rules", (), None),
    ("Meter gaps flagged", "max_gap_h", "Data quality · your meter link", (), None),
    ("Stuck meter flagged", "stuck_h", "Data quality · your meter link", (), None),
    ("Holidays and shutdowns flagged", "shutdown_pct", "Data quality · your plant's calendar", (), None),
    ("Meter data recent enough", "stale_days", "Data quality · your meter link", (), None),
    ("Small AI tries before the large one", "small_tries", "AI router · deck slide 6", (), None),
    ("Names removed before any AI", "redact_names", "DPDP Act 2023 · deck slide 12", (), None),
    ("A person approves every plan", None, "RightHour design · MeitY AI Guidelines 2025", (), lambda: "always"),
    ("AI never writes a number", None, "Number guard in code · deck slide 6", (), lambda: "always"),
    ("Uploads read only as data", None, "Prompt-injection guard · deck slide 12", (), lambda: "always"),
]


def page_guardrails() -> None:
    page_header("Guardrails", "Every limit the agent must keep: change it, see where it comes from, see whether it held.")
    update_plan_bar("gr")
    checks = (state.get("policy") or {}).get("checks", []) if run else []
    spec = {x[1]: x for x in LIMITS}
    head = st.columns([3, 3.2, 1], vertical_alignment="bottom")
    for c, t in zip(head, ["Guardrail and where it comes from", "Setting", "This run"]):
        c.markdown(f"<div class='rh-sub' style='margin:4px 0'>{t}</div>", unsafe_allow_html=True)
    with st.container(key="grrows"):
        for i, (name, key, src, match, fixed) in enumerate(GUARDRAILS):
            with st.container(border=True, key=f"gr_{i}"):
                c1, c2, c4 = st.columns([3, 3.2, 1], vertical_alignment="center")
                c1.markdown(f"<div class='gr-n'>{escape(name)}</div><div class='gr-s'>{escape(src)}</div>", unsafe_allow_html=True)
                with c2:
                    if key in spec:
                        limit_widget(spec[key], "gr")
                    else:
                        html(f"<div class='gr-fixed'>{ICONS['lock']}<span>{escape(fixed())}</span><em>fixed</em></div>")
                hit = [x for x in checks if any(x["rule"].startswith(m) or m in x["rule"] for m in match)] if match else []
                if not hit:
                    tag = "<span class='rh-tag'>not checked</span>"
                else:
                    ok = all(x["ok"] for x in hit)
                    tag = f"<span class='rh-tag {'ok' if ok else 'ask'}'>{ICONS['check' if ok else 'alert']}{'held' if ok else 'broken'}</span>"
                c4.markdown(f"<div class='gr-t'>{tag}</div>", unsafe_allow_html=True)


def page_control_room() -> None:
    page_header("Control room", "Every part of the agent, and every rule it follows.")
    ss.setdefault("cr_view", "Diagram")
    v = st.segmented_control("Control room view", ["Diagram", "All rules and limits"], key="cr_view", label_visibility="collapsed",
                             format_func=lambda x: {"Diagram": ":material/account_tree: Diagram", "All rules and limits": ":material/tune: All rules and limits"}[x]) or "Diagram"
    if v == "Diagram":
        control_room()
    else:
        all_limits()


def page_check() -> None:
    page_header("Next-day check", "After the day runs, compare the real meter readings with the plan. The agent learns from the gap.")
    if not ss.approved:
        html(f"<div class='rh-empty'><h2>Nothing to check yet</h2><p>Approve a plan on Today first. After the day, come back here "
             "to see the real saving.</p></div>")
        st.button("Go to Today", icon=":material/today:", type="primary", on_click=goto, args=("Today",), key="chk_today")
    else:
        with st.container(border=True):
            pane_loop_day([])
    savings_split()
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
    if ss.proc.get("redact_names", True):          # names removed before any AI sees the data (deck slide 12)
        f["plant"] = "the plant"
    if not LLM.live:
        return ASK.offline_answer(q, f)
    model = MODELS[ss.provider]["T2"]
    history = "".join(f"{m['role']}: {m['text']}\n" for m in ss.chat[-6:])
    facts = ASK.context_text(f)
    allowed = numbers_in(facts + " " + q + " " + history) | {str(i) for i in range(25)} | {"00", "15", "30", "45"}   # hours, minutes and small counts are fine
    sysmsg, bad = ASK.SYSTEM + "\n\n" + facts, []
    for attempt in (1, 2):                       # every number in the answer must be in the facts; one redo, then plain code
        try:
            text, tin, tout = LLM.complete(model, sysmsg, f"{history}manager: {q}", 700)
        except Exception as e:  # noqa: BLE001
            return f"The AI model did not answer ({e}). Here is what plain code can tell you:\n\n" + ASK.offline_answer(q, f)
        bad = number_guard(text.replace(":", " "), allowed) + (["the answer is cut short"] if len(text.strip()) < 20 else [])
        ss.audit.log(ctx.run_id if ctx else "ask", "Ask RightHour", "answer_question", tier="T2", model=model, mode=LLM.mode,
                     reason="free-form question: large model", tokens_in=tin, tokens_out=tout,
                     cost_usd=cost_usd(model, tin, tout), detail={"question": q, "attempt": attempt, "number_guard": bad or "passed"})
        if not bad:
            return text
        sysmsg = (ASK.SYSTEM + f"\n\nYour last answer used numbers that are not in the facts ({', '.join(bad[:6])}). "
                  "Answer again using only numbers that appear in the facts.\n\n" + facts)
    return ("The AI answer had numbers I could not check, so plain code answers instead:\n\n" + ASK.offline_answer(q, f))


ss.setdefault("chat", [])


@st.dialog("Ask RightHour", width="small")
def ask_dialog() -> None:
    big = bool(ss.get("ask_big"))
    if big:                                       # full screen: the corner panel grows to fill the window
        html("<style>[data-testid='stDialog'] > div{left:24px !important;right:24px !important;top:24px !important;bottom:24px !important;"
             "width:auto !important;max-height:none !important;}</style>")
    c1, c2 = st.columns([5, 1.4], vertical_alignment="center")
    c1.markdown(f"<div class='rh-note'>{'Large model: ' + escape(MODELS[ss.provider]['T2']) if LLM.live else 'Offline: answers status, savings and history.'}</div>",
                unsafe_allow_html=True)
    if c2.button("Smaller" if big else "Full screen", key="ask_big_btn", type="tertiary",
                 icon=":material/close_fullscreen:" if big else ":material/open_in_full:"):
        ss.ask_big = not big
        st.rerun(scope="fragment")
    q = None
    sugg = st.empty()                             # suggestions only before the conversation starts
    if not ss.chat:
        with sugg.container():
            for i, sug in enumerate(ASK.SUGGESTIONS):
                if st.button(sug, key=f"sug_{i}", width="stretch", type="secondary"):
                    q = sug
    log = st.container(height=(560 if big else 300) if ss.chat else 120, border=False, key="chatlog")
    with log:
        for m in ss.chat:
            with st.chat_message("user" if m["role"] == "manager" else "assistant", avatar=":material/person:" if m["role"] == "manager" else ":material/bolt:"):
                st.markdown(m["text"])
    q = st.chat_input("Ask about the plan, savings, history...", key="ask_in") or q
    if q:
        sugg.empty()
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


def _setup_done() -> None:
    ss.setup_done = True


@st.dialog("Set up the AI model", width="medium", icon=":material/settings:", on_dismiss=_setup_done)
def setup_dialog() -> None:
    """Shown each time the app opens, so the model and its connection are sorted before the first plan."""
    html("<div class='rh-note'>Pick a provider, check the models, and press <b>Test connection</b>. "
         "Without a model the app still plans: plain code writes the text. Numbers never come from a model.</div>")
    model_controls()
    if (ss.w_provider or ss.provider, ss.get(f"key_{ss.w_provider or ss.provider}") or None) != ss.llm_sig:
        st.rerun()                            # provider or key changed: rebuild the AI client, and this window opens again
    ok = str(ss.get("model_ok", "")).startswith("Connected") or ss.provider == "Offline"
    if st.button("Start planning" if ok else "Continue without testing", type="primary" if ok else "secondary",
                 icon=":material/play_arrow:", width="stretch", key="setup_go"):
        ss.setup_done = True
        st.rerun()


# ------------------------------------------------------------------ draw
if not ss.get("setup_done"):
    setup_dialog()
sidebar()
with st.container(key="askfab"):
    if st.button("Ask RightHour", icon=":material/forum:", type="primary", key="ask_open"):
        ask_dialog()
{"Today": page_today, "Guardrails": page_guardrails, "Upload data": page_data, "Plant setup": page_setup, "Control room": page_control_room,
 "Next-day check": page_check, "History": page_history}[ss.view]()
persist()
