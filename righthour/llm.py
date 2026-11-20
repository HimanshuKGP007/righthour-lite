"""Optional language-model calls. Two providers, one switch: Groq or Claude (Anthropic). With no key, or "Offline", the
T1 and T2 steps run as deterministic templates and parsers, and the log says so (mode = 'offline template').
Numbers never come from a language model in any mode.

Both providers are called over plain HTTP with the standard library: no extra package to install.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

PROVIDERS = ("Offline", "Groq", "Claude")
KEY_ENV = {"Groq": "GROQ_API_KEY", "Claude": "ANTHROPIC_API_KEY"}
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"


def default_provider() -> str:
    for p in ("Groq", "Claude"):
        if os.environ.get(KEY_ENV[p]):
            return p
    return "Offline"


class LLMClient:
    def __init__(self, provider: str = "Offline", api_key: str | None = None):
        self.provider = provider if provider in PROVIDERS else "Offline"
        env = KEY_ENV.get(self.provider)
        self.key = ((api_key or (os.environ.get(env) if env else "")) or "").strip()
        self.live = self.provider != "Offline" and bool(self.key)

    @property
    def mode(self) -> str:
        return "live API" if self.live else "offline template"

    @property
    def label(self) -> str:
        if self.live:
            return f"{self.provider} live"
        return "Offline templates" if self.provider == "Offline" else f"{self.provider}: add a key"

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "User-Agent": "righthour-lite/1.0", **headers})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{self.provider} error {e.code}: {e.read().decode(errors='replace')[:200]}") from e

    def complete(self, model: str, system: str, prompt: str, max_tokens: int = 800) -> tuple[str, int, int]:
        if not self.live:
            raise RuntimeError("offline")
        if self.provider == "Groq":
            d = self._post(GROQ_URL, {"Authorization": f"Bearer {self.key}"},
                           {"model": model, "max_tokens": max_tokens, "temperature": 0,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
            u = d.get("usage", {})
            return d["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        d = self._post(CLAUDE_URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                       {"model": model, "max_tokens": max_tokens, "system": system,
                        "messages": [{"role": "user", "content": prompt}]})
        u = d.get("usage", {})
        text = "".join(b.get("text", "") for b in d.get("content", []))
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text)}


def number_guard(text: str, allowed: set[str]) -> list[str]:
    """Every number in an explanation must come from the solver's facts. Returns the numbers that don't."""
    allowed_clean = {a.replace(",", "") for a in allowed}
    return sorted(n for n in numbers_in(text) if n not in allowed_clean)
