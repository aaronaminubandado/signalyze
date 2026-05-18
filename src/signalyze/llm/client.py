"""Provider-agnostic LLM wrapper with cache, retry, structured output, and cost guard.

All in-pipeline LLM calls go through `LLMClient.structured_call`. If no provider is
configured, calls raise `LLMUnavailableError` so callers can degrade gracefully to
"deterministic-only" mode without hidden surprises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from signalyze.config import Settings, get_settings
from signalyze.storage.llm_cache import LLMCache
from signalyze.utils.logging import get_logger

logger = get_logger("signalyze.llm")


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM provider is configured but a call was attempted."""


class CostBudgetExceededError(RuntimeError):
    """Raised when the per-run LLM USD budget is exhausted."""


@dataclass
class LLMUsage:
    """Aggregated usage across a single pipeline run."""

    calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


# Approx pricing per 1M tokens (USD). Used for the soft per-run budget guard.
# These are intentionally rough; cache hits cost zero so the budget guards real spend.
_PRICING_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5-mini": (0.50, 2.00),
}


class LLMClient:
    """Cached, retry-wrapped LLM client supporting structured (JSON-schema) output."""

    def __init__(self, settings: Settings, cache: LLMCache):
        self.settings = settings
        self.cache = cache
        self.usage = LLMUsage()
        self._provider = settings.env.llm_provider.lower()
        self._model = settings.env.llm_model
        self._budget_usd = settings.env.llm_max_usd_per_run
        self._client: Any | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_available(self) -> bool:
        if self._provider == "none":
            return False
        if self._provider == "openai":
            return bool(self.settings.env.openai_api_key)
        return False

    def structured_call(
        self,
        *,
        prompt_version: str,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a JSON-decoded response that conforms to `schema`.

        Cached by `(model, prompt_version, normalized(system+user))`.
        """
        if not self.is_available:
            raise LLMUnavailableError(
                "LLM provider not configured (set SIGNALYZE_LLM_PROVIDER and an API key)."
            )

        content = system + "\n\n---\n\n" + user
        key = self.cache.make_key(self._model, prompt_version, content)
        cached = self.cache.get(key)
        if cached is not None:
            self.usage.cache_hits += 1
            return cached

        if self.usage.cost_usd >= self._budget_usd:
            raise CostBudgetExceededError(
                f"LLM budget exceeded: ${self.usage.cost_usd:.4f} / ${self._budget_usd:.4f}"
            )

        response, tokens_in, tokens_out = self._call_provider(system=system, user=user, schema=schema)
        cost = self._estimate_cost_usd(tokens_in, tokens_out)

        self.usage.calls += 1
        self.usage.tokens_in += tokens_in
        self.usage.tokens_out += tokens_out
        self.usage.cost_usd += cost

        self.cache.set(
            key,
            model=self._model,
            prompt_version=prompt_version,
            content_hash=key.split(":")[-1],
            response=response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
        return response

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _call_provider(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], int, int]:
        if self._provider == "openai":
            return self._call_openai(system=system, user=user, schema=schema)
        raise LLMUnavailableError(f"Unknown LLM provider: {self._provider}")

    def _call_openai(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], int, int]:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - import guard
                raise LLMUnavailableError(
                    "openai package not installed; run `pip install signalyze[llm]`"
                ) from exc
            self._client = OpenAI(api_key=self.settings.env.openai_api_key)

        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "signalyze_output", "schema": schema, "strict": True},
            },
            temperature=0,
        )
        content = completion.choices[0].message.content or "{}"
        parsed = json.loads(content)
        usage = completion.usage
        tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0
        return parsed, tokens_in, tokens_out

    def _estimate_cost_usd(self, tokens_in: int, tokens_out: int) -> float:
        pricing = _PRICING_PER_M_TOKENS.get(self._model)
        if pricing is None:
            return 0.0
        in_cost, out_cost = pricing
        return (tokens_in / 1_000_000) * in_cost + (tokens_out / 1_000_000) * out_cost


_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Process-wide LLM client singleton."""
    global _singleton
    if _singleton is None:
        settings = get_settings()
        cache = LLMCache(settings.resolve(settings.paths.llm_cache_path))
        _singleton = LLMClient(settings=settings, cache=cache)
    return _singleton
