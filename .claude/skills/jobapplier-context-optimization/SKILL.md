---
name: jobapplier-context-optimization
description: Assess or improve JobApplier prompt reuse, cache-prefix stability, retrieval scope, and token overhead using measured cost and factual-fidelity evidence.
---

Optimize the repeated work actually present in the request path. Distinguish API prompt caching, agent context management and local result reuse; they have different effects on cost and output.

- Inspect request structure before proposing compression. Put reusable instructions and unchanged candidate evidence before changing job/date details when preparing an explicitly selected caching candidate. Keep evidence in its original trust role and preserve factual constraints.
- Prefer deterministic extraction, bounded retrieval and compact tool results over asking a model to rewrite the same processing code repeatedly. Keep original evidence retrievable through existing access controls; never place private résumé text, credentials or full provider responses in tracked notes.
- Do not summarize away qualifications, unknowns, refusal criteria or reviewer findings to manufacture token savings. Lossy evidence compression, extra reviewers, new model routing and changed study order require their own task scope and quality evidence.
- Compare total work, including cache creation, output, coordination and any compaction calls. Report measured observations separately from estimates; remove an optimization whose overhead exceeds its benefit.
- During the comparison, keep assigned cases, model/settings, scoring-before-correction rules and no-retry/unknown-outcome policy fixed. Cache hits may reduce settled charges but must not be assumed in the pre-dispatch cap reservation.

Original project guidance informed by [Agent Skills for Context Engineering](https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering/tree/main/skills/context-optimization). Its numerical targets are not acceptance thresholds for this project. No third-party executable code, hooks, broad skill pack or automatic context-compaction policy is installed.
