"""Design tokens, CSS and drawn icons for the RightHour Lite console.

Direction: a calm operations console for a plant manager at a desk in daylight. Deck navy for structure, the three tariff
colours (slate = normal, amber = solar, red = peak) used only where they mean a tariff band, tabular numerals everywhere
money appears, depth from soft layered shadows rather than borders alone.
"""
from __future__ import annotations

NAVY, NAVY_DARK, NAVY_60, NAVY_30, NAVY_10 = "#14284B", "#0C1A33", "#4D6488", "#A7B4CA", "#EDF1F6"
INK, MUTED, RULE = "#1B2230", "#5B6474", "#DDE3EC"
RED, GREEN, AMBER, AMBER_TEXT = "#B83A2E", "#2F7350", "#D9A21B", "#8A6414"
FONT = "'IBM Plex Sans', Inter, system-ui, sans-serif"

# Drawn icons: one 20px grid, 1.6 stroke, round caps.
def _svg(body: str, size: int = 18) -> str:
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 20 20' fill='none' stroke='currentColor' stroke-width='1.6' "
            f"stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{body}</svg>")


ICONS = {
    "check": _svg("<path d='M4.5 10.5l3.5 3.5 7.5-8'/>"),
    "alert": _svg("<path d='M10 6v5M10 14.2v.1'/><path d='M8.6 3.4L2.4 14.2A1.4 1.4 0 003.6 16.3h12.8a1.4 1.4 0 001.2-2.1L11.4 3.4a1.6 1.6 0 00-2.8 0z'/>"),
    "dot": _svg("<circle cx='10' cy='10' r='2.2' fill='currentColor' stroke='none'/>"),
    "bolt": _svg("<path d='M11 2.5L4.5 11h4.2L8 17.5 15.5 8.6h-4.3z'/>"),
    "sun": _svg("<circle cx='10' cy='10' r='3.2'/><path d='M10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7l1.3 1.3M14 14l1.3 1.3M4.7 15.3L6 14M14 6l1.3-1.3'/>"),
    "shield": _svg("<path d='M10 2.5l6 2.2v4.6c0 3.6-2.5 6.3-6 8-3.5-1.7-6-4.4-6-8V4.7z'/><path d='M7.3 10l2 2 3.5-3.8'/>"),
    "person": _svg("<circle cx='10' cy='6.6' r='3'/><path d='M3.8 17c.5-3.2 3-5 6.2-5s5.7 1.8 6.2 5'/>"),
    "chip": _svg("<rect x='5' y='5' width='10' height='10' rx='2'/><path d='M8 2.5v2.5M12 2.5v2.5M8 15v2.5M12 15v2.5M2.5 8H5M2.5 12H5M15 8h2.5M15 12h2.5'/>"),
    "clock": _svg("<circle cx='10' cy='10' r='7.2'/><path d='M10 5.8V10l2.8 1.8'/>"),
    "upload": _svg("<path d='M10 13V3.5M6.2 7.2L10 3.5l3.8 3.7'/><path d='M3.5 12.5v2.3A1.7 1.7 0 005.2 16.5h9.6a1.7 1.7 0 001.7-1.7v-2.3'/>"),
    "doc": _svg("<path d='M11.5 2.5H6A1.5 1.5 0 004.5 4v12A1.5 1.5 0 006 17.5h8a1.5 1.5 0 001.5-1.5V6.5z'/><path d='M11.5 2.5v4h4M7.5 10.5h5M7.5 13.5h5'/>"),
    "notes": _svg("<rect x='4' y='3.5' width='12' height='14' rx='1.6'/><path d='M7.5 2.5v2M12.5 2.5v2M7 8.5h6M7 11.5h6M7 14.5h3.5'/>"),
    "clipboard": _svg("<rect x='4.5' y='3.5' width='11' height='14' rx='1.6'/><path d='M7.5 3.5V2.8h5v.7M7.5 9.5l1.6 1.6 3.4-3.6M7.5 14h5'/>"),
    "pulse": _svg("<path d='M2.5 10.5h3.2l2-5 3.6 9 2-4h4.2'/>"),
    "history": _svg("<path d='M3.6 10a6.4 6.4 0 101.9-4.5L3.5 7.5'/><path d='M3.5 3.8v3.7h3.7M10 6.8V10l2.4 1.5'/>"),
}

