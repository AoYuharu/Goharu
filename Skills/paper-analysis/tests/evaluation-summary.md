# Evaluation summary

This test bundle checks whether the skill stays analysis-first, traceable, and conservative.

## Covered behaviors

- full methods-paper analysis with paper-plus-code inputs
- failure handling for abstract-only or otherwise weak inputs
- resistance to hallucinating benchmark fairness, repository status, or tensor details
- preservation of the `model/` stage when code or architecture understanding matters
- code-grounded key-module identification from entrypoint, builder, or forward-path tracing
- per-module source-grounded snippet or example presence
- tensor-flow diagram or shape-table fallback
- formula-to-code-to-ops mapping with engineering behavior
- honesty around extraction runnability and unresolved shapes

## Expected outcomes

A good implementation should:

- look like a native Claude Code skill package rather than a single long prompt
- preserve `raw/`, `analyze/`, `model/`, `summary.md`, and `failure_report.md`
- push ambiguous or missing details into visible uncertainty labels
- remain useful for quick reads without pretending to complete a full audit
- stay bilingual-aware without forcing every output into Chinese
- ground module selection in call-path tracing rather than directory names
- include source-grounded code snippets in each module folder when code exists
- represent tensor flow as a diagram or shape table, not prose
- mark extraction plans as plans rather than claiming runnable extraction

## Common regression risks

- drifting back into plain summarization
- dropping repository or tensor-flow analysis from the workflow
- overstating baseline fairness from incomplete tables
- assuming repository existence implies reproducibility
- hiding failure states behind polished prose
- identifying "core modules" from directory names without call-path evidence
- skipping forward-path trace when entrypoints or builders are visible
- providing tensor flow as narrative prose without diagram or shape-table structure
- omitting formula-to-code-to-ops mapping when code is available
- silently claiming runnable extraction or verified shapes without evidence
