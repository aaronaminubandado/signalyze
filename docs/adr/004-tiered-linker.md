# ADR 004 — Four-tier linker with explicit confidence scores

- **Status:** Accepted
- **Date:** 2026-04

## Decision

The signal-to-follow-up linker tries four strategies in order, and stamps every
resulting link with `link_method`, `link_confidence`, and `reasons`:

| Tier | Method            | Trigger                                                                  | Confidence band |
| ---- | ----------------- | ------------------------------------------------------------------------ | --------------- |
| 1    | `reply_to`        | Follow-up's Telegram `reply_to_msg_id` resolves to a known signal.       | 0.98            |
| 2    | `temporal_numeric`| Within the active window AND cites a price/TP index matching the signal. | 0.55 – 0.95     |
| 3    | `recent_open`     | Within the active window with no numeric match.                          | 0.30 – 0.95     |
| 4    | `llm_tiebreak`    | Two or more tier-2/3 candidates within `tiebreak_epsilon` of each other. | inherits        |

Links below `min_link_confidence` (default 0.6) are excluded from headline
metrics and are exported by `signalyze link export-review` for human review.

## Why

- **`reply_to` is free and correct.** ~85% of follow-ups in the historical
  data are explicit replies; matching them yields ~0.98 confidence without
  any heuristics.
- **Numeric matches are unambiguous when they exist.** A follow-up that quotes
  TP3 = 4710 and a signal in the same group an hour earlier with TP3 = 4710
  is almost certainly linked, regardless of message order.
- **Recency-only is a known-noisy bucket.** It is kept because excluding it
  would drop ~7% of otherwise plausible links, but it's heavily down-weighted
  and surfaced for review.
- **LLM tiebreak is gated.** It fires only when rules genuinely cannot decide
  and an LLM is configured; this keeps cost and latency bounded.

## Alternatives considered

1. **`reply_to` only.** Rejected: ~15% of follow-ups in this corpus do not
   carry `reply_to_msg_id`. Many groups paste TP-hit messages as new posts.
2. **Pure LLM matcher per follow-up.** Rejected on cost; also throws away the
   free `reply_to` signal.
3. **A single fused score with no method tag.** Rejected: surfacing the method
   makes debugging trivial and supports downstream filtering (e.g. compute
   per-method win rates to detect biased tiers).

## Consequences

- The linker is incremental and pure: feeding it more signals/follow-ups
  produces more links but never rewrites prior decisions.
- Manual review is a first-class workflow, not an afterthought: the CSV export
  contains the exact follow-up and signal texts side-by-side.
- Downstream stages may demand a `link_confidence` floor (e.g.
  `evaluate reported --min-link-confidence 0.6`) so weak links never poison
  headline numbers.
