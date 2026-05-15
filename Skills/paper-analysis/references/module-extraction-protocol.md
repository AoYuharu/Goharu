# Module extraction protocol

Engineering-grade rules for selecting, extracting, tracing, and documenting core modules when
code is available. Makes the `model/core_modules/` stage a genuine engineering investigation,
not a folder summary.

Activate when: code/repo is available and the user asks for code understanding, repo-grounded
analysis, module extraction, tensor/data-flow tracing, implementation fidelity, or math-to-code
mapping. If code is absent, keep the same artifact structure conceptually but downgrade to
paper-inferred and label the downgrade.

## 1. Module selection — "key" means call-path evidence

A module counts as key only if the inspected code shows it:
- lies on the main train/eval/forward path
- implements the paper's distinctive math or algorithm
- materially transforms tensor state
- controls routing, memory, attention, retrieval, temporal state, graph passing, fusion,
  decoding, aggregation, or loss in a way central to the paper's claims
- is a bottleneck or dominant engineering risk clearly exposed by the code

Never select based on: directory name, filename, paper prose claims alone, or because a module
is a convenience wrapper with little algorithmic content. Record rejected candidates and why.

## 2. Module evidence capture

For each selected module, record: module name, source anchors (file:line), caller path, callee path,
input origin, output destination, path membership (train/eval/forward/loss), architectural rationale,
uncertainties.

## 3. Extraction boundary

`runnable_extraction.md` must describe the smallest boundary that preserves real algorithmic behavior.

Define: required imports, helper modules, config objects, tensor contracts, state/caches, external
functions, what can be stubbed safely, and what cannot be stubbed without changing semantics.

Allowed: wrapping in a harness, replacing unavailable data with fake tensors when the operator
contract is preserved, documenting missing dependencies.

Not allowed: rewriting math for easier explanation, silently removing logic branches that matter,
pretending a conceptual rewrite is the original module.

## 4. Fake-input forward reasoning

`fake_input_demo.md` uses fake tensors/data to reason about the module contract, not to imply
real execution success. State: whether the demo is runnable / partial / reasoning plan only;
fake input shapes, dtypes, and what each tensor stands in for; unresolved assumptions.

Use fake inputs only when they preserve the module's visible interface. If dynamic control flow,
external state, or missing deps make reasoning too speculative, keep the file and mark it blocked.

## 5. Execution-status labels

- `OBSERVED_CODE_PATH` — traced in inspected source
- `FAKE_INPUT_FORWARD_PLAN` — reasoning scaffold exists but not executed
- `PARTIAL_RUNNABLE_EXTRACTION_PLAN` — extraction boundary defined but not verified
- `UNVERIFIED_SHAPE_INFERENCE` — shape derived from code logic, not runtime
- `BLOCKED_BY_DEPENDENCY_ENTANGLEMENT` — cannot isolate without resolving external deps

Never blur these. Never claim execution from a reasoning plan.

## 6. Tensor/data-flow tracing

Per module `shape_trace.md`: input tensors, each major transformation step, important intermediates,
output tensors, shape source (observed / inferred / unresolved).

Package-level `tensor_flow.md`: prefer a diagram grounded in observed path; fall back to a
stage-by-stage shape table (`Stage | Code anchor | Input shape | Output shape | Key op | Confidence | Note`).
Confidence: `observed`, `inferred_from_weights`, `inferred_from_config`, `unresolved`.

## 7. Formula → code → ops → behavior mapping

Per module `math_mapping.md`: paper equation → code anchor → PyTorch/tensor ops → engineering
behavior → mismatch notes.

Package-level `math_to_code_map.md`: cross-module summary table.

## 8. Engineering interpretation

Per module `engineering_analysis.md`: compute bottlenecks, memory hotspots, parallelization barriers,
data movement costs, quantization/precision sensitivity, deployment complexity, maintainability risk,
debugging difficulty. Tie every assessment to visible code structure, tensor ops, or dependency patterns.

## 9. Failure handling

If a module cannot be cleanly isolated, shapes are too dynamic, or code is incomplete:
keep the per-module folder, fill what is known, mark missing pieces, downgrade readiness.

Record: missing builder context, hidden config coupling, dynamic shape dependence, custom CUDA/fused
kernel deps, external checkpoint assumptions, undocumented preprocessing contracts, unresolved loss
wiring.

## Per-module folder contract

```
module_name/
├── README.md              — overview, why key, source anchors, caller/callee, readiness
├── original_code.md       — source-grounded excerpt with anchors, omissions noted
├── runnable_extraction.md — boundary, deps, blocked simplifications
├── fake_input_demo.md     — fake tensor spec, invocation sketch, runnable status
├── shape_trace.md         — step-by-step shape changes with confidence markers
├── math_mapping.md        — formula → code → ops → behavior
├── engineering_analysis.md — performance/memory/deployment/maintainability
└── module_relations.md    — upstream/downstream, loss-path relation, dependency gaps
```

If a file cannot be completed honestly, keep it and state why.

## Minimum evidence standard

A passing module analysis requires: call-path-based selection, exact source anchors, at least one
code snippet per module, a fake-input forward plan or explicit reason it's blocked, layer-by-layer
shape reasoning or explicit unresolved markers, formula→code→ops→behavior mapping, engineering
interpretation, and clear distinction between observed code and inference. Anything weaker must
be marked partial.
