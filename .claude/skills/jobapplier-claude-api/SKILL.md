---
name: jobapplier-claude-api
description: Develop or review JobApplier's Claude request adapter, prompt caching, usage accounting, or Claude-specific offline tests while preserving its multi-provider architecture.
---

Use this guidance for development in this repository. It does not enable API Skills, change account settings, or authorize paid calls.

- Inspect the current adapter, operation limits, usage normalization and their tests before changing request construction. Preserve the existing raw-HTTP transport and other providers unless the task explicitly changes that architecture.
- Check the relevant current [Anthropic API documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) when model capabilities, cache thresholds, TTLs or prices matter. Record the model and pricing source date; do not copy illustrative rates into production defaults.
- Keep ordinary requests unchanged unless the caller explicitly selects the reviewed caching path. Keep candidate and job evidence in user content; cache eligibility does not turn source material into trusted instructions.
- Account separately for uncached input, cache reads, each write TTL and output. Incomplete or contradictory usage remains unknown. Reserve for a cache miss/write before dispatch; expected hits are not spending authority.
- Prefer synthetic mocked request/response checks first. Do not add hidden warm-up calls, automatic retries, model routing, API tools or payer fallbacks.
- A request or prompt change needs new candidate evidence. Preserve frozen comparison guards and historical receipts; do not loosen them to admit the new shape.

Original project guidance informed by [Anthropic's claude-api skill](https://github.com/anthropics/skills/tree/main/skills/claude-api). No upstream scripts, hooks or skill body are bundled. Repository instructions and the user's approved scope govern consequential actions.
