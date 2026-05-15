# Engineering paper types

Use this file to adjust emphasis by paper type. The package structure stays stable, but the deepest
questions should change with the paper.

## Methods or algorithm paper

Primary questions:

- What bottleneck is the method solving?
- What is actually new versus recombined?
- Are the method steps specific enough to implement?
- Do the experiments support the claimed gains?
- Does the code, if present, expose the method clearly?

Emphasize:

- `claim_evidence_map.md`
- `experiment_audit.md`
- `core_modules.md`
- `tensor_flow.md`
- `math_to_code_map.md`

When code is available, additionally require:

- true key-module identification by entrypoint or forward-path tracing
- source-grounded code snippets or examples for each selected module
- fake-data forward reasoning for each selected module
- tensor-flow tracing with diagram or shape-table output
- formula -> code -> tensor ops -> engineering behavior mapping
- engineering bottleneck analysis per module
- explicit uncertainty labels on unresolved shapes, dependencies, or loss-path segments

## Benchmark or evaluation paper

Primary questions:

- Is the benchmark definition clear?
- Are baselines appropriate and fairly configured?
- Are metrics meaningful and consistently reported?
- Are conclusions stronger than the evaluation supports?
- Are hidden test-time tricks, prompt budgets, or compute differences affecting fairness?

Emphasize:

- benchmark protocol clarity
- baseline fairness
- score interpretation
- coverage gaps
- overclaim risk

Do not force benchmarks into full module extraction unless code-grounded model understanding is
part of the user request.

## Systems or infrastructure paper

Primary questions:

- What system constraint or operational problem is addressed?
- What is the real deployment setting?
- Are latency, throughput, memory, reliability, or scaling claims well supported?
- What assumptions or infrastructure dependencies are hidden?
- Is the evaluation realistic for production-like use?

Emphasize:

- `engineering_analysis.md`
- systems claim support
- scale and resource assumptions
- failure mode visibility

When code is available, additionally require:

- true key-module identification by entrypoint or forward-path tracing
- source-grounded code snippets or examples for each selected module
- per-module engineering bottleneck analysis (performance, memory, parallelization, deployment)
- tensor-flow tracing with diagram or shape-table output
- formula -> code -> tensor ops -> engineering behavior mapping

## Theory-heavy hybrid paper

Primary questions:

- Which claims are formally proved versus empirically suggested?
- What assumptions make the theory matter in practice?
- Is the bridge from theorem to implementation explicit?
- Does the code or experiment actually instantiate the stated theory?

Emphasize:

- separating theorem-backed claims from empirical claims
- assumptions and scope
- `math_to_code_map.md`
- limits of implementation inference

When code is available, additionally require:

- true key-module identification by entrypoint or forward-path tracing
- source-grounded code snippets or examples for each selected module
- formula-to-code mapping with explicit uncertainty where the implementation simplifies or diverges from the theory
- distinction between direct theory-to-code alignment and empirically tuned code

## Applied engineering paper

Primary questions:

- What practical task or workflow improves?
- Are the reported gains operationally meaningful?
- What deployment constraints, data assumptions, or domain shifts matter?
- Is reproducibility strong enough for adoption?

Emphasize:

- engineering payoff
- hidden operational costs
- domain-specific assumptions
- reproducibility risk

When code is available, additionally require:

- true key-module identification by entrypoint or forward-path tracing
- source-grounded code snippets or examples for each selected module
- per-module deployment and maintainability analysis
- explicit labeling of missing data pipeline, preprocessing, or config logic

## Dataset or resource paper

Primary questions:

- What resource gap is being filled?
- Is data construction or curation transparent?
- Are coverage, bias, and quality control visible?
- Are access, licensing, or maintenance concerns discussed?

Emphasize:

- source generation workflow
- QC process
- reuse boundaries
- access and sustainability notes

## Review or survey paper

Primary questions:

- What framework organizes the field?
- Are comparisons descriptive or evaluative?
- Where does the survey overstate consensus?
- What gaps, controversies, or open questions are most useful?

Emphasize:

- taxonomy quality
- evidence boundaries
- what is synthesized versus directly established

Do not force reviews into module extraction unless the user explicitly requests code-grounded
model understanding.

## Default adaptation rule

If a paper spans categories, choose the category that drives the strongest user need and note the
secondary type. Example: a method paper with a large benchmark should still use methods logic first
if the user is trying to understand implementation, but it should borrow benchmark-audit checks for
fairness.
