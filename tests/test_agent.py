"""The agentic chat: actions proposed for any message are checked by code before anything changes."""
from righthour import act as ACT
from righthour import config as CFG

MS = CFG.defaults()["machines"]["shiftable"]
SPEC = {"margin_pct": ("slider", 0, 20, "Safety margin (%)"), "block_new_peak": ("toggle", None, None, "No new peak"),
        "max_moves": ("optint", 0, len(MS), "Most machines moved"), "method": ("radio", ["weekday", "recent"], None, "Past days")}


def test_offline_reads_the_deck_moments():
    kinds = lambda t, w="approval": [a["type"] for a in ACT.propose_offline(t, MS, w)]  # noqa: E731
    assert kinds("No. The press line needs the night crew.") == ["reject", "rule"]
    assert kinds("Clouds tomorrow") == ["weather"]
    assert kinds("Run the press line at 10 am") == ["pin"]
    assert kinds("Fewer moves, please") == ["set"]
    assert kinds("Yes, 10 am is fine", "choose_relaxation") == ["relax"]
    assert kinds("Why move the compressors?") == ["why"]


def test_code_refuses_bad_actions():
    acts = [{"type": "set", "key": "margin_pct", "value": 50}, {"type": "set", "key": "nope", "value": 1},
            {"type": "pin", "machine": "press", "hour": 22}, {"type": "pin", "machine": "ghost", "hour": 9},
            {"type": "approve"}, {"type": "launch_rocket"}]
    ok, bad = ACT.check(acts, MS, SPEC, None, [])
    assert ok == [] and len(bad) == 6


def test_code_accepts_and_cleans_good_actions():
    acts = [{"type": "set", "key": "margin_pct", "value": "5"}, {"type": "set", "key": "block_new_peak", "value": "yes"},
            {"type": "set", "key": "max_moves", "value": None}, {"type": "pin", "machine": "press", "hour": 10.0},
            {"type": "rule", "machine": "oven", "kind": "not_before", "hour": 20}, {"type": "window", "machine": "pumps", "earliest": 0, "latest": 8}]
    ok, bad = ACT.check(acts, MS, SPEC, "approval", [])
    assert not bad and [a.get("value", a.get("hour")) for a in ok[:4]] == [5, True, None, 10]


class StubLLM:                    # stands in for Groq: returns a fixed answer, so the wiring is tested without a key
    def complete(self, model, system, prompt, n):
        assert "margin_pct" in system and "press" in system
        return '{"actions": [{"type":"set","key":"margin_pct","value":8}, {"type":"pin","machine":"press","hour":11}]}', 900, 60


def test_llm_proposals_go_through_the_same_checks():
    acts, toks = ACT.propose_llm(StubLLM(), "m", "keep 8% headroom and press at 11", MS, SPEC, "approval", [])
    ok, bad = ACT.check(acts, MS, SPEC, "approval", [])
    assert [a["type"] for a in ok] == ["set", "pin"] and not bad and toks == (900, 60)
