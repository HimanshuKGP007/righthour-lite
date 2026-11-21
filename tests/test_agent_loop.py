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
    assert "only promised to check" in llm.prompts[1]


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
