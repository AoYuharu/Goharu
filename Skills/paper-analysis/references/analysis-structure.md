# Analysis structure

Package anatomy and per-file deliverable specs.

## Package layers

1. `raw/` — extraction and source inventory, no interpretation
2. `analyze/` — claim, evidence, experiment, and engineering reasoning
3. `model/` — repository, module, tensor/data-flow, math-to-code reasoning
4. `summary.md` — concise synthesis
5. `failure_report.md` — explicit gaps, breakpoints, unverified areas

## `raw/` deliverables

### `source_inventory.md`
What files, excerpts, figures, and links were supplied; provenance notes.

### `paper_structure.md`
Title, authors, sections/headings, appendix presence, figure/table/equation counts.

### `sections/`
Each major section (Abstract, Introduction, Related Work, Methods, Results, Discussion,
Conclusion) extracted as a standalone file. Keep raw text — no polishing.

### `overall_summary.md`
Summary of all extracted content: what the paper covers, its scope, and key terms.

### `figure_table_index.md`
Figure/table numbers, panels, captions, pages, and what each visibly contains.

### `citations.bib`
One BibTeX entry per cited item. For each: where in the paper it appears, why it is invoked.

### `citation_notes.md`
Cited prior work categories, benchmark references, dependency notes, unresolved entries.

### `extraction_log.md`
OCR issues, unreadable regions, missing sections, repo clone status.

## `analyze/` deliverables

### `objective_overview.md`
Problem being solved, claimed novelty, paper type, evidence pathway for main thesis.

### `claim_evidence_map.md`
Minimal row: claim ID/text, category, evidence source, support strength, risk flags, note.

Claim categories: `performance`, `efficiency`, `robustness`, `scalability`, `interpretability`,
`theory`, `systems`, `engineering_practicality`.

Support: `direct` / `partial` / `indirect` / `unsupported_from_supplied_material`.

Common risk flags: `weak_baseline`, `unclear_metric`, `supplement_dependent_claim`,
`unfair_compute_comparison`, `missing_ablation`, `missing_statistics`,
`missing_implementation_detail`, `unclear_data_split`, `code_paper_mismatch`.

### `insight_extraction.md`
Core insights worth remembering, why they matter, whether directly demonstrated or interpretive.
For each insight: what gap in Related Work it fills, what prior methods lacked.

### `experiment_audit.md`
Evaluation design, baseline fairness, metrics clarity, ablations/robustness/controls, missing
details that limit confidence. Use risk flags from claim-evidence taxonomy.

### `engineering_analysis.md`
Adoption value, assumptions needed for gains to hold, hidden costs/dependencies/barriers,
academically interesting vs. operationally actionable, what engineers still need to know.

## `model/` deliverables (code-grounded; downgrade to paper-inferred if no repo)

### `repository_detection.md`
Whether repo referenced, whether inspected, whether appears paper-aligned, what couldn't be checked.
Labels: `repository_detected` / `repository_inspected` / `repository_matches_paper` — never
`reproduction_confirmed` without direct run evidence.

### `repo_structure.md`
Top-level modules, likely entry points, config/script surface, major missing pieces.

### `training_eval_path.md`
Reconstruct: `config/CLI → model builder → instantiated modules → forward → output heads → loss`.
For each segment: observed / partially observed / unclear. If entrypoints absent, state fallback.

Template skeleton:
```
- Entrypoint:
- Config/CLI flow:
- Model builder:
- Top-level forward anchor:
- Output head:
- Loss path:
- Path breakpoints / uncertainties:
```

### `core_modules.md`
Selection rationale, not a directory listing. Per selected module: why key, call-path evidence,
source anchors, train/eval/forward path status, uncertainties. Also list rejected candidates
and why they were excluded.

### `tensor_flow.md`
Preferred: diagram grounded in observed path. Fallback: shape table.

Shape table columns: `Stage | Code anchor | Input shape | Output shape | Key op | Confidence | Note`.
Confidence values: `observed`, `inferred_from_weights`, `inferred_from_config`, `unresolved`.

### `math_to_code_map.md`
Cross-module summary: `Paper equation | Module | Code anchor | PyTorch ops | Tensor transformation | Engineering behavior | Confidence`.

### `core_modules/module_index.md`
All selected modules with one-sentence rationale, dependency links, readiness per module.

### Per-module folder (`core_modules/<name>/`)
```
README.md              — overview, why key, source anchors, caller/callee, readiness
original_code.md       — source-grounded excerpt with file:line anchors, omitted dependencies noted
runnable_extraction.md — smallest credible extraction boundary, required deps, blocked simplifications
fake_input_demo.md     — fake tensor spec, invocation sketch, whether runnable/partial/blocked
shape_trace.md         — step-by-step shape changes with confidence markers
math_mapping.md        — formula → code anchor → ops → behavior, mismatch notes
engineering_analysis.md — compute/memory/parallelization/quantization/deployment/maintainability
module_relations.md    — upstream inputs, downstream consumers, loss-path relation, dependency gaps
```

If a file cannot be completed honestly, keep it and state why. Readiness per module:
`complete` / `partial_with_gaps` / `blocked`.

## `summary.md`
What the paper contributes, what is convincingly supported, what is weak or ambiguous,
whether code and paper align, what to verify next.

## `failure_report.md`
Missing source material, low-confidence inference zones, extraction failures, inaccessible
supplement/repo, blocked module extraction boundaries, unresolved shapes/loss-path segments.
Must be present whenever there are material gaps.

## Task-mode templates

### `quick-read` skeleton
```
Readiness (mode, state, sources, gaps)
What the paper is doing (problem, idea, contribution)
What seems supported / What is uncertain
Discussion hooks
```

### `claim-audit` skeleton
```
Readiness
Claim-evidence table (claim | type | evidence | support | risk | note)
Overclaim risk summary
```

### `benchmark-audit` skeleton
```
Readiness
Benchmark scope (task, datasets, metrics, baselines)
Claim-evidence table
Findings (baseline fairness, compute comparability, metric clarity, missing ablations, overclaim risk)
Bottom line
```

### `methods-audit` skeleton
```
Readiness
Method decomposition (problem, steps, claimed novelty)
Implementation traceability (code anchors, entry points, missing detail, math-to-code confidence)
Flow reasoning (input → transformations → output, uncertain boundaries)
Reproducibility risks
```

### `discussion-prep` skeleton
```
Paper snapshot (problem, claim, why people care)
Three strengths / Three weaknesses
Discussion questions
What to verify next
```

## Compression rules

- `quick-read`: all five layers conceptually visible, but compressed.
- `triage-only`: emphasize source inventory, readiness, and next steps.
- `claim-audit`: emphasize claim_evidence_map and failure notes.
- `benchmark-audit`: emphasize experiment_audit and baseline-fairness notes.
- `discussion-prep`: emphasize insights, strengths, weaknesses, open questions.
- If code is absent: preserve `model/` structure conceptually, downgrade to paper-inferred, label the downgrade.