# The brand mark: a clock whose hand is a lightning bolt. "The right hour to use power."
LOGO = ("<svg class='rh-logo' width='36' height='36' viewBox='0 0 36 36' aria-hidden='true'>"
        "<rect width='36' height='36' rx='9' fill='#14284B'/>"
        "<circle cx='18' cy='18' r='11' fill='none' stroke='#fff' stroke-opacity='.9' stroke-width='2'/>"
        "<path d='M18 7v2.6M18 26.4V29M7 18h2.6M26.4 18H29' stroke='#fff' stroke-opacity='.55' stroke-width='1.6' stroke-linecap='round'/>"
        "<path d='M19.6 10.5l-5.4 8.3h4l-1.6 6.7 6.2-9h-4.1z' fill='#D9A21B'/></svg>")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root{
  --navy:#14284B; --navy-d:#0C1A33; --navy-60:#4D6488; --navy-30:#A7B4CA; --navy-10:#EDF1F6; --navy-05:#F5F7FA;
  --ink:#1B2230; --muted:#5B6474; --rule:#DDE3EC; --paper:#F6F8FB;
  --red:#B83A2E; --green:#2F7350; --amber:#D9A21B; --amber-t:#8A6414;
  --green-bg:#EAF4EE; --red-bg:#FBEDEB; --amber-bg:#FBF3DE;
  --ease:cubic-bezier(.16,1,.3,1);
  --sh1:0 1px 2px rgba(12,26,51,.06), 0 1px 1px rgba(12,26,51,.04);
  --sh2:0 1px 2px rgba(12,26,51,.05), 0 10px 28px -14px rgba(12,26,51,.22);
}
html, body, .stApp, .stMarkdown, .stButton button, .stTextArea textarea, .stTextInput input, .stSelectbox, .stRadio, .stTabs,
.stNumberInput input, [data-testid="stSidebar"] label, [data-testid="stSidebar"] p, [data-testid="stSidebar"] small,
[data-testid="stFileUploaderDropzone"] small, [data-testid="stFileUploaderDropzone"] button { font-family: 'IBM Plex Sans', Inter, system-ui, sans-serif; }
[data-testid="stIconMaterial"], .material-icons, [class*="material-symbols"] { font-family: "Material Symbols Rounded", "Material Icons" !important; }
code, pre, .mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; }
.stApp { background: var(--paper); color: var(--ink); }
::selection { background: var(--navy); color: #fff; }
* { scrollbar-width: thin; scrollbar-color: var(--navy-30) transparent; }
*::-webkit-scrollbar { width: 10px; height: 10px; } *::-webkit-scrollbar-thumb { background: var(--navy-30); border-radius: 8px; border: 2px solid var(--paper); }
input, textarea { caret-color: var(--navy); }
:focus-visible { outline: 2px solid var(--navy); outline-offset: 2px; border-radius: 6px; }
a { text-underline-offset: .18em; text-decoration-thickness: 1px; }
#MainMenu, footer, [data-testid="stDeployButton"], [data-testid="stStatusWidget"] { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1360px; }
h1,h2,h3,h4 { color: var(--navy); letter-spacing: -.01em; text-wrap: balance; }
[data-testid="stSidebar"] { min-width:300px; }
p { text-wrap: pretty; }
.num, [data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }

/* ---------- masthead */
.rh-top { display:flex; align-items:center; gap:14px; padding:6px 2px 18px; }
.rh-mark { width:38px; height:38px; border-radius:10px; background:var(--navy); color:#fff; display:grid; place-items:center;
           box-shadow: var(--sh2); }
.rh-mark svg { width:22px; height:22px; color:var(--amber); }
.rh-top .name { font-size:22px; font-weight:650; color:var(--navy); letter-spacing:-.02em; line-height:1.1; }
.rh-top .sub { color:var(--muted); font-size:14px; margin-top:2px; max-width:46ch; }
.rh-top .site { margin-left:auto; text-align:right; color:var(--muted); font-size:13px; line-height:1.45; }
.rh-top .site b { color:var(--ink); font-weight:600; }
.rh-pill { display:inline-flex; align-items:center; gap:7px; font-size:12.5px; font-weight:550; padding:4px 10px; border-radius:8px;
           background:#fff; border:1px solid var(--rule); color:var(--ink); box-shadow:var(--sh1); }
.rh-pill.live { background:var(--green-bg); border-color:#BFDCCB; color:var(--green); }

/* ---------- the day's steps: numbered, each one says its own state in words */
.rh-steps { display:grid; grid-template-columns:repeat(7, minmax(0,1fr)); gap:8px; margin:0 0 14px; padding:0; list-style:none; }
.rh-steps li { background:#fff; border:1px solid var(--rule); border-top:4px solid var(--navy-10); border-radius:10px; padding:10px 12px 12px; min-width:0; }
.rh-steps .top { display:flex; align-items:center; justify-content:space-between; gap:6px; margin-bottom:8px; }
.rh-steps .no { font-size:13px; font-weight:650; color:var(--muted); }
.rh-steps .sw { font-size:12.5px; font-weight:600; color:var(--muted); }
.rh-steps .t { font-size:15.5px; font-weight:650; color:var(--ink); line-height:1.25; }
.rh-steps .s { font-size:13px; color:var(--muted); margin-top:3px; line-height:1.4; }
.rh-steps li.done, .rh-steps li.ok { border-top-color:var(--green); } .rh-steps li.done .sw, .rh-steps li.ok .sw { color:var(--green); }
.rh-steps li.active, .rh-steps li.alert { border-top-color:var(--red); background:var(--red-bg); border-color:#EDC4BF; box-shadow:var(--sh2); }
.rh-steps li.active .sw, .rh-steps li.alert .sw { color:var(--red); } .rh-steps li.active .no, .rh-steps li.alert .no { color:var(--red); }
.rh-steps li.wait { background:var(--navy-05); } .rh-steps li.wait .t { color:var(--muted); font-weight:600; }
@media (max-width: 1100px){ .rh-steps{ grid-template-columns:repeat(2,minmax(0,1fr)); } }
.rh-next { display:flex; align-items:center; gap:12px; background:var(--navy); color:#fff; border-radius:12px; padding:13px 18px; margin:0 0 14px; font-size:15.5px; line-height:1.4; }
.rh-next b { font-weight:650; color:#fff; } .rh-next .lbl { font-weight:650; color:var(--amber); flex:none; }
.rh-next.you { background:var(--red); } .rh-next.you .lbl { color:#fff; }
.rh-next.done { background:var(--green); } .rh-next.done .lbl { color:#fff; }
.rh-tag { display:inline-flex; align-items:center; font-size:12.5px; font-weight:650; padding:2px 9px; border-radius:6px; background:var(--navy-10); color:var(--muted); white-space:nowrap; }
.rh-tag.ok { background:var(--green-bg); color:var(--green); } .rh-tag.ask { background:var(--red-bg); color:var(--red); } .rh-tag.part { background:var(--amber-bg); color:var(--amber-t); }

/* ---------- surfaces */
.rh-card { background:#fff; border:1px solid var(--rule); border-radius:12px; padding:16px 18px; margin-bottom:14px; box-shadow:var(--sh1); }
.rh-card h4 { margin:0 0 8px; color:var(--navy); font-size:15.5px; font-weight:650; display:flex; align-items:center; gap:8px; }
.rh-card h4 { padding:0 !important; line-height:1.3; }
.rh-card h4 svg { color:var(--navy-60); flex:none; }
.rh-card p { margin:0 0 6px; color:var(--ink); font-size:14.5px; line-height:1.55; max-width:68ch; }
.rh-card.panel { background:var(--navy-05); }
.rh-card.approve { background:var(--green-bg); border-color:#BFDCCB; } .rh-card.approve h4, .rh-card.approve h4 svg { color:var(--green); }
.rh-card.alert { background:var(--red-bg); border-color:#EDC4BF; } .rh-card.alert h4, .rh-card.alert h4 svg { color:var(--red); }
.rh-chip { display:inline-flex; align-items:center; font-size:12.5px; font-weight:600; padding:2px 8px; border-radius:6px; letter-spacing:.01em;
           border:1px solid var(--navy-30); color:var(--navy); background:#fff; margin-right:8px; font-variant-numeric:tabular-nums; }
.rh-chip.t0 { background:var(--navy-10); border-color:var(--navy-10); } .rh-chip.t1 { background:#fff; }
.rh-chip.t2 { background:var(--navy); border-color:var(--navy); color:#fff; }
.rh-chip.human { background:var(--green-bg); border-color:#BFDCCB; color:var(--green); }
.rh-chip.ok { background:var(--green-bg); border-color:#BFDCCB; color:var(--green); }
.rh-chip.bad { background:var(--red-bg); border-color:#EDC4BF; color:var(--red); }
.rh-chip.warn { background:var(--amber-bg); border-color:#EBD79F; color:var(--amber-t); }
.rh-note { color:var(--muted); font-size:13.5px; line-height:1.55; }
.rh-key { font-size:12.5px; color:var(--muted); display:flex; gap:16px; flex-wrap:wrap; align-items:center; margin:2px 0 10px; }
.rh-key i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; vertical-align:-1px; }

/* ---------- the bill: one bar, saving as its own segment */
.rh-bill { display:grid; grid-template-columns:minmax(0,1.55fr) minmax(0,1fr); gap:14px; margin:0 0 16px; }
.rh-panel { background:#fff; border:1px solid var(--rule); border-radius:14px; padding:18px 20px; box-shadow:var(--sh2); }
.rh-panel .cap { font-size:12.5px; color:var(--muted); font-weight:550; }
.rh-big { font-size:44px; line-height:1; font-weight:650; letter-spacing:-.035em; color:var(--green); font-variant-numeric:tabular-nums; margin:6px 0 2px; }
.rh-big small { font-size:15px; font-weight:500; color:var(--muted); letter-spacing:0; margin-left:6px; }
.rh-bar { display:flex; height:14px; border-radius:7px; overflow:hidden; background:var(--navy-10); margin:16px 0 8px; }
.rh-bar .a { background:var(--navy); transform-origin:left; animation:rh-draw 1s var(--ease) both; }
.rh-bar .b { background:var(--green); transform-origin:left; animation:rh-draw 1.2s var(--ease) .15s both; background-image:repeating-linear-gradient(135deg, rgba(255,255,255,.28) 0 3px, transparent 3px 7px); }
.rh-legend { display:flex; justify-content:space-between; gap:12px; font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
.rh-legend b { color:var(--ink); font-weight:600; }
.rh-meter { position:relative; height:12px; border-radius:6px; background:linear-gradient(90deg, var(--navy-10), var(--navy-10)); margin:34px 0 34px; }
.rh-meter .fill { position:absolute; inset:0 auto 0 0; border-radius:6px; background:var(--navy); animation:rh-draw .9s var(--ease) both; transform-origin:left; }
.rh-meter .tick { position:absolute; top:-6px; bottom:-6px; width:2px; background:var(--ink); border-radius:1px; }
.rh-meter .tick span { position:absolute; white-space:nowrap; font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
.rh-meter .tick.top span { bottom:calc(100% + 4px); } .rh-meter .tick.bot span { top:calc(100% + 4px); }
.rh-meter .tick.cd { background:var(--red); } .rh-meter .tick.cd span { color:var(--red); }
@media (max-width: 900px){ .rh-bill{ grid-template-columns:1fr; } }

/* ---------- stats */
.rh-stats { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:12px; margin:6px 0 14px; }
.rh-stat { background:#fff; border:1px solid var(--rule); border-radius:12px; padding:14px 16px; box-shadow:var(--sh1); }
.rh-stat .v { font-size:28px; font-weight:650; color:var(--navy); letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1.1; }
.rh-stat .v.green { color:var(--green); } .rh-stat .v.red { color:var(--red); }
.rh-stat .l { font-size:13px; color:var(--muted); margin-top:5px; line-height:1.4; }

/* ---------- tables */
.rh-log { width:100%; border-collapse:separate; border-spacing:0; font-size:14px; background:#fff; border:1px solid var(--rule); border-radius:12px; overflow:hidden; box-shadow:var(--sh1); }
.rh-log th { text-align:left; color:var(--muted); background:var(--navy-05); font-weight:600; font-size:13px; padding:9px 12px; border-bottom:1px solid var(--rule); }
.rh-log td { border-bottom:1px solid var(--navy-10); padding:9px 12px; vertical-align:top; color:var(--ink); line-height:1.45; }
.rh-log tr:last-child td { border-bottom:0; } .rh-log tbody tr:hover td { background:var(--navy-05); }
.rh-log td.r, .rh-log th.r { text-align:right; font-variant-numeric:tabular-nums; }
.rh-log td.m { font-family:'IBM Plex Mono', monospace; font-size:12.5px; color:var(--muted); white-space:nowrap; }
.rh-mix { display:flex; height:10px; border-radius:5px; overflow:hidden; background:var(--navy-10); margin:8px 0 6px; }
.rh-mix span { display:block; } .rh-mix .m0 { background:var(--navy-30); } .rh-mix .m1 { background:var(--navy-60); } .rh-mix .m2 { background:var(--navy); } .rh-mix .mh { background:var(--green); }

/* ---------- empty state */
.rh-empty { background:#fff; border:1px solid var(--rule); border-radius:16px; padding:34px 36px; box-shadow:var(--sh2); max-width:880px; }
.rh-empty h2 { margin:0 0 8px; font-size:26px; letter-spacing:-.025em; } .rh-empty p { color:var(--ink); font-size:15px; line-height:1.6; max-width:62ch; }
.rh-empty ol { margin:16px 0 0; padding:0; list-style:none; display:grid; gap:10px; }
.rh-empty li { display:flex; gap:12px; align-items:flex-start; font-size:14px; color:var(--muted); }
.rh-empty li svg { color:var(--navy); flex:none; margin-top:1px; }

/* ---------- streamlit parts */
div[data-testid="stSidebar"] { background:#fff; border-right:1px solid var(--rule); }
div[data-testid="stSidebar"] h3 { font-size:12.5px !important; font-weight:600 !important; color:var(--muted) !important; text-transform:none; letter-spacing:0; }
.stButton button, .stDownloadButton button { border-radius:9px; font-weight:550; border:1px solid var(--rule); box-shadow:var(--sh1);
   transition: transform .18s var(--ease), box-shadow .18s var(--ease), background .18s var(--ease); }
.stButton button:hover:not(:disabled) { transform:translateY(-1px); box-shadow:var(--sh2); border-color:var(--navy-30); }
.stButton button:active:not(:disabled) { transform:translateY(0); }
.stButton button[kind="primary"] { background:var(--navy); border-color:var(--navy); color:#fff; }
.stButton button[kind="primary"]:hover:not(:disabled) { background:var(--navy-d); border-color:var(--navy-d); }
.stButton button:disabled { opacity:.5; }
.stTabs [data-baseweb="tab-list"] { gap:4px; border-bottom:1px solid var(--rule); }
.stTabs [data-baseweb="tab"] { font-size:14.5px; font-weight:550; color:var(--muted); padding:10px 14px; }
.stTabs [aria-selected="true"] { color:var(--navy); }
.stTabs [data-baseweb="tab-highlight"] { background:var(--navy); height:2px; }
.stTextArea textarea, .stTextInput input, [data-baseweb="select"] > div { border-radius:9px; }
[data-testid="stExpander"] { border:1px solid var(--rule); border-radius:12px; background:#fff; box-shadow:var(--sh1); }
[data-testid="stPlotlyChart"] { background:#fff; border:1px solid var(--rule); border-radius:14px; padding:8px 8px 2px; box-shadow:var(--sh2); margin-bottom:12px; }
@media (max-width: 700px){ .rh-top .site{ display:none; } }

/* ---------- architecture diagram */
.dg-wrap { background:#fff; border:1px solid var(--rule); border-radius:14px; padding:14px; box-shadow:var(--sh2); overflow-x:auto; }
svg.dg { width:100%; min-width:820px; height:auto; display:block; font-family: 'IBM Plex Sans', system-ui, sans-serif; }
.dg-panel { fill:none; stroke:var(--navy-30); stroke-width:1.2; stroke-dasharray:2 3; } .dg-panel.p2 { fill:var(--navy-05); }
.dg-pipe { fill:var(--navy-10); stroke:var(--navy-30); stroke-width:1; }
.dg-pl { font-size:14px; font-weight:650; fill:var(--navy); } .dg-pt { font-size:18px; font-weight:650; fill:var(--navy); }
.dg-ln { stroke:var(--navy); stroke-width:1.6; } .dg-dash { stroke:var(--navy-60); stroke-width:1.6; stroke-dasharray:6 5; } .dg-dash.gold { stroke:var(--amber-t); }
.dg-dash.on { stroke:var(--green); stroke-dasharray:none; } .dg-dash.gold.on { stroke:var(--amber-t); stroke-dasharray:none; }
.dg-loop { font-size:14.5px; font-weight:600; fill:var(--navy-60); } .dg-loop.gold { fill:var(--amber-t); }
.dg-box rect { fill:#fff; stroke:var(--navy); stroke-width:1.4; } .dg-box.pipe rect { fill:#fff; stroke:var(--navy-30); }
.dg-box.dark rect { fill:var(--navy); } .dg-box.green rect { fill:var(--green-bg); stroke:var(--green); } .dg-box.human rect { stroke:var(--green); stroke-width:1.8; }
.dg-t { font-size:16.5px; font-weight:650; fill:var(--ink); } .dg-s { font-size:13.5px; fill:var(--muted); }
.dg-box.dark .dg-t { fill:#fff; } .dg-box.dark .dg-s { fill:#D6DEEC; } .dg-box.green .dg-t { fill:var(--green); }
.dg-code { font-family:'IBM Plex Mono', monospace; font-size:12px; fill:var(--navy-60); } .dg-box.dark .dg-code { fill:#A7B4CA; }
.dg-box .bd rect { fill:var(--navy-10); stroke:#fff; stroke-width:2.5; paint-order:stroke; } .dg-box .bd text { font-size:12.5px; font-weight:650; fill:var(--muted); }
.dg-box.s-ran .bd rect { fill:var(--green-bg); } .dg-box.s-ran .bd text { fill:var(--green); }
.dg-box.s-ask .bd rect { fill:var(--red-bg); stroke:none; } .dg-box.s-ask .bd text { fill:var(--red); }
.dg-box.s-part .bd rect { fill:var(--amber-bg); } .dg-box.s-part .bd text { fill:var(--amber-t); }
.dg-box.s-wait { opacity:.8; } .dg-box.s-ask rect { stroke:var(--red); stroke-width:2; }

/* ---------- manager console */
.rh-sec { display:flex; align-items:baseline; gap:10px; margin:22px 0 12px; padding-bottom:8px; border-bottom:1px solid var(--rule); }
.rh-sec .t { font-size:19px; font-weight:650; color:var(--navy); letter-spacing:-.01em; }
.rh-sec .d { font-size:14px; color:var(--muted); }
.rh-ch { display:flex; align-items:center; gap:10px; font-size:16px; font-weight:650; color:var(--ink); line-height:1.3; }
.rh-ch .sub { font-weight:400; font-size:12px; color:var(--muted); }
.rh-ch .rh-tag { margin-left:2px; }
.rh-kv { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px 14px; margin:6px 0 8px; }
.rh-kv.three { grid-template-columns:repeat(3, minmax(0,1fr)); }
.rh-kv div { font-size:13px; color:var(--muted); line-height:1.3; }
.rh-kv b { display:block; font-size:21px; font-weight:650; color:var(--navy); letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.rh-kv b.g { color:var(--green); } .rh-kv b.r { color:var(--red); } .rh-kv b.s { font-size:14px; letter-spacing:0; }
.rh-bands { display:flex; height:22px; border-radius:6px; overflow:hidden; margin:6px 0 4px; font-size:11px; font-weight:600; }
.rh-bands span { display:flex; align-items:center; justify-content:center; color:#fff; white-space:nowrap; overflow:hidden; }
.rh-tiny { width:100%; border-collapse:collapse; font-size:13.5px; }
.rh-tiny td, .rh-tiny th { padding:5px 4px; border-bottom:1px solid var(--navy-10); text-align:left; vertical-align:top; }
.rh-tiny th { color:var(--muted); font-weight:600; font-size:11.5px; }
.rh-tiny td.r, .rh-tiny th.r { text-align:right; font-variant-numeric:tabular-nums; }
.rh-tiny td.m { font-family:'IBM Plex Mono', monospace; color:var(--muted); white-space:nowrap; }
.rh-tiny tr:last-child td { border-bottom:0; }
.rh-legend-hld { display:flex; gap:18px; font-size:13.5px; color:var(--muted); align-items:center; margin:0 0 6px; flex-wrap:wrap; }
[data-testid="stVerticalBlockBorderWrapper"] { background:#fff; border-radius:12px !important; box-shadow:var(--sh1); }
.stButton button[kind="tertiary"] { border:0; box-shadow:none; background:transparent; color:var(--navy-60); font-size:13.5px; padding:0 2px; min-height:0; }
.stButton button[kind="tertiary"]:hover:not(:disabled) { color:var(--navy); text-decoration:underline; transform:none; box-shadow:none; }
div[data-testid="stSegmentedControl"] button { font-weight:550; }
/* ---------- app shell: left menu, page headers, cards */
section[data-testid="stSidebar"] > div { padding-top: .2rem; }
.rh-brand { display:flex; align-items:center; gap:11px; padding:4px 4px 18px; margin-bottom:10px; border-bottom:1px solid var(--rule); }
.rh-brand .n { font-size:18px; font-weight:650; color:var(--navy); letter-spacing:-.02em; line-height:1.15; }
.rh-brand .s { font-size:12.5px; color:var(--muted); }
.st-key-nav { gap:2px !important; }
.st-key-nav button { justify-content:flex-start; gap:10px; background:transparent; border:0; box-shadow:none; color:var(--ink);
   font-weight:500; padding:8px 12px; min-height:40px; border-radius:9px; }
.st-key-nav button:hover:not(:disabled) { background:var(--navy-05); transform:none; box-shadow:none; color:var(--navy); }
.st-key-nav button [data-testid="stIconMaterial"] { color:var(--navy-60); font-size:20px; }
.st-key-nav [class*="st-key-navon_"] button { background:var(--navy-10); color:var(--navy); font-weight:650; }
.st-key-nav [class*="st-key-navon_"] button [data-testid="stIconMaterial"] { color:var(--navy); }
.st-key-nav button p { font-size:14.5px; }
.st-key-nav button > div { justify-content:flex-start !important; width:100%; }
.st-key-nav button [data-testid="stMarkdownContainer"] { text-align:left; }
.rh-side { margin:18px 0 12px; padding:14px 14px 10px; background:var(--navy-05); border:1px solid var(--rule); border-radius:12px; }
.rh-side .h { font-size:12px; color:var(--muted); font-weight:550; }
.rh-side .v { font-size:14px; font-weight:600; color:var(--ink); line-height:1.35; margin:2px 0 10px; }
.rh-side dl { display:grid; grid-template-columns:auto 1fr; gap:6px 12px; margin:0; font-size:13px; }
.rh-side dt { color:var(--muted); } .rh-side dd { margin:0; color:var(--ink); text-align:right; font-variant-numeric:tabular-nums; }
.rh-side .rh-tag { font-size:12px; padding:1px 7px; }
.rh-ph { padding:4px 0 16px; }
.rh-ph h1 { font-size:28px !important; font-weight:650 !important; letter-spacing:-.025em; margin:0 !important; padding:0 !important; line-height:1.2 !important; }
.rh-ph p { color:var(--muted); font-size:15px; margin:4px 0 0; max-width:80ch; }
.rh-ct { display:flex; align-items:flex-start; gap:11px; margin:0 0 12px; }
.rh-ct .i { width:34px; height:34px; border-radius:9px; background:var(--navy-10); color:var(--navy); display:grid; place-items:center; flex:none; }
.rh-ct .t { font-size:16px; font-weight:650; color:var(--ink); line-height:1.25; }
.rh-ct .s { font-size:13px; color:var(--muted); line-height:1.4; margin-top:1px; }
.rh-lead { max-width:78ch; margin:4px 0 10px; color:var(--ink); font-size:14.5px; line-height:1.55; }
.rh-p { margin:0 0 6px; font-size:14.5px; line-height:1.55; color:var(--ink); }
.rh-ok, .rh-alert { border-radius:10px; padding:11px 13px; font-size:14px; line-height:1.5; margin:0 0 10px; }
.rh-ok { background:var(--green-bg); color:#1F4F37; } .rh-ok b { color:var(--green); }
.rh-alert { background:var(--red-bg); color:#7A2219; } .rh-alert b { color:var(--red); }
.rh-ok svg, .rh-alert svg { vertical-align:-4px; margin-right:6px; }
.st-key-planbar { background:#fff; }
.st-key-planbar [data-testid="stRadio"] label p { font-size:14px; }
.st-key-decide { border-color:var(--navy-30) !important; }
.rh-next, .rh-steps { margin-bottom:14px; }
[data-testid="stFileUploaderDropzone"] { background:var(--navy-05); border:1.5px dashed var(--navy-30); border-radius:12px; padding:18px; }
[data-testid="stFileUploaderDropzone"]:hover { border-color:var(--navy-60); background:var(--navy-10); }
[data-testid="stVerticalBlockBorderWrapper"] > div { gap:.6rem; }
/* ---------- control room: one box's process */
.st-key-boxpanel { border-color:var(--navy-30) !important; }
.st-key-boxpanel .rh-ct .t { font-size:18px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.rh-flow { list-style:none; margin:6px 0 16px; padding:0; display:grid; gap:0; }
.rh-flow li { display:grid; grid-template-columns:34px 1fr; gap:12px; position:relative; padding:0 0 16px; }
.rh-flow li:not(:last-child)::before { content:""; position:absolute; left:16px; top:30px; bottom:2px; width:2px; background:var(--navy-10); }
.rh-flow .n { width:32px; height:32px; border-radius:50%; background:#fff; border:1.5px solid var(--navy-30); color:var(--navy);
              display:grid; place-items:center; font-weight:650; font-size:14px; font-variant-numeric:tabular-nums; }
.rh-flow li:last-child .n { background:var(--navy); border-color:var(--navy); color:#fff; }
.rh-flow .t { font-size:15px; font-weight:650; color:var(--ink); line-height:1.3; padding-top:5px; }
.rh-flow .d { font-size:14px; color:var(--muted); line-height:1.5; margin-top:2px; max-width:80ch; }
.rh-flow .d b { color:var(--ink); } .rh-flow .d b.off { color:var(--red); }
.rh-src2 { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(0,1fr); gap:18px; padding:14px 16px; background:var(--navy-05);
           border:1px solid var(--rule); border-radius:10px; font-size:13.5px; color:var(--muted); line-height:1.5; }
.rh-src2 b { color:var(--ink); font-weight:600; } .rh-src2 ul { margin:4px 0 0; padding-left:18px; } .rh-src2 p { margin:2px 0 8px; }
@media (max-width: 900px){ .rh-src2 { grid-template-columns:1fr; } }
/* ---------- Ask RightHour: the floating button and the chat */
.st-key-askfab { position:fixed; right:28px; bottom:26px; z-index:999990; width:auto !important; }
.st-key-askfab button { min-height:52px; padding:0 22px 0 18px; border-radius:26px; font-size:15.5px; font-weight:650; gap:10px;
   box-shadow:0 2px 4px rgba(12,26,51,.12), 0 14px 32px -10px rgba(12,26,51,.45); }
.st-key-askfab button [data-testid="stIconMaterial"] { color:var(--amber); font-size:22px; }
.st-key-askfab button:hover:not(:disabled) { transform:translateY(-2px); }
[data-testid="stChatMessage"] { background:transparent; padding:6px 0; }
[data-testid="stChatMessageAvatarAssistant"] { background:var(--navy); color:var(--amber); }
@media (max-width: 700px){ .st-key-askfab { right:16px; bottom:16px; } }
.rh-kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin:4px 0 14px; }
.rh-kpis > div { background:#fff; border:1px solid var(--rule); border-radius:10px; padding:12px 14px; }
.rh-kpis b { display:block; font-size:24px; font-weight:650; color:var(--navy); letter-spacing:-.02em; line-height:1.15; font-variant-numeric:tabular-nums; }
.rh-kpis b.g { color:var(--green); } .rh-kpis b.r { color:var(--red); } .rh-kpis b.s { font-size:16px; letter-spacing:0; padding:4px 0 3px; }
.rh-kpis span { font-size:13px; color:var(--muted); }
.rh-sub { font-size:14px; font-weight:650; color:var(--ink); margin:18px 0 8px; }
.rh-locked { margin-top:14px; padding:12px 16px; background:var(--navy-05); border:1px solid var(--rule); border-radius:10px; font-size:13.5px; color:var(--muted); line-height:1.55; }
.rh-locked b { color:var(--ink); } .rh-locked ul { margin:4px 0 0; padding-left:18px; }
/* ---------- control-room polish: one sharp typeface, labels as instrument captions, tighter panels */
html, body, .stApp { font-size:14.5px; }
.stApp { --cap: 11.5px; }
.rh-ph { padding:0 0 14px; } .rh-ph h1 { font-size:24px !important; letter-spacing:-.015em; } .rh-ph p { font-size:14px; margin-top:2px; }
.rh-panel .cap, .rh-kpis span, .rh-side .h, .rh-kv div, .rh-stat .l, .rh-sub {
  font-size:var(--cap); text-transform:uppercase; letter-spacing:.07em; font-weight:600; color:var(--muted); }
.rh-sub { margin:20px 0 10px; }
.rh-kpis b, .rh-big, .rh-kv b, .rh-stat .v { font-family:'IBM Plex Sans', sans-serif; font-weight:600; font-variant-numeric:tabular-nums; }
.rh-kpis > div { border-radius:8px; padding:12px 14px 11px; border-color:var(--rule); }
.rh-kpis span { display:block; margin-top:3px; }
.rh-big { font-size:40px; letter-spacing:-.02em; }
.rh-card, .rh-panel, .rh-stat, .rh-log, .rh-empty, [data-testid="stPlotlyChart"], [data-testid="stExpander"] { border-radius:8px !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius:10px !important; }
.rh-panel { box-shadow:var(--sh1); }
.rh-log { font-size:13.5px; } .rh-log th { font-size:var(--cap); text-transform:uppercase; letter-spacing:.06em; }
.rh-tiny th { text-transform:uppercase; letter-spacing:.06em; }
.rh-next { padding:10px 16px; font-size:14.5px; border-radius:8px; }
/* the day's steps: a slim status strip, details on hover */
.rh-steps { gap:6px; margin-bottom:12px; }
.rh-steps li { padding:8px 10px 9px; border-radius:8px; border-top-width:3px; }
.rh-steps .s { display:none; }
.rh-steps .top { margin-bottom:2px; } .rh-steps .t { font-size:14px; } .rh-steps .no, .rh-steps .sw { font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; }
.rh-meta { display:flex; flex-wrap:wrap; gap:6px 14px; align-items:center; font-size:12.5px; color:var(--muted); margin:0 0 12px; }
.rh-meta b { color:var(--ink); font-weight:600; }
/* settings search in the left bar */
.st-key-sethits { gap:2px !important; margin:-4px 0 10px; padding:6px; background:#fff; border:1px solid var(--rule); border-radius:8px; box-shadow:var(--sh2); }
.st-key-sethits button { justify-content:flex-start; border:0 !important; box-shadow:none !important; background:transparent; min-height:32px; padding:4px 8px; font-weight:500; }
.st-key-sethits button > div { justify-content:flex-start !important; width:100%; }
.st-key-sethits button:hover { background:var(--navy-05) !important; }
/* Ask RightHour: a corner panel, not a full-screen window */
[data-testid="stDialog"] { background:transparent !important; pointer-events:none; }
[data-testid="stDialog"] > div { pointer-events:auto; position:fixed !important; right:24px; bottom:90px; top:auto !important; left:auto !important;
  margin:0 !important; transform:none !important; width:min(440px, calc(100vw - 32px)) !important; max-width:none !important;
  max-height:min(66vh, 680px); overflow:auto; border-radius:12px; border:1px solid var(--navy-30);
  box-shadow:0 4px 10px rgba(12,26,51,.10), 0 24px 60px -16px rgba(12,26,51,.45); }
[data-testid="stDialog"] section[role="dialog"] { position:static !important; width:auto !important; max-width:none !important; margin:0 !important;
  box-shadow:none !important; border:0 !important; transform:none !important; }
[data-testid="stDialog"] section[role="dialog"] h2 { font-size:17px !important; }
@media (max-width: 700px){ [data-testid="stDialog"] > div { right:16px; bottom:84px; } }
@media (prefers-reduced-motion: reduce){ *, *::before, *::after { animation:none !important; transition:none !important; } }
</style>
"""
