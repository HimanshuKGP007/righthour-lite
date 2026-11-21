"""Optional language-model calls. Providers: Local (offline), Groq, Grok (xAI) and Claude (Anthropic). With no key, or "Offline", the
T1 and T2 steps run as deterministic templates and parsers, and the log says so (mode = 'offline template').
Numbers never come from a language model in any mode.

Both providers are called over plain HTTP with the standard library: no extra package to install.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

PROVIDERS = ("Offline", "Groq", "Grok", "Claude")
LABEL = {"Offline": "Local (offline)", "Groq": "Groq", "Grok": "Grok (xAI)", "Claude": "Claude"}
KEY_ENV = {"Groq": "GROQ_API_KEY", "Grok": "XAI_API_KEY", "Claude": "ANTHROPIC_API_KEY"}
CHAT_URL = {"Groq": "https://api.groq.com/openai/v1/chat/completions", "Grok": "https://api.x.ai/v1/chat/completions"}
MODELS_URL = {"Groq": "https://api.groq.com/openai/v1/models", "Grok": "https://api.x.ai/v1/models",
              "Claude": "https://api.anthropic.com/v1/models?limit=100"}
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
_NOT_CHAT = ("whisper", "tts", "guard", "playai", "orpheus", "embed", "image", "imagine", "video", "vision-only", "moderation")


def default_provider() -> str:
    for p in ("Groq", "Grok", "Claude"):
        if os.environ.get(KEY_ENV[p]):
            return p
    return "Offline"


class LLMClient:
    def __init__(self, provider: str = "Offline", api_key: str | None = None, error_sink: dict | None = None):
        # error_sink belongs to one browser session, so one visitor never sees another visitor's errors
        self.sink = error_sink if error_sink is not None else {}
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
            return f"{LABEL[self.provider]} connected"
        return "Local templates, no AI calls" if self.provider == "Offline" else f"{LABEL[self.provider]}: add a key"

    def _auth(self) -> dict:
        if self.provider == "Claude":
            return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        return {"Authorization": f"Bearer {self.key}"}

    def _post(self, url: str, headers: dict, body: dict | None = None) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json", "User-Agent": "righthour-lite/1.0", **headers})
        for attempt in range(3):                   # "too many requests" (429): wait as the provider asks, then try again
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    body_txt = e.read().decode(errors="replace")
                    m = re.search(r"try again in ([\d.]+)(ms|s)", body_txt)
                    wait = float(m.group(1)) / (1000 if m and m.group(2) == "ms" else 1) if m else float(e.headers.get("retry-after") or 3)
                    time.sleep(min(max(wait, 0.5), 20) + 0.5)
                    continue
                self._fail(e)
            except (urllib.error.URLError, TimeoutError) as e:
                self.sink["text"] = f"{LABEL[self.provider]} could not be reached"
                raise RuntimeError(self.sink["text"]) from e
        raise RuntimeError(self.sink.get("text") or "no answer")

    def _fail(self, e) -> None:
        if True:
            body_ = e.read().decode(errors="replace")
            msg = re.sub(r"(gsk_|sk-|xai-)[A-Za-z0-9_\-]+", "[key hidden]", body_)
            msg = re.sub(r"org_[A-Za-z0-9]+", "[account]", msg)
            m_ = re.search(r'"message"\s*:\s*"([^"]{0,220})', msg)            # the provider's own words, not the raw JSON
            msg = m_.group(1) if m_ else msg[:200]
            self.sink["text"] = f"{LABEL[self.provider]} error {e.code}: {msg}"
            raise RuntimeError(self.sink["text"]) from e

    def list_models(self) -> list[str]:
        """The models this key can really use right now, asked of the provider itself."""
        if not self.live:
            raise RuntimeError("add a key first")
        d = self._post(MODELS_URL[self.provider], self._auth())
        ids = sorted({m.get("id", "") for m in d.get("data", []) if m.get("id")})
        return [i for i in ids if not any(w in i.lower() for w in _NOT_CHAT)]

    def complete(self, model: str, system: str, prompt: str, max_tokens: int = 800) -> tuple[str, int, int]:
        if not self.live:
            raise RuntimeError("offline")
        if self.provider in CHAT_URL:
            body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            if re.search(r"gpt-oss|qwen3|reason", model.lower()):     # thinking models: think briefly, and leave room for the answer
                body.update(max_tokens=max_tokens + 1500, reasoning_effort="low")
            d = self._post(CHAT_URL[self.provider], self._auth(), body)
            u = d.get("usage", {})
            return d["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        d = self._post(CLAUDE_URL, self._auth(),
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


def suggest_models(provider: str, ids: list[str]) -> tuple[str, str]:
    """Pick a small and a large model from a live list: the small one for routine steps, the large one for hard cases."""
    if not ids:
        raise ValueError("no models to choose from")

    def size(i: str) -> float:
        m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", i.lower().replace("-", " ").replace("_", " "))
        if m:
            return float(m.group(1))
        low = i.lower()
        if any(w in low for w in ("haiku", "mini", "instant", "fast", "flash", "lite", "small")):
            return 8.0
        if any(w in low for w in ("opus", "heavy", "versatile", "large", "sonnet")):
            return 400.0
        return 100.0

    ranked = sorted(ids, key=lambda i: (size(i), i))
    if provider == "Claude":
        hk = [i for i in ids if "haiku" in i]
        sn = [i for i in ids if "sonnet" in i]
        return (sorted(hk)[-1] if hk else ranked[0], sorted(sn)[-1] if sn else ranked[-1])
    skip = r"preview|deprecated|whisper|guard|tts|playai|orpheus|allam|embed|distil|audio|vision"   # not general chat models
    prefer = [i for i in ids if not re.search(skip, i.lower())] or ids
    pr = sorted(prefer, key=lambda i: (size(i), i))
    known = [i for i in pr if re.search(r"instant|gpt-oss|llama|qwen|gemma|mixtral|kimi|grok|mistral", i.lower())] or pr
    return known[0], known[-1]
