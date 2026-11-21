"""Does the agent understand many ways of saying things? Groq, large model, no plain-code help.
Run:  .venv/bin/python scripts/live_understand_check.py"""
import os, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get("GROQ_API_KEY"):
    os.environ["GROQ_API_KEY"] = re.search(r'GROQ_API_KEY\s*=\s*"([^"]+)"', (ROOT / ".streamlit" / "secrets.toml").read_text()).group(1)
from righthour import act as ACT, config as CFG  # noqa: E402
from righthour.llm import LLMClient  # noqa: E402

MS = CFG.defaults()["machines"]["shiftable"]
SPEC = {"margin_pct": ("slider", 0, 20, "Safety margin under the contract (%)"), "block_new_peak": ("toggle", None, None, "Never set a new monthly peak"),
        "max_moves": ("optint", 0, len(MS), "Most machines moved in a day"), "min_saving_rs": ("money", 0, 1_000_000, "Smallest saving worth a change (₹)"),
        "solar_on": ("toggle", None, None, "Count rooftop solar"), "use_learning": ("toggle", None, None, "Use the learned correction")}
CASES = [  # message, what must appear in the actions
    ("I want to reduce machine switches to 1", "max_moves"), ("Only touch one machine today", "max_moves"),
    ("Please don't shuffle more than a couple of machines", "max_moves"), ("The team hates changes, keep things mostly as they are", "max_moves"),
    ("We're short on night staff so the shot blaster can't run late", "shotblast"), ("Compressors should be done before lunch", "compressors"),
    ("Let's stay well clear of our contract limit", "margin_pct"), ("Don't bother unless it's worth at least five grand", "min_saving_rs"),
    ("Panels are covered in dust this week", "solar_on"), ("Monsoon is coming tomorrow", "weather"),
    ("Can we push the oven a bit later, say 9 at night?", "oven"), ("Squeeze out every rupee", "max_moves"),
    ("Pumps can start any time after 3 in the morning", "pumps"), ("We got a rush order: 150 kW grinding job, 3 hours, must finish by 6 pm", "order"),
    ("Never let the demand go above this month's highest", "block_new_peak"), ("How much are we saving today?", "answer"),
    # unseen phrasings, none of them in the agent's examples
    ("The press operator leaves at 4, so it has to be wrapped up by then", "press"), ("Grid's been flaky, give us breathing room", "margin_pct"),
    ("Honestly just leave the pumps alone", "pumps"), ("Big wedding in town, half the crew is off, minimal disruption please", "max_moves"),
    ("Sky's clear all day tomorrow", "Clear sky"), ("Forget the forecast tweak, use plain averages", "use_learning"),
    ("Oven guy only comes in after 7 pm", "oven"), ("Tiny savings aren't worth the hassle, 2000 minimum", "min_saving_rs"),
    ("Is the plan safe for our contract?", "answer"), ("Inverter tripped, no solar today", "solar_on"),
]
llm = LLMClient("Groq")
ok_n = 0
for msg, want in CASES:
    try:
        acts, _ = ACT.propose_llm(llm, "openai/gpt-oss-120b", msg, MS, SPEC, "approval", [])
    except Exception as e:  # noqa: BLE001
        acts = [{"error": str(e)[:80]}]
    hit = want in str(acts)
    ok_n += hit
    print(("PASS " if hit else "FAIL ") + f"{msg!r}\n     {acts}")
print(f"\n{ok_n}/{len(CASES)} understood")
