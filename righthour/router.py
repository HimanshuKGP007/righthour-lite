"""Model router (Section 7.5): the smallest capable tier by default, a large model only on escalation.

Rules are deterministic so every decision can be explained, and every decision is logged.
"""
from __future__ import annotations

from dataclasses import dataclass

# Models per provider for the T1 (small) and T2 (large) tiers. Price per million tokens (input, output): check each
# provider's pricing page before presenting.
MODELS = {
    "Groq": {"T1": "llama-3.1-8b-instant", "T2": "llama-3.3-70b-versatile"},
    "Grok": {"T1": "grok-3-mini", "T2": "grok-3"},
    "Claude": {"T1": "claude-haiku-5-5", "T2": "claude-sonnet-5-5"},
    "Offline": {"T1": "llama-3.1-8b-instant", "T2": "llama-3.3-70b-versatile"},  # named in the log, never called
}
PRICES = {
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
DECK_PRICES = {k: PRICES[k] for k in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")}
_ACTIVE = {"provider": "Groq"}


def set_provider(provider: str) -> None:
    """The UI switch. Every route() call after this picks that provider's models."""
    _ACTIVE["provider"] = provider if provider in MODELS else "Groq"


def set_models(provider: str, small: str, large: str) -> None:
    """The model picker: the small and the large model chosen on screen replace the built-in names for that provider."""
    if provider in MODELS and provider != "Offline" and small and large:
        MODELS[provider] = {"T1": small, "T2": large}


def tier_model(tier: str) -> str | None:
    return None if tier == "T0" else MODELS[_ACTIVE["provider"]][tier]


SMALL_MODEL_MAX_INPUT = 100_000

TASK_DEFAULT = {
    "validate_data": "T0", "forecast_load": "T0", "solar_forecast": "T0", "tariff_lookup": "T0",
    "optimise": "T0", "policy_check": "T0", "verify_savings": "T0", "learn": "T0",
    "constraint_parse": "T1", "explain": "T1", "orchestrate": "T1", "replan": "T1",
    "tariff_parse": "T2", "answer_question": "T2",
}
# Typical token use per call, for cost logging when no live API call is made (team estimate).
TYPICAL_TOKENS = {"constraint_parse": (1500, 300), "explain": (3000, 400), "orchestrate": (2000, 200),
                  "replan": (5000, 800), "tariff_parse": (100_000, 10_000), "answer_question": (4000, 600)}


@dataclass
class Route:
    task: str
    tier: str
    model: str | None
    reason: str


def route(task: str, attempt: int = 1, input_tokens: int = 0, novel: bool = False, infeasible: bool = False) -> Route:
    tier = TASK_DEFAULT.get(task, "T1")
    reason = {"constraint_parse": "short, formulaic note to JSON: small model", "explain": "templated daily explanation: small model",
              "orchestrate": "routine run: small model picks the next tool", "replan": "routine re-plan: small model",
              "answer_question": "free-form question: large model"}.get(task, f"default tier for '{task}'")
    if tier == "T0":
        return Route(task, "T0", None, "deterministic code or ML; no language model")
    if tier == "T1" and attempt >= 3:
        tier, reason = "T2", "small-model output failed validation twice"
    elif tier == "T1" and infeasible:
        tier, reason = "T2", "optimiser found no feasible plan; a soft rule must be chosen"
    elif tier == "T1" and novel:
        tier, reason = "T2", "novel input (first run or unseen document)"
    elif tier == "T1" and input_tokens > SMALL_MODEL_MAX_INPUT:
        tier, reason = "T2", "input too long for the small model"
    elif tier == "T2" and task == "tariff_parse":
        reason = "new tariff order: parsed once, then shared with every plant in the state"
    return Route(task, tier, tier_model(tier), reason)


def cost_usd(model: str | None, tokens_in: int, tokens_out: int) -> float:
    if not model:
        return 0.0
    pin, pout = PRICES.get(model, (0.0, 0.0))   # a model chosen on screen may have no price on file: cost shows as $0
    return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout


def annual_cost_model(calls_per_day: int = 6, tin: int = 4000, tout: int = 1000, days: int = 300,
                      small_share: float = 0.9, sites: int = 500) -> dict:
    """Reproduces the deck's cost ladder (Section 7.5) so the numbers on Slide 5 can be re-derived."""
    per_call = {m: cost_usd(m, tin, tout) for m in DECK_PRICES}
    routed = small_share * per_call["claude-haiku-5-5"] + (1 - small_share) * per_call["claude-opus-5-5"]
    rows = {
        "All Opus 5.5": per_call["claude-opus-5-5"] * calls_per_day,
        "All Sonnet 5.5": per_call["claude-sonnet-5-5"] * calls_per_day,
        "Routed 90% Haiku / 10% Opus": routed * calls_per_day,
    }
    return {k: {"per_day": v, "per_site_year": v * days, "fleet_year": v * days * sites} for k, v in rows.items()}
