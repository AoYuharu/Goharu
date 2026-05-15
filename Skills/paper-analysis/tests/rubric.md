# Test rubric

Use this rubric to judge whether the skill behavior is acceptable.

## Pass criteria

A passing output must satisfy all of the following:

- The mode fits the request and the readiness label is honest.
- The output does not collapse into a plain summary when the request calls for analysis.
- Major claims are tied to supplied evidence or clearly marked unsupported.
- No metrics, repositories, figures, tensor flows, code paths, or implementation details are fabricated.
- `raw/`, `analyze/`, and `model/` are all preserved conceptually when relevant.
- `model/` reasoning stays conservative and visibly marks uncertainty.
- Experiment critique covers the main fairness or evidence risks instead of only restating results.
- Failure states and missing source material are explicit.

## Strong pass signals

- The output cleanly separates `SOURCE_FACT`, `AUTHOR_CLAIM`, and `ASSISTANT_INFERENCE` where needed.
- Benchmark gains and engineering practicality are analyzed separately.
- Repository detection is distinguished from repository inspection and reproduction.
- Supplement-dependent claims are labeled instead of silently accepted.
- The final summary tells the user what is supported, what is weak, and what to verify next.

## Code-grounded module analysis requirements

When code or a repository is available, the following additional criteria apply:

### Required for pass

- Key modules are selected by call-path tracing from entrypoints, builders, or top-level `forward()`, not by filenames alone.
- Each selected module includes exact source anchors.
- Each selected module includes at least one source-grounded code snippet or example.
- The output distinguishes observed code structure from hypothetical extraction planning.
- Each selected module has a fake-input forward plan or an explicit reason it cannot be made credible.
- Tensor flow is represented as a diagram or shape table, not vague prose alone.
- Math-to-code mapping includes actual tensor ops and engineering behavior, not only high-level correspondence.
- Unresolved shapes, missing dependencies, or incomplete loss paths are explicitly marked.

### Strong pass

- Per-module folders follow the required artifact contract.
- `core_modules.md` reads as a selection rationale, not a directory listing.
- Rejected modules are documented with reasons for rejection.
- Extraction plans distinguish runnable extraction from planning-only.
- Shape traces distinguish shapes observed from code, inferred from weights, and unresolved.
- Every readiness label matches actual content.

## Fail conditions

Any of these should count as failure:

- confident claims unsupported by supplied paper, supplement, or inspected code
- invented benchmark fairness, code behavior, or tensor dimensions
- omission of important uncertainty despite incomplete source material
- claiming code execution or reproduction without direct verification
- drifting into generic praise or high-level summary only
- dropping the `model/` stage when the request includes repository or architecture understanding
- hiding extraction or access failures
- identifying `core modules` from filenames only without call-path evidence
- omitting source-grounded code snippets or examples when code exists
- skipping forward-path tracing when entrypoints or builders are visible
- providing only narrative tensor-flow prose without a diagram or shape-table structure
- omitting formula-to-code-to-ops mapping when code equations are visible
- silently claiming runnable extraction, verified shapes, or faithful extraction without sufficient evidence
