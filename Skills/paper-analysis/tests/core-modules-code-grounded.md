# Test: core modules code-grounded

## Scenario

The user supplies a paper PDF and a fully accessible local code folder. The `train.py` entrypoint,
model builder, and top-level `forward()` path are all traceable. The user asks for code-grounded
understanding of the model's core modules.

## Expected behavior

- Module selection is driven by call-path tracing from `train.py` or the main model builder, not by folder names.
- Every selected module has exact source anchors.
- Every per-module folder contains at least one source-grounded code snippet or example.
- `core_modules.md` reads as a selection rationale with path evidence, not a directory listing.
- Rejected or non-key modules are documented with reasons for rejection.

## Minimum acceptable output traits

- `training_eval_path.md` shows a concrete trace from entrypoint to builder to top-level `forward()`.
- `core_modules.md` includes a selection basis statement and registers path evidence for each module.
- Every per-module folder contains `original_code.md` with file-path-and-line-anchored snippets.
- Every per-module folder contains `fake_input_demo.md` with a fake-input plan or an explicit `blocked` note.
- Every per-module folder contains `shape_trace.md` with shape sources labeled `observed`, `inferred_from_weights`, or `unresolved`.
- Every per-module folder contains `math_mapping.md` connecting formulas to code anchors and tensor ops.
- `module_index.md` summarizes selection rationale, upstream/downstream, and readiness per module.
- Readiness labels per module match content: no module marked `complete` where dependencies are unresolved.

## Automatic fail triggers

- modules are selected by scanning directory names without call-path evidence
- `core_modules.md` lists modules without path evidence or selection rationale
- no source-grounded code snippets are present in per-module folders
- fake-input demo is absent or is silently treated as verified runnable code
- shape trace omits confidence labels or invents shapes without basis
- math mapping stops at "maps to file" without actual tensor ops or engineering behavior
- rejected modules are not documented
- module readiness labels claim `complete` where extraction is unresolved

## Partial-evidence scenario

When code is partial (e.g., builder is visible but loss path is not traced):

- The per-module folder structure should still be created.
- Each file that cannot be completed should state why.
- Status should be `partial_with_gaps` or `blocked`, never `complete`.
- `failure_report.md` should list which module boundaries are blocked.
- Missing dependencies should be named, not silently omitted.
