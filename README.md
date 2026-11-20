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

## The Manager console (the first screen)

One page from which the plant manager runs everything. It reads left to right like the design diagram:

- **1 Inputs, what goes in:** meter data (steel file, sample, or your CSV; auto-scale), the live feed (hold back 7 days, release a day, `incoming/` folder), the day, goal and weather, today's production notes, price bands and contract demand, rooftop solar, and the language model.
- **2 Telemetry, what the agents are doing:** this run's steps by tier (T0/T1/T2/human), model calls and spend, why the large model was used, data health, the forecast, the optimiser's schedule, guardrail results, the newest agent steps with their diagram box, and the live-feed log.
- **3 Outputs, what comes out:** your decision (approve, edit, reject, or which rule to relax), saving and max demand against the contract, the work order, and the next-day check with what the agent learned and a savings history.

**Every panel links back to the clickable design diagram.** The small diagram at the top opens any box in the Control room with one click, and each panel's `↗` link opens its own box there: where its data comes from, what it did in this run, and its full settings. The Control room has a link back to the console. Status dots on the panels and on the diagram come from the same run log: green ran, red needs you, hollow not run yet.

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
