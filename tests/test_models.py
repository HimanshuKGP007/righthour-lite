from righthour import llm
from righthour.router import MODELS, cost_usd, set_models


def test_suggest_models_picks_small_and_large():
    small, large = llm.suggest_models("Groq", ["llama-3.1-8b-instant", "openai/gpt-oss-120b", "qwen/qwen3-32b"])
    assert small == "llama-3.1-8b-instant" and large == "openai/gpt-oss-120b"
    assert llm.suggest_models("Claude", ["claude-haiku-5-5", "claude-sonnet-5-5"]) == ("claude-haiku-5-5", "claude-sonnet-5-5")


def test_chosen_model_without_a_price_costs_zero_not_a_crash():
    old = dict(MODELS["Grok"])
    set_models("Grok", "grok-x-mini", "grok-x")
    assert MODELS["Grok"] == {"T1": "grok-x-mini", "T2": "grok-x"}
    assert cost_usd("grok-x", 1000, 1000) == 0.0
    set_models("Grok", old["T1"], old["T2"])


def test_four_providers_and_key_names():
    assert llm.PROVIDERS == ("Offline", "Groq", "Grok", "Claude")
    assert llm.KEY_ENV["Grok"] == "XAI_API_KEY"
