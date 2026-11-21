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

When the app opens it tests the AI model. If the model answers, the plan starts at once; if not, a setup window asks for the provider and key, with **Test connection**.

The left menu has one screen per job. At the top: **sign in** to a plant (plant code and a passphrase of 8+ characters) and **search settings**.

- **Today:** pick the day, the solar forecast and the goal. A new plan is made by itself when any of them changes. The step line shows where the day is (Goal, Data, Analysis, Decision, Problem, Action, Learning). The plan box shows each move on a 24-hour strip with its own saving, what is off today, and four buttons: **Approve**, **Edit** (start times and Keep, under the plan box), **Reject**, **Ask why** (one answer under the plan box). Beside it, **Talk to the agent**.
- **Upload data:** meter readings (CSV, the steel plant data or the sample), data health, the live feed, the day's production notes, a new price order, and the free bill check.
- **Plant setup:** machines (with the days each one **runs on**), crew rules, prices and contract, plant holidays, rooftop solar, the AI model, factory templates, and **Keep your settings**.
- **Guardrails:** every limit the agent must keep, one row each: where it comes from, its setting, and whether it held in this run. Includes the data checks.
- **Control room:** the agents' design diagram, lit up from the run. Click a box for **Results**, **Controls** and **Activity**. **Factory memory** shows what the agent remembers for this plant, where it came from, and buttons to forget it. **All rules and limits** lists every setting.
- **Next-day check:** compare the real meter readings with the approved plan; the forecast correction learns from the gap; the shared-savings split.
- **History:** every agent step of this run, and the full audit trail.

**Talk to the agent (the four moments, deck slide 7).** Type anything: an order, a fact ("the panels are dusty", "short on night staff"), a limit ("reduce machine switches to 1"), a question. The AI model turns it into actions; code checks every action; the agent **tries the change first** and shows the saving now and with the change, the moves and the peak. **Set as plan** (or "yes") uses it; **Discard** (or "no") drops it. A machine rule asks once: just today, or from now on. Without an AI key, plain code reads the common phrasings.

**Data check** looks at: missing readings and the longest gap, repeated timestamps, readings off the 15-minute mark, negatives, spikes, a stuck meter, wrong units against the contract, holidays and shutdowns (against the same weekday), part days, power factor, and how recent the data is. Each limit is on the Guardrails screen and in the Check box.

**Numbers never come from a model.** The optimiser (OR-Tools) does the maths. Any AI text (the plan explanation, Ask RightHour) is checked: a number not in the facts makes the model try again, then plain code answers.

**Ask RightHour** (bottom right, every screen) answers from the facts in the app; **Full screen** makes it bigger. Times show the plant's local time (`RIGHTHOUR_TZ`, default Asia/Kolkata).

**Where data is kept on the hosted app.** A signed-in plant's memory and audit trail are on the server, so a new tab or the next day loads them after sign-in. The shared demo plant uses `plant_config.json` and `righthour_audit.sqlite`. Streamlit Cloud clears the server's disk on a restart or redeploy: download **Plant setup → Keep your settings** first and load it back after.

- Scheduled machines are taken at a power factor near 1, the worked example's assumption. Base load uses metered PF.
- Starts are on the hour. A tie-break of a few paise keeps machines at their usual time unless moving saves money,
  and centres moved runs in the solar window.

## Not built (doc items left for the team)

- Battery co-optimisation (optional in Section 8.1).
- Prophet forecasting (the doc's "moving average, then Prophet": this is the first step).
- The backup video (Day 10).
