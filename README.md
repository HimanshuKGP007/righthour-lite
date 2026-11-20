# RightHour Lite: ToD Bill Optimiser Agent (prototype)

Built to Section 8 of the RightHour deck design document: one site, one tariff, one day ahead.
It reads a plant's meter data, the Time-of-Day tariff, a solar forecast and the production rules. It proposes
tomorrow's schedule with a rupee saving and a plain-English explanation, asks a person to approve it, then checks
the real saving the next day.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py          # the demo UI
python cli_demo.py            # the 90-second script in a terminal
pytest -q                     # 15 tests, including the ₹2,704 hand calculation
```

**Real data.** Download `Steel_industry_data.csv` from the UCI Machine Learning Repository ("Steel Industry Energy
Consumption"). Load it from the sidebar, or run `python cli_demo.py path/to/Steel_industry_data.csv`. The load is
auto-scaled to the demo site and labelled as Korean data. Without the file, choose **Use sample**, a labelled
synthetic profile. `data/synthetic_in_uci_format.csv` tests the upload path.

**Live language-model calls (optional, Groq).** Set `GROQ_API_KEY` (or paste the key in the sidebar). T1 steps then call
`llama-3.1-8b-instant` and T2 steps `llama-3.3-70b-versatile`. No extra package is needed. Without a key they run as
offline templates and parsers, and every log row says which mode ran. Numbers never come from a language model in either mode.

## The screens

The left menu has one screen per job:

- **Today:** pick the day, the solar forecast and the goal, press **Generate plan**. See the saving, the max demand and the schedule chart. Write a note, then approve or reject. **Change the plan** lets you set start times and tick **Keep** for machines that must not move; **Update plan** plans again around them. Every decision and note goes to the audit trail. The work order appears here after approval.
- **Upload data:** meter readings (upload a CSV, or use the steel plant data or the sample), data health, the live feed, the day's production notes, and a new price order (paste it or upload a .txt).
- **Plant setup:** machines and crew rules, prices and contract, rooftop solar, and the AI model.
- **Control room:** the agents' design diagram, lit up from the run. Click a box. A panel opens with **Results** (its numbers, charts and steps), **Controls** (every setting of that box) and **Activity** (what it did, in plain words). Every setting in the app can be reached from a box. **Guardrails** are set here: a safety margin under the contract demand, never set a new monthly peak, the most machines moved in a day, and the smallest saving worth a change. The optimiser obeys them and the rule check enforces them.

**Search settings** at the top of the left menu finds any setting (for example "small model", "margin", "solar") and opens its box in the Control room.
- **Next-day check:** compare the real meter readings with the approved plan, and see the savings history.
- **History:** every agent step of this run, and the full audit trail.

**The morning brief and the four moments (deck slides 4 and 7).** Today opens with the brief: "Move 2 machines. Save about ₹2,700", each move on a 24-hour strip with its own saving, ✓ all orders on time, ✓ under contract demand, and Approve, Edit, Reject, Ask why. Beside it, **Talk to the agent** holds the four moments:
- *Re-plans:* when the work does not fit, the agent asks which rule may flex, with one-click answers.
- *Explains:* "Why move the press line to 11 am?" "Power costs a third less then … No deadline moves." (computed from the tariff, not written by a model)
- *Remembers:* reject with a reason, and the agent offers to save it as a rule (keep at usual time, never start before, always finish by). Every later plan obeys it. The rules are listed, and can be removed, in **Factory memory**.
- *Reacts:* after any re-plan the agent says what changed and why ("Clouds tomorrow. I moved … Still saves ₹1,100.").

**Type anything.** The conversation box takes free text: "Approve", "No. The press line needs the night crew." (rejects and saves a rule), "Never start the oven before 8 pm", "Run the press line at 10 am", "Clouds tomorrow", "New order: grinding line, 400 kW for 4 hours…", "Yes, 10 am is fine", "Why move the compressors?", or any question. Plain code reads these offline; with an AI key the small model reads free wording and code checks every field before anything changes.

Also from the deck: send the work order to the shift lead (logged), a **free bill check** on Upload data, the **shared-savings split** on Next-day check, **factory templates** (foundry, plastics, textiles, cold storage), names removed before any AI sees the data, and **Keep your settings** (download and load) on Plant setup.

**Where data is kept on the hosted app.** Each browser tab has its own session: notes, kept machines, chat and rules-and-limits changes live there and end with the tab. Saved plant settings go to `plant_config.json` and the audit trail to `righthour_audit.sqlite` on the server's disk: shared by everyone using the app, and cleared when Streamlit restarts or redeploys. Use **Plant setup → Keep your settings** to keep your own copy.

**Ask RightHour** (bottom right, on every screen) answers questions about the status, savings, history and the plan, from the facts in the app. With an AI key it uses the large model and can answer any question. Offline, plain code answers the common ones.

## How it maps to the doc

| Doc (Section 8) | Here |
|---|---|
| Data & Forecast agent: validate CSV, forecast 24 h base load (moving average), solar from Open-Meteo | `agents.step_data`, `data.py`: same-weekday 4-week average; Open-Meteo with a clear-sky fallback |
| Tariff & Constraints agent: tariff text to JSON slots (LLM + schema + validator); constraints; conflicts | `parse_tariff` (T2 once, cached by fingerprint, person signs off); `parse_note` (T1, retry, escalate); Rule 8A checks |
| Orchestrator / Explainer: optimiser, re-plan if no valid schedule, policy checks, explanation, approval | `graph.py` (LangGraph with two interrupts) + `step_decide/problem/action` |
| Tools `forecast_load()`, `get_solar_forecast()`, `parse_tariff()`, `optimise_schedule()`, `verify_savings()`, `write_report()` | `data.forecast_load`, `data.solar_forecast`, `agents.parse_tariff`, `optimiser.optimise_schedule` (OR-Tools MILP), `agents.verify_and_learn`, explanation + audit CSV |
| Model router `route(task, attempt, input_size)` with a log | `router.route` (T0/T1/T2), logged with tokens and cost |
| SQLite audit log | `audit.py`, `righthour_audit.sqlite` |
| Streamlit UI in the deck's colours | `app.py`, `.streamlit/config.toml`, tokens from Section 4.2 |

## The demo script (Section 8.4), as the app runs it

1. **Use sample** (or load the UCI CSV), then **Generate plan**. The chart shows evening load in the peak band.
2. **Agent log** tab: forecast (T0), tariff from the library (no model call), constraints parsed (T1), optimiser (T0),
   explanation (T1). No large-model call.
3. **Plan**: compressors and press line move from 17:00–21:00 to 11:00–15:00, furnace untouched, **₹2,704 a day**.
4. The explanation reads: "Moved 800 kVAh from peak (₹10.55) to solar hours (₹7.17). Max demand unchanged; all deadlines met."
5. Turn on **Add the new grinding order** and generate again. There is no valid schedule. The agent tests each flexible
   rule (T2) and asks which one to relax. Choose, and it re-plans.
6. **Edit the plan before approving**: set the grinding line to 10:00. The policy check blocks it (contract demand).
7. **Approve plan**, then **Verify and learn**, then **Run next-day check**. Pick "Press line started 3 hours late"
   to show a verified saving below the projection and the forecast correction the agent learns.

Extra moment from Section 2.2: **Clouds forecast for tomorrow** lowers the solar estimate and the agent re-plans.

## Assumptions (all illustrative, in `data/`)

- Tariff: peak ₹10.55 and solar ₹7.17 are the deck's unverified rates. Normal ₹8.79 and the demand charge of ₹600
  per kVA a month are team assumptions. Replace them from MERC Case No. 75 of 2025.
- Site: contract demand 1,000 kVA, this month's maximum 900 kVA, 150 kWp rooftop solar, 300 working days a year.
- Machines and windows: five shiftable loads, one furnace batch (from a daily operator note), one new order.
- Scheduled machines are taken at a power factor near 1, the worked example's assumption. Base load uses metered PF.
- Starts are on the hour. A tie-break of a few paise keeps machines at their usual time unless moving saves money,
  and centres moved runs in the solar window.

## Not built (doc items left for the team)

- Battery co-optimisation (optional in Section 8.1).
- Prophet forecasting (the doc's "moving average, then Prophet": this is the first step).
- The backup video (Day 10).
