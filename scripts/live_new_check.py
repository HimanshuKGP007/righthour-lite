"""Live test of the newer parts with your Groq key:  .venv/bin/python scripts/live_new_check.py

1. "Why this plan": the model rewrites the explanation; code checks every number (the log shows each try).
2. Ask RightHour: the model answers from the facts; code checks every number in the answer.
3. Chat rules: the model turns a message into a rule; code decides one-time, lasting, or ask the manager.
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get("GROQ_API_KEY"):
    m = re.search(r'GROQ_API_KEY\s*=\s*"([^"]+)"', (ROOT / ".streamlit" / "secrets.toml").read_text())
    os.environ["GROQ_API_KEY"] = m.group(1)

from righthour import act as ACT, ask as ASK, config as CFG, data as D, router as R  # noqa: E402
from righthour.agents import PROCESS_DEFAULTS, Context  # noqa: E402
from righthour.audit import AuditLog  # noqa: E402
from righthour.graph import start  # noqa: E402
from righthour.llm import LLMClient, number_guard, numbers_in, suggest_models  # noqa: E402

llm = LLMClient("Groq")
small, large = suggest_models("Groq", llm.list_models())
R.set_models("Groq", small, large)
print("models:", small, "/", large)
cfg, hist = CFG.defaults(), D.synthetic_history()
audit = AuditLog(pathlib.Path(tempfile.mktemp()))
ctx = Context(cfg["site"], cfg["tariff"], cfg["machines"], hist, llm=llm, audit=audit, process=dict(PROCESS_DEFAULTS))
_, _, st, _ = start(ctx)
print("\n1. WHY THIS PLAN")
for r in audit.rows():
    if r["action"] == "explain":
        print("  try:", r["model"], "| number check:", r["detail"][:120])
print("  text:", (st.get("explanation") or "")[:300])

print("\n2. ASK RIGHTHOUR")
sav = st.get("saving_rs") or 0
facts = (f"# Today's plan\nSaving: ₹{sav:,.0f}. Plan cost ₹{st['plan']['total_rs']:,.0f}; usual ₹{st['baseline']['total_rs']:,.0f}. "
         f"Peak {st['plan']['peak_kva']:.0f} kVA. Contract demand {cfg['site']['contract_demand_kva']} kVA. Schedule: {st['plan']['schedule']}")
for q in ["How much do we save today?", "What will we save in a year if every day is like today?"]:
    allowed = numbers_in(facts + " " + q) | {str(i) for i in range(25)} | {"00", "15", "30", "45"}
    out, _, _ = llm.complete(large, ASK.SYSTEM + "\n\n" + facts, f"manager: {q}", 400)
    bad = number_guard(out.replace(":", " "), allowed)
    print(f"  > {q}\n    number check: {bad or 'passed'}\n    answer: {out[:220]!r}")

print("\n3. CHAT RULES: ONE-TIME OR FUTURE")
src = (ROOT / "app.py").read_text()
exec(src[src.index("_ALWAYS = "):src.index("def _confirm_rule_answer")])
MS = cfg["machines"]["shiftable"]
spec = {"max_moves": ("optint", 0, len(MS), "Most machines moved in a day")}
for msg in ["The press line must not start before 3 pm", "From now on the oven never starts before 7 pm",
            "Just for tomorrow, the pumps must finish by 6 am"]:
    acts, _ = ACT.propose_llm(llm, large, msg, MS, spec, None, [])
    ok, bad = ACT.check(ACT.merge(acts, ACT.propose_offline(msg, MS, None)), MS, spec, None, [])
    rules = [a for a in ok if a["type"] == "rule"]
    print(f"  > {msg}\n    rule: {rules}\n    scope: {_rule_scope(msg)}")
