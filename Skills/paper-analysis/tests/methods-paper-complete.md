# Test: methods paper complete

## Scenario

The user provides a full methods paper PDF, readable figures, and a local code folder. They ask for a
rigorous engineering analysis rather than a summary.

## Expected behavior

- Route to `full-analysis`.
- Preserve the staged package with `raw/`, `analyze/`, and `model/`.
- Use the code folder to strengthen repository, module, math-to-code, and tensor-flow sections.
- Keep any unresolved tensor detail visible instead of guessing.

## Minimum acceptable output traits

- readiness is `complete` or `partial_with_gaps`, depending on actual code coverage
- claim-evidence mapping covers the main method and benchmark claims
- experiment audit comments on baseline fairness and ablations
- `model/` includes repository detection, `training_eval_path.md` with entrypoint or builder trace, `core_modules.md` with call-path-based module selection, and tensor/data-flow notes
- final summary distinguishes well-supported results from paper-only inference

## Code-grounded module requirements

When code is available, additionally require:

- `training_eval_path.md` shows a concrete trace with source anchors (not generic descriptions)
- `core_modules.md` selects modules from observed call-path evidence (not directory names)
- each key module includes at least one source-grounded code snippet or example
- tensor flow uses a diagram or shape-table fallback, not narrative prose alone
- `math_to_code_map.md` connects equations to code anchors, ops, tensor effects, and engineering behavior
- fake-input forward plans are present for each selected module or explicitly marked blocked
- unresolved shapes, missing dependencies, and incomplete loss paths are labeled

## Automatic fail triggers

- output is mostly a narrative summary
- repository is treated as validated without inspection
- tensor shapes are invented without evidence
- code execution or reproduction is claimed without direct verification
- core modules are identified from filenames only without call-path evidence
- source-grounded code snippets or examples are omitted when code exists
- tensor flow is described as narrative prose without diagram or shape-table structure
- formula-to-code mapping omits tensor ops or engineering behavior
- runnable extraction or verified shapes are claimed without sufficient evidence
