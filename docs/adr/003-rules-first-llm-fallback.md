# ADR 003 — Rules-first parsing with cached LLM fallback

- **Status:** Accepted
- **Date:** 2026-04

## Decision

Every parser (classifier, signal extractor, follow-up extractor) follows the
same hybrid pattern:

1. A deterministic rule pass produces `(payload, confidence, reasons)`.
2. If `confidence >= settings.parse.llm_escalation_threshold`, the rule output
   is accepted as-is.
3. Otherwise — and only when an LLM is configured — the message is sent to a
   provider-agnostic `LLMClient` with a strict JSON schema. The response is
   cached by `(model, prompt_version, content_hash)` so re-runs are free.

`parse_method` (`rules` or `llm`), `parse_version`, `parse_confidence`, and
`parse_reasons` are persisted alongside every row, so the entire pipeline can be
audited and replayed.

## Why

- Headline cost on the available historical data (~12k messages, ~5k signals)
  with a pure LLM-per-message design would be on the order of dollars per run,
  every run. Rules-first reduces this to dozens of escalations at most.
- Determinism is critical for a portfolio/learning artifact: anyone running
  `signalyze parse signals` twice must get bit-identical results.
- Rules surface their own failure modes (low confidence, ambiguous keywords),
  which makes it obvious *when* the LLM is being relied on and *why*.
- Schema-constrained LLM output (`structured_call`) eliminates the entire class
  of "the model returned slightly different JSON" failures.

## Alternatives considered

1. **LLM-only parsing.** Rejected on cost and determinism grounds; also fragile
   without a strong cache.
2. **Rules-only.** Rejected because some signal formats (image-only signals,
   heavily emoji-decorated text, group-specific abbreviations) genuinely need
   a fallback. Even after the parser passes the golden bar, real messages keep
   producing new variations.
3. **Train a small classifier.** Rejected for v1: not enough labelled data, and
   the LLM fallback already covers the long tail at trivial cost.

## Consequences

- The cost guard `SIGNALYZE_LLM_MAX_USD_PER_RUN` (default $2) is hard-enforced
  in `LLMClient.structured_call`; once breached, the runner falls back to the
  rule output.
- Adding a new group's quirky format means: add a deterministic rule, expand
  the golden set, re-run. If the rule can't be expressed cleanly, leave it as
  an LLM-escalation path and document it.
- Prompts are versioned (`classify-v1`, `parse-signal-v1`, `parse-followup-v1`,
  `link-tiebreak-v1`); changing a prompt invalidates the cache for that prompt
  only.
