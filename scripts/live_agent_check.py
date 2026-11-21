"""Live test of the agentic chat with your Groq key. Run:  .venv/bin/python scripts/live_agent_check.py

Reads GROQ_API_KEY from the environment or .streamlit/secrets.toml. For each message it shows what the large model
proposed, what code accepted or refused, and the plan's saving after re-planning, so you can see the output change.
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get("GROQ_API_KEY"):
    sec = ROOT / ".streamlit" / "secrets.toml"
    m = re.search(r'GROQ_API_KEY\s*=\s*"([^"]+)"', sec.read_text()) if sec.exists() else None
    if not m:
        sys.exit("No Groq key. Put GROQ_API_KEY = \"...\" in .streamlit/secrets.toml")
    os.environ["GROQ_API_KEY"] = m.group(1)

from righthour import act as ACT, config as CFG, data as D  # noqa: E402
from righthour.agents import PROCESS_DEFAULTS, Context  # noqa: E402
from righthour.audit import AuditLog  # noqa: E402
from righthour.graph import start  # noqa: E402
from righthour.llm import LLMClient  # noqa: E402
from righthour.router import MODELS  # noqa: E402

cfg, hist = CFG.defaults(), D.synthetic_history()
MS = cfg["machines"]["shiftable"]
SPEC = {"margin_pct": ("slider", 0, 20, "Safety margin under the contract (%)"), "block_new_peak": ("toggle", None, None, "Never set a new monthly peak"),
        "max_moves": ("optint", 0, len(MS), "Most machines moved in a day"), "min_saving_rs": ("money", 0, 1_000_000, "Smallest saving worth a change (₹)"),
        "solar_on": ("toggle", None, None, "Count rooftop solar"), "use_learning": ("toggle", None, None, "Use the learned correction")}
llm = LLMClient("Groq")
model = MODELS["Groq"]["T2"]


def plan(proc, pins, rules, cloudy):
    c = {**cfg, "machines": {**cfg["machines"], "learned_rules": rules}}
    ctx = Context(cfg["site"], cfg["tariff"], c["machines"], hist, cloudy=cloudy, audit=AuditLog(pathlib.Path(tempfile.mktemp())),
                  process={**PROCESS_DEFAULTS, **proc, "pins": pins})
    _, _, st, _ = start(ctx)
    return st.get("status"), st.get("saving_rs"), (st.get("plan") or {}).get("schedule")


MESSAGES = ["Fewer moves please, the floor is short-staffed", "Give us some headroom on the grid, maybe 8 percent",
            "The press line needs the night crew", "Run the compressors at 1 pm", "It's going to be overcast tomorrow",
            "Don't count the rooftop panels, they're being cleaned", "Only change things if we save at least 3000 rupees",
            "Undo the limit on moves and save as much as you can"]
proc, pins, rules, cloudy = {}, {}, [], False
print("start:", plan(proc, pins, rules, cloudy)[:2])
for msg in MESSAGES:
    try:
        acts, _ = ACT.propose_llm(llm, model, msg, MS, SPEC, "approval", [])
    except Exception as e:  # noqa: BLE001
        print(f"\n> {msg}\n  model error: {e}")
        continue
    ok, bad = ACT.check(acts, MS, SPEC, "approval", [])
    for a in ok:
        if a["type"] == "set":
            proc[a["key"]] = a["value"]
        elif a["type"] == "pin":
            pins[a["machine"]] = a["hour"]
        elif a["type"] == "rule":
            rules.append({"machine": a["machine"], "kind": a["kind"], "hour": a["hour"]})
        elif a["type"] == "weather":
            cloudy = a["value"] == "Cloudy day"
    status, sav, sched = plan(proc, pins, rules, cloudy)
    print(f"\n> {msg}\n  proposed: {acts}\n  refused:  {bad or '-'}\n  plan:     {status}, saving {round(sav) if sav else sav}, {sched}")
