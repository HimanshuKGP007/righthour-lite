"""The chat agent's loop, with a scripted fake model (no network)."""
import json

import pytest

from righthour import agent_loop as AG


class Fake:
    def __init__(self, replies):
        self.replies, self.prompts, self.last_model = list(replies), [], "fake"

    def complete(self, model, system, prompt, max_tokens=800):
        self.prompts.append(prompt)
        return json.dumps(self.replies.pop(0)), 100, 20


TOOLS = {"better_options": ("{} other moves", lambda a: "Press line: 10:00 would lower the cost by ₹150, but your limits stop it.")}


def test_a_promise_to_check_is_sent_back_and_answered():
    llm = Fake([{"final": {"reply": "I'll check the cost if the press line starts at 10:00.", "actions": []}},
                {"tool": "better_options", "args": {}},
                {"final": {"reply": "No. The press line at 10:00 would save ₹150, but your limits stop it.", "actions": []}}])
    r = AG.run(llm, "m", "Would any other move save more?", "snapshot", "", TOOLS, "actions")
    assert r["reply"].startswith("No.") and [s["tool"] for s in r["steps"]] == ["better_options"] and r["calls"] == 3
    assert "was a plan, not an answer" in llm.prompts[1]


def test_invented_number_is_sent_back():
    llm = Fake([{"final": {"reply": "It would save ₹9,999.", "actions": []}},
                {"final": {"reply": "It would save ₹150.", "actions": []}}])
    r = AG.run(llm, "m", "save more?", "Press line could save ₹150", "", TOOLS, "actions")
    assert r["reply"] == "It would save ₹150."


def test_a_proposed_change_may_say_ill_try():
    llm = Fake([{"final": {"reply": "I'll try that.", "actions": [{"type": "set", "key": "margin_pct", "value": 5}]}}])
    r = AG.run(llm, "m", "use a 5% margin", "snapshot", "", TOOLS, "actions")
    assert r["actions"] and r["calls"] == 1


def test_gives_up_cleanly_so_plain_code_answers():
    llm = Fake([{"final": {"reply": "Let me check that."}}] * 5)
    with pytest.raises(ValueError):
        AG.run(llm, "m", "x?", "s", "", TOOLS, "actions")


@pytest.mark.parametrize("promise", ["I’ll look for any alternative moves that could save more.", "Let me quickly check that.",
                                     "I will search for a better schedule.", "I'm going to compare the options."])
def test_every_kind_of_promise_is_caught(promise):
    llm = Fake([{"final": {"reply": promise, "actions": []}}, {"tool": "better_options", "args": {}},
                {"final": {"reply": "No better move: your limits stop the press line at 10:00.", "actions": []}}])
    r = AG.run(llm, "m", "Can you check if there's any other way to increase savings?", "s", "", TOOLS, "actions")
    assert r["steps"] and r["reply"].startswith("No better move")


def test_chain_moves_on_when_a_model_reply_is_rejected():
    from righthour.llm import LLMClient
    c, tried = LLMClient("Offline"), []

    def fake(model, system, prompt, max_tokens=800):
        tried.append(model)
        if model == "openai/gpt-oss-120b":
            raise RuntimeError("Groq error 429: rate limit reached on tokens per day")
        if model == "openai/gpt-oss-20b":
            raise RuntimeError("Groq error 400: Tool choice is none, but model called a tool")
        return "ok", 1, 1
    c._complete = fake
    assert c.complete("openai/gpt-oss-120b", "s", "p")[0] == "ok" and c.last_model == "qwen/qwen3.8-27b"


def test_a_reply_marked_unanswered_is_carried_out_whatever_its_wording():
    """Not phrase matching: the agent marks its own reply as not yet an answer, and the loop makes it finish the job."""
    llm = Fake([{"final": {"reply": "There may be a few other options worth exploring here.", "actions": [], "answered": False}},
                {"tool": "better_options", "args": {}},
                {"final": {"reply": "No: your limits stop the press line at 10:00, which would save ₹150.", "actions": [], "answered": True}}])
    r = AG.run(llm, "m", "any other way to save?", "s", "", TOOLS, "actions")
    assert [s["tool"] for s in r["steps"]] == ["better_options"] and r["reply"].startswith("No:")
    assert "was a plan, not an answer" in llm.prompts[1]


def test_never_loops_forever():
    """A model that only ever asks for more lookups, or only ever plans, is stopped after MAX_CALLS calls."""
    for script in ([{"tool": "better_options", "args": {}}] * 50,
                   [{"final": {"reply": "Some thoughts.", "actions": [], "answered": False}}] * 50,
                   [{"nonsense": 1}] * 50):
        llm = Fake(script)
        with pytest.raises(ValueError):
            AG.run(llm, "m", "x", "s", "", TOOLS, "actions")
        assert len(llm.prompts) <= AG.MAX_CALLS
